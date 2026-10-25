import datetime as dt
from dataclasses import replace
from unittest.mock import Mock

import pytest
from tests.test_report import _order

from executor.broker import Clock
from executor.cli import main
from executor.journal import Journal
from executor.order_registry import order_state, refresh_orders


@pytest.mark.parametrize(
    ("status", "filled", "market_open", "expected"),
    [
        ("new", "0", False, "pending_market_closed"),
        ("pending_cancel", "0", False, "pending_market_closed"),
        ("done_for_day", "0", False, "pending_market_closed"),
        ("suspended", "0", False, "pending_market_closed"),
        ("partially_filled", "3", False, "pending_market_closed"),
        ("new", "0", True, "new"),
        ("new", "0", None, "new"),
        ("canceled", "0", False, "canceled"),
        ("filled", "15.9684", False, "filled"),
    ],
)
def test_closed_market_state_preserves_terminal_and_partial_truth(
    status, filled, market_open, expected
):
    assert order_state(_order(status=status, filled_qty=filled), market_open) == expected


def test_registry_updates_automatically_and_preserves_broker_status(tmp_path, capsys):
    now = dt.datetime(2026, 9, 20, 20, tzinfo=dt.UTC)
    clock = Clock(timestamp=now, is_open=False, next_open=now, next_close=now)
    order = _order(status="pending_cancel", filled_qty="0")
    client = Mock()
    client.list_orders.return_value = (order,)
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity.ndjson")
    first = refresh_orders(tmp_path, client, clock, journal)
    assert first["orders"][order.id]["registry_status"] == "pending_market_closed"
    assert first["orders"][order.id]["broker_status"] == "pending_cancel"
    refresh_orders(tmp_path, client, clock, journal)
    assert len(journal.read_journal_entries()) == 1
    refresh_orders(tmp_path, client, replace(clock, is_open=True), journal)
    assert len(journal.read_journal_entries()) == 2
    assert main(["status", "--state-root", str(tmp_path)]) == 0
    assert "pending_cancel" in capsys.readouterr().out
    client.list_orders.return_value = ()
    missing = refresh_orders(tmp_path, client, clock, journal)
    assert missing["orders"][order.id]["registry_status"] == "observation_unavailable"


def test_primary_cli_is_small(capsys):
    assert main(["--help"]) == 0
    text = capsys.readouterr().out
    assert "run | status" in text
    assert "submit --session" not in text


def test_no_command_runs_automatic_workflow(monkeypatch):
    settings = Mock()
    monkeypatch.setattr("executor.cli.ExecutorSettings.from_environment", lambda: settings)
    run = Mock(return_value=0)
    monkeypatch.setattr("executor.cli.run_daemon", run)
    assert main([]) == 0
    run.assert_called_once_with(settings)
