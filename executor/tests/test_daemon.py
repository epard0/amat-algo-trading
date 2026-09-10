"""The scheduler: which phase runs, when, and exactly once.

Every test here corresponds to a failure observed in the running deployment,
not to a hypothetical. The daemon had no tests, and the two defects it shipped
with were both in this file's subject matter: a state file that could not
represent two pending phases of the same name, and a fixed grace window that
expired before a suspended host woke up.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from executor.broker import CalendarSession
from executor.config import ExecutorSettings, RiskLimits
from executor.daemon import Daemon
from executor.errors import ConfigurationError
from executor.journal import Journal

#: The digest of the plan payload used throughout `tests/test_plan_source.py`
#: and `tests/test_submit.py` -- kept in every `_settings()`'s allowlist too,
#: alongside the digest of the strategy file `_settings()` actually writes, so
#: submit-phase tests built on that plan payload remain valid.
_PLAN_PAYLOAD_DIGEST = "2712f694227321e4" + "0" * 48

NEW_YORK = "America/New_York"
REGULAR_OPEN = dt.time(9, 30)
REGULAR_CLOSE = dt.time(16, 0)
HALF_DAY_CLOSE = dt.time(13, 0)


@dataclass
class _FakeClock:
    timestamp: dt.datetime
    is_open: bool = True
    next_open: dt.datetime | None = None
    next_close: dt.datetime | None = None


class _FakeClient:
    """A broker that only answers the two questions the scheduler asks.

    Anything the scheduler is not supposed to call during these tests is simply
    absent, so an unintended account fetch or order submission raises
    ``AttributeError`` rather than passing silently.
    """

    def __init__(self, sessions: tuple[CalendarSession, ...], now: dt.datetime) -> None:
        self.sessions = sessions
        self.now = now
        self.calendar_calls = 0

    def get_clock(self) -> _FakeClock:
        return _FakeClock(timestamp=self.now)

    def get_calendar(self, start: dt.date, end: dt.date) -> tuple[CalendarSession, ...]:
        self.calendar_calls += 1
        return tuple(session for session in self.sessions if start <= session.date <= end)


def _session(date: str, close: dt.time = REGULAR_CLOSE) -> CalendarSession:
    return CalendarSession(
        date=dt.date.fromisoformat(date), open_local=REGULAR_OPEN, close_local=close
    )


def _settings(tmp_path: Path) -> ExecutorSettings:
    strategy_config = tmp_path / "amat" / "config.toml"
    strategy_config.parent.mkdir(parents=True, exist_ok=True)
    content = b"[strategy]\nname = 'amat-etf-regime-quality'\n"
    strategy_config.write_bytes(content)
    strategy_digest = hashlib.sha256(content).hexdigest()
    return ExecutorSettings(
        trading_key_id="key",
        trading_secret_key="secret",
        amat_root=tmp_path / "amat",
        strategy_config=strategy_config,
        snapshot=tmp_path / "amat" / "snapshot",
        state_root=tmp_path / "state",
        report_retention_days=7,
        plan_lead_minutes=10,
        report_delay_minutes=15,
        submit_cutoff_minutes=30,
        plan_command=("true",),
        dry_run=True,
        smtp=None,
        limits=RiskLimits(
            authorized_config_sha256=frozenset({_PLAN_PAYLOAD_DIGEST, strategy_digest}),
            disabled=False,
            max_session_notional=Decimal("1000000"),
            max_orders_per_session=100,
            max_symbol_notional=Decimal("1000000"),
            equity_tolerance=Decimal("1"),
        ),
    )


def _daemon(tmp_path: Path, client: _FakeClient) -> Daemon:
    daemon = Daemon(_settings(tmp_path))
    daemon._client = client  # type: ignore[assignment]
    return daemon


# -- startup authorization ----------------------------------------------------


def test_daemon_refuses_to_start_with_an_unauthorized_strategy_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.strategy_config.parent.mkdir(parents=True, exist_ok=True)
    settings.strategy_config.write_text("[strategy]\nname='x'\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not in AUTHORIZED_CONFIG_SHA256"):
        Daemon(settings)


def _at(date: str, hour: int, minute: int) -> dt.datetime:
    from zoneinfo import ZoneInfo

    return dt.datetime.combine(
        dt.date.fromisoformat(date), dt.time(hour, minute), tzinfo=ZoneInfo(NEW_YORK)
    )


def _events(settings: ExecutorSettings, name: str) -> list[dict[str, object]]:
    journal = Journal(settings.journal_path, settings.equity_curve_path)
    return [entry for entry in journal.read_journal_entries() if entry.get("event") == name]


# -- the state ledger --------------------------------------------------------


def test_two_sessions_of_the_same_phase_are_both_remembered(tmp_path: Path) -> None:
    """The defect that produced 41,000 duplicate journal records.

    The v1 state file was ``{"submit": "<one session>"}``. Marking a second
    session done overwrote the first, which then looked pending again, which
    overwrote the second, forever.
    """
    daemon = _daemon(tmp_path, _FakeClient((), _at("2026-08-20", 12, 0)))

    daemon._mark_done("submit", dt.date(2026, 8, 19))
    daemon._mark_done("submit", dt.date(2026, 8, 20))

    assert daemon._already_done("submit", dt.date(2026, 8, 19))
    assert daemon._already_done("submit", dt.date(2026, 8, 20))
    assert not daemon._already_done("submit", dt.date(2026, 8, 21))
    assert not daemon._already_done("report", dt.date(2026, 8, 20))


def test_the_ledger_survives_a_new_process(tmp_path: Path) -> None:
    """Restart safety is a property of the file, not of the in-memory cache."""
    first = _daemon(tmp_path, _FakeClient((), _at("2026-08-20", 12, 0)))
    first._mark_done("submit", dt.date(2026, 8, 19))
    first._mark_done("report", dt.date(2026, 8, 19))

    second = _daemon(tmp_path, _FakeClient((), _at("2026-08-20", 12, 0)))
    assert second._already_done("submit", dt.date(2026, 8, 19))
    assert second._already_done("report", dt.date(2026, 8, 19))


def test_a_v1_state_file_is_migrated_rather_than_ignored(tmp_path: Path) -> None:
    """An upgrade in place must not look like a blank slate."""
    settings = _settings(tmp_path)
    settings.ensure_directories()
    (settings.state_root / "daemon_state.json").write_text(
        json.dumps({"submit": "2026-08-20", "report": "2026-08-19"}), encoding="utf-8"
    )

    daemon = _daemon(tmp_path, _FakeClient((), _at("2026-08-20", 12, 0)))

    assert daemon._already_done("submit", dt.date(2026, 8, 20))
    assert daemon._already_done("report", dt.date(2026, 8, 19))


def test_an_unreadable_state_file_does_not_wedge_the_daemon(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    (settings.state_root / "daemon_state.json").write_text("{not json", encoding="utf-8")

    daemon = _daemon(tmp_path, _FakeClient((), _at("2026-08-20", 12, 0)))

    assert not daemon._already_done("submit", dt.date(2026, 8, 20))
    daemon._mark_done("submit", dt.date(2026, 8, 20))
    assert daemon._already_done("submit", dt.date(2026, 8, 20))


def test_the_ledger_is_bounded(tmp_path: Path) -> None:
    """It must not grow without limit across years of sessions."""
    daemon = _daemon(tmp_path, _FakeClient((), _at("2026-08-20", 12, 0)))
    day = dt.date(2024, 1, 1)
    for offset in range(300):
        daemon._mark_done("submit", day + dt.timedelta(days=offset))

    payload = json.loads((_settings(tmp_path).state_root / "daemon_state.json").read_text())
    assert len(payload["completed"]) <= 64
    # The newest survive: those are the only ones the calendar window can see.
    newest = (day + dt.timedelta(days=299)).isoformat()
    assert f"submit:{newest}" in payload["completed"]
    assert "submit:2024-01-01" not in payload["completed"]


# -- missed phases -----------------------------------------------------------


def test_a_missed_phase_is_recorded_once_not_on_every_tick(tmp_path: Path) -> None:
    """The observable symptom: two warnings every four minutes, indefinitely."""
    sessions = (_session("2026-08-18"), _session("2026-08-19"), _session("2026-08-20"))
    # After every window for all three sessions has closed.
    client = _FakeClient(sessions, _at("2026-08-20", 23, 0))
    daemon = _daemon(tmp_path, client)

    for _ in range(5):
        daemon.tick()

    missed = _events(daemon._settings, "phase_missed")
    keys = {(entry["phase"], entry["session"]) for entry in missed}
    assert len(missed) == len(keys), "a settled phase was reconsidered on a later tick"
    # The restart window reaches far enough back to settle every phase in the
    # retained audit horizon, including a multi-day host outage.
    assert keys == {
        (phase, session)
        for phase in ("submit", "report")
        for session in ("2026-08-18", "2026-08-19", "2026-08-20")
    }


def test_restart_records_every_session_missed_during_a_weeklong_outage(tmp_path: Path) -> None:
    sessions = tuple(_session(f"2026-08-{day:02d}") for day in range(24, 29))
    client = _FakeClient(sessions, _at("2026-09-01", 10, 0))
    daemon = _daemon(tmp_path, client)

    # One missed phase is settled per tick only insofar as due work returns
    # early; missed work is intentionally drained in a single pass.
    daemon.tick()

    missed = _events(daemon._settings, "phase_missed")
    keys = {(entry["phase"], entry["session"]) for entry in missed}
    assert keys == {
        (phase, session.date.isoformat()) for phase in ("submit", "report") for session in sessions
    }
    assert all(
        daemon._already_done(phase, session.date)
        for session in sessions
        for phase in ("submit", "report")
    )


def test_a_past_session_is_never_due(tmp_path: Path) -> None:
    """Whatever else happens, yesterday's rebalance must not be placed today."""
    sessions = (_session("2026-08-19"), _session("2026-08-20"))
    client = _FakeClient(sessions, _at("2026-08-20", 10, 30))
    daemon = _daemon(tmp_path, client)

    from zoneinfo import ZoneInfo

    now = client.now.astimezone(ZoneInfo(NEW_YORK))
    phases = daemon._phases(now, ZoneInfo(NEW_YORK))
    yesterday = [p for p in phases if p.session == dt.date(2026, 8, 19)]

    assert yesterday, "the calendar window should still contain yesterday"
    assert all(phase.is_missed(now) for phase in yesterday)
    assert not any(phase.is_due(now) for phase in yesterday)


