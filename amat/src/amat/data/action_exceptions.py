"""The per-record exception ledger for ambiguous corporate actions.

Why this exists
---------------
``load_corporate_actions`` fails closed when a snapshot records two different
values for the same (symbol, ex-date, kind). That is correct: summing invents
income and picking one arbitrarily discards it, and guardrail 9 forbids
guessing. But failing closed forever also blocks every study over a universe
containing the conflicted symbol -- which is why the 29-symbol study could not
run at all.

The registry names exactly two acceptable resolutions:

    "a re-ingested snapshot with the conflict corrected at source, or a
    documented per-record exception ledger that states which reading was
    chosen and why."

This module is the second one. It does not relax the check. It provides a place
to record a *human decision about one specific record*, with its evidence, so
that the decision is reviewable, attributable, and scoped -- and so that every
conflict without such a decision still fails closed exactly as before.

What the ledger cannot do
-------------------------
It cannot make a wrong reading right, and it deliberately makes a stale one
loud. Three properties are enforced rather than trusted:

**Scoped to one snapshot.** Entries carry a ``snapshot_id``. A re-ingested
snapshot is a different identifier, so it inherits no exception; the conflict
(if it survives) fails closed again and must be re-decided against the new
data. Silent inheritance is precisely how a one-off judgement becomes an
unexamined default.

**The observed values must match the recorded ones.** An entry states both the
value it chose and the values it rejected. If the partition no longer offers
exactly that set, the entry describes a record that no longer exists and the
load fails rather than applying a decision made about different data.

**Every conflict needs its own entry.** The ledger is a list of specific
records, never a policy. There is no wildcard, no per-symbol rule, and no
"resolve conflicts by taking the smaller value" switch, because each of those
would resolve *future* ambiguities that nobody has looked at.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from amat.errors import ConfigurationError, DataIntegrityError

__all__ = [
    "DEFAULT_LEDGER_FILENAME",
    "ActionException",
    "ActionExceptionLedger",
    "PriceDiscontinuity",
    "default_ledger_path",
    "load_action_exceptions",
]

#: Location relative to the repository root. One file, so that every judgement
#: of this kind is reviewable in one diff.
DEFAULT_LEDGER_FILENAME = Path("configs/data/action-exceptions.toml")

_REQUIRED_FIELDS = ("snapshot_id", "symbol", "ex_date", "kind", "chosen", "rejected", "reason")


@dataclass(frozen=True, slots=True)
class ActionException:
    """One recorded decision about one ambiguous record."""

    snapshot_id: str
    symbol: str
    ex_date: dt.date
    kind: str
    chosen: float
    rejected: tuple[float, ...]
    reason: str
    recorded_at: dt.date | None = None

    @property
    def key(self) -> tuple[str, dt.date, str]:
        """Identity within one snapshot: the conflict this entry resolves."""
        return (self.symbol, self.ex_date, self.kind)

    @property
    def observed_values(self) -> tuple[float, ...]:
        """Every value the partition must offer for this entry to still apply."""
        return tuple(sorted((self.chosen, *self.rejected)))


@dataclass(frozen=True, slots=True)
class PriceDiscontinuity:
    """One adjusted-price jump confirmed to be a market move, not bad data.

    ``_check_daily_quality`` fails closed on any single-session move in the
    *adjusted* series beyond its threshold, because the leading explanation for
    one is a corporate action the vendor did not record. That threshold was set
    against broad, liquid ETFs. A widened universe reaches instruments that
    genuinely move that far -- a junior-gold-miner fund in a liquidity crunch
    is not a broad-market fund -- and the check cannot tell the two apart.

    An entry here says a human looked at one specific bar and confirmed the
    move. It is the same shape of claim as :class:`ActionException` and carries
    the same guards: scoped to one snapshot id, matched against the value
    actually observed, and required to state its evidence.
    """

    snapshot_id: str
    symbol: str
    session: dt.date
    observed_move: float
    reason: str
    tolerance: float = 0.005
    recorded_at: dt.date | None = None

    @property
    def key(self) -> tuple[str, dt.date]:
        return (self.symbol, self.session)


@dataclass(frozen=True, slots=True)
class ActionExceptionLedger:
    """Recorded decisions, indexed for one snapshot at a time."""

    entries: tuple[ActionException, ...] = ()
    source: Path | None = None
    discontinuities: tuple[PriceDiscontinuity, ...] = ()

    def permits_move(self, *, snapshot_id: str, symbol: str, session: dt.date, move: float) -> bool:
        """Whether this exact jump has a confirmation on file.

        Raises:
            DataIntegrityError: if an entry exists but the move is not the one
                it was written about. A confirmation of a +62.7% bar says
                nothing about a +80% bar on the same date, and applying it
                there would launder unexamined data through an examined
                decision.
        """
        for entry in self.discontinuities:
            if entry.snapshot_id != snapshot_id or entry.key != (symbol, session):
                continue
            if abs(move - entry.observed_move) > entry.tolerance:
                message = (
                    f"the price-discontinuity exception for {symbol} {session.isoformat()} in "
                    f"snapshot {snapshot_id} was recorded against a move of "
                    f"{entry.observed_move:+.4f} (tolerance {entry.tolerance}), but the pull "
                    f"now shows {move:+.4f}. The confirmation was made about different data; "
                    f"re-examine the bar and update the ledger."
                )
                raise DataIntegrityError(message)
            return True
        return False

    def for_snapshot(self, snapshot_id: str) -> dict[tuple[str, dt.date, str], ActionException]:
        """Entries applying to ``snapshot_id``, keyed by the conflict they resolve."""
        return {entry.key: entry for entry in self.entries if entry.snapshot_id == snapshot_id}

    def resolve(
        self,
        *,
        snapshot_id: str,
        symbol: str,
        ex_date: dt.date,
        kind: str,
        observed: Iterable[float],
    ) -> float | None:
        """The chosen value for one conflict, or ``None`` if none was recorded.

        Raises:
            DataIntegrityError: if an entry exists but the partition's values
                are not the ones it was written about.
        """
        entry = self.for_snapshot(snapshot_id).get((symbol, ex_date, kind))
        if entry is None:
            return None

        seen = tuple(sorted(float(value) for value in observed))
        if seen != entry.observed_values:
            message = (
                f"the action exception for {symbol} {ex_date.isoformat()} {kind} in snapshot "
                f"{snapshot_id} was recorded against values {list(entry.observed_values)}, but "
                f"the partition now offers {list(seen)}. The decision was made about different "
                f"data and must not be applied to this; re-examine the record and update "
                f"the ledger."
            )
            raise DataIntegrityError(message)
        return entry.chosen


def default_ledger_path(snapshot_directory: Path) -> Path:
    """Where the ledger lives, given a snapshot directory.

    A snapshot sits at ``<root>/data/snapshots/<id>``, so the repository root is
    three levels up. Derived rather than configured because a *second* ledger
    path would let one run resolve a conflict that another run fails on.
    """
    return Path(snapshot_directory).resolve().parents[2] / DEFAULT_LEDGER_FILENAME


def _parse_recorded_at(value: object, *, path: Path, entry: str) -> dt.date | None:
    """Normalize TOML datetimes and reject values that cannot satisfy the model."""
    if isinstance(value, dt.datetime):
        return value.date()
    if value is None or isinstance(value, dt.date):
        return value

    message = f"{path}: {entry} has a non-date recorded_at {value!r}"
    raise ConfigurationError(message)


def load_action_exceptions(path: Path) -> ActionExceptionLedger:
    """Read the ledger. A missing file is an empty ledger, not an error.

    Absence must be permissive because the check it feeds is the strict one: no
    ledger means every conflict fails closed, which is the behaviour that
    existed before this module.

    Raises:
        ConfigurationError: on unparseable TOML or a malformed entry. A ledger
            that cannot be read is not treated as empty -- that would silently
            re-arm a conflict an operator believed they had resolved, or worse,
            hide a typo that dropped an entry.
    """
    path = Path(path)
    if not path.is_file():
        return ActionExceptionLedger(entries=(), source=None)

    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        message = f"action exception ledger {path} could not be read: {exc}"
        raise ConfigurationError(message) from exc

    raw_entries = payload.get("exception", [])
    if not isinstance(raw_entries, list):
        message = f"{path}: `exception` must be an array of tables"
        raise ConfigurationError(message)

    entries: list[ActionException] = []
    seen: set[tuple[str, str, dt.date, str]] = set()
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            message = f"{path}: exception #{index + 1} is not a table"
            raise ConfigurationError(message)
        missing = [field for field in _REQUIRED_FIELDS if field not in raw]
        if missing:
            message = f"{path}: exception #{index + 1} is missing {', '.join(missing)}"
            raise ConfigurationError(message)

        ex_date = raw["ex_date"]
        if isinstance(ex_date, dt.datetime):
            ex_date = ex_date.date()
        if not isinstance(ex_date, dt.date):
            message = f"{path}: exception #{index + 1} has a non-date ex_date {ex_date!r}"
            raise ConfigurationError(message)

        rejected = raw["rejected"]
        if not isinstance(rejected, list) or not rejected:
            message = (
                f"{path}: exception #{index + 1} must list the rejected value(s); an entry "
                f"that rejects nothing is not resolving a conflict"
            )
            raise ConfigurationError(message)

        reason = str(raw["reason"]).strip()
        if len(reason) < 40:
            # A one-word reason is not a record of a decision. The whole point
            # of the ledger is that a later reader can re-derive the judgement.
            message = (
                f"{path}: exception #{index + 1} needs a reason that states the evidence "
                f"(got {len(reason)} characters)"
            )
            raise ConfigurationError(message)

        entry = ActionException(
            snapshot_id=str(raw["snapshot_id"]),
            symbol=str(raw["symbol"]),
            ex_date=ex_date,
            kind=str(raw["kind"]),
            chosen=float(raw["chosen"]),
            rejected=tuple(float(value) for value in rejected),
            reason=reason,
            recorded_at=_parse_recorded_at(
                raw.get("recorded_at"), path=path, entry=f"exception #{index + 1}"
            ),
        )
        identity = (entry.snapshot_id, entry.symbol, entry.ex_date, entry.kind)
        if identity in seen:
            message = (
                f"{path}: two exceptions resolve {entry.symbol} {entry.ex_date.isoformat()} "
                f"{entry.kind} in snapshot {entry.snapshot_id}; which one wins would be "
                f"decided by file order"
            )
            raise ConfigurationError(message)
        if entry.chosen in entry.rejected:
            message = f"{path}: exception #{index + 1} both chooses and rejects {entry.chosen}"
            raise ConfigurationError(message)
        seen.add(identity)
        entries.append(entry)

    return ActionExceptionLedger(
        entries=tuple(entries),
        source=path,
        discontinuities=_parse_discontinuities(payload.get("price_discontinuity", []), path),
    )


def _parse_discontinuities(raw_entries: object, path: Path) -> tuple[PriceDiscontinuity, ...]:
    """Read the ``[[price_discontinuity]]`` table, with the same strictness."""
    if not isinstance(raw_entries, list):
        message = f"{path}: `price_discontinuity` must be an array of tables"
        raise ConfigurationError(message)

    required = ("snapshot_id", "symbol", "session", "observed_move", "reason")
    parsed: list[PriceDiscontinuity] = []
    seen: set[tuple[str, str, dt.date]] = set()
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            message = f"{path}: price_discontinuity #{index + 1} is not a table"
            raise ConfigurationError(message)
        missing = [field for field in required if field not in raw]
        if missing:
            message = f"{path}: price_discontinuity #{index + 1} is missing {', '.join(missing)}"
            raise ConfigurationError(message)

        session = raw["session"]
        if isinstance(session, dt.datetime):
            session = session.date()
        if not isinstance(session, dt.date):
            message = f"{path}: price_discontinuity #{index + 1} has a non-date session {session!r}"
            raise ConfigurationError(message)

        reason = str(raw["reason"]).strip()
        if len(reason) < 40:
            message = (
                f"{path}: price_discontinuity #{index + 1} needs a reason that states the "
                f"evidence (got {len(reason)} characters)"
            )
            raise ConfigurationError(message)

        entry = PriceDiscontinuity(
            snapshot_id=str(raw["snapshot_id"]),
            symbol=str(raw["symbol"]),
            session=session,
            observed_move=float(raw["observed_move"]),
            reason=reason,
            tolerance=float(raw.get("tolerance", 0.005)),
            recorded_at=_parse_recorded_at(
                raw.get("recorded_at"),
                path=path,
                entry=f"price_discontinuity #{index + 1}",
            ),
        )
        if entry.tolerance <= 0:
            message = (
                f"{path}: price_discontinuity #{index + 1} has a non-positive tolerance; the "
                f"entry could never match anything"
            )
            raise ConfigurationError(message)
        identity = (entry.snapshot_id, entry.symbol, entry.session)
        if identity in seen:
            message = (
                f"{path}: two price_discontinuity entries confirm {entry.symbol} "
                f"{entry.session.isoformat()} in snapshot {entry.snapshot_id}"
            )
            raise ConfigurationError(message)
        seen.add(identity)
        parsed.append(entry)
    return tuple(parsed)
