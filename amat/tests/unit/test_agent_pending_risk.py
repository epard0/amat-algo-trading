"""Pending authorization cannot be omitted from a new shadow risk decision."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from autoresearch.improvements.agent_portfolio_arm import (
    ArmPolicy,
    AuthorizedIntent,
    ContractError,
    MarketEvidence,
    ResearchContext,
    hold_contract,
    validate_action_contract,
)


@pytest.fixture
def context():
    now = dt.datetime(2026, 10, 24, tzinfo=dt.UTC)
    policy = ArmPolicy(
        max_gross=Decimal("0.6"), max_abs_net=Decimal("0.6"), max_symbol=Decimal("0.5")
    )
    return ResearchContext(
        now,
        now,
        now + dt.timedelta(days=3),
        "next_session_open",
        now + dt.timedelta(days=3, minutes=5),
        "synthetic-only",
        Decimal(1000),
        Decimal(1000),
        Decimal(0),
        (),
        tuple(
            MarketEvidence(s, s + "-evidence", now, Decimal(100), Decimal(10))
            for s in ("QQQ", "SPY")
        ),
        (),
        policy,
    )


def pending(context, *, side="buy", filled="0", remaining="4", expired=False):
    quantity = Decimal(filled) + Decimal(remaining)
    intent = AuthorizedIntent(
        "original",
        "QQQ",
        side,
        quantity,
        Decimal(filled),
        Decimal(remaining),
        Decimal(400),
        context.decision_at - dt.timedelta(seconds=1) if expired else context.expires_at,
        context.account_id,
        context.policy.identity,
    )
    return replace(context, authorized_intents=(intent,))


def buy(context):
    value = hold_contract(context, model_id="vendor/model:free", reason="fixture")
    value["actions"] = [
        {
            "kind": "open",
            "reason": "inert regression",
            "symbol": "SPY",
            "side": "buy",
            "quantity": "4",
            "limit_price": "100",
            "evidence_id": "SPY-evidence",
        }
    ]
    return value


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("filled", ["0", "3"])
@pytest.mark.parametrize("expired", [False, True])
def test_different_symbol_pending_exposure_blocks_new_risk(context, side, filled, expired):
    ctx = pending(context, side=side, filled=filled, expired=expired)
    before = ctx.authorized_intents
    with pytest.raises(ContractError, match="outstanding intent risk"):
        validate_action_contract(buy(ctx), ctx)
    assert ctx.authorized_intents == before


def test_hold_preserves_pending_intent_and_reservation(context):
    ctx = pending(context, expired=True)
    result = validate_action_contract(
        hold_contract(ctx, model_id="vendor/model:free", reason="await actual settlement"), ctx
    )
    assert result["new_cash_commitment"] == "0"
    assert result["existing_pending_cash"] == "400"


def test_different_symbol_reduction_remains_possible(context):
    ctx = pending(replace(context, positions=(("SPY", Decimal(1)),), confirmed_cash=Decimal(900)))
    value = buy(ctx)
    value["actions"][0].update(kind="close", side="sell", quantity="1")
    assert validate_action_contract(value, ctx)["execution_authority"] == "none"


def test_fully_filled_intent_does_not_release_still_reserved_cash(context):
    ctx = pending(context, filled="4", remaining="0")
    result = validate_action_contract(buy(ctx), ctx)
    assert result["existing_pending_cash"] == "400"
    assert result["new_cash_commitment"] == "400.400"
    ctx = replace(
        ctx, authorized_intents=(replace(ctx.authorized_intents[0], pending_cash=Decimal(600)),)
    )
    with pytest.raises(ContractError, match="confirmed cash"):
        validate_action_contract(buy(ctx), ctx)


def test_fresh_settled_context_admits_same_sized_proposal(context):
    assert validate_action_contract(buy(context), context)["status"] == "admissible_for_replay_only"
