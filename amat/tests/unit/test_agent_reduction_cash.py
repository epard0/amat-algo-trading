"""A reserve deficit blocks spending, not a backed research-only sale."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from autoresearch.improvements.agent_portfolio_arm import (
    ArmPolicy,
    ContractError,
    MarketEvidence,
    ResearchContext,
    hold_contract,
    validate_action_contract,
)


def context():
    now = dt.datetime(2026, 10, 23, 20, tzinfo=dt.UTC)
    return ResearchContext(
        now,
        now,
        now + dt.timedelta(days=3),
        "next_session_open",
        now + dt.timedelta(days=3, minutes=5),
        "synthetic-no-cash-exit",
        Decimal(10000),
        Decimal(0),
        Decimal(0),
        (("QQQ", Decimal(100)),),
        tuple(MarketEvidence(s, s, now, Decimal(100), Decimal(100)) for s in ("QQQ", "SPY")),
        (),
        ArmPolicy(max_symbol=Decimal(1)),
    )


def action(kind, side, quantity, symbol="QQQ"):
    value = {
        "kind": kind,
        "reason": "synthetic cash-reserve regression",
        "symbol": symbol,
        "side": side,
        "quantity": str(quantity),
        "limit_price": "90",
        "evidence_id": symbol,
    }
    if kind == "stop_loss":
        value["trigger_price"] = "95"
    return value


@pytest.mark.parametrize("kind,quantity", [("close", 100), ("resize", 1), ("stop_loss", 100)])
def test_fully_invested_backed_sell_does_not_need_purchase_cash(kind, quantity):
    ctx = context()
    p = hold_contract(ctx, model_id="synthetic/model:free", reason="fixture")
    p["actions"] = [action(kind, "sell", quantity)]
    assert validate_action_contract(p, ctx)["new_cash_commitment"] == "0"


def test_fully_invested_hold_has_no_spending():
    ctx = context()
    assert (
        validate_action_contract(
            hold_contract(ctx, model_id="synthetic/model:free", reason="hold"), ctx
        )["new_cash_commitment"]
        == "0"
    )


def test_sell_proceeds_cannot_fund_a_new_buy():
    ctx = context()
    p = hold_contract(ctx, model_id="synthetic/model:free", reason="fixture")
    p["actions"] = [action("close", "sell", 100), action("open", "buy", 1, "SPY")]
    with pytest.raises(ContractError, match="confirmed cash"):
        validate_action_contract(p, ctx)


def test_reserve_deficit_still_blocks_purchases():
    ctx = replace(context(), confirmed_cash=Decimal(50), equity=Decimal(10050))
    p = hold_contract(ctx, model_id="synthetic/model:free", reason="fixture")
    p["actions"] = [action("open", "buy", 1, "SPY")]
    with pytest.raises(ContractError, match="confirmed cash"):
        validate_action_contract(p, ctx)
