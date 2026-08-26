"""Immutable market-data snapshots and their manifests.

A snapshot is a directory under ``data/snapshots/<snapshot_id>/`` containing
three Parquet partitions and a manifest. Once written it is never modified; a
new pull produces a new identifier. :func:`amat.paths.reserve_immutable_directory`
enforces that by refusing an identifier that already exists.

Partitions
----------
``daily.parquet``
    One row per (session, symbol). Carries the contract's three price bases
    side by side and clearly named: ``adjusted_close`` for features,
    ``raw_close`` for valuation, and the raw OHLC/volume for diagnostics.
``window.parquet``
    One row per (session, symbol) with the raw VWAP and traded volume of the
    execution window. A session/symbol with no window bar is simply absent,
    which the panel turns into a missed fill rather than an imputed price.
``actions.parquet``
    Cash dividends and splits by ex-date, recorded so that valuation can be
    reconciled without inferring events from price jumps.

Quality gates run at load time, not only at write time, so a snapshot that was
valid when written cannot be silently reinterpreted later by changed code.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Self

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from amat.backtest.panel import MarketPanel
from amat.data.action_exceptions import (
    DEFAULT_LEDGER_FILENAME,
    ActionExceptionLedger,
    default_ledger_path,
    load_action_exceptions,
)
from amat.data.alpaca import DailyBar, DividendEvent, SplitEvent, WindowBar
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.types import SessionDate, Symbol, parse_symbol
from amat.errors import DataIntegrityError
from amat.paths import reserve_immutable_directory

__all__ = [
    "SnapshotManifest",
    "load_corporate_actions",
    "load_panel",
    "load_manifest",
    "partition_digest",
    "write_snapshot",
]

_DAILY = "daily.parquet"
_WINDOW = "window.parquet"
_ACTIONS = "actions.parquet"
_MANIFEST = "manifest.json"

#: A single-session move larger than this in a *split-adjusted* series is
#: treated as an unexplained discontinuity rather than a market move, unless a
#: split on that ex-date accounts for it.
_MAX_ADJUSTED_SESSION_MOVE = 0.60


class SnapshotManifest(BaseModel):
    """Provenance for one immutable snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    feed: str = Field(min_length=1)
    as_of: dt.datetime
    symbols: tuple[str, ...] = Field(min_length=1)
    first_session: dt.date
    last_session: dt.date
    session_count: int = Field(gt=0)
    daily_rows: int = Field(gt=0)
    window_rows: int = Field(ge=0)
    action_rows: int = Field(ge=0)
    execution_window_start: dt.time
    execution_window_end: dt.time
    exchange_timezone: str = Field(min_length=1)
    partition_sha256: dict[str, str]
    window_coverage: float = Field(ge=0.0, le=1.0)
    """Fraction of (session, symbol) cells that have a usable execution window.

    Reported rather than enforced: a gap is a documented limitation of the
    sample, and the runner records a missed fill for each one. A silently
    back-filled window would be far worse than a visible coverage number.
    """

    @model_validator(mode="after")
    def _require_utc_and_ordered_coverage(self) -> Self:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() != dt.timedelta(0):
            message = "as_of must be a timezone-aware UTC datetime"
            raise ValueError(message)
        if self.last_session < self.first_session:
            message = "last_session must not precede first_session"
            raise ValueError(message)
        for name in (_DAILY, _WINDOW, _ACTIONS):
            digest = self.partition_sha256.get(name)
            if digest is None or len(digest) != 64:
                message = f"manifest is missing a sha256 for partition {name!r}"
                raise ValueError(message)
        return self

    def to_json(self) -> str:
        """Deterministic serialization: sorted keys, no incidental whitespace."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def partition_digest(path: Path) -> str:
    """SHA-256 of a partition file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_snapshot(
    root: Path,
    snapshot_id: str,
    *,
    raw_bars: Sequence[DailyBar],
    adjusted_bars: Sequence[DailyBar],
    window_bars: Sequence[WindowBar],
    dividends: Sequence[DividendEvent],
    splits: Sequence[SplitEvent],
    provider: str,
    feed: str,
    as_of: dt.datetime,
    execution_window_start: dt.time,
    execution_window_end: dt.time,
    exchange_timezone: str,
) -> tuple[Path, SnapshotManifest]:
    """Write a new immutable snapshot and return its directory and manifest.

    Raises:
        DataIntegrityError: if the raw and adjusted pulls disagree on coverage,
            or if any partition fails a quality gate.
        ImmutableArtifactError: if ``snapshot_id`` already exists.
    """
    daily = _build_daily_frame(raw_bars, adjusted_bars)
    _check_daily_quality(
        daily,
        splits,
        snapshot_id=snapshot_id,
        exceptions=load_action_exceptions(Path(root) / DEFAULT_LEDGER_FILENAME),
    )
    window = _build_window_frame(window_bars)
    actions = _build_actions_frame(dividends, splits)

    sessions = daily["session_date"].unique().sort()
    symbols = tuple(sorted(daily["symbol"].unique().to_list()))
    cells = len(sessions) * len(symbols)

    directory = reserve_immutable_directory(root, Path("data/snapshots") / snapshot_id)
    daily.write_parquet(directory / _DAILY)
    window.write_parquet(directory / _WINDOW)
    actions.write_parquet(directory / _ACTIONS)

    manifest = SnapshotManifest(
        snapshot_id=snapshot_id,
        provider=provider,
        feed=feed,
        as_of=as_of,
        symbols=symbols,
        first_session=sessions[0],
        last_session=sessions[-1],
        session_count=len(sessions),
        daily_rows=daily.height,
        window_rows=window.height,
        action_rows=actions.height,
        execution_window_start=execution_window_start,
        execution_window_end=execution_window_end,
        exchange_timezone=exchange_timezone,
        partition_sha256={
            name: partition_digest(directory / name) for name in (_DAILY, _WINDOW, _ACTIONS)
        },
        window_coverage=(window.height / cells) if cells else 0.0,
    )
    (directory / _MANIFEST).write_text(manifest.to_json(), encoding="utf-8")
    return directory, manifest


