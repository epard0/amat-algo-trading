"""Economic refusal cases for the inert portfolio-agent research arm."""

import copy
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
    now = dt.datetime(2026, 10, 23, 20, tzinfo=dt.UTC)
    return ResearchContext(
        decision_at=now,
        account_observed_at=now - dt.timedelta(seconds=1),
        execution_at=now + dt.timedelta(days=3),
        execution_clock="next_session_open",
        expires_at=now + dt.timedelta(days=3, minutes=5),
        account_id="paper-hash",
        equity=Decimal("10000"),
        confirmed_cash=Decimal("9000"),
        restricted_cash=Decimal(0),
        positions=(("QQQ", Decimal(10)),),
        evidence=tuple(
            MarketEvidence(s, s + "-hash", now, Decimal(100), Decimal(100)) for s in ("QQQ", "SPY")
        ),
        authorized_intents=(),
        policy=ArmPolicy(),
    )


def proposal(  # noqa: PLR0917 - fixture builder
    context, kind="open", symbol="SPY", side="buy", quantity="10", price="101", trigger=None
):
    p = hold_contract(context, model_id="vendor/model:free", reason="explicit abstention")
    action = {
        "kind": kind,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "limit_price": price,
        "evidence_id": symbol + "-hash",
        "reason": "fixed research hypothesis",
    }
    if trigger is not None:
        action["trigger_price"] = trigger
    p["actions"] = [action]
    return p


def test_hold_is_inert_and_hash_bound(context):
    p = hold_contract(context, model_id="vendor/model:free", reason="no actionable evidence")
    receipt = validate_action_contract(p, context)
    assert receipt["execution_authority"] == "none"
    assert receipt["new_cash_commitment"] == "0"
    assert receipt["status"] == "admissible_for_replay_only"
    changed = copy.deepcopy(p)
    changed["execution_authority"] = "paper"
    with pytest.raises(ContractError, match="execution_authority"):
        validate_action_contract(changed, context)


def test_buy_reserves_costs_and_no_sale_proceeds(context):
    result = validate_action_contract(proposal(context), context)
    assert Decimal(result["new_cash_commitment"]) == Decimal("1011.010")
    low = replace(context, confirmed_cash=Decimal(100), equity=Decimal(1100))
    p = proposal(low, kind="close", symbol="QQQ", side="sell", quantity="10", price="100")
    p["actions"] += proposal(low, quantity="1", price="100")["actions"]
    with pytest.raises(ContractError, match="confirmed cash"):
        validate_action_contract(p, low)


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", "other"),
        ("policy_id", "other"),
        ("evidence_sha256", "other"),
        ("execution_at", "2026-10-23T20:00:00+00:00"),
        ("research_only", False),
    ],
)
def test_identity_and_clock_cannot_be_rewritten(context, field, value):
    p = proposal(context)
    p[field] = value
    with pytest.raises(ContractError):
        validate_action_contract(p, context)


@pytest.mark.parametrize(
    "kind,side,trigger,price",
    [
        ("stop_loss", "buy", "90", "91"),
        ("stop_loss", "sell", "110", "109"),
        ("take_profit", "sell", "90", "89"),
    ],
)
def test_wrong_side_protection_refused(context, kind, side, trigger, price):
    with pytest.raises(ContractError):
        validate_action_contract(
            proposal(context, kind, "QQQ", side, "10", price, trigger), context
        )


def test_stop_and_take_profit_for_long_and_short_holdings(context):
    for kind, trigger, price in [("stop_loss", "90", "89"), ("take_profit", "110", "109")]:
        validate_action_contract(
            proposal(context, kind, "QQQ", "sell", "10", price, trigger), context
        )
    short = replace(
        context,
        positions=(("QQQ", Decimal(-10)),),
        confirmed_cash=Decimal(11000),
        restricted_cash=Decimal(1000),
    )
    for kind, trigger, price in [("stop_loss", "110", "111"), ("take_profit", "90", "91")]:
        validate_action_contract(proposal(short, kind, "QQQ", "buy", "10", price, trigger), short)
    with pytest.raises(ContractError, match="reversal"):
        validate_action_contract(proposal(context, "resize", "QQQ", "sell", "11"), context)


