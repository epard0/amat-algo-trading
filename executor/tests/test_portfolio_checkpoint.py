"""A verified checkpoint cannot hide drift or discard correction evidence."""

import datetime as dt
import json
from dataclasses import replace
from decimal import Decimal

import pytest
from tests.test_portfolio_monitor import _fill, _setup
from tests.test_report import _account

from executor.broker import Position
from executor.portfolio_monitor import refresh_portfolio


def prepare(tmp_path):
    client, clock, journal = _setup(tmp_path)
    refresh_portfolio(tmp_path, client, clock, journal)
    old = (tmp_path / "portfolio-baseline.json").read_bytes()
    later = replace(clock, timestamp=clock.timestamp + dt.timedelta(days=31))
    client.activity_history.return_value = (_fill(),)
    client.get_account.return_value = replace(_account(), cash=Decimal("39800"))
    client.list_positions.return_value = (Position.from_payload({"symbol": "SPY", "qty": "2"}),)
    return client, later, journal, old


def test_checkpoint_preserves_origin_history_and_replays_once(tmp_path):
    client, clock, journal, old = prepare(tmp_path)
    result = refresh_portfolio(tmp_path, client, clock, journal)
    assert result["checkpoint_created"] is True
    baseline = json.loads((tmp_path / "portfolio-baseline.json").read_text())
    proof = json.loads(next((tmp_path / "portfolio-checkpoints").glob("*.json")).read_text())
    assert proof["previous_baseline"] == json.loads(old)
    assert proof["activities"]["f1"] == _fill()
    assert baseline["activities_after"] == json.loads(old)["activities_after"]
    result = refresh_portfolio(tmp_path, client, clock, journal)
    assert result["status"] == "reconciled"
    assert result["activity_count_since_baseline"] == 0
    assert "checkpoint_created" not in result
    client.activity_history.return_value = (
        _fill(),
        {"id": "d", "activity_type": "DIV", "net_amount": "3"},
    )
    client.get_account.return_value = replace(_account(), cash=Decimal("39803"))
    result = refresh_portfolio(tmp_path, client, clock, journal)
    assert result["status"] == "reconciled"
    assert result["activity_count_since_baseline"] == 1


@pytest.mark.parametrize(
    ("cash", "shares"), [("39800.005", "2"), ("39800", "2.0000005"), ("39700", "2")]
)
def test_checkpoint_cannot_erase_even_tolerated_drift(tmp_path, cash, shares):
    client, clock, journal, old = prepare(tmp_path)
    client.get_account.return_value = replace(_account(), cash=Decimal(cash))
    client.list_positions.return_value = (Position.from_payload({"symbol": "SPY", "qty": shares}),)
    result = refresh_portfolio(tmp_path, client, clock, journal)
    assert result["exact_match"] is False
    assert "checkpoint_created" not in result
    assert (tmp_path / "portfolio-baseline.json").read_bytes() == old


@pytest.mark.parametrize("damage", ["proof", "anchor", "activity", "missing"])
def test_changed_checkpoint_or_old_activity_fails_closed(tmp_path, damage):
    client, clock, journal, _ = prepare(tmp_path)
    refresh_portfolio(tmp_path, client, clock, journal)
    path = tmp_path / "portfolio-baseline.json"
    if damage == "proof":
        next((tmp_path / "portfolio-checkpoints").glob("*.json")).write_text("{}")
    elif damage == "missing":
        next((tmp_path / "portfolio-checkpoints").glob("*.json")).unlink()
    elif damage == "anchor":
        value = json.loads(path.read_text())
        value["snapshot"]["cash"] = "0"
        path.write_text(json.dumps(value))
    else:
        client.activity_history.return_value = ()
    before = path.read_bytes()
    assert (
        refresh_portfolio(tmp_path, client, clock, journal)["status"] == "observation_unavailable"
    )
    assert path.read_bytes() == before


def test_failed_proof_write_preserves_old_baseline(tmp_path, monkeypatch):
    import executor.portfolio_checkpoint as module

    client, clock, journal, old = prepare(tmp_path)
    monkeypatch.setattr(module.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("disk")))
    # The same os module also affects the status projection: observe the error,
    # then confirm the authoritative baseline was never replaced.
    with pytest.raises(OSError, match="disk"):
        refresh_portfolio(tmp_path, client, clock, journal)
    assert (tmp_path / "portfolio-baseline.json").read_bytes() == old


def test_two_checkpoints_verify_ancestors(tmp_path):
    client, clock, journal, _ = prepare(tmp_path)
    refresh_portfolio(tmp_path, client, clock, journal)
    ancestor = next((tmp_path / "portfolio-checkpoints").glob("*.json"))
    client.activity_history.return_value = (
        _fill(),
        {"id": "d", "activity_type": "DIV", "net_amount": "3"},
    )
    client.get_account.return_value = replace(_account(), cash=Decimal("39803"))
    clock = replace(clock, timestamp=clock.timestamp + dt.timedelta(days=31))
    assert refresh_portfolio(tmp_path, client, clock, journal)["checkpoint_created"]
    assert len(list((tmp_path / "portfolio-checkpoints").glob("*.json"))) == 2
    ancestor.write_text("{}")
    assert (
        refresh_portfolio(tmp_path, client, clock, journal)["status"] == "observation_unavailable"
    )