def load_manifest(directory: Path) -> SnapshotManifest:
    """Read a snapshot manifest and verify every partition digest.

    Raises:
        DataIntegrityError: if a partition is missing or its content no longer
            matches the digest recorded when the snapshot was written.
    """
    path = Path(directory) / _MANIFEST
    if not path.is_file():
        message = f"snapshot has no manifest: {directory}"
        raise DataIntegrityError(message)
    manifest = SnapshotManifest.model_validate_json(path.read_text(encoding="utf-8"))
    for name, expected in manifest.partition_sha256.items():
        partition = Path(directory) / name
        if not partition.is_file():
            message = f"snapshot {manifest.snapshot_id} is missing partition {name}"
            raise DataIntegrityError(message)
        actual = partition_digest(partition)
        if actual != expected:
            message = (
                f"snapshot {manifest.snapshot_id} partition {name} has been modified: "
                f"expected sha256 {expected}, found {actual}"
            )
            raise DataIntegrityError(message)
    return manifest


def load_panel(
    directory: Path,
    symbols: Sequence[str],
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    with_context: bool = False,
) -> tuple[MarketPanel, SnapshotManifest]:
    """Build a :class:`MarketPanel` for ``symbols`` from an immutable snapshot.

    The session axis is the set of sessions on which *every* requested symbol
    has an adjusted close. Requiring complete rows is what makes the Ledoit-Wolf
    covariance estimate well defined; a symbol that only partly covers the range
    is rejected by name instead of being padded, because padding would invent
    returns the market never produced.

    Args:
        with_context: Also load session highs, lows and volume for
            :mod:`amat.context`. Off by default so that every existing caller
            keeps the panel it already had, and so that the extra columns are
            requested by the code that actually needs levels.

    Raises:
        DataIntegrityError: if a symbol is absent, or if no complete session
            remains after intersecting coverage.
    """
    manifest = load_manifest(directory)
    wanted = [str(parse_symbol(raw)) for raw in symbols]
    if len(set(wanted)) != len(wanted):
        message = "duplicate symbols requested"
        raise DataIntegrityError(message)
    missing = [symbol for symbol in wanted if symbol not in manifest.symbols]
    if missing:
        message = f"snapshot {manifest.snapshot_id} lacks symbols: {', '.join(missing)}"
        raise DataIntegrityError(message)

    daily = pl.read_parquet(Path(directory) / _DAILY).filter(pl.col("symbol").is_in(wanted))
    if start is not None:
        daily = daily.filter(pl.col("session_date") >= start)
    if end is not None:
        daily = daily.filter(pl.col("session_date") <= end)

    complete = (
        daily.filter(pl.col("adjusted_close").is_not_null() & pl.col("raw_close").is_not_null())
        .group_by("session_date")
        .agg(pl.col("symbol").n_unique().alias("covered"))
        .filter(pl.col("covered") == len(wanted))
        .select("session_date")
        .sort("session_date")
    )
    sessions: list[dt.date] = complete["session_date"].to_list()
    if not sessions:
        message = (
            f"no session in snapshot {manifest.snapshot_id} has complete coverage for all "
            f"{len(wanted)} requested symbols over the requested range"
        )
        raise DataIntegrityError(message)

    session_index = {session: row for row, session in enumerate(sessions)}
    column_index = {symbol: column for column, symbol in enumerate(wanted)}
    shape = (len(sessions), len(wanted))
    adjusted = np.full(shape, np.nan)
    raw = np.full(shape, np.nan)

    frame = daily.filter(pl.col("session_date").is_in(sessions))
    for session, symbol, adj_close, raw_close in zip(
        frame["session_date"],
        frame["symbol"],
        frame["adjusted_close"],
        frame["raw_close"],
        strict=True,
    ):
        row = session_index[session]
        column = column_index[symbol]
        adjusted[row, column] = adj_close
        raw[row, column] = raw_close

    vwaps = np.full(shape, np.nan)
    volumes = np.zeros(shape)
    window = pl.read_parquet(Path(directory) / _WINDOW).filter(
        pl.col("symbol").is_in(wanted) & pl.col("session_date").is_in(sessions)
    )
    for session, symbol, vwap, volume in zip(
        window["session_date"], window["symbol"], window["vwap"], window["volume"], strict=True
    ):
        row = session_index[session]
        column = column_index[symbol]
        vwaps[row, column] = vwap
        volumes[row, column] = volume

    highs = lows = session_volumes = None
    if with_context:
        highs, lows, session_volumes = _context_arrays(frame, session_index, column_index, shape)

    panel = MarketPanel(
        sessions=tuple(sessions),
        symbols=tuple(Symbol(symbol) for symbol in wanted),
        adjusted_closes=adjusted,
        raw_closes=raw,
        window_vwaps=vwaps,
        window_volumes=volumes,
        source=f"{manifest.provider}:{manifest.feed}:{manifest.snapshot_id}",
        adjusted_highs=highs,
        adjusted_lows=lows,
        session_volumes=session_volumes,
    )
    return panel, manifest


