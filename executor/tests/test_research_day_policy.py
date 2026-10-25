"""Causal admission, cancellation races and isolated persistent research ownership."""

import copy
import datetime as dt
import fcntl
import json
from decimal import Decimal

import pytest
from tests.research_day_fixtures import END, NOW, START, contract, event, observation, rebind

from executor.errors import PlanError
from executor.plan_source import parse_plan
from executor.research_day_policy import (
    ResearchStore,
    admit,
    initial_state,
    observe,
    validate_contract,
    verify_state,
)


@pytest.mark.parametrize("cost", [2, 6, 10])
def test_realistic_limit_commands_and_all_cost_funding(cost):
    p = contract(cost)
    state, commands = admit(initial_state(p), observation(p), NOW)
    assert len(commands) == 1
    body = commands[0]["body"]
    assert body["qty"] == "1"
    assert body["limit_price"] == "101"
    assert body["type"] == "limit"
    assert body["time_in_force"] == "day"
    assert Decimal(state["pending_cash"]) == Decimal(101) * (1 + Decimal(cost) / 10000)
    assert admit(state, observation(p), NOW)[1] == []
    verify_state(state, p)
    with pytest.raises(PlanError):
        parse_plan(p)


@pytest.mark.parametrize("cost", [2, 6, 10])
def test_prefix_cap_is_known_before_submit_and_not_future_full_window(cost):
    p = contract(cost)
    p["original_plan"]["proposal"]["actions"][0]["quantity"] = "20"
    p = rebind(p)
    o = observation(p)
    o["symbols"]["QQQ"]["prefix_volume"] = "550"
    s, commands = admit(initial_state(p), o, NOW)
    assert commands[0]["body"]["qty"] == "5"
    assert s["orders"][0]["original_quantity"] == "20"
    assert s["orders"][0]["remaining_quantity"] == "20"


@pytest.mark.parametrize(
    "field",
    [
        "future_quote",
        "stale_quote",
        "future_volume",
        "old_volume",
        "units",
        "halt",
        "crossed",
        "account",
        "calendar",
        "actions",
        "cash",
        "missing",
    ],
)
def test_unknown_or_noncausal_evidence_consumes_attempt_without_order(field):
    p = contract()
    o = observation(p)
    r = o["symbols"]["QQQ"]
    if field == "future_quote":
        r["quote_time"] = (NOW + dt.timedelta(seconds=1)).isoformat()
    elif field == "stale_quote":
        r["quote_time"] = START.isoformat()
    elif field == "future_volume":
        r["prefix_available_at"] = (NOW + dt.timedelta(seconds=1)).isoformat()
    elif field == "old_volume":
        r["prefix_end"] = START.isoformat()
    elif field == "units":
        r["volume_unit"] = "lots"
    elif field == "halt":
        r["tradable"] = False
    elif field == "crossed":
        r["bid"] = "102"
    elif field == "account":
        o["account"]["account_id"] = "OTHER"
    elif field == "calendar":
        o["calendar"]["session_date"] = "2026-10-25"
    elif field == "actions":
        o["corporate_actions"]["actions"] = [{"kind": "split"}]
    elif field == "cash":
        o["account"]["confirmed_cash"] = "9999"
    else:
        del o["symbols"]["QQQ"]
    s, commands = admit(initial_state(p), o, NOW)
    assert not commands
    assert s["attempted"]
    assert s.get("admission_block")
    assert admit(s, observation(p), NOW)[1] == []


@pytest.mark.parametrize("seconds", [30, 300, 301, 3600])
def test_early_or_expired_admission_has_no_order(seconds):
    p = contract()
    s, commands = admit(initial_state(p), observation(p), START + dt.timedelta(seconds=seconds))
    assert commands == []
    assert s["attempted"]


@pytest.mark.parametrize(
    ("kind", "bid", "expected"),
    [
        ("stop_loss", "95", False),
        ("stop_loss", "90", True),
        ("stop_loss", "85", False),
        ("take_profit", "110", True),
        ("take_profit", "105", False),
    ],
)
def test_one_quote_trigger_and_original_limit(kind, bid, expected):
    p = contract()
    a = p["original_plan"]["proposal"]["actions"][0]
    a.update(
        kind=kind,
        side="sell",
        quantity="10",
        trigger_price="90" if kind == "stop_loss" else "110",
        limit_price="89" if kind == "stop_loss" else "109",
    )
    p = rebind(p)
    o = observation(p)
    o["symbols"]["QQQ"].update(bid=bid, ask=str(Decimal(bid) + Decimal(".5")))
    s, c = admit(initial_state(p), o, NOW)
    assert bool(c) == expected
    assert s["fills"] == []  # a quote/trigger never fabricates a fill