# -- the submit window -------------------------------------------------------


def test_the_submit_window_ends_a_fixed_time_before_the_close(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(NEW_YORK)
    client = _FakeClient((_session("2026-08-20"),), _at("2026-08-20", 9, 0))
    daemon = _daemon(tmp_path, client)

    submit = next(
        phase
        for phase in daemon._phases(client.now.astimezone(zone), zone)
        if phase.name == "submit"
    )

    assert submit.at == _at("2026-08-20", 9, 20)  # open minus the 10-minute lead
    assert submit.until == _at("2026-08-20", 15, 30)  # close minus the 30-minute cutoff


def test_a_half_day_narrows_the_window_without_being_told_it_is_a_half_day(
    tmp_path: Path,
) -> None:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(NEW_YORK)
    client = _FakeClient((_session("2026-11-27", close=HALF_DAY_CLOSE),), _at("2026-11-27", 9, 0))
    daemon = _daemon(tmp_path, client)

    submit = next(
        phase
        for phase in daemon._phases(client.now.astimezone(zone), zone)
        if phase.name == "submit"
    )

    assert submit.until == _at("2026-11-27", 12, 30)


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (9, 19, False),  # before the lead
        (9, 20, True),  # exactly on time
        (11, 45, True),  # late, but the day is still tradable
        (15, 30, True),  # the last permitted instant
        (15, 31, False),  # inside the close cutoff
    ],
)
def test_when_a_submit_may_run(tmp_path: Path, hour: int, minute: int, expected: bool) -> None:
    """A host that slept through the open may still rebalance; one that slept
    until the bell may not."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(NEW_YORK)
    client = _FakeClient((_session("2026-08-20"),), _at("2026-08-20", hour, minute))
    daemon = _daemon(tmp_path, client)

    now = client.now.astimezone(zone)
    submit = next(phase for phase in daemon._phases(now, zone) if phase.name == "submit")

    assert submit.is_due(now) is expected


def test_a_late_submit_is_recorded_as_late(tmp_path: Path, monkeypatch) -> None:
    """Guardrail 9: the report must say the fills were not opening fills."""
    sessions = (_session("2026-08-20"),)
    client = _FakeClient(sessions, _at("2026-08-20", 11, 45))
    daemon = _daemon(tmp_path, client)

    seen: dict[str, int] = {}

    def _capture(session: dt.date, *, minutes_late: int = 0) -> None:
        seen[session.isoformat()] = minutes_late

    monkeypatch.setattr(daemon, "run_submit", _capture)
    daemon.tick()

    assert seen == {"2026-08-20": 145}  # 09:20 to 11:45


def test_an_on_time_submit_is_not_flagged(tmp_path: Path, monkeypatch) -> None:
    client = _FakeClient((_session("2026-08-20"),), _at("2026-08-20", 9, 21))
    daemon = _daemon(tmp_path, client)

    seen: dict[str, int] = {}
    monkeypatch.setattr(
        daemon,
        "run_submit",
        lambda session, *, minutes_late=0: seen.update({session.isoformat(): minutes_late}),
    )
    daemon.tick()

    assert seen == {"2026-08-20": 1}


def test_lateness_reaches_the_report(tmp_path: Path) -> None:
    """Guardrail 9 end to end: the journal records it, the report reads it back."""
    client = _FakeClient((_session("2026-08-20"),), _at("2026-08-20", 13, 31))
    daemon = _daemon(tmp_path, client)
    daemon._journal.record(
        "submit_delayed", session=dt.date(2026, 8, 20), phase="submit", minutes_late=251
    )
    daemon._journal.record(
        "order_submitted",
        session=dt.date(2026, 8, 20),
        symbol="SPY",
        side="buy",
        quantity="10.725415740",
        client_order_id="amat-x",
    )

    payload = daemon._last_submission_for(dt.date(2026, 8, 20))

    assert payload["minutes_late"] == 251
    assert payload["executed"] is True


def test_an_on_time_submission_reports_no_lateness(tmp_path: Path) -> None:
    client = _FakeClient((_session("2026-08-20"),), _at("2026-08-20", 13, 31))
    daemon = _daemon(tmp_path, client)
    daemon._journal.record(
        "order_submitted",
        session=dt.date(2026, 8, 20),
        symbol="SPY",
        side="buy",
        quantity="1",
        client_order_id="amat-x",
    )

    assert daemon._last_submission_for(dt.date(2026, 8, 20))["minutes_late"] == 0


def test_a_hand_run_submit_derives_its_own_lateness(tmp_path: Path) -> None:
    """`executor submit` is off-schedule by construction and must say so."""
    client = _FakeClient((_session("2026-08-20"),), _at("2026-08-20", 13, 31))
    daemon = _daemon(tmp_path, client)

    assert daemon.minutes_past_schedule(dt.date(2026, 8, 20)) == 251  # 09:20 to 13:31
    # A session the calendar does not contain has no schedule to be late against.
    assert daemon.minutes_past_schedule(dt.date(2026, 8, 25)) == 0


# -- the loop ----------------------------------------------------------------


def test_a_phase_that_keeps_raising_is_eventually_settled(tmp_path: Path, monkeypatch) -> None:
    """A bad morning must not become an infinite retry against the broker.

    Each raise is retried (the window is wide open and the attempt count is
    below the cap), but the cap is finite: once exhausted the phase is
    settled as done rather than reconsidered forever.
    """
    client = _FakeClient((_session("2026-08-20"),), _at("2026-08-20", 9, 25))
    daemon = _daemon(tmp_path, client)

    calls = []

    def _boom(session: dt.date, *, minutes_late: int = 0) -> None:
        calls.append(session)
        message = "the strategy subprocess failed"
        raise RuntimeError(message)

    monkeypatch.setattr(daemon, "run_submit", _boom)
    for _ in range(4):
        daemon.tick()

    assert len(calls) == 3  # the first attempt plus two retries, then the cap is hit
    assert daemon._already_done("submit", dt.date(2026, 8, 20))
    failures = _events(daemon._settings, "phase_failed")
    assert len(failures) == 3
    assert "RuntimeError" in str(failures[0]["error"])


def test_a_transient_submit_failure_is_retried_within_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case: a broker blip clears well before the submit window closes."""
    client = _FakeClient((_session("2026-09-10"),), _at("2026-09-10", 9, 25))
    daemon = _daemon(tmp_path, client)
    calls: list[str] = []

    def flaky(session: dt.date, *, minutes_late: int = 0) -> None:
        calls.append(session.isoformat())
        if len(calls) == 1:
            raise RuntimeError("broker hiccup")

    monkeypatch.setattr(daemon, "run_submit", flaky)

    sleep = daemon.tick()
    assert calls == ["2026-09-10"]
    assert sleep == pytest.approx(300.0)
    assert not daemon._already_done("submit", dt.date(2026, 9, 10))

    client.now = _at("2026-09-10", 9, 31)
    daemon.tick()
    assert calls == ["2026-09-10", "2026-09-10"]
    assert daemon._already_done("submit", dt.date(2026, 9, 10))
    failed = _events(daemon._settings, "phase_failed")
    retried = _events(daemon._settings, "phase_retry_scheduled")
    assert failed
    assert retried