def _context_arrays(
    frame: pl.DataFrame,
    session_index: dict[dt.date, int],
    column_index: dict[str, int],
    shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Session highs, lows and volume, with highs and lows put on the adjusted basis.

    The partition stores ``raw_high`` and ``raw_low`` but no adjusted equivalents,
    so they are rescaled by each row's own factor ``adjusted_close / raw_close``.
    That is the same proportional adjustment the vendor applied to the close, so
    the high, low and close of a session stay in the ratio the market actually
    traded them at.

    Doing this per row rather than once per symbol is what makes it correct
    across a split or a distribution: the factor changes on every ex-date, and a
    single whole-history factor would misplace every level before the most
    recent one. Volume is deliberately **not** rescaled -- a split multiplies
    share counts, but the volume feature is a z-score against the instrument's
    own trailing baseline, and rescaling would inject a step into that baseline
    for a non-market reason.
    """
    highs = np.full(shape, np.nan)
    lows = np.full(shape, np.nan)
    volumes = np.zeros(shape)
    for session, symbol, high, low, volume, raw_close, adj_close in zip(
        frame["session_date"],
        frame["symbol"],
        frame["raw_high"],
        frame["raw_low"],
        frame["raw_volume"],
        frame["raw_close"],
        frame["adjusted_close"],
        strict=True,
    ):
        row = session_index[session]
        column = column_index[symbol]
        # A non-positive raw close makes the factor meaningless. The snapshot's
        # own quality gate already rejects those at write time, so this is a
        # belt-and-braces guard that leaves NaN rather than inventing a level.
        if raw_close is None or raw_close <= 0 or adj_close is None:
            continue
        factor = adj_close / raw_close
        highs[row, column] = high * factor
        lows[row, column] = low * factor
        volumes[row, column] = volume
    return highs, lows, volumes


def load_corporate_actions(
    directory: Path,
    symbols: Sequence[str],
    *,
    sessions: Sequence[dt.date],
    exceptions: ActionExceptionLedger | None = None,
) -> dict[SessionDate, tuple[CorporateAction, ...]]:
    """Read ``actions.parquet`` into the form :func:`run_backtest` consumes.

    This is the input without which :class:`~amat.backtest.accounting.SessionAccountant`
    silently reports an ex-dividend session as a pure price loss. The partition
    has been written since the first ingest; nothing read it back until now, so
    every result produced before 2026-08-10 values positions at the raw close
    while crediting none of the distributions those positions earned.

    Three vendor-data hazards are resolved here rather than downstream, because
    each one has a *plausible* wrong answer that changes reported profit:

    **Exact duplicates collapse.** The snapshot carries two identical rows for
    QQQ on 2022-09-19, both ``0.51856``. QQQ pays quarterly and every
    neighbouring quarter is a single row, so this is one dividend reported
    twice. Summing would credit the position twice; keeping one is the only
    reading consistent with the payment schedule.

    **Conflicting duplicates fail closed, unless a decision is on file.** SHY on
    2021-12-01 carries two rows with *different* rates (``0.018007`` and
    ``0.031906``). That is genuinely ambiguous -- a monthly distribution plus a
    year-end special, or a restated figure -- and the partition does not say
    which. Guardrail 9 forbids guessing, so the default is still refusal.

    What a caller *may* do is record a decision about one specific record in
    :data:`~amat.data.action_exceptions.DEFAULT_LEDGER_FILENAME`, stating the
    reading chosen, the readings rejected, and the evidence. Only records named
    there are resolved; the entry is scoped to one snapshot id and is checked
    against the values actually present, so it cannot outlive the data it was
    written about. Every other conflict raises exactly as before. See
    :mod:`amat.data.action_exceptions`.

    **Ex-dates must land on a session.** An action dated outside ``sessions``
    could never be applied, because the loop never advances through that date.
    Dropping it would lose the entitlement silently, so it raises instead.

    Args:
        directory: Snapshot directory. Its manifest digests are verified first,
            so a modified partition fails here rather than being trusted.
        symbols: Instruments to load actions for. Others are ignored; an action
            for an unheld symbol is not an error, merely irrelevant.
        sessions: The panel's session axis, used to reject unapplicable dates.

    Returns:
        Actions keyed by the session they take effect on, ready to pass to
        ``run_backtest(..., actions_by_session=...)``.

    Raises:
        DataIntegrityError: on a conflicting duplicate or an ex-date outside the
            session axis.
        ConfigurationError: propagated from :class:`~amat.domain.actions.CorporateAction`
            when a row cannot form a valid action -- a non-positive dividend
            rate, or a split whose ratio is 1 and therefore says nothing.
    """
    load_manifest(directory)
    wanted = {str(parse_symbol(raw)) for raw in symbols}
    session_set = set(sessions)
    if not session_set:
        raise DataIntegrityError("cannot load corporate actions for an empty session axis")
    first_session = min(session_set)
    last_session = max(session_set)
    frame = pl.read_parquet(Path(directory) / _ACTIONS).filter(
        pl.col("symbol").is_in(wanted)
        & pl.col("ex_date").is_between(first_session, last_session, closed="both")
    )

    # Collapse exact repeats before looking for conflicts, so that a duplicated
    # row is not mistaken for two payments on the same date.
    frame = frame.unique(subset=["symbol", "ex_date", "kind", "value"], keep="first")

    conflicts = (
        frame.group_by(["symbol", "ex_date", "kind"])
        .agg(pl.col("value").sort().alias("values"))
        .filter(pl.col("values").list.len() > 1)
        .sort("ex_date", "symbol")
    )
    if conflicts.height:
        if exceptions is None:
            exceptions = load_action_exceptions(default_ledger_path(Path(directory)))
        snapshot_id = load_manifest(directory).snapshot_id

        # Resolve only the records a human has decided in writing. Anything
        # else still fails closed, so this widens the ledger by exactly the
        # entries someone signed for and by nothing else.
        unresolved: list[tuple[str, dt.date, str, object]] = []
        resolved: list[tuple[str, dt.date, str, float]] = []
        for row in conflicts.to_dicts():
            symbol = str(row["symbol"])
            conflict_ex_date = row["ex_date"]
            kind = str(row["kind"])
            values = row["values"]
            if not isinstance(conflict_ex_date, dt.date):
                message = (
                    f"snapshot {directory} has a corporate action with a non-date "
                    f"ex_date {conflict_ex_date!r}"
                )
                raise DataIntegrityError(message)
            chosen = exceptions.resolve(
                snapshot_id=snapshot_id,
                symbol=symbol,
                ex_date=conflict_ex_date,
                kind=kind,
                observed=values,
            )
            if chosen is None:
                unresolved.append((symbol, conflict_ex_date, kind, values))
            else:
                resolved.append((symbol, conflict_ex_date, kind, chosen))

        if unresolved:
            detail = "; ".join(
                f"{symbol} {ex_date.isoformat()} {kind}={values}"
                for symbol, ex_date, kind, values in unresolved[:5]
            )
            message = (
                f"snapshot {directory} records {len(unresolved)} corporate action(s) with "
                f"conflicting values on one ex-date and no recorded decision: {detail}. A "
                f"distribution that cannot be resolved to a single rate must not be guessed "
                f"at; correct the snapshot, or record the reading and its evidence in "
                f"{DEFAULT_LEDGER_FILENAME}."
            )
            raise DataIntegrityError(message)

        # Drop every value for a resolved record, then reinstate the one the
        # ledger chose. Filtering to the chosen value directly would silently
        # keep a row whose float repr differs from the recorded one.
        for symbol, resolved_ex_date, kind, chosen in resolved:
            frame = frame.filter(
                ~(
                    (pl.col("symbol") == symbol)
                    & (pl.col("ex_date") == resolved_ex_date)
                    & (pl.col("kind") == kind)
                )
            )
            frame = pl.concat(
                [
                    frame,
                    pl.DataFrame(
                        {
                            "symbol": [symbol],
                            "ex_date": [resolved_ex_date],
                            "kind": [kind],
                            "value": [chosen],
                        },
                        schema=frame.schema,
                    ),
                ]
            )

    actions: dict[SessionDate, list[CorporateAction]] = {}
    for row in frame.sort("ex_date", "symbol", "kind").to_dicts():
        ex_date: dt.date = row["ex_date"]
        if ex_date < first_session or ex_date > last_session:
            # Out of the panel's date range entirely. This is what a
            # date-bounded run means: a 2016 dividend is not an entitlement a
            # backtest starting in 2019 could ever hold, so excluding it is
            # scope, not loss. Found the hard way on 2026-08-11, when the first
            # `--start`-bounded study failed closed on every pre-window
            # dividend in the partition.
            continue
        if ex_date not in session_set:
            # INSIDE the covered range but on no session -- a weekend-stamped
            # ex-date or a coverage hole. Unlike the out-of-range case, a
            # position could exist here and its entitlement would be silently
            # lost, so this still fails closed.
            message = (
                f"{row['symbol']} has a {row['kind']} on {ex_date.isoformat()}, which is "
                "inside the panel's range but not a session; the entitlement could never "
                "be applied and must not be dropped silently"
            )
            raise DataIntegrityError(message)
        kind = ActionKind(row["kind"])
        # Decimal(str(x)) rather than Decimal(x): the partition stores float64,
        # and Decimal(0.228012) expands the binary approximation to 50-odd
        # digits of noise that then propagates into every cash balance. Going
        # through the shortest repr keeps the value the vendor actually quoted.
        # This matches SessionAccountant._accrue, which converts rates the same
        # way for the same reason.
        value = Decimal(str(row["value"]))
        action = CorporateAction(
            symbol=Symbol(row["symbol"]),
            session_date=ex_date,
            kind=kind,
            cash_per_share=value if kind == ActionKind.CASH_DIVIDEND else Decimal(0),
            ratio=value if kind == ActionKind.SPLIT else Decimal(1),
        )
        actions.setdefault(ex_date, []).append(action)

    return {session: tuple(items) for session, items in actions.items()}


# -- construction and quality gates -----------------------------------------


def _build_daily_frame(
    raw_bars: Sequence[DailyBar], adjusted_bars: Sequence[DailyBar]
) -> pl.DataFrame:
    """Join the raw and adjusted pulls on (session, symbol).

    An inner join is deliberate. The two pulls are separate API calls over the
    same range, and a key present in only one of them means the vendor's view
    changed between them -- which invalidates the pairing of adjusted feature
    prices with raw valuation prices for that cell. Such a cell is dropped and
    counted, never half-populated.
    """
    if not raw_bars or not adjusted_bars:
        message = "both a raw and an adjusted daily pull are required"
        raise DataIntegrityError(message)

    raw = pl.DataFrame(
        {
            "session_date": [bar.session_date for bar in raw_bars],
            "symbol": [bar.symbol for bar in raw_bars],
            "raw_open": [bar.open for bar in raw_bars],
            "raw_high": [bar.high for bar in raw_bars],
            "raw_low": [bar.low for bar in raw_bars],
            "raw_close": [bar.close for bar in raw_bars],
            "raw_volume": [bar.volume for bar in raw_bars],
            "raw_vwap": [bar.vwap for bar in raw_bars],
            "trade_count": [bar.trade_count for bar in raw_bars],
        }
    )
    adjusted = pl.DataFrame(
        {
            "session_date": [bar.session_date for bar in adjusted_bars],
            "symbol": [bar.symbol for bar in adjusted_bars],
            "adjusted_close": [bar.close for bar in adjusted_bars],
        }
    )
    for name, frame in (("raw", raw), ("adjusted", adjusted)):
        duplicated = frame.height - frame.select("session_date", "symbol").unique().height
        if duplicated:
            message = f"{name} daily pull contains {duplicated} duplicate (session, symbol) rows"
            raise DataIntegrityError(message)

    joined = raw.join(adjusted, on=["session_date", "symbol"], how="inner")
    return joined.sort("session_date", "symbol")


def _check_daily_quality(
    daily: pl.DataFrame,
    splits: Sequence[SplitEvent],
    *,
    snapshot_id: str = "",
    exceptions: ActionExceptionLedger | None = None,
) -> None:
    """Fail closed on impossible OHLC, non-positive prices, or unexplained jumps.

    A jump may also be cleared by a recorded confirmation in the exception
    ledger -- see :class:`~amat.data.action_exceptions.PriceDiscontinuity`. The
    threshold here was calibrated against broad, liquid ETFs; a wider universe
    reaches instruments that legitimately move further, and the check cannot
    distinguish those from a corporate action the vendor failed to record.
    Confirmations are per-bar, scoped to one snapshot id, and matched against
    the move actually observed, so this widens the gate by exactly the bars
    somebody looked at and by nothing else.
    """
    impossible = daily.filter(
        (pl.col("raw_high") < pl.col("raw_low"))
        | (pl.col("raw_close") > pl.col("raw_high"))
        | (pl.col("raw_close") < pl.col("raw_low"))
        | (pl.col("raw_open") > pl.col("raw_high"))
        | (pl.col("raw_open") < pl.col("raw_low"))
    )
    if impossible.height:
        sample = impossible.head(3).select("session_date", "symbol").rows()
        message = f"{impossible.height} bars violate OHLC ordering, e.g. {sample}"
        raise DataIntegrityError(message)

    non_positive = daily.filter((pl.col("raw_close") <= 0) | (pl.col("adjusted_close") <= 0))
    if non_positive.height:
        sample = non_positive.head(3).select("session_date", "symbol").rows()
        message = f"{non_positive.height} bars have a non-positive close, e.g. {sample}"
        raise DataIntegrityError(message)

    # A split-adjusted series should not jump. Where it does, a split on that
    # ex-date is the only acceptable explanation.
    known = {(split.symbol, split.ex_date) for split in splits}
    moves = (
        daily.sort("symbol", "session_date")
        .with_columns(
            (pl.col("adjusted_close") / pl.col("adjusted_close").shift(1).over("symbol") - 1).alias(
                "move"
            )
        )
        .filter(pl.col("move").abs() > _MAX_ADJUSTED_SESSION_MOVE)
    )
    ledger = exceptions or ActionExceptionLedger()
    unexplained = [
        (symbol, session, move)
        for symbol, session, move in zip(
            moves["symbol"], moves["session_date"], moves["move"], strict=True
        )
        if (symbol, session) not in known
        and not ledger.permits_move(
            snapshot_id=snapshot_id, symbol=symbol, session=session, move=move
        )
    ]
    if unexplained:
        message = (
            f"{len(unexplained)} adjusted-price discontinuities are not explained by a "
            f"recorded split or a confirmation in {DEFAULT_LEDGER_FILENAME}, e.g. "
            f"{unexplained[:3]}"
        )
        raise DataIntegrityError(message)


def _build_window_frame(window_bars: Sequence[WindowBar]) -> pl.DataFrame:
    """Frame the execution windows, dropping unusable cells explicitly.

    A zero-volume or non-positive-VWAP window is *not* a fill at some other
    price; it is an absence. Dropping it here means the panel presents no window
    and the execution model records a missed fill, which is what the contract
    requires.
    """
    schema = {
        "session_date": pl.Date,
        "symbol": pl.String,
        "vwap": pl.Float64,
        "volume": pl.Float64,
    }
    if not window_bars:
        return pl.DataFrame(schema=schema)
    frame = pl.DataFrame(
        {
            "session_date": [bar.session_date for bar in window_bars],
            "symbol": [bar.symbol for bar in window_bars],
            "vwap": [bar.vwap for bar in window_bars],
            "volume": [bar.volume for bar in window_bars],
        },
        schema=schema,
    )
    return (
        frame.filter((pl.col("vwap") > 0) & (pl.col("volume") > 0))
        .unique(subset=["session_date", "symbol"], keep="first")
        .sort("session_date", "symbol")
    )


def _build_actions_frame(
    dividends: Sequence[DividendEvent], splits: Sequence[SplitEvent]
) -> pl.DataFrame:
    """Frame dividends and splits into one long table keyed by ex-date."""
    schema = {"symbol": pl.String, "ex_date": pl.Date, "kind": pl.String, "value": pl.Float64}
    rows = [
        {
            "symbol": event.symbol,
            "ex_date": event.ex_date,
            "kind": "cash_dividend",
            "value": event.rate,
        }
        for event in dividends
    ] + [
        {"symbol": event.symbol, "ex_date": event.ex_date, "kind": "split", "value": event.ratio}
        for event in splits
    ]
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows, schema=schema).sort("ex_date", "symbol", "kind")
