"""What the operator sees, what disk keeps, and when a log file is retired.

``bot.py`` owns the wiring; this module owns the rules, and every rule here is
either pure or takes its clock and its paths as arguments. That is the same
discipline ``execution/risk_gate.py`` follows and it is kept for the same
reason: a policy that decides whether a person ever finds out what happened to
their money must be testable without a settings object, a clock, a network or a
ledger.

Two files are described here and they are deliberately different animals.

The **run log** is a transcript. It is named ``kx15-NNNN-MM-DD-YY.log``, it
rotates weekly or on size, and rotation *deletes* what came before it, because
an operator asked for a live account of the running bot rather than an archive.
The **ledger audit log** is evidence. It is appended to forever, it is never
rotated, and it is written with a plain file handle rather than through loguru
so that a silenced console, a broken sink or a rotation cannot take it away.

Nothing in this module may ever touch ``trade_ledger.db``, ``trades.jsonl`` or
``decisions.jsonl``. Those are the durable record; the run log is not.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TextIO

# Console verbosities, quietest first. "quiet" is the default: the console
# carries events, and everything else lands in the file.
SILENT = "silent"
QUIET = "quiet"
VERBOSE = "verbose"
CONSOLE_LEVELS = (SILENT, QUIET, VERBOSE)

# Every line meant for a person carries one of these on ``logger.bind(kind=)``.
# An untagged record reaches the file and, unless it is a warning or worse,
# nothing else.
KIND_EVENT = "event"
KIND_SCAN = "scan"
KIND_WAIT = "wait"
KIND_STATS = "stats"

CONSOLE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss!UTC} | {level} | {message}"
VERBOSE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss!UTC} | {level} | "
    "{name}:{function}:{line} - {message}"
)
FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS!UTC} | {level: <8} | "
    "{name}:{function}:{line} - {message}"
)

WARNING_LEVEL_NO = 30

# The last seconds of a wait are spoken aloud, so a watching operator can see
# the process is alive rather than hung.
COUNTDOWN_SECONDS = 5

LEDGER_AUDIT_NAME = "kx15-ledger-audit.log"

# A run log and nothing else: four-or-more counter digits, then MM-DD-YY. The
# ledger audit log, trades.jsonl, decisions.jsonl and the SQLite ledger cannot
# match this, which is precisely what makes deletion on rotation safe.
_RUN_LOG_PATTERN = re.compile(r"^kx15-(\d{4,})-(\d{2}-\d{2}-\d{2})\.log$")


# --------------------------------------------------------------- naming ----

def run_log_name(counter: int, day: date) -> str:
    """``kx15-0001-08-10-26.log`` — counter first, then the date it opened."""
    if counter < 1:
        raise ValueError("run log counters start at 1")
    # Zero padding to four digits that simply gets wider once the counter needs
    # a fifth, so 9999 -> 10000 is an ordinary increment rather than a rollover
    # anyone has to think about.
    return f"kx15-{counter:04d}-{day:%m-%d-%y}.log"


def counter_of(name: str) -> int | None:
    """The counter in a run log's name, or None if it is not a run log."""
    match = _RUN_LOG_PATTERN.match(name)
    return int(match.group(1)) if match else None


def date_of(name: str) -> date | None:
    """The day a run log opened, read from its own name.

    The filename is the authority rather than the file's mtime: mtime is real
    wall-clock time, and every rotation rule here is driven by an injected
    clock so it can be tested. Mixing the two makes the weekly boundary
    untestable and, worse, silently wrong under a clock change.
    """
    match = _RUN_LOG_PATTERN.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(2), "%m-%d-%y").date()
    except (ValueError, IndexError):
        return None


def next_counter(names: Iterable[str]) -> int:
    """One past the highest counter already present; 1 for an empty directory."""
    counters = [counter for counter in map(counter_of, names) if counter]
    return max(counters, default=0) + 1


def is_run_log(path: Path, log_dir: Path) -> bool:
    """Whether deleting ``path`` on rotation would destroy only a transcript.

    Deliberately strict on all four counts: the name has to be a run log's, the
    file has to sit directly in the log directory, it has to be a regular file,
    and it must not be a symlink pointing at something that matters.
    """
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if path.parent.resolve() != Path(log_dir).resolve():
            return False
    except OSError:
        return False
    return counter_of(path.name) is not None


