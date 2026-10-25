import datetime as dt
import json
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock

import pytest
from tests.test_report import _account

from executor.broker import AlpacaPaperClient, Clock, Position
from executor.errors import BrokerError
from executor.journal import Journal
from executor.portfolio_monitor import refresh_portfolio


def _setup(tmp_path):
    now = dt.datetime(2026, 9, 21, tzinfo=dt.UTC)
    clock = Clock(now, False, now, now)
    client = Mock()
    client.get_account.return_value = _account()
    client.list_positions.return_value = ()
    client.activity_history.return_value = ()
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity.ndjson")
    return client, clock, journal


def _fill(key="f1", **changes):
    return dict(
        id=key, activity_type="FILL", symbol="SPY", side="buy", qty="2", price="100", **changes
    )


def test_automatic_baseline_fill_cash_flows_and_restart(tmp_path):
    client, clock, journal = _setup(tmp_path)
    assert refresh_portfolio(tmp_path, client, clock, journal)["status"] == "baseline_recorded"
    baseline = (tmp_path / "portfolio-baseline.json").read_bytes()
    client.activity_history.return_value = (
        _fill(),
        {"id": "d1", "activity_type": "DIV", "net_amount": "3", "qty": "99"},
        {"id": "fee1", "activity_type": "FEE", "net_amount": "-0.01"},
    )
    client.get_account.return_value = replace(_account(), cash=Decimal("39802.99"))
    client.list_positions.return_value = (Position.from_payload({"symbol": "SPY", "qty": "2"}),)
    result = refresh_portfolio(tmp_path, client, clock, journal)
    assert result["status"] == "reconciled"
    assert result["activity_count_since_baseline"] == 3
    assert result["position_differences"] == {}
    assert refresh_portfolio(tmp_path, client, clock, journal)["status"] == "reconciled"
    assert (tmp_path / "portfolio-baseline.json").read_bytes() == baseline
    assert len(journal.read_journal_entries()) == 2


def test_discrepancy_never_rebases_and_recovers_when_activity_arrives(tmp_path):
    client, clock, journal = _setup(tmp_path)
    refresh_portfolio(tmp_path, client, clock, journal)
    client.get_account.return_value = replace(_account(), cash=Decimal("40100"))
    for _ in range(2):
        result = refresh_portfolio(tmp_path, client, clock, journal)
        assert result["status"] == "discrepancy"
        assert Decimal(result["cash_difference"]) == 100
    client.activity_history.return_value = (
        {"id": "cash", "activity_type": "CSD", "net_amount": "100"},
    )
    assert refresh_portfolio(tmp_path, client, clock, journal)["status"] == "reconciled"


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "split", "activity_type": "SSP", "qty": "10", "net_amount": "0"},
        {"id": "div", "activity_type": "DIV", "net_amount": "NaN"},
        {"id": "div", "activity_type": "DIV", "net_amount": "4", "status": "pending"},
        {
            "id": "bad",
            "activity_type": "FILL",
            "side": "sell",
            "qty": "0",
            "price": "10",
            "symbol": "SPY",
        },
    ],
)
def test_unknown_or_invalid_activity_is_unavailable(tmp_path, bad):
    client, clock, journal = _setup(tmp_path)
    refresh_portfolio(tmp_path, client, clock, journal)
    client.activity_history.return_value = (bad,)
    assert (
        refresh_portfolio(tmp_path, client, clock, journal)["status"] == "observation_unavailable"
    )


def test_broker_failure_replaces_green_without_losing_baseline(tmp_path):
    client, clock, journal = _setup(tmp_path)
    refresh_portfolio(tmp_path, client, clock, journal)
    refresh_portfolio(tmp_path, client, clock, journal)
    client.activity_history.side_effect = BrokerError("private detail")
    result = refresh_portfolio(tmp_path, client, clock, journal)
    assert result["status"] == "observation_unavailable"
    assert result["last_reconciled_at"] == clock.timestamp.isoformat()
    assert "private detail" not in (tmp_path / "portfolio-monitor.json").read_text()