def test_overlapping_authority_and_pending_cash_cannot_be_reused(context):
    intent = AuthorizedIntent(
        "existing",
        "QQQ",
        "buy",
        Decimal(10),
        Decimal(3),
        Decimal(7),
        Decimal(8500),
        context.expires_at,
        context.account_id,
        context.policy.identity,
    )
    pending = replace(context, authorized_intents=(intent,))
    with pytest.raises(ContractError, match="existing authority"):
        validate_action_contract(proposal(pending, "resize", "QQQ"), pending)
    with pytest.raises(ContractError, match="confirmed cash"):
        validate_action_contract(proposal(pending), pending)
    invalid = replace(pending, authorized_intents=(replace(intent, remaining_quantity=Decimal(8)),))
    with pytest.raises(ContractError, match="authorization"):
        validate_action_contract(
            hold_contract(invalid, model_id="v/m:free", reason="hold"), invalid
        )


def test_stale_future_missing_data_and_bad_nav_fail_closed(context):
    for ctx in (
        replace(context, account_observed_at=context.decision_at - dt.timedelta(hours=1)),
        replace(
            context,
            evidence=(
                replace(
                    context.evidence[0], available_at=context.decision_at + dt.timedelta(seconds=1)
                ),
            ),
        ),
        replace(context, evidence=(context.evidence[1],)),
        replace(context, equity=Decimal(9999)),
    ):
        with pytest.raises(ContractError):
            validate_action_contract(hold_contract(ctx, model_id="v/m:free", reason="hold"), ctx)


@pytest.mark.parametrize("quantity", ["0.5", "101", "NaN", "Infinity", "-1", 3])
def test_integer_capacity_and_exact_valid_numbers(context, quantity):
    with pytest.raises(ContractError):
        validate_action_contract(proposal(context, quantity=quantity), context)


def test_short_and_inverse_are_separate_capabilities(context):
    with pytest.raises(ContractError, match="short"):
        validate_action_contract(proposal(context, side="sell"), context)
    short = replace(
        context,
        policy=replace(context.policy, allow_direct_short=True),
        evidence=(
            context.evidence[0],
            replace(
                context.evidence[1],
                borrow_available=True,
                borrow_annual_rate=Decimal("0.06"),
                short_stress_validated=True,
            ),
        ),
    )
    assert Decimal(
        validate_action_contract(proposal(short, "hedge", side="sell"), short)[
            "new_cash_commitment"
        ]
    ) > Decimal(1000)
    missing = replace(
        short, evidence=(short.evidence[0], replace(short.evidence[1], borrow_annual_rate=None))
    )
    with pytest.raises(ContractError, match="short"):
        validate_action_contract(proposal(missing, side="sell"), missing)
    inverse = replace(
        context,
        evidence=(context.evidence[0], replace(context.evidence[1], instrument="inverse_etf")),
    )
    with pytest.raises(ContractError, match="inverse"):
        validate_action_contract(proposal(inverse), inverse)
    inverse = replace(inverse, policy=replace(context.policy, allow_inverse=True))
    validate_action_contract(proposal(inverse), inverse)


def test_intraday_requires_explicit_verified_clock_capability(context):
    intra = replace(
        context,
        execution_clock="next_bar_open",
        execution_at=context.decision_at + dt.timedelta(minutes=1),
    )
    with pytest.raises(ContractError, match="intraday"):
        validate_action_contract(proposal(intra), intra)
    intra = replace(intra, policy=replace(intra.policy, allow_intraday=True))
    validate_action_contract(proposal(intra), intra)