# ------------------------------------------------------------- rotation ----

def next_weekly_boundary(moment: datetime) -> datetime:
    """The first Sunday 00:00 strictly after ``moment``, in ``moment``'s zone.

    Strictly after, so a file that happens to open exactly on the boundary
    lives out its week rather than rotating on its first line.
    """
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    boundary = midnight + timedelta(days=(6 - moment.weekday()) % 7)
    if boundary <= moment:
        boundary += timedelta(days=7)
    return boundary


def rotation_reason(
    *,
    size_bytes: int,
    max_bytes: int,
    opened_at: datetime,
    now: datetime,
) -> str | None:
    """Why this file should be retired, or None to keep writing to it."""
    if max_bytes > 0 and size_bytes >= max_bytes:
        return f"reached {size_bytes} bytes (limit {max_bytes})"
    if now >= next_weekly_boundary(opened_at):
        return "crossed the Sunday 00:00 weekly boundary"
    return None


def ensure_log_dir(log_dir: Path) -> Path:
    """Create the log directory owner-only if it does not exist yet."""
    log_dir = Path(log_dir)
    existed = log_dir.is_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    if not existed:
        # mkdir's mode is filtered through the umask, and this directory holds
        # a full account of somebody's positions and P&L.
        log_dir.chmod(0o700)
    return log_dir


def purge_run_logs(log_dir: Path, *, keep: Path | None = None) -> list[Path]:
    """Delete the run logs in ``log_dir``, never anything else.

    ``is_run_log`` is the guard, and it is the only thing standing between this
    function and the ledger. A file that refuses to be deleted is skipped: a
    stuck transcript is not a reason to stop trading.
    """
    log_dir = Path(log_dir)
    deleted: list[Path] = []
    try:
        entries = sorted(log_dir.iterdir())
    except OSError:
        return deleted
    for entry in entries:
        if keep is not None and entry == keep:
            continue
        if not is_run_log(entry, log_dir):
            continue
        try:
            entry.unlink()
        except OSError:
            continue
        deleted.append(entry)
    return deleted