def test_baseline_activity_disappearance_and_account_change(tmp_path):
    client, clock, journal = _setup(tmp_path)
    client.activity_history.return_value = (_fill(),)
    refresh_portfolio(tmp_path, client, clock, journal)
    client.activity_history.return_value = ()
    assert (
        refresh_portfolio(tmp_path, client, clock, journal)["reason"]
        == "baseline_activity_missing_or_changed"
    )
    client.get_account.return_value = replace(_account(), account_number="other")
    assert (
        refresh_portfolio(tmp_path, client, clock, journal)["reason"] == "account_identity_changed"
    )


def test_inconsistent_snapshot_does_not_create_baseline(tmp_path):
    client, clock, journal = _setup(tmp_path)
    client.get_account.side_effect = [_account(), replace(_account(), cash=Decimal("2"))]
    assert (
        refresh_portfolio(tmp_path, client, clock, journal)["status"] == "observation_unavailable"
    )
    assert not (tmp_path / "portfolio-baseline.json").exists()


def test_corrupt_baseline_is_preserved(tmp_path):
    client, clock, journal = _setup(tmp_path)
    path = tmp_path / "portfolio-baseline.json"
    path.write_text("{bad")
    assert (
        refresh_portfolio(tmp_path, client, clock, journal)["status"] == "observation_unavailable"
    )
    assert path.read_text() == "{bad"


def test_activity_pagination_is_complete_or_fails(monkeypatch):
    client = AlpacaPaperClient("key", "secret")
    first = [{"id": str(i)} for i in range(100)]
    get = Mock(side_effect=[first, [{"id": "last"}]])
    monkeypatch.setattr(client, "_get", get)
    now = dt.datetime(2026, 9, 21, tzinfo=dt.UTC)
    assert len(client.list_activities(after=now)) == 101
    assert get.call_args.args[1]["page_token"] == "99"
    get.side_effect = [first, first]
    with pytest.raises(BrokerError, match="repeated"):
        client.list_activities(after=now)
    get.side_effect = [first]
    with pytest.raises(BrokerError, match="bound"):
        client.list_activities(after=now, max_pages=1)


def test_status_includes_portfolio(tmp_path, capsys):
    from executor.cli import main

    client, clock, journal = _setup(tmp_path)
    refresh_portfolio(tmp_path, client, clock, journal)
    (tmp_path / "order-registry.json").write_text(
        json.dumps(
            {
                "observed_at": clock.timestamp.isoformat(),
                "market_is_open": False,
                "orders": {},
            }
        )
    )
    assert main(["status", "--state-root", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["portfolio"]["status"] == "baseline_recorded"


def test_status_labels_stale_observations():
    from executor.cli import _freshness

    assert _freshness("2020-01-01T00:00:00+00:00") == "stale"
    assert _freshness(None) == "unavailable"
    assert _freshness("2026-01-01T00:00:00") == "unavailable"
    assert _freshness(dt.datetime.now(dt.UTC).isoformat()) == "fresh"


def test_unexplained_shares_and_partial_sell_are_reconciled(tmp_path):
    client, clock, journal = _setup(tmp_path)
    client.list_positions.return_value = (Position.from_payload({"symbol": "SPY", "qty": "5"}),)
    refresh_portfolio(tmp_path, client, clock, journal)
    client.list_positions.return_value = (Position.from_payload({"symbol": "SPY", "qty": "4.5"}),)
    result = refresh_portfolio(tmp_path, client, clock, journal)
    assert result["position_differences"] == {"SPY": "-0.5"}
    client.activity_history.return_value = (
        {
            "id": "partial",
            "activity_type": "FILL",
            "symbol": "SPY",
            "side": "sell",
            "qty": "0.5",
            "price": "100",
            "cum_qty": "2",  # each activity's qty, not cumulative order fill, must be used
        },
    )
    client.get_account.return_value = replace(_account(), cash=Decimal("40050"))
    assert refresh_portfolio(tmp_path, client, clock, journal)["status"] == "reconciled"


def test_invalid_monitor_metadata_does_not_block_recovery(tmp_path):
    client, clock, journal = _setup(tmp_path)
    refresh_portfolio(tmp_path, client, clock, journal)
    (tmp_path / "portfolio-monitor.json").write_text("[]")
    assert refresh_portfolio(tmp_path, client, clock, journal)["status"] == "reconciled"
