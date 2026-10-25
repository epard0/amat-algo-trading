"""Corporate legs, history partitioning and actual observation time."""

import datetime as dt
import sqlite3
from decimal import Decimal
from unittest.mock import Mock

import pytest

from executor.broker import AlpacaPaperClient
from executor.corporate_actions import share_effect
from executor.errors import BrokerError
from executor.observation_window import observe_window


def leg(qty):
    return {
        "activity_type": "SPLIT",
        "activity_subtype": "FSPLIT",
        "ref_id": "leg",
        "status": "executed",
        "currency": "USD",
        "qty": qty,
        "net_amount": "0",
        "details": {
            "ca_id": "action",
            "symbol": "SF",
            "old_qty": "0.54796447",
            "new_qty": "0.821946705",
        },
    }


def test_documented_split_legs_are_summed_not_applied_twice():
    removed = share_effect(leg("-0.54796447"))
    added = share_effect(leg("0.821946705"))
    assert removed[1] + added[1] == Decimal("0.273982235")
    assert removed[2] + added[2] == 0


@pytest.mark.parametrize("qty", ["garbage", "NaN", "5"])
def test_invalid_corporate_legs_fail_closed(qty):
    with pytest.raises(ValueError, match="invalid"):
        share_effect(leg(qty))


def test_legacy_ssp_is_not_misread_as_stream_leg():
    with pytest.raises(ValueError, match="unsupported"):
        share_effect({"activity_type": "SSP", "qty": "10"})


def test_history_partitions_overlap_deduplicate_and_reject_changes(monkeypatch):
    client = AlpacaPaperClient("key", "secret")
    error = BrokerError("activity pagination exceeded its bound; history is incomplete")
    call = Mock(
        side_effect=[error, ({"id": "x", "qty": "1"},), ({"id": "x", "qty": "1"}, {"id": "y"})]
    )
    monkeypatch.setattr(client, "list_activities", call)
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    assert len(client.activity_history(after=now, until=now + dt.timedelta(days=1))) == 2
    calls = call.call_args_list
    assert calls[1].kwargs["after"] < calls[2].kwargs["until"]
    call.side_effect = [error, ({"id": "x", "qty": "1"},), ({"id": "x", "qty": "2"},)]
    with pytest.raises(BrokerError, match="changed"):
        client.activity_history(after=now, until=now + dt.timedelta(days=1))


def test_observation_window_requires_real_distinct_open_observations():
    db = sqlite3.connect(":memory:")
    now = dt.datetime(2026, 1, 5, 15, tzinfo=dt.UTC)
    for _ in range(30):
        result = observe_window(db, config="v13", now=now, market_open=True, healthy=True)
    assert result["completed_sessions"] == 0
    for minute in range(4, 33, 4):
        observe_window(
            db, config="v13", now=now + dt.timedelta(minutes=minute), market_open=True, healthy=True
        )
    result = observe_window(
        db, config="v13", now=now + dt.timedelta(days=1), market_open=False, healthy=True
    )
    assert result["completed_sessions"] == 1
    assert result["status"] == "collecting"
    other = observe_window(
        db, config="other", now=now + dt.timedelta(days=1), market_open=False, healthy=True
    )
    assert other["completed_sessions"] == 0