def test_gross_net_symbol_limits_and_no_contingent_risk_credit(context):
    for policy in (
        replace(context.policy, max_gross=Decimal("0.15")),
        replace(context.policy, max_abs_net=Decimal("0.15")),
        replace(context.policy, max_symbol=Decimal("0.05")),
    ):
        ctx = replace(context, policy=policy)
        with pytest.raises(ContractError, match="risk limit"):
            validate_action_contract(proposal(ctx), ctx)
    ctx = replace(context, policy=replace(context.policy, max_gross=Decimal("0.15")))
    p = proposal(ctx, "stop_loss", "QQQ", "sell", "10", "89", "90")
    p["actions"] += proposal(ctx)["actions"]
    with pytest.raises(ContractError, match="risk limit"):
        validate_action_contract(p, ctx)


def test_model_cannot_smuggle_code_brackets_or_paid_model(context):
    p = proposal(context)
    p["code"] = "place_order()"
    with pytest.raises(ContractError, match="unknown"):
        validate_action_contract(p, context)
    p = proposal(context)
    p["actions"][0]["python"] = "return 1"
    with pytest.raises(ContractError, match="unknown"):
        validate_action_contract(p, context)
    p = proposal(context)
    p["actions"] *= 2
    with pytest.raises(ContractError, match="multiple actions"):
        validate_action_contract(p, context)
    p = proposal(context)
    p["model_id"] = "paid-model"
    with pytest.raises(ContractError, match="free model"):
        validate_action_contract(p, context)


def test_short_proceeds_not_free_cash_and_policy_cannot_under_collateralize(context):
    bad = replace(context, positions=(("QQQ", Decimal(-10)),), confirmed_cash=Decimal(11000))
    with pytest.raises(ContractError, match="proceeds"):
        validate_action_contract(hold_contract(bad, model_id="v/m:free", reason="hold"), bad)
    bad = replace(context, policy=replace(context.policy, short_collateral_fraction=Decimal("0.1")))
    with pytest.raises(ContractError, match="full collateral"):
        validate_action_contract(hold_contract(bad, model_id="v/m:free", reason="hold"), bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", []),
        ("kind", {}),
        ("symbol", []),
        ("side", []),
        ("quantity", None),
        ("limit_price", {}),
        ("reason", ["code"]),
    ],
)
def test_malformed_model_types_raise_contract_refusal(context, field, value):
    p = proposal(context)
    p["actions"][0][field] = value
    with pytest.raises(ContractError):
        validate_action_contract(p, context)


@pytest.mark.parametrize("limit_field", ["max_gross", "max_abs_net", "max_symbol"])
@pytest.mark.parametrize("reverse", [False, True])
def test_multi_action_risk_keeps_every_limit_price(context, limit_field, reverse):
    # Marks imply only $800, but executable proposed limits commit $960.
    # The earlier $480 leg must not fall back to its $400 marked value when
    # the second leg is checked. Test both action orders and all limit types.
    limits = {"max_gross": Decimal(1), "max_abs_net": Decimal(1), "max_symbol": Decimal(1)}
    limits[limit_field] = Decimal("0.45") if limit_field == "max_symbol" else Decimal("0.9")
    ctx = replace(
        context,
        equity=Decimal(1000),
        confirmed_cash=Decimal(1000),
        positions=(),
        policy=replace(context.policy, one_way_bps=Decimal(0), **limits),
    )
    p = proposal(ctx, symbol="QQQ", quantity="4", price="120")
    p["actions"] += proposal(ctx, symbol="SPY", quantity="4", price="120")["actions"]
    if reverse:
        p["actions"].reverse()
    with pytest.raises(ContractError, match="risk limit"):
        validate_action_contract(p, ctx)


def test_multi_action_below_risk_limit_remains_admissible(context):
    ctx = replace(
        context,
        equity=Decimal(1000),
        confirmed_cash=Decimal(1000),
        positions=(),
        policy=replace(
            context.policy,
            one_way_bps=Decimal(0),
            max_gross=Decimal("0.9"),
            max_abs_net=Decimal("0.9"),
            max_symbol=Decimal("0.45"),
        ),
    )
    p = proposal(ctx, symbol="QQQ", quantity="3", price="120")
    p["actions"] += proposal(ctx, symbol="SPY", quantity="3", price="120")["actions"]
    assert Decimal(validate_action_contract(p, ctx)["new_cash_commitment"]) == Decimal(720)
