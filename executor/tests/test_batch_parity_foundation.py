"""Native partial-trim/full-exit distinctions in isolated staged paper execution."""

from dataclasses import replace
from decimal import Decimal

import pytest
from tests.test_batch import Broker, advance, plan

from executor.batch import BatchPolicy, digest, load_batch, save_batch
from executor.errors import ConfigurationError
from executor.plan_source import parse_plan


@pytest.mark.parametrize("whole_share", [False, True])
def test_partial_sale_rounding_preserves_authorized_intent(tmp_path, whole_share):
    broker = Broker()
    broker.positions = {"SPY": Decimal("10.25")}
    payload = dict(plan().raw)
    payload["current_positions"] = {"SPY": "10.25"}
    payload["intents"] = [
        {**payload["intents"][0], "quantity": "2.8", "target_weight": 0.5, "reason": "weight_delta"}
    ]
    original = parse_plan(payload)
    policy = BatchPolicy(whole_share_buys=whole_share)
    result = advance(tmp_path, broker, original, policy=policy)
    sent = Decimal("2") if whole_share else Decimal("2.8")
    assert broker.posts[0]["quantity"] == sent
    row = result.state["intents"][0]
    assert row["original_authorized_quantity"] == "2.8"
    broker.fill(broker.posts[0]["client_order_id"], sent)
    final = advance(tmp_path, broker, original, policy=policy)
    row = final.state["intents"][0]
    assert Decimal(row["cumulative_filled_quantity"]) == sent
    assert Decimal(row["remaining_intent_quantity"]) == Decimal("2.8") - sent
    assert len(broker.posts) == 1
    assert not final.pending


def test_fractional_full_exit_is_preserved_in_whole_share_mode(tmp_path):
    broker = Broker()
    broker.positions = {"SPY": Decimal("10.25")}
    payload = dict(plan().raw)
    payload["current_positions"] = {"SPY": "10.25"}
    payload["intents"] = [
        {**payload["intents"][0], "quantity": "10.25", "reason": "position_transition"}
    ]
    advance(tmp_path, broker, parse_plan(payload), policy=BatchPolicy(whole_share_buys=True))
    assert broker.posts[0]["quantity"] == Decimal("10.25")


def test_post_budget_cash_refresh_cannot_create_fractional_whole_share_buy(tmp_path):
    broker = Broker()
    broker.account = replace(broker.account, cash=Decimal("1000"), buying_power=Decimal("1000"))
    payload = dict(plan().raw)
    payload["intents"] = [{**payload["intents"][1], "quantity": "9.75"}]
    original_get = broker.get_account
    calls = 0

    def decreasing_cash():
        nonlocal calls
        calls += 1
        value = original_get()
        if calls >= 3:
            return replace(value, cash=Decimal("550"), buying_power=Decimal("550"))
        return value

    broker.get_account = decreasing_cash
    result = advance(
        tmp_path, broker, parse_plan(payload), policy=BatchPolicy(whole_share_buys=True)
    )
    assert broker.posts[0]["quantity"] == Decimal("5")
    row = result.state["intents"][0]
    assert row["original_authorized_quantity"] == "9.75"
    assert Decimal(result.state["pending_cash_commitments"]) <= Decimal("540")


def test_updated_partial_sell_semantics_cannot_resume_an_old_policy_identity(tmp_path):
    broker = Broker()
    policy = BatchPolicy(whole_share_buys=True)
    advance(tmp_path, broker, policy=policy)
    path = tmp_path / "batches/2026-08-11.json"
    state = load_batch(path)
    legacy_policy = dict(state["policy"])
    legacy_policy.pop("partial_sell_quantity_quantum")
    legacy_policy.pop("full_exit_quantity_policy")
    state["policy"] = legacy_policy
    state["policy_id"] = digest(legacy_policy)
    save_batch(path, state)
    assert policy.identity != digest(legacy_policy)
    with pytest.raises(ConfigurationError, match="policy_id mismatch"):
        advance(tmp_path, broker, policy=policy)
    assert len(broker.posts) == 1
