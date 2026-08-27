"""The paper/live control file: format, tolerance, and the safety invariants.

This file sits on the path to placing real orders, so the tests that matter
most are the ones about damage: every way the file can be wrong has to resolve
to paper, and neither writer may erase the other's field.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from execution.mode_control import (
    STALE_AFTER_SECONDS,
    ModeControl,
    ModeControlError,
    ModeState,
    default_path,
    normalise,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def control(tmp_path) -> ModeControl:
    return ModeControl(tmp_path / "runtime_mode.json")


# ------------------------------------------------------------- normalise ----

@pytest.mark.parametrize("value, expected", [("live", "live"), ("PAPER", "paper"),
                                             (" Live ", "live")])
def test_normalise_accepts_the_two_modes(value: str, expected: str) -> None:
    assert normalise(value) == expected


@pytest.mark.parametrize("value", ["", "demo", "LIVEE", "true", "1"])
def test_normalise_rejects_anything_else(value: str) -> None:
    with pytest.raises(ModeControlError):
        normalise(value)


# ----------------------------------------------------------- round trips ----

def test_absent_file_reads_as_paper(control: ModeControl) -> None:
    state = control.read()

    assert state.requested == "paper"
    assert state.active is None      # nothing has ever reported
    assert state.live_available is False


def test_request_round_trips(control: ModeControl) -> None:
    control.request("live", by="dashboard@127.0.0.1", now=NOW)
    state = control.read()

    assert state.requested == "live"
    assert state.requested_at == NOW
    assert state.requested_by == "dashboard@127.0.0.1"


def test_report_active_round_trips(control: ModeControl) -> None:
    control.report_active("paper", live_available=True, pid=99, now=NOW)
    state = control.read()

    assert state.active == "paper"
    assert state.active_pid == 99
    assert state.live_available is True
    assert state.active_at == NOW


def test_a_report_does_not_erase_a_request(control: ModeControl) -> None:
    """The bot's heartbeat and the dashboard's request share one file.

    If a status write clobbered a request that landed a moment earlier, the
    operator would see the switch acknowledged and then silently ignored.
    """
    control.request("live", by="dashboard", now=NOW)
    control.report_active("paper", live_available=True, now=NOW)

    state = control.read()
    assert state.requested == "live"
    assert state.active == "paper"
    assert state.pending is True


def test_a_request_does_not_erase_the_last_report(control: ModeControl) -> None:
    control.report_active("paper", live_available=True, pid=7, now=NOW)
    control.request("live", by="dashboard", now=NOW)

    state = control.read()
    assert state.active == "paper"
    assert state.active_pid == 7
    assert state.live_available is True


def test_adopt_request_lets_the_command_line_win(control: ModeControl) -> None:
    """A request in the file is the operator speaking last week.

    Restarting with an explicit flag is the operator speaking now, so startup
    must not resume a stale request for the other book.
    """
    control.request("live", by="dashboard", now=NOW)

    control.report_active("paper", live_available=True, adopt_request=True, now=NOW)

    state = control.read()
    assert state.requested == "paper"
    assert state.requested_by == "command line"
    assert state.pending is False


# ------------------------------------------------------------- tolerance ----

def test_corrupt_json_reads_as_paper(control: ModeControl) -> None:
    control.path.write_text("{ this is not json", encoding="utf-8")

    state = control.read()

    assert state.requested == "paper"
    assert "unreadable" in state.note


def test_a_json_array_reads_as_paper(control: ModeControl) -> None:
    control.path.write_text('["live"]', encoding="utf-8")

    assert control.read().requested == "paper"


@pytest.mark.parametrize("value", ["LIVE!", "", None, 1, {"a": 1}])
def test_an_unknown_requested_mode_reads_as_paper(control: ModeControl, value) -> None:
    """Every ambiguity resolves to the side that cannot lose money."""
    control.path.write_text(json.dumps({"requested": value}), encoding="utf-8")

    assert control.read().requested == "paper"


def test_an_unknown_active_mode_reads_as_no_report(control: ModeControl) -> None:
    """Unparseable is 'nobody has reported', not 'a bot is running paper'."""
    control.path.write_text(json.dumps({"active": "banana"}), encoding="utf-8")

    assert control.read().active is None


def test_unreadable_file_does_not_raise(control: ModeControl, monkeypatch) -> None:
    """A read failure must not stop a bot that is already trading."""
    control.path.write_text("{}", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr(type(control.path), "read_text", boom)

    assert control.read().requested == "paper"


# ------------------------------------------------------------- staleness ----

def test_a_state_with_no_report_is_stale(control: ModeControl) -> None:
    assert control.read().is_stale(now=NOW) is True


def test_a_fresh_report_is_not_stale() -> None:
    state = ModeState(active="paper", active_at=NOW)

    assert state.is_stale(now=NOW + timedelta(seconds=30)) is False


def test_an_old_report_is_stale() -> None:
    state = ModeState(active="paper", active_at=NOW)
    later = NOW + timedelta(seconds=STALE_AFTER_SECONDS + 1)

    assert state.is_stale(now=later) is True


def test_the_stale_window_outlasts_a_whole_market() -> None:
    """Holding a position must never look like a dead process.

    A 15-minute market plus settlement polling is the longest the bot can
    legitimately go without touching the control file.
    """
    assert STALE_AFTER_SECONDS > 15 * 60


# --------------------------------------------------------------- on disk ----

def test_the_file_is_not_world_readable(control: ModeControl) -> None:
    control.request("live", by="dashboard", now=NOW)

    assert control.path.stat().st_mode & 0o077 == 0


def test_writing_leaves_no_temporary_files_behind(control: ModeControl) -> None:
    """The write is atomic: a reader sees the old file or the new one."""
    control.request("live", by="dashboard", now=NOW)
    control.report_active("paper", live_available=False, now=NOW)

    names = {p.name for p in control.path.parent.iterdir()}
    assert names <= {"runtime_mode.json", "runtime_mode.json.lock"}


def test_the_file_is_valid_json_a_human_can_read(control: ModeControl) -> None:
    control.request("live", by="dashboard", now=NOW)

    payload = json.loads(control.path.read_text(encoding="utf-8"))
    assert payload["requested"] == "live"
    assert payload["schema_version"] == 1


def test_pid_defaults_to_the_reporting_process(control: ModeControl) -> None:
    control.report_active("paper", live_available=False, now=NOW)

    assert control.read().active_pid == os.getpid()


def test_default_path_sits_beside_the_ledger(tmp_path) -> None:
    assert default_path(tmp_path / "state" / "trade_ledger.db") == (
        tmp_path / "state" / "runtime_mode.json"
    )


def test_request_rejects_an_unknown_mode(control: ModeControl) -> None:
    with pytest.raises(ModeControlError):
        control.request("demo")
    assert not control.path.exists()
