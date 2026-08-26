"""Orchestration for one immutable market-data pull.

The pull has four stages, in this order:

1. raw daily bars, which define the session calendar;
2. adjusted daily bars over the same range;
3. one execution-window request per session in that calendar;
4. corporate actions over the same range.

Stage 3 dominates the wall clock: the vendor cannot filter intraday bars by
time-of-day, so a bulk request would return every five-minute interval of every
session and discard 97% of it. One request per session returns only the window,
at the cost of one request per session. Progress is checkpointed to
``data/interim`` after every session so that an interrupted pull resumes instead
of restarting -- the checkpoint is mutable scratch, never evidence.

The end date is exclusive of the current session on purpose. A daily bar for a
session that has not closed is not a completed observation, and admitting one
would break the contract's rule that features use completed sessions only.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from amat.data.alpaca import AlpacaMarketData, DailyBar, WindowBar
from amat.data.snapshot import SnapshotManifest, write_snapshot
from amat.errors import DataIntegrityError

__all__ = ["IngestionPlan", "ingest_snapshot"]

_CHECKPOINT_VERSION = 1


class IngestionPlan:
    """What to pull. Kept separate from the client so it is trivially testable."""

    def __init__(
        self,
        symbols: Sequence[str],
        start: dt.date,
        end: dt.date,
        *,
        window_start: dt.time = dt.time(9, 30),
        window_end: dt.time = dt.time(9, 35),
        exchange_timezone: str = "America/New_York",
    ) -> None:
        if not symbols:
            message = "an ingestion plan needs at least one symbol"
            raise DataIntegrityError(message)
        if end <= start:
            message = f"end ({end}) must be after start ({start})"
            raise DataIntegrityError(message)
        # ``end`` is exclusive, so an end of today is the normal case: it means
        # "through the last completed session". An end beyond today would ask
        # for a session that has not happened.
        today = dt.datetime.now(tz=dt.UTC).date()
        if end > today + dt.timedelta(days=1):
            message = f"end ({end}) is in the future; the last completed session is at most {today}"
            raise DataIntegrityError(message)
        self.symbols = tuple(dict.fromkeys(symbols))
        self.start = start
        self.end = end
        self.window_start = window_start
        self.window_end = window_end
        self.exchange_timezone = exchange_timezone


def ingest_snapshot(
    client: AlpacaMarketData,
    plan: IngestionPlan,
    *,
    root: Path,
    snapshot_id: str,
    provider: str = "alpaca",
    feed: str = "sip",
    checkpoint_path: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, SnapshotManifest]:
    """Execute ``plan`` and write a new immutable snapshot.

    Raises:
        DataIntegrityError: if either daily pull is empty, or if the two pulls
            cover different session ranges by more than one session at each
            edge. A wider disagreement means the vendor's view shifted between
            the calls, which invalidates pairing adjusted and raw prices.
    """
    say = progress or (lambda _message: None)

    # ``plan.end`` is exclusive; the vendor's ``end`` parameter is inclusive.
    # Converting here rather than at the call sites keeps the client faithful to
    # the documented API and keeps the exclusive-of-the-open-session rule in one
    # place. Requesting the current session is not merely wrong, it is rejected:
    # the SIP feed refuses a range covering an in-progress session outright.
    last = plan.end - dt.timedelta(days=1)

    say(f"daily raw: {len(plan.symbols)} symbols, {plan.start} through {last}")
    raw_bars = client.daily_bars(
        plan.symbols,
        plan.start,
        last,
        adjustment="raw",
        exchange_timezone=plan.exchange_timezone,
    )
    if not raw_bars:
        message = f"raw daily pull returned no bars for {plan.start}..{last}"
        raise DataIntegrityError(message)

    say("daily adjusted")
    adjusted_bars = client.daily_bars(
        plan.symbols,
        plan.start,
        last,
        adjustment="all",
        exchange_timezone=plan.exchange_timezone,
    )
    if not adjusted_bars:
        message = f"adjusted daily pull returned no bars for {plan.start}..{last}"
        raise DataIntegrityError(message)

    _check_pull_alignment(raw_bars, adjusted_bars)

    sessions = sorted({bar.session_date for bar in raw_bars})
    say(f"calendar: {len(sessions)} sessions, {sessions[0]} to {sessions[-1]}")

    window_bars = _collect_window_bars(
        client, plan, sessions, checkpoint_path=checkpoint_path, progress=say
    )
    say(f"execution windows: {len(window_bars)} bars")

    say("corporate actions")
    dividends, splits = client.corporate_actions(plan.symbols, plan.start, last)
    say(f"corporate actions: {len(dividends)} dividends, {len(splits)} splits")

    return write_snapshot(
        root,
        snapshot_id,
        raw_bars=raw_bars,
        adjusted_bars=adjusted_bars,
        window_bars=window_bars,
        dividends=dividends,
        splits=splits,
        provider=provider,
        feed=feed,
        as_of=dt.datetime.now(tz=dt.UTC),
        execution_window_start=plan.window_start,
        execution_window_end=plan.window_end,
        exchange_timezone=plan.exchange_timezone,
    )


def _check_pull_alignment(raw: Sequence[DailyBar], adjusted: Sequence[DailyBar]) -> None:
    """Require the two daily pulls to describe the same session range."""
    raw_sessions = {bar.session_date for bar in raw}
    adjusted_sessions = {bar.session_date for bar in adjusted}
    only_raw = raw_sessions - adjusted_sessions
    only_adjusted = adjusted_sessions - raw_sessions
    if len(only_raw) > 1 or len(only_adjusted) > 1:
        message = (
            "raw and adjusted daily pulls disagree on the session calendar "
            f"({len(only_raw)} sessions only in raw, {len(only_adjusted)} only in adjusted); "
            "the vendor's view changed between the two requests"
        )
        raise DataIntegrityError(message)


def _collect_window_bars(
    client: AlpacaMarketData,
    plan: IngestionPlan,
    sessions: Sequence[dt.date],
    *,
    checkpoint_path: Path | None,
    progress: Callable[[str], None],
) -> list[WindowBar]:
    """Fetch one execution window per session, resuming from a checkpoint."""
    collected: dict[dt.date, list[WindowBar]] = {}
    if checkpoint_path is not None and checkpoint_path.is_file():
        collected = _read_checkpoint(checkpoint_path, plan)
        progress(f"resumed from checkpoint: {len(collected)} sessions already fetched")

    for position, session in enumerate(sessions):
        if session in collected:
            continue
        collected[session] = client.execution_window_bars(
            plan.symbols,
            session,
            window_start=plan.window_start,
            window_end=plan.window_end,
            exchange_timezone=plan.exchange_timezone,
        )
        if checkpoint_path is not None:
            _write_checkpoint(checkpoint_path, plan, collected)
        if position % 100 == 0:
            progress(f"execution windows: {position + 1}/{len(sessions)} sessions")

    return [bar for session in sessions for bar in collected.get(session, [])]


def _checkpoint_key(plan: IngestionPlan) -> str:
    """Identify the plan a checkpoint belongs to.

    A checkpoint written for one symbol set or window must never be reused for
    another; the key makes a mismatch detectable rather than silently mixing
    two pulls into one snapshot.
    """
    return "|".join(
        [
            ",".join(plan.symbols),
            plan.window_start.isoformat(),
            plan.window_end.isoformat(),
            plan.exchange_timezone,
        ]
    )


def _read_checkpoint(path: Path, plan: IngestionPlan) -> dict[dt.date, list[WindowBar]]:
    """Load a checkpoint, ignoring one written for a different plan."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get("version") != _CHECKPOINT_VERSION or payload.get("key") != _checkpoint_key(plan):
        return {}

    collected: dict[dt.date, list[WindowBar]] = {}
    for session_text, rows in payload.get("sessions", {}).items():
        session = dt.date.fromisoformat(session_text)
        collected[session] = [
            WindowBar(
                symbol=row["symbol"],
                session_date=session,
                window_start=dt.datetime.fromisoformat(row["window_start"]),
                window_end=dt.datetime.fromisoformat(row["window_end"]),
                vwap=float(row["vwap"]),
                volume=float(row["volume"]),
            )
            for row in rows
        ]
    return collected


def _write_checkpoint(
    path: Path, plan: IngestionPlan, collected: dict[dt.date, list[WindowBar]]
) -> None:
    """Persist progress atomically so an interrupted write cannot corrupt it."""
    payload = {
        "version": _CHECKPOINT_VERSION,
        "key": _checkpoint_key(plan),
        "sessions": {
            session.isoformat(): [
                {
                    "symbol": bar.symbol,
                    "window_start": bar.window_start.isoformat(),
                    "window_end": bar.window_end.isoformat(),
                    "vwap": bar.vwap,
                    "volume": bar.volume,
                }
                for bar in bars
            ]
            for session, bars in collected.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.partial")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)