def test_partial_late_fill_cancel_confirmation_restart_and_duplicate(tmp_path):
    p = contract()
    p["original_plan"]["proposal"]["actions"][0]["quantity"] = "20"
    p = rebind(p)
    o = observation(p)
    o["symbols"]["QQQ"]["prefix_volume"] = "550"
    store = ResearchStore(tmp_path, p)
    s, c = store.step("admit", o, NOW)
    assert c[0]["body"]["qty"] == "5"
    assert ResearchStore(tmp_path, p).step("admit", o, NOW)[1] == []
    e = event(s, qty="2", status="partially_filled")
    s, _ = store.step("observe", e, START + dt.timedelta(seconds=63))
    assert Decimal(s["pending_cash"]) == Decimal(3) * Decimal(101) * Decimal("1.0006")
    s, c = store.step("expire", {}, END)
    assert len(c) == 1
    assert c[0]["kind"] == "cancel_request"
    assert ResearchStore(tmp_path, p).step("expire", {}, END)[1] == []
    e = event(s, qty="3", status="pending_cancel", seconds=301, event_id="late")
    s, _ = store.step("observe", e, START + dt.timedelta(seconds=302))
    assert s["fills"][-1]["late_after_submission_cutoff"] is True
    assert Decimal(s["pending_cash"]) == Decimal(2) * Decimal(101) * Decimal("1.0006")
    same, _ = store.step("observe", e, START + dt.timedelta(seconds=303))
    assert same == s
    e = event(s, qty="3", status="canceled", seconds=303, event_id="cancel")
    s, _ = store.step("observe", e, START + dt.timedelta(seconds=304))
    assert s["pending_cash"] == "0"
    assert s["orders"][0]["remaining_quantity"] == "17"
    assert len(s["fills"]) == 2
    assert Decimal(s["cash"]) == Decimal(9000) - Decimal("301.5") - Decimal(".1809")
    assert s["orders"][0]["expires_at"] == p["original_plan"]["context"]["expires_at"]


def test_unknown_ack_is_reserved_and_never_retried(tmp_path):
    p = contract()
    store = ResearchStore(tmp_path, p)
    s, c = store.step("admit", observation(p), NOW)
    assert c
    assert s["orders"][0]["status"] == "unknown"
    s, c = ResearchStore(tmp_path, p).step("expire", {}, END)
    assert not c
    assert s["reconciliation_required"]
    assert Decimal(s["pending_cash"]) == Decimal("101.0606")
    assert ResearchStore(tmp_path, p).step("admit", observation(p), END)[1] == []


@pytest.mark.parametrize(
    "field",
    [
        "account_id",
        "policy_id",
        "symbol",
        "side",
        "quantity",
        "limit_price",
        "type",
        "time_in_force",
        "order_id",
        "status",
        "cumulative_filled",
        "average_price",
        "occurred_at",
    ],
)
def test_bad_broker_observation_quarantines_without_releasing_commitment(tmp_path, field):
    p = contract()
    store = ResearchStore(tmp_path, p)
    s, _ = store.step("admit", observation(p), NOW)
    e = event(s, qty="1", status="filled")
    e[field] = {
        "quantity": "2",
        "cumulative_filled": "2",
        "average_price": "102",
        "limit_price": "102",
        "occurred_at": START.isoformat(),
    }.get(field, "OTHER")
    if field == "order_id":
        e[field] = ""  # a new valid opaque ID is otherwise allowed
    with pytest.raises(ValueError, match="quarantined"):
        store.step("observe", e, START + dt.timedelta(seconds=63))
    s = json.loads((tmp_path / "state.json").read_text())
    assert s["quarantine"]
    assert s["fills"] == []
    assert Decimal(s["pending_cash"]) == Decimal("101.0606")
    assert store.step("admit", observation(p), NOW)[1] == []


def test_changed_duplicate_and_decreasing_cumulative_refused():
    p = contract()
    s, _ = admit(initial_state(p), observation(p), NOW)
    e = event(s, qty="1", status="filled")
    s = observe(s, e, START + dt.timedelta(seconds=63))
    changed = {**e, "status": "canceled"}
    with pytest.raises(ValueError, match="duplicate"):
        observe(s, changed, END)
    with pytest.raises(ValueError, match="Cumulative"):
        observe(s, event(s, qty="0", seconds=64, event_id="backward"), END)


def test_state_corruption_missing_state_foreign_owner_and_lock(tmp_path):
    p = contract()
    store = ResearchStore(tmp_path, p)
    store.step("admit", observation(p), NOW)
    other = copy.deepcopy(p)
    other["original_plan"]["context"]["account_id"] = "OTHER"
    other = rebind(other)
    with pytest.raises(ValueError, match="owner"):
        ResearchStore(tmp_path, other).step("expire", {}, END)
    with (tmp_path / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            store.step("expire", {}, END)
    original = (tmp_path / "state.json").read_text()
    (tmp_path / "state.json").write_text("{}")
    with pytest.raises((ValueError, KeyError)):
        store.step("expire", {}, END)
    (tmp_path / "state.json").write_text(original)
    (tmp_path / "state.json").unlink()
    with pytest.raises(ValueError, match="owner/state"):
        store.step("expire", {}, END)


def test_existing_pending_intents_block_new_risk_even_after_original_expiry():
    p = contract()
    ctx = p["original_plan"]["context"]
    ctx["authorized_intents"] = [
        {
            "intent_id": "old",
            "symbol": "SPY",
            "side": "buy",
            "original_quantity": "7",
            "cumulative_fills": "3",
            "remaining_quantity": "4",
            "pending_cash": "400",
            "expires_at": START.isoformat(),
            "account_id": ctx["account_id"],
            "policy_id": "rebound",
        }
    ]
    p = rebind(p)
    with pytest.raises(ValueError, match="New risk"):
        validate_contract(p)
    p["original_plan"]["proposal"]["actions"] = [{"kind": "hold", "reason": "Preserve pending"}]
    p = rebind(p)
    s, c = admit(initial_state(p), observation(p), NOW)
    assert not c
    assert s["pending_cash"] == "400"
    assert s["original_authorized_intents"] == p["original_plan"]["context"]["authorized_intents"]
