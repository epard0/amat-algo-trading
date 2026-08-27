"""Append-only record of every evaluation the bot performs and why.

``trades.jsonl`` only records orders that happened, and the ledger only records
fills. Neither answers the question that actually drives improvement: *why did
the bot not trade the other 40 times?* This module records one JSON object per
market per pass containing the full feature vector, the regime call, the
probability, every gate in the order it was evaluated, and the final verdict.

The file is newline-delimited JSON so it can be replayed with nothing more
than ``json.loads`` per line, loaded into pandas, or grepped. Records carry a
``schema_version`` so a reader can tell old lines from new ones.

Settlement outcomes are appended later as separate ``settlement`` records
keyed by ``trade_id``. Joining decisions to settlements is what makes the log
usable for calibration - measuring whether "model said 70%" actually settles
YES about 70% of the time, per regime.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1


def _encode(value: Any) -> Any:
    """Make Decimals, datetimes, and dataclass-ish values JSON-safe.

    Decimals become strings, never floats: a lossy round-trip through binary
    floating point is exactly the bug the rest of this codebase avoids by
    using Decimal for money in the first place.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_encode(item) for item in value]
    return value


@dataclass
class GateOutcome:
    name: str
    allowed: bool
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Serialise, omitting what a passing gate has nothing to say about.

        ``reason`` describes the *failure*, and callers pass it unconditionally
        (``trace.gate("cooldown", not cooling, "ticker is in cooldown")``), so
        emitting it on a pass produced lines reading ``"allowed": true,
        "reason": "ticker is in post-failure cooldown"`` - which states the
        opposite of what happened. Dropping it fixes the record and removes
        roughly a third of the bytes in the gate list at the same time.
        """
        record: dict[str, Any] = {"name": self.name, "allowed": self.allowed}
        if not self.allowed and self.reason:
            record["reason"] = self.reason
        if self.detail:
            record["detail"] = _encode(self.detail)
        return record


class DecisionTrace:
    """Accumulates one evaluation's reasoning, then writes a single record.

    Usage mirrors how the bot actually evaluates: gates are appended in the
    order they run, and the first blocking gate ends the evaluation. The trace
    is written even when nothing is traded - especially then.
    """

    def __init__(
        self,
        log: "DecisionLog",
        *,
        cycle_id: str,
        mode: str,
        environment: str,
        asset: str,
        ticker: str,
    ) -> None:
        self._log = log
        self.decision_id = uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc)
        self.payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "decision",
            "decision_id": self.decision_id,
            "cycle_id": cycle_id,
            "timestamp": self.started_at.isoformat(),
            "mode": mode,
            "environment": environment,
            "asset": asset,
            "ticker": ticker,
        }
        self.gates: list[GateOutcome] = []

    def set(self, key: str, value: Any) -> "DecisionTrace":
        self.payload[key] = _encode(value)
        return self

    def update(self, values: dict[str, Any]) -> "DecisionTrace":
        for key, value in values.items():
            self.set(key, value)
        return self

    def gate(
        self,
        name: str,
        allowed: bool,
        reason: str | None = None,
        **detail: Any,
    ) -> bool:
        """Record a gate result and return ``allowed`` so callers can branch.

        ``if not trace.gate("min_edge", edge >= floor, "..."): return None``
        keeps the reasoning and the control flow in one place, which is the
        only way this log stays truthful as the gates change.
        """
        self.gates.append(GateOutcome(name, allowed, reason, detail))
        return allowed

    def finish(self, decision: str, reason: str | None = None) -> None:
        self.payload["gates"] = [gate.as_dict() for gate in self.gates]
        self.payload["blocked_by"] = next(
            (gate.name for gate in self.gates if not gate.allowed), None
        )
        self.payload["decision"] = decision
        self.payload["decision_reason"] = reason
        self.payload["elapsed_seconds"] = round(
            (datetime.now(timezone.utc) - self.started_at).total_seconds(), 4
        )
        self._log.write(self.payload)


# The log is a rolling diagnostic window, not an archive. At roughly 106 MB per
# day of continuous running it reaches 38 GB in a year, and every reader of it
# — `view_decisions.py`, the dashboard, the fitter — has to scan the whole file.
#
# Retention is one day and the previous day is DISCARDED, not archived. That is
# a deliberate choice and it is only safe because nothing downstream treats this
# file as a durable record: the ledger holds the trades, `trades.jsonl` holds
# the audit stream, and model training reads market history rebuilt from the
# exchange rather than from whatever the bot happened to observe. If any of
# those three stops being true, this policy has to be revisited before the next
# rotation, because by then the data is gone.
RETENTION_SECONDS = 24 * 60 * 60

# A run of identical verdicts on the same ticker is one fact, not N facts.
#
# The dominant record by an order of magnitude is "this market is not in the
# entry window yet", and it is re-derived from the clock every pass: over half
# an hour, 15 tickers produced 1,292 of those lines, one of them 121 times, each
# a byte-for-byte repeat but for a countdown. Collapsing a run to its opening
# record plus one closing summary keeps every fact the log held - which gate,
# which numbers, how many times, over what span - and drops ~97% of the lines.
#
# MAX_RUN_SECONDS bounds how long a run may be held open. A 15-minute market is
# the unit of work here, so a five-minute cap still samples any state several
# times before it can matter, and it keeps `tail -f` showing a live process.
MAX_RUN_SECONDS = 300.0

# Verdicts that are never collapsed. Everything here either moved money or
# needs to be visible the instant it happens.
ALWAYS_WRITE = frozenset({"traded", "order_rejected", "halted", "error"})


@dataclass
class _Run:
    """An open run of identical verdicts on one ticker."""

    key: tuple[Any, ...]
    opened_at: datetime
    last_at: datetime
    folded: int
    payload: dict[str, Any]


class DecisionLog:
    """Newline-delimited JSON sink. Never raises into the trading path."""

    def __init__(
        self,
        path: Path,
        enabled: bool = True,
        *,
        retention_seconds: float = RETENTION_SECONDS,
        max_run_seconds: float = MAX_RUN_SECONDS,
        now: "Callable[[], datetime] | None" = None,
        record_sink: "Callable[[dict[str, Any]], None] | None" = None,
    ) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.retention_seconds = float(retention_seconds)
        self.max_run_seconds = float(max_run_seconds)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._record_sink = record_sink
        # Resolved lazily from the file itself, so a restart inherits the age of
        # the log it is appending to rather than resetting the clock. A bot
        # restarted hourly would otherwise never rotate.
        self._rotate_at: datetime | None = None
        # One append handle for the process's life. The old code opened and
        # closed the file per record, so a routine pass cost mkdir + exists +
        # open + write + close; this costs one write.
        self._handle: Any = None
        self._runs: dict[str, _Run] = {}

    # ------------------------------------------------------------------ file

    def _open(self) -> Any:
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        return self._handle

    def _drop_handle(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def close(self) -> None:
        """Flush open runs and release the handle.

        Called at shutdown. Without it the last run of every ticker - the one
        covering the moment the process stopped - would never be written, which
        is precisely the window an operator goes looking for.
        """
        try:
            self.flush()
        finally:
            self._drop_handle()

    def _first_record_time(self) -> datetime | None:
        """When the current file began, read from its own first line.

        The first line, not the filesystem timestamp: mtime moves on every
        write and ctime is not creation time on Linux. One short read of a
        large file, and only when the age is unknown.
        """
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                first = handle.readline()
        except OSError:
            return None
        if not first.strip():
            return None
        try:
            stamp = json.loads(first).get("timestamp")
            moment = datetime.fromisoformat(str(stamp))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

    def _rotate_if_due(self) -> None:
        """Discard the log once it is older than the retention window.

        Truncated in place rather than unlinked and recreated: a reader holding
        the path keeps reading the same inode, and there is no window in which
        the file does not exist. The append handle is opened O_APPEND, so its
        next write lands at the new end of file without a seek.

        The due time is computed once and then compared, so the common case is
        a datetime comparison rather than the ``exists()`` syscall this used to
        make on every single record.
        """
        if self.retention_seconds <= 0:
            return
        now = self._now()
        if self._rotate_at is None:
            started = self._first_record_time() or now
            self._rotate_at = started + timedelta(seconds=self.retention_seconds)
        if now < self._rotate_at:
            return
        self._rotate_at = now + timedelta(seconds=self.retention_seconds)
        try:
            with self.path.open("w", encoding="utf-8"):
                pass
        except OSError:
            return
        from loguru import logger

        logger.info(
            "Decision log rotated: {} exceeded {:.0f}h and was discarded",
            self.path.name,
            self.retention_seconds / 3600,
        )

    # ------------------------------------------------------------------ runs

    def _collapsible(self, payload: dict[str, Any]) -> str | None:
        """The ticker a record may be folded under, or None to write it now."""
        if self.max_run_seconds <= 0:
            return None
        if payload.get("record_type") != "decision":
            return None
        if payload.get("decision") in ALWAYS_WRITE:
            return None
        ticker = payload.get("ticker")
        return str(ticker) if ticker else None

    def _emit_run(self, ticker: str) -> None:
        """Write the summary for a run, if anything was folded into it."""
        run = self._runs.get(ticker)
        if run is None or run.folded == 0:
            return
        record = dict(run.payload)
        # `evaluations` is how many evaluations this one record stands for, so a
        # reader counting the funnel sums `record.get("evaluations", 1)` and
        # arrives at exactly the number it would have counted line by line.
        record["evaluations"] = run.folded
        record["first_evaluated_at"] = run.opened_at.isoformat()
        record["last_evaluated_at"] = run.last_at.isoformat()
        run.folded = 0
        run.opened_at = run.last_at
        self._emit(record)

    def flush(self) -> None:
        """Write out every open run. Safe to call at any time."""
        for ticker in list(self._runs):
            self._emit_run(ticker)

    # ----------------------------------------------------------------- write

    def trace(
        self,
        *,
        cycle_id: str,
        mode: str,
        environment: str,
        asset: str,
        ticker: str,
    ) -> DecisionTrace:
        return DecisionTrace(
            self,
            cycle_id=cycle_id,
            mode=mode,
            environment=environment,
            asset=asset,
            ticker=ticker,
        )

    def write(self, payload: dict[str, Any]) -> None:
        """Record an evaluation, folding it into an open run when it repeats."""
        # The durable sink receives every raw evaluation before the diagnostic
        # JSONL folds repeated verdicts. It is independent of the rolling file
        # toggle: disabling a convenience log must not disable strict training
        # telemetry. Like the file writer, it can never trade-block.
        if self._record_sink is not None:
            try:
                self._record_sink(payload)
            except Exception:  # pragma: no cover - diagnostic isolation
                from loguru import logger

                logger.opt(exception=True).warning("Decision record sink failed")
        if not self.enabled:
            return
        ticker = self._collapsible(payload)
        if ticker is None:
            # Not foldable. Close whatever run this ticker had first, so the
            # summary never lands after the event that ended it.
            held = payload.get("ticker")
            if held:
                self._close_run(str(held))
            self._emit(payload)
            return

        key = (payload.get("decision"), payload.get("blocked_by"))
        now = self._now()
        run = self._runs.get(ticker)
        if run is not None and run.key == key:
            run.folded += 1
            run.last_at = now
            run.payload = payload
            if (now - run.opened_at).total_seconds() >= self.max_run_seconds:
                self._emit_run(ticker)
            return

        # The verdict changed: close the previous run, then write this one in
        # full so a state change is visible the moment it happens.
        self._close_run(ticker)
        self._runs[ticker] = _Run(key, now, now, 0, payload)
        self._emit(payload)

    def _close_run(self, ticker: str) -> None:
        self._emit_run(ticker)
        self._runs.pop(ticker, None)

    def _emit(self, payload: dict[str, Any]) -> None:
        """Serialise one record to the file. Swallows its own failures."""
        try:
            self._rotate_if_due()
            line = json.dumps(_encode(payload), separators=(",", ":"))
            handle = self._open()
            handle.write(line + "\n")
            handle.flush()
        except Exception:  # pragma: no cover - diagnostics must never trade-block
            # A failed diagnostic write must not abort a cycle or, worse, leave
            # a position unmonitored. Losing a log line is the cheap failure.
            self._drop_handle()
            from loguru import logger

            logger.opt(exception=True).warning("Decision log write failed")

    def record_event(self, record_type: str, **fields: Any) -> None:
        """Write a non-decision record (order placement, settlement, halt)."""
        payload = {
            "schema_version": SCHEMA_VERSION,
            "record_type": record_type,
            # Through the log's own clock, so the timestamps a record carries
            # and the age rotation measures are the same quantity.
            "timestamp": self._now().isoformat(),
        }
        payload.update(fields)
        self.write(payload)

    def read_all(self) -> list[dict[str, Any]]:
        """Load every record. Used by the analysis CLI and by tests."""
        self.flush()
        if not self.path.is_file():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records
