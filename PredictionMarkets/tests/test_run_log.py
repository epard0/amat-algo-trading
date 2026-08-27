"""Rules for what an operator sees and what disk keeps.

Rotation deletes files, so most of what is asserted here is what it refuses to
delete. The ledger and its audit trail are the whole reason the guard exists.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from execution.run_log import (
    COUNTDOWN_SECONDS,
    LEDGER_AUDIT_NAME,
    QUIET,
    SILENT,
    VERBOSE,
    HourlyClock,
    LedgerAudit,
    LifetimeStats,
    PeriodCounters,
    RunLogFile,
    console_admits,
    console_sink_options,
    counter_of,
    countdown_ticks,
    ensure_log_dir,
    format_hourly_block,
    format_scan_line,
    format_trade_block,
    is_run_log,
    next_counter,
    next_weekly_boundary,
    purge_run_logs,
    resolve_console_level,
    rotation_reason,
    run_log_name,
)


class FakeClock:
    """An injected wall clock, because rotation is a calendar decision."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **delta) -> None:
        self.moment += timedelta(**delta)


# ------------------------------------------------------------ file names ---

def test_the_first_log_of_the_day_is_numbered_one() -> None:
    assert run_log_name(1, date(2026, 8, 10)) == "kx15-0001-08-10-26.log"


def test_the_counter_widens_past_four_digits_rather_than_rolling_over() -> None:
    """9999 -> 10000 has to stay an ordinary increment, not a wrap."""
    assert run_log_name(9999, date(2026, 8, 10)) == "kx15-9999-08-10-26.log"
    assert run_log_name(10000, date(2026, 8, 10)) == "kx15-10000-08-10-26.log"
    assert run_log_name(123456, date(2026, 1, 2)) == "kx15-123456-01-02-26.log"


def test_a_counter_below_one_is_refused() -> None:
    with pytest.raises(ValueError):
        run_log_name(0, date(2026, 8, 10))


def test_next_counter_follows_the_highest_present_including_five_digits() -> None:
    names = [
        "kx15-0001-08-10-26.log",
        "kx15-9999-08-10-26.log",
        "kx15-10000-08-10-26.log",
        LEDGER_AUDIT_NAME,
        "trades.jsonl",
    ]
    assert next_counter(names) == 10001


def test_next_counter_starts_at_one_for_an_empty_directory() -> None:
    assert next_counter([]) == 1


def test_the_ledger_audit_log_is_not_a_run_log() -> None:
    """It shares the kx15- prefix and must still survive every rotation."""
    assert counter_of(LEDGER_AUDIT_NAME) is None
    assert counter_of("kx15-0001-08-10-26.log") == 1
    assert counter_of("kx15-001-08-10-26.log") is None
    assert counter_of("decisions.jsonl") is None
    assert counter_of("trade_ledger.db") is None


# --------------------------------------------------------------- deletion --

