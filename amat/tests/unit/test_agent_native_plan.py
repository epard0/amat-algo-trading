"""Exact research plan round trips, native parity and blocked legacy routing."""

import copy
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from autoresearch.improvements.agent_native_adapter import execute_adapter
from autoresearch.improvements.agent_native_plan import (
    export_native_plan,
    restore_native_plan,
)

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "native_plan_fixtures", ROOT / "amat/scripts/run_native_agent_adapter_study.py"
)
assert spec is not None and spec.loader is not None
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def plan_fixture(case="resize", cost=6):
    context, proposal, evidence, coverage = fixtures.fixture(cost, case)
    plan = fixtures.compile_adapter(proposal, context)
    return context, plan, evidence, coverage


@pytest.mark.parametrize("case", fixtures.CASES)
@pytest.mark.parametrize("cost", [2, 6, 10])
def test_native_roundtrip_preserves_entire_plan_and_execution(case, cost):
    context, plan, evidence, coverage = plan_fixture(case, cost)
    packet = json.loads(json.dumps(export_native_plan(plan)))
    restored = restore_native_plan(packet, context)
    assert restored == plan
    direct = execute_adapter(plan, evidence, action_coverage=coverage)
    replay = execute_adapter(restored, evidence, action_coverage=coverage)
    assert replay == direct
    fills, missed, ledger, attr = fixtures.native_control(context, evidence, case, cost)
    assert replay["fills"] == json.loads(json.dumps([asdict(f) for f in fills], default=str))
    assert replay["missed"] == json.loads(json.dumps([asdict(m) for m in missed], default=str))
    assert replay["ledger"] == fixtures.ledger_json(ledger)
    assert replay["accounting"] == (
        json.loads(json.dumps(asdict(attr), default=str)) if attr else None
    )
    assert packet["native_plan"]["expires_at"] == context.expires_at.isoformat()
    assert packet["original_pending_cash"] == direct["original_pending_cash"]
    assert (
        packet["adapter_plan"]["context"]["authorized_intents"]
        == direct["original_authorized_intents"]
    )
    quantities = [
        a["quantity"] for a in packet["adapter_plan"]["proposal"]["actions"] if a["kind"] != "hold"
    ]
    assert quantities == [i["quantity"] for i in packet["native_plan"]["intents"]]


@pytest.mark.parametrize(
    "field",
    [
        "quantity",
        "trigger",
        "limit",
        "expiry",
        "pending",
        "account",
        "capabilities",
        "extra",
        "authority",
        "boolean_as_integer",
    ],
)
def test_tampering_is_refused_even_with_resealed_outer_digest(field):
    context, plan, _, _ = plan_fixture("stop_triggered")
    value = copy.deepcopy(export_native_plan(plan))
    if field == "quantity":
        value["native_plan"]["intents"][0]["quantity"] = "11"
    elif field == "trigger":
        value["conditions"][0]["trigger_price"] = "100"
    elif field == "limit":
        value["conditions"][0]["limit_price"] = "1"
    elif field == "expiry":
        value["native_plan"]["expires_at"] = "2026-10-27T20:00:00+00:00"
    elif field == "pending":
        value["original_pending_cash"] = "0.01"
    elif field == "account":
        value["account_id"] = "other-account"
    elif field == "capabilities":
        value["native_plan"]["required_capabilities"] = []
    elif field == "extra":
        value["unexpected"] = True
    elif field == "authority":
        value["execution_authority"] = "paper"
    else:
        value["research_only"] = 1
    value["envelope_sha256"] = fixtures.canonical_sha(
        {k: v for k, v in value.items() if k != "envelope_sha256"}
    )
    with pytest.raises(ValueError):
        restore_native_plan(value, context)


def test_supplied_trusted_context_cannot_be_replaced_by_file_context():
    context, plan, _, _ = plan_fixture("pending_hold")
    packet = export_native_plan(plan)
    other = replace(context, account_id="different-trusted-account")
    with pytest.raises(ValueError):
        restore_native_plan(packet, other)
    intent = replace(
        context.authorized_intents[0], expires_at=context.expires_at - dt.timedelta(days=1)
    )
    with pytest.raises(ValueError):
        restore_native_plan(packet, replace(context, authorized_intents=(intent,)))


def test_actual_executor_parser_refuses_both_envelopes_and_transport_is_offline():
    packets = [export_native_plan(plan_fixture(case)[1]) for case in fixtures.CASES]
    completed = subprocess.run(  # noqa: S603 -- fixed local helper, no shell or credentials
        [sys.executable, str(ROOT / "executor/scripts/research_native_contract_probe.py")],
        input=json.dumps(packets),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
        env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "executor/src")},
    )
    results = json.loads(completed.stdout)
    for packet, result in zip(packets, results, strict=True):
        assert result["network_calls"] == 0
        assert result["broker_execution_parity"] is False
        assert len(result["parser_refusals"]) == 2
        for intent, body in zip(
            packet["native_plan"]["intents"], result["wire_payloads"], strict=True
        ):
            assert fixtures.D(body["qty"]) == fixtures.D(intent["quantity"])
            assert body["time_in_force"] == "day"
            assert "stop_price" not in body and "expires_at" not in body