def test_a_phase_gives_up_after_the_attempt_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dependency that never recovers must not be retried forever."""
    client = _FakeClient((_session("2026-09-10"),), _at("2026-09-10", 9, 25))
    daemon = _daemon(tmp_path, client)

    def boom(session: dt.date, *, minutes_late: int = 0) -> None:
        message = "x"
        raise RuntimeError(message)

    monkeypatch.setattr(daemon, "run_submit", boom)
    for minute in (25, 31, 37):
        client.now = _at("2026-09-10", 9, minute)
        daemon.tick()
    assert daemon._already_done("submit", dt.date(2026, 9, 10))


def test_the_loop_sleeps_toward_the_next_phase_not_past_it(tmp_path: Path) -> None:
    client = _FakeClient((_session("2026-08-21"),), _at("2026-08-20", 23, 0))
    daemon = _daemon(tmp_path, client)

    sleep_for = daemon.tick()

    assert 0 < sleep_for <= 240.0


# -- journal rotation --------------------------------------------------------


def test_the_journal_rotates_at_the_cap_and_readers_span_both_files(tmp_path: Path) -> None:
    """A single defect must not be able to fill the disk."""
    journal = Journal(
        tmp_path / "journal.ndjson", tmp_path / "equity.ndjson", max_journal_bytes=2048
    )
    for index in range(200):
        journal.record("noise", index=index, padding="x" * 100)

    assert journal.previous_journal_path.is_file()
    assert (tmp_path / "journal.ndjson").stat().st_size < 4096

    entries = journal.read_journal_entries()
    indices = [entry["index"] for entry in entries if entry.get("event") == "noise"]
    assert indices == sorted(indices), "entries must come back oldest first"
    assert 199 in indices, "the newest event must be readable"


def test_the_equity_curve_is_never_rotated(tmp_path: Path) -> None:
    """It is the entire performance record; truncating its head is unrecoverable."""
    from decimal import Decimal

    journal = Journal(
        tmp_path / "journal.ndjson", tmp_path / "equity.ndjson", max_journal_bytes=512
    )
    for offset in range(50):
        journal.record_equity(
            session=dt.date(2026, 1, 1) + dt.timedelta(days=offset),
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            gross_exposure=0.0,
            positions={},
        )

    assert not (tmp_path / "equity.1.ndjson").exists()
    assert len(journal.read_equity_curve()) == 50