def test_only_run_logs_in_the_log_directory_are_deletable(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    run = log_dir / "kx15-0001-08-10-26.log"
    run.write_text("x")
    for name in (LEDGER_AUDIT_NAME, "trade_ledger.db", "trades.jsonl", "notes.log"):
        (log_dir / name).write_text("keep")
    nested = log_dir / "nested"
    nested.mkdir()
    elsewhere = nested / "kx15-0002-08-10-26.log"
    elsewhere.write_text("keep")

    assert is_run_log(run, log_dir) is True
    assert is_run_log(log_dir / LEDGER_AUDIT_NAME, log_dir) is False
    assert is_run_log(log_dir / "trade_ledger.db", log_dir) is False
    assert is_run_log(elsewhere, log_dir) is False
    assert is_run_log(nested, log_dir) is False


def test_a_symlink_wearing_a_run_logs_name_is_not_deletable(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    ledger = tmp_path / "trade_ledger.db"
    ledger.write_text("the source of truth")
    link = log_dir / "kx15-0007-08-10-26.log"
    link.symlink_to(ledger)

    assert is_run_log(link, log_dir) is False

    purge_run_logs(log_dir)

    assert ledger.read_text() == "the source of truth"
    assert link.is_symlink()


def test_purging_leaves_every_durable_record_alone(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "kx15-0001-08-10-26.log").write_text("old")
    (log_dir / "kx15-0002-08-10-26.log").write_text("older")
    (log_dir / LEDGER_AUDIT_NAME).write_text("evidence")
    (log_dir / "trades.jsonl").write_text("audit stream")

    deleted = purge_run_logs(log_dir, keep=log_dir / "kx15-0002-08-10-26.log")

    assert [path.name for path in deleted] == ["kx15-0001-08-10-26.log"]
    assert (log_dir / "kx15-0002-08-10-26.log").exists()
    assert (log_dir / LEDGER_AUDIT_NAME).read_text() == "evidence"
    assert (log_dir / "trades.jsonl").read_text() == "audit stream"


def test_the_log_directory_is_created_owner_only(tmp_path: Path) -> None:
    log_dir = tmp_path / "fresh"

    ensure_log_dir(log_dir)

    assert log_dir.is_dir()
    assert log_dir.stat().st_mode & 0o777 == 0o700


# -------------------------------------------------------------- rotation ---

def test_the_weekly_boundary_is_the_next_sunday_midnight() -> None:
    saturday = datetime(2026, 8, 8, 23, 30)
    assert next_weekly_boundary(saturday) == datetime(2026, 8, 9, 0, 0)


def test_a_file_opened_on_the_boundary_lives_out_its_week() -> None:
    """Otherwise the first line written on a Sunday rotates the file at once."""
    sunday_midnight = datetime(2026, 8, 9, 0, 0)
    assert next_weekly_boundary(sunday_midnight) == datetime(2026, 8, 16, 0, 0)


def test_a_sunday_afternoon_waits_for_the_following_sunday() -> None:
    assert next_weekly_boundary(datetime(2026, 8, 9, 15, 0)) == datetime(
        2026, 8, 16, 0, 0
    )


def test_size_and_the_calendar_are_both_rotation_triggers() -> None:
    opened = datetime(2026, 8, 10, 9, 0)
    assert (
        rotation_reason(
            size_bytes=10,
            max_bytes=100,
            opened_at=opened,
            now=opened + timedelta(hours=1),
        )
        is None
    )
    assert "bytes" in rotation_reason(
        size_bytes=100,
        max_bytes=100,
        opened_at=opened,
        now=opened + timedelta(hours=1),
    )
    assert "Sunday" in rotation_reason(
        size_bytes=0,
        max_bytes=100,
        opened_at=opened,
        now=datetime(2026, 8, 16, 0, 0),
    )


def test_rotating_on_size_opens_a_new_file_and_drops_the_old_one(
    tmp_path: Path,
) -> None:
    clock = FakeClock(datetime(2026, 8, 10, 9, 0))
    sink = RunLogFile(tmp_path / "logs", max_bytes=25, now=clock)

    sink.write("a" * 30 + "\n")
    first = sink.path
    sink.write("b" * 30 + "\n")
    second = sink.path
    sink.close()

    assert first is not None and second is not None
    assert first.name == "kx15-0001-08-10-26.log"
    assert second.name == "kx15-0002-08-10-26.log"
    assert first.exists() is False
    assert "rotated from kx15-0001-08-10-26.log" in second.read_text()
    assert "bbbb" in second.read_text()


def test_rotating_on_sunday_keeps_only_the_new_file(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 8, 7, 12, 0))
    sink = RunLogFile(tmp_path / "logs", max_bytes=10_000_000, now=clock)

    sink.write("friday\n")
    friday = sink.path
    clock.moment = datetime(2026, 8, 9, 0, 0, 1)
    sink.write("sunday\n")
    sunday = sink.path
    sink.close()

    assert friday is not None and sunday is not None
    assert friday != sunday
    assert friday.exists() is False
    assert sunday.name == "kx15-0002-08-09-26.log"
    assert "Sunday" in sunday.read_text()


def test_rotation_never_touches_the_ledger_audit_log(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    clock = FakeClock(datetime(2026, 8, 10, 9, 0))
    audit = LedgerAudit(log_dir / LEDGER_AUDIT_NAME, now=clock)
    audit.record("record_open_trade", trade_id="t-1", ticker="KXBTC15M-X")

    sink = RunLogFile(log_dir, max_bytes=25, now=clock)
    sink.write("a" * 30 + "\n")
    sink.write("b" * 30 + "\n")
    sink.close()

    assert "record_open_trade" in (log_dir / LEDGER_AUDIT_NAME).read_text()


def test_an_existing_directory_keeps_its_numbering(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "kx15-0004-08-01-26.log").write_text("an earlier run\n")
    clock = FakeClock(datetime(2026, 8, 10, 9, 0))

    sink = RunLogFile(log_dir, max_bytes=10_000_000, now=clock)
    sink.write("this run\n")
    sink.close()

    assert sink.path is not None
    assert sink.path.name == "kx15-0005-08-10-26.log"
    assert (log_dir / "kx15-0004-08-01-26.log").exists()


# --------------------------------------------------------------- console ---

def test_the_command_line_beats_configuration_beats_quiet() -> None:
    assert resolve_console_level() == QUIET
    assert resolve_console_level(configured="verbose") == VERBOSE
    assert resolve_console_level(silent=True, configured="verbose") == SILENT
    assert resolve_console_level(verbose=True, configured="silent") == VERBOSE
    assert resolve_console_level(configured="nonsense") == QUIET


def test_silent_and_verbose_together_are_refused() -> None:
    with pytest.raises(ValueError):
        resolve_console_level(silent=True, verbose=True)


def test_a_quiet_console_takes_tagged_lines_and_every_warning() -> None:
    assert console_admits(level_no=20, kind="event") is True
    assert console_admits(level_no=20, kind="scan") is True
    assert console_admits(level_no=10, kind=None) is False
    assert console_admits(level_no=20, kind=None) is False
    assert console_admits(level_no=30, kind=None) is True
    assert console_admits(level_no=40, kind=None) is True


def test_silent_stays_a_warning_threshold() -> None:
    """--silent has always meant "money and health only"; keep it meaning that."""
    assert console_sink_options(SILENT)["level"] == "WARNING"
    assert "filter" not in console_sink_options(SILENT)
    assert "filter" in console_sink_options(QUIET)
    assert "filter" not in console_sink_options(VERBOSE)


def test_a_non_interactive_stderr_gets_one_countdown_line_not_five() -> None:
    assert countdown_ticks(COUNTDOWN_SECONDS, tty=True) == (5, 4, 3, 2, 1)
    assert countdown_ticks(COUNTDOWN_SECONDS, tty=False) == (5,)
    assert countdown_ticks(0, tty=True) == ()


# ------------------------------------------------------------- one-liners --

def test_a_pass_is_one_line_for_every_asset() -> None:
    line = format_scan_line(
        [
            ("BTC", "no-edge 0.028"),
            ("ETH", "no-edge 0.011"),
            ("SOL", "divergence"),
            ("DOGE", "window"),
        ]
    )
    assert line == (
        "scan  BTC no-edge 0.028 | ETH no-edge 0.011 | SOL divergence | DOGE window"
    )


def test_the_selected_asset_is_marked_in_the_scan_line() -> None:
    line = format_scan_line([("BTC", "edge 0.081"), ("ETH", "window")], selected="BTC")
    assert line.startswith("scan  >BTC edge 0.081")


def test_an_empty_pass_still_says_so() -> None:
    assert format_scan_line([]) == "scan  no market evaluated"


def test_a_trade_line_carries_the_reasoning_that_justified_it() -> None:
    block = format_trade_block(
        mode="paper",
        asset="BTC",
        ticker="KXBTC15M-26AUG102030-30",
        outcome="yes",
        contracts=Decimal("24"),
        entry_price=Decimal("0.62"),
        total_debit=Decimal("15.04"),
        probability=Decimal("0.68"),
        edge=Decimal("0.06"),
        edge_floor=Decimal("0.02"),
        regime="TREND",
        late=True,
        sizing_fraction=Decimal("0.124"),
        gates_passed=("min_edge", "settlement_margin"),
        settles_at=datetime(2026, 8, 10, 20, 30),
    )

    assert "TRADE  PAPER BTC YES 24 @ $0.62" in block
    assert "debit $15.04" in block
    assert "P=68.0%" in block
    assert "edge=6.0%" in block
    assert "floor=2.0%" in block
    assert "regime=TREND" in block
    assert "LATE ENTRY" in block
    assert "gates passed: min_edge, settlement_margin" in block


# ---------------------------------------------------------------- hourly ---

def test_the_hourly_block_fires_on_the_clock_not_on_uptime() -> None:
    clock = HourlyClock(datetime(2026, 8, 10, 9, 58))

    assert clock.due(datetime(2026, 8, 10, 9, 59, 59)) is False
    assert clock.due(datetime(2026, 8, 10, 10, 0, 1)) is True

    started, opened = clock.mark(datetime(2026, 8, 10, 10, 0, 1))

    assert started == datetime(2026, 8, 10, 9, 0)
    assert opened == datetime(2026, 8, 10, 10, 0)
    assert clock.due(datetime(2026, 8, 10, 10, 59)) is False


def test_a_long_settlement_sleep_produces_one_block_not_a_backlog() -> None:
    """Nothing is queued while the bot is waiting on a position."""
    clock = HourlyClock(datetime(2026, 8, 10, 9, 0))

    assert clock.due(datetime(2026, 8, 10, 12, 5)) is True
    clock.mark(datetime(2026, 8, 10, 12, 5))
    assert clock.due(datetime(2026, 8, 10, 12, 59)) is False


def test_the_hourly_block_names_the_gate_that_refused_most() -> None:
    period = PeriodCounters()
    for _ in range(7):
        period.record_evaluation()
        period.record_rejection("min_edge")
    period.record_rejection("model_market_divergence")
    period.record_trade()
    period.record_settlement("WON", Decimal("1.25"))
    period.record_settlement("LOST", Decimal("-2.00"))

    block = format_hourly_block(
        period_start=datetime(2026, 8, 10, 9, 0),
        period_end=datetime(2026, 8, 10, 10, 0),
        period=period,
        lifetime=LifetimeStats(
            trades=31,
            wins=22,
            losses=8,
            voids=1,
            pnl=Decimal("21.71"),
            balance=Decimal("121.71"),
        ),
        mode="paper",
    )

    assert "hour 2026-08-10 09:00-10:00 PAPER" in block
    assert "W/L/V 1/1/0" in block
    assert "pnl -0.75" in block
    assert "evaluations 7" in block
    assert "top gate min_edge x7" in block
    assert "all-time | trades 31 W/L/V 22/8/1 win 73.3%" in block
    assert "balance $121.71" in block


def test_an_idle_hour_reports_no_gate_and_no_balance() -> None:
    block = format_hourly_block(
        period_start=datetime(2026, 8, 10, 9, 0),
        period_end=datetime(2026, 8, 10, 10, 0),
        period=PeriodCounters(),
        lifetime=LifetimeStats(),
        mode="live",
    )

    assert "top gate none" in block
    assert "balance n/a" in block


def test_resetting_a_period_clears_its_gate_tally() -> None:
    period = PeriodCounters()
    period.record_rejection("min_edge")
    period.record_evaluation()

    period.reset()

    assert period.evaluations == 0
    assert period.top_gate() is None


# ---------------------------------------------------------- ledger audit ---

def test_every_ledger_write_leaves_one_greppable_line(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 8, 10, 9, 0))
    audit = LedgerAudit(tmp_path / LEDGER_AUDIT_NAME, now=clock)

    line = audit.record(
        "record_open_trade",
        trade_id="t-1",
        ticker="KXBTC15M-26AUG102030-30",
        mode="live",
        contracts=Decimal("24"),
        price=Decimal("0.62"),
        result="read back status=OPEN",
        rowcount=1,
    )

    assert line is not None
    assert "method=record_open_trade" in line
    assert "trade_id=t-1" in line
    assert "ticker=KXBTC15M-26AUG102030-30" in line
    assert "mode=live" in line
    assert "contracts=24" in line
    assert "price=0.62" in line
    assert "rowcount=1" in line
    assert (tmp_path / LEDGER_AUDIT_NAME).read_text().count("\n") == 1


def test_a_failed_ledger_write_is_recorded_at_error_with_the_money(
    tmp_path: Path,
) -> None:
    """A funded position with no ledger row has to be reconstructable by hand."""
    audit = LedgerAudit(tmp_path / LEDGER_AUDIT_NAME)

    line = audit.failure(
        "record_open_trade",
        error=sqlite_failure(),
        trade_id=None,
        ticker="KXBTC15M-26AUG102030-30",
        mode="live",
        contracts=Decimal("24"),
        price=Decimal("0.62"),
        order_id="venue-order-99",
    )

    assert line is not None
    assert "| ERROR |" in line
    assert "RuntimeError: disk I/O error" in line
    assert "order_id=venue-order-99" in line


def test_a_multiline_error_stays_one_audit_line(tmp_path: Path) -> None:
    audit = LedgerAudit(tmp_path / LEDGER_AUDIT_NAME)

    audit.failure("settle_trade", error="database is locked\nretry later")

    assert (tmp_path / LEDGER_AUDIT_NAME).read_text().count("\n") == 1


def test_a_disabled_audit_writes_nothing_rather_than_raising(tmp_path: Path) -> None:
    """A half-built bot must still be able to place and monitor a trade."""
    audit = LedgerAudit(None)

    assert audit.enabled is False
    assert audit.record("settle_trade", trade_id="t-1") is None
    assert list(tmp_path.iterdir()) == []


def test_the_audit_log_creates_its_directory_owner_only(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    audit = LedgerAudit(log_dir / LEDGER_AUDIT_NAME)

    audit.record("record_open_trade", trade_id="t-1")

    assert log_dir.stat().st_mode & 0o777 == 0o700


def sqlite_failure() -> RuntimeError:
    return RuntimeError("disk I/O error")


def test_a_restart_resumes_the_current_transcript(tmp_path: Path) -> None:
    """The counter counts rotations, not process launches.

    A box that restarts the service nightly would otherwise reach a new file
    every day, litter the directory, and never actually perform the weekly
    rotation the operator was promised.
    """
    log_dir = tmp_path / "logs"
    clock = FakeClock(datetime(2026, 8, 10, 9, 0))

    first = RunLogFile(log_dir, max_bytes=10_000_000, now=clock)
    first.write("run one\n")
    first.close()

    second = RunLogFile(log_dir, max_bytes=10_000_000, now=clock)
    second.write("run two\n")
    second.close()

    assert first.path == second.path
    assert [p.name for p in sorted(log_dir.iterdir())] == ["kx15-0001-08-10-26.log"]
    assert first.path is not None
    assert first.path.read_text() == "run one\nrun two\n"


def test_a_restart_after_the_weekly_boundary_starts_a_new_one(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # 1 August 2026 is a Saturday, so a Sunday 00:00 falls before the 10th.
    (log_dir / "kx15-0004-08-01-26.log").write_text("last week\n")

    sink = RunLogFile(log_dir, max_bytes=10_000_000,
                      now=FakeClock(datetime(2026, 8, 10, 9, 0)))
    sink.write("this week\n")
    sink.close()

    assert sink.path is not None
    assert sink.path.name == "kx15-0005-08-10-26.log"


def test_a_restart_onto_an_oversized_transcript_starts_a_new_one(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "kx15-0001-08-10-26.log").write_text("x" * 200)

    sink = RunLogFile(log_dir, max_bytes=100,
                      now=FakeClock(datetime(2026, 8, 10, 9, 0)))
    sink.write("fresh\n")
    sink.close()

    assert sink.path is not None
    assert sink.path.name == "kx15-0002-08-10-26.log"


def test_the_log_age_comes_from_the_name_not_the_filesystem(tmp_path: Path) -> None:
    """mtime is real wall-clock time and every rule here uses an injected clock.

    Mixing the two makes the weekly boundary untestable, and silently wrong the
    first time somebody copies a log directory or restores it from a backup.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    stale = log_dir / "kx15-0002-01-05-26.log"
    stale.write_text("january\n")          # mtime is now; the name says January

    sink = RunLogFile(log_dir, max_bytes=10_000_000,
                      now=FakeClock(datetime(2026, 8, 10, 9, 0)))
    assert sink.current() is None