class RunLogFile:
    """The rotating ``kx15-NNNN-MM-DD-YY.log``, used as a loguru sink.

    Rotation starts a new file and deletes the transcripts it replaces. That is
    safe only because ``is_run_log`` refuses to name anything else, and because
    the durable records - the SQLite ledger, ``trades.jsonl``,
    ``decisions.jsonl`` and the ledger audit log - either live elsewhere or
    cannot match the run-log filename pattern. Nothing here opens them.
    """

    def __init__(
        self,
        log_dir: Path,
        *,
        max_bytes: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.max_bytes = int(max_bytes)
        # Local, not UTC: the weekly boundary is Sunday 00:00 where the operator
        # is, and the filename carries the day they would call it.
        self._now = now or datetime.now
        self._handle: TextIO | None = None
        self._opened_at: datetime | None = None
        self._size = 0
        self._counter = 0
        self.path: Path | None = None

    def current(self) -> Path | None:
        """The newest run log that is still inside its rotation window.

        Restarting the bot must not start a new transcript. The counter counts
        *rotations*, not process launches: a box that restarts the service
        nightly would otherwise reach kx15-9999 in twenty-seven years of
        numbering and litter the directory in the meantime, while the weekly
        rotation the operator was promised never actually happened.
        """
        try:
            entries = [
                entry for entry in self.log_dir.iterdir()
                if is_run_log(entry, self.log_dir)
            ]
        except OSError:
            return None
        newest, newest_counter = None, -1
        for entry in entries:
            counter = counter_of(entry.name)
            if counter is not None and counter > newest_counter:
                newest, newest_counter = entry, counter
        if newest is None:
            return None
        try:
            size = newest.stat().st_size
        except OSError:
            return None
        opened = date_of(newest.name)
        if opened is None:
            return None
        moment = self._now()
        opened_at = datetime.combine(opened, time.min)
        if moment.tzinfo is not None:
            opened_at = opened_at.replace(tzinfo=moment.tzinfo)
        if rotation_reason(
            size_bytes=size,
            max_bytes=self.max_bytes,
            opened_at=opened_at,
            now=moment,
        ) is not None:
            return None
        self._counter = newest_counter
        return newest

    def open(self) -> TextIO:
        ensure_log_dir(self.log_dir)
        moment = self._now()
        resumed = self.current()
        if resumed is not None:
            path = resumed
        else:
            # Rotation empties the directory, so the counter has to be
            # remembered rather than re-derived, or every rotation would
            # reopen kx15-0001.
            counter = max(
                next_counter(entry.name for entry in self.log_dir.iterdir()),
                self._counter + 1,
            )
            self._counter = counter
            path = self.log_dir / run_log_name(counter, moment.date())
        handle = path.open("a", encoding="utf-8")
        self._handle = handle
        self._size = path.stat().st_size
        self._opened_at = moment
        self.path = path
        return handle

    def write(self, text: str) -> None:
        handle = self._handle
        opened_at = self._opened_at
        if handle is None or opened_at is None:
            handle = self.open()
            opened_at = self._opened_at or self._now()
        reason = rotation_reason(
            size_bytes=self._size,
            max_bytes=self.max_bytes,
            opened_at=opened_at,
            now=self._now(),
        )
        if reason is not None:
            handle = self.rotate(reason)
        handle.write(text)
        handle.flush()
        self._size += len(text.encode("utf-8"))

    def __call__(self, message: Any) -> None:
        """loguru sink protocol: a Message is a str carrying its record."""
        self.write(str(message))

    def rotate(self, reason: str) -> TextIO:
        retired = self.path
        self.close()
        # Purge first, so `current()` finds nothing to resume and open()
        # allocates the next counter.
        purge_run_logs(self.log_dir)
        handle = self.open()
        header = (
            f"--- rotated from {retired.name if retired else 'nothing'}: "
            f"{reason}. Previous run-log entries were deleted; the ledger, "
            "trades.jsonl and the ledger audit log are untouched.\n"
        )
        handle.write(header)
        handle.flush()
        self._size += len(header.encode("utf-8"))
        return handle

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None


# ---------------------------------------------------------- console rules --

def resolve_console_level(
    *,
    silent: bool = False,
    verbose: bool = False,
    configured: str | None = None,
) -> str:
    """CLI flag beats configuration beats the quiet default."""
    if silent and verbose:
        raise ValueError("--silent and --verbose are opposites")
    if silent:
        return SILENT
    if verbose:
        return VERBOSE
    candidate = (configured or "").strip().lower()
    return candidate if candidate in CONSOLE_LEVELS else QUIET


def console_admits(*, level_no: int, kind: str | None) -> bool:
    """Whether a quiet console shows this record.

    Tagged lines are the ones written for a person to read. Everything at
    WARNING or worse is shown whether it was tagged or not, because silencing a
    warning once made a blind bot look exactly like a working one.
    """
    return kind is not None or level_no >= WARNING_LEVEL_NO


def console_filter() -> Callable[[dict[str, Any]], bool]:
    """A loguru filter implementing the quiet console."""

    def _filter(record: dict[str, Any]) -> bool:
        return console_admits(
            level_no=record["level"].no,
            kind=record["extra"].get("kind"),
        )

    return _filter


def console_sink_options(level: str) -> dict[str, Any]:
    """The loguru ``add()`` arguments for a console verbosity.

    ``silent`` is expressed as a plain WARNING threshold rather than as a
    filter, because that is exactly what it has always meant: routine scan
    output disappears, and money, health and errors do not.
    """
    if level == SILENT:
        return {"level": "WARNING", "format": CONSOLE_FORMAT}
    if level == VERBOSE:
        return {"level": "DEBUG", "format": VERBOSE_FORMAT}
    return {"level": "DEBUG", "format": CONSOLE_FORMAT, "filter": console_filter()}


def countdown_ticks(seconds: int, *, tty: bool) -> tuple[int, ...]:
    """Which marks of the final countdown to speak.

    A terminal can carry one line a second and the operator is watching it. A
    file keeps them forever and nobody is, so a non-interactive stderr gets the
    single opening mark. Neither gets ANSI escapes: a cursor-rewriting
    countdown is a screenful of control bytes once it lands in a log.
    """
    if seconds <= 0:
        return ()
    if not tty:
        return (seconds,)
    return tuple(range(seconds, 0, -1))


# ------------------------------------------------------------- scan line ---

def format_scan_line(
    entries: Sequence[tuple[str, str]],
    *,
    selected: str | None = None,
) -> str:
    """One line for a whole evaluation pass: every asset and how it ended.

    Ten gates times five assets every five seconds is not information, it is
    weather. The decision log keeps the full reasoning; the console gets the
    verdict.
    """
    if not entries:
        return "scan  no market evaluated"
    parts = [
        f"{'>' if asset == selected else ''}{asset} {verdict}"
        for asset, verdict in entries
    ]
    return "scan  " + " | ".join(parts)


# ----------------------------------------------------------- trade block ---

def format_trade_block(
    *,
    mode: str,
    asset: str,
    ticker: str,
    outcome: str,
    contracts: Decimal,
    entry_price: Decimal,
    total_debit: Decimal,
    probability: Decimal,
    edge: Decimal,
    edge_floor: Decimal,
    regime: str,
    late: bool,
    sizing_fraction: Decimal,
    gates_passed: Sequence[str],
    settles_at: datetime,
) -> str:
    """The full justification for an order, in the two lines it takes to give it.

    A trade is the only thing this bot does that costs money, so its line
    carries every number the decision turned on rather than a summary of them.
    """
    entry = (
        f"TRADE  {mode.upper()} {asset} {outcome.upper()} "
        f"{contracts} @ ${entry_price} debit ${total_debit} "
        f"({sizing_fraction:.1%} of bankroll) | {ticker}"
    )
    reasoning = (
        f"       P={probability:.1%} price={entry_price} edge={edge:.1%} "
        f"floor={edge_floor:.1%} regime={regime}"
        + ("  LATE ENTRY" if late else "")
        + f" | settles {settles_at.isoformat()}"
    )
    gates = "       gates passed: " + (", ".join(gates_passed) or "none recorded")
    return "\n".join((entry, reasoning, gates))


# ---------------------------------------------------------- hourly block ---

@dataclass
class PeriodCounters:
    """In-process tallies for one reporting period.

    The ledger owns the all-time truth; this owns only what this process has
    seen since the last block, which is the part the ledger cannot answer -
    how many evaluations happened and which gate did most of the refusing.
    """

    evaluations: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    voids: int = 0
    pnl: Decimal = Decimal("0")
    gate_rejections: Counter[str] = field(default_factory=Counter)

    def record_evaluation(self) -> None:
        self.evaluations += 1

    def record_rejection(self, gate: str) -> None:
        self.gate_rejections[gate] += 1

    def record_trade(self) -> None:
        self.trades += 1

    def record_settlement(self, status: str, pnl: Decimal | None) -> None:
        if status == "WON":
            self.wins += 1
        elif status == "LOST":
            self.losses += 1
        else:
            self.voids += 1
        self.pnl += pnl or Decimal("0")

    def top_gate(self) -> tuple[str, int] | None:
        ranked = self.gate_rejections.most_common(1)
        return ranked[0] if ranked else None

    def reset(self) -> None:
        self.evaluations = 0
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.voids = 0
        self.pnl = Decimal("0")
        self.gate_rejections = Counter()


@dataclass(frozen=True)
class LifetimeStats:
    """The ledger's all-time answer, as the hourly block wants to print it."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    voids: int = 0
    pnl: Decimal = Decimal("0")
    balance: Decimal | None = None


def win_rate(wins: int, losses: int) -> Decimal:
    decided = wins + losses
    if decided <= 0:
        return Decimal("0")
    return Decimal(wins) / Decimal(decided)


def floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


class HourlyClock:
    """Fires on the wall-clock hour, never on 3600 seconds of uptime.

    It is asked rather than awaited. Nothing here sleeps, schedules or wakes
    anything, so a due block cannot stand between an open position and its
    settlement poll: the bot checks at the points it was already awake.
    """

    def __init__(self, started: datetime) -> None:
        self.period_start = floor_hour(started)

    def due(self, now: datetime) -> bool:
        return floor_hour(now) > self.period_start

    def mark(self, now: datetime) -> tuple[datetime, datetime]:
        """Close the current period and open the one ``now`` falls in."""
        started, self.period_start = self.period_start, floor_hour(now)
        return started, self.period_start


def format_hourly_block(
    *,
    period_start: datetime,
    period_end: datetime,
    period: PeriodCounters,
    lifetime: LifetimeStats,
    mode: str,
) -> str:
    """The compact hour-boundary summary: this period, then everything."""
    top = period.top_gate()
    top_gate = f"{top[0]} x{top[1]}" if top else "none"
    balance = "n/a" if lifetime.balance is None else f"${lifetime.balance:.2f}"
    heading = (
        f"hour {period_start:%Y-%m-%d %H:%M}-{period_end:%H:%M} {mode.upper()} | "
        f"trades {period.trades} W/L/V {period.wins}/{period.losses}/"
        f"{period.voids} win {win_rate(period.wins, period.losses):.1%} "
        f"pnl {period.pnl:+.2f}"
    )
    detail = (
        f"       evaluations {period.evaluations} | top gate {top_gate}"
    )
    all_time = (
        f"       all-time | trades {lifetime.trades} W/L/V {lifetime.wins}/"
        f"{lifetime.losses}/{lifetime.voids} win "
        f"{win_rate(lifetime.wins, lifetime.losses):.1%} "
        f"pnl {lifetime.pnl:+.2f} | balance {balance}"
    )
    return "\n".join((heading, detail, all_time))


# ------------------------------------------------------- ledger audit log --

def _field(value: Any) -> str:
    """One audit field, flattened so a record is always exactly one line."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _utcnow() -> datetime:
    """A timestamp that names its own zone, unlike a bare local ``now()``.

    Evidence about an exchange position is read in the exchange's clock, and
    an offset in the line means nobody has to guess which one it was written in.
    """
    return datetime.now(timezone.utc)


class LedgerAudit:
    """One line per ledger write, so recording can be checked rather than trusted.

    Append-only and never rotated: the run log is a transcript that rotation
    deliberately discards, whereas this is the file somebody opens to answer
    "was that trade actually written?". It bypasses loguru for the same reason -
    a silenced console or a failed sink must not be able to take the evidence
    with it.

    A disabled instance (``path=None``) writes nothing, which is what a
    partially constructed bot gets rather than an exception: an audit trail
    must never be the reason a position goes unrecorded.
    """

    def __init__(
        self,
        path: Path | None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self._now = now or _utcnow

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def record(
        self,
        method: str,
        *,
        level: str = "INFO",
        trade_id: str | None = None,
        ticker: str | None = None,
        mode: str | None = None,
        contracts: Decimal | None = None,
        price: Decimal | None = None,
        result: str | None = None,
        **extra: Any,
    ) -> str | None:
        """Write one audit line and return it, or None when disabled."""
        fields = {
            "method": method,
            "trade_id": trade_id,
            "ticker": ticker,
            "mode": mode,
            "contracts": contracts,
            "price": price,
            "result": result,
        }
        fields.update(extra)
        line = f"{self._now().isoformat()} | {level} | " + " | ".join(
            f"{key}={_field(value)}" for key, value in fields.items()
        )
        return self._append(line)

    def failure(
        self,
        method: str,
        *,
        error: BaseException | str,
        **fields: Any,
    ) -> str | None:
        """Record a ledger write that did not happen, with the money attached."""
        detail = (
            f"{type(error).__name__}: {error}"
            if isinstance(error, BaseException)
            else str(error)
        )
        return self.record(method, level="ERROR", result=detail, **fields)

    def _append(self, line: str) -> str | None:
        if self.path is None:
            return None
        try:
            ensure_log_dir(self.path.parent)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            # The caller has already been told through loguru; failing to write
            # the audit line must not unwind a path that is holding a position.
            return None
        return line


DISABLED_LEDGER_AUDIT = LedgerAudit(None)
