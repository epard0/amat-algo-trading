"""Independent synthetic native-adapter contract/fill/accounting regressions.

These fixed fixtures contain no real market observations or performance evidence.
"""

import copy
import datetime as dt
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from amat.domain.bars import ExecutionWindowBar
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.positions import Position
from autoresearch.improvements.agent_native_adapter import (
    ObservedWindowEvidence,
    canonical_sha,
    compile_adapter,
    execute_adapter,
)
from autoresearch.improvements.agent_portfolio_arm import (
    ArmPolicy,
    AuthorizedIntent,
    ContractError,
    MarketEvidence,
    ResearchContext,
    hold_contract,
)

D = Decimal
START = dt.datetime(2026, 10, 26, 13, 30, tzinfo=dt.UTC)
END = START + dt.timedelta(minutes=5)
DECISION = dt.datetime(2026, 10, 23, 20, tzinfo=dt.UTC)


@pytest.fixture
def context():
    return ResearchContext(
        decision_at=DECISION,
        account_observed_at=DECISION,
        execution_at=START,
        execution_clock="next_session_open",
        expires_at=END + dt.timedelta(seconds=1),
        account_id="SYNTHETIC-NATIVE-ADAPTER-ONLY",
        equity=D("10000"),
        confirmed_cash=D("9000"),
        restricted_cash=D("0"),
        positions=(("QQQ", D("10")),),
        evidence=tuple(
            MarketEvidence(s, f"synthetic-{s}", DECISION, D("100"), D("100"))
            for s in ("QQQ", "SPY")
        ),
        authorized_intents=(),
        policy=ArmPolicy(),
    )


def proposal(
    ctx, *, kind="resize", symbol="QQQ", side="buy", quantity="1", limit="101", trigger=None
):
    value = hold_contract(ctx, model_id="synthetic/model:free", reason="Synthetic abstention")
    value["actions"] = [
        {
            "kind": kind,
            "reason": "Fixed synthetic execution regression",
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "limit_price": limit,
            "evidence_id": f"synthetic-{symbol}",
        }
    ]
    if trigger is not None:
        value["actions"][0]["trigger_price"] = trigger
    return value


def evidence(symbol="QQQ", *, price="100", volume="10000", low="99", high="101", quote="100"):
    return ObservedWindowEvidence(
        bar=ExecutionWindowBar(
            symbol, START.date(), START, END, D(price), D(volume), "synthetic-source"
        ),
        observed_at=END,
        low=None if low is None else D(low),
        high=None if high is None else D(high),
        source_id="synthetic-source",
        opening_quote=None if quote is None else D(quote),
        opening_quote_observed_at=None if quote is None else START,
        range_complete=True,
    )


def coverage():
    return {
        "from_session": DECISION.date().isoformat(),
        "to_session": START.date().isoformat(),
        "source_id": "synthetic-no-actions",
        "actions": [],
        "actions_sha256": canonical_sha([]),
    }


def run(ctx, value=None, ev=None, **kwargs):
    plan = compile_adapter(value or proposal(ctx), ctx)
    return execute_adapter(
        plan, {"QQQ": evidence()} if ev is None else ev, action_coverage=coverage(), **kwargs
    )


def fill_value(fill, key):
    return fill[key] if isinstance(fill, dict) else getattr(fill, key)


@pytest.mark.parametrize("bps", ["2", "6", "10"])
def test_one_share_exact_delta_fees_and_native_cash_conservation(context, bps):
    ctx = replace(context, policy=replace(context.policy, one_way_bps=D(bps)))
    result = run(ctx)
    assert len(result["fills"]) == 1
    fill = result["fills"][0]
    assert D(fill_value(fill, "quantity")) == D("1")
    assert D(fill_value(fill, "price")) == D("100")
    assert D(fill_value(fill, "cost_cash")) == D("100") * D(bps) / D("10000")
    assert context.positions == (("QQQ", D("10")),)
    assert result["execution_authority"] == "none"
    assert result["performance_evidence"] is False
    fees = D("100") * D(bps) / D("10000")
    assert D(result["ledger"]["cash"]) == D("8900") - fees
    assert D(result["ledger"]["positions"]["QQQ"]["quantity"]) == D("11")
    assert D(result["ledger"]["equity"]) == D("10000") - fees
    assert D(result["accounting"]["fees"]) == fees
    assert D(result["accounting"]["market_pnl"]) == D("0")


def test_hold_is_inert_and_preserves_actual_positions(context):
    hold = hold_contract(context, model_id="synthetic/model:free", reason="Synthetic hold")
    result = run(context, hold)
    assert result["fills"] == []
    assert context.confirmed_cash == D("9000")
    assert context.positions == (("QQQ", D("10")),)


@pytest.mark.parametrize(
    "kind,trigger,quote,low,high",
    [
        ("stop_loss", "95", "94", "93", "95"),
        ("take_profit", "105", "106", "105", "107"),
    ],
)
def test_protection_triggers_only_on_actual_opening_quote(context, kind, trigger, quote, low, high):  # noqa: PLR0917 - fixed fixture fields
    p = proposal(
        context,
        kind=kind,
        side="sell",
        quantity="10",
        limit="90" if kind == "stop_loss" else "105",
        trigger=trigger,
    )
    result = run(context, p, {"QQQ": evidence(price=quote, quote=quote, low=low, high=high)})
    assert sum(D(fill_value(f, "quantity")) for f in result["fills"]) == D("10")


@pytest.mark.parametrize(
    "kind,trigger,low,high",
    [
        ("stop_loss", "95", "90", "110"),
        ("take_profit", "105", "90", "110"),
    ],
)
def test_intrawindow_extrema_are_never_inferred_as_trigger(context, kind, trigger, low, high):
    p = proposal(
        context,
        kind=kind,
        side="sell",
        quantity="10",
        limit="90" if kind == "stop_loss" else "105",
        trigger=trigger,
    )
    result = run(context, p, {"QQQ": evidence(low=low, high=high, quote="100")})
    assert result["fills"] == []


@pytest.mark.parametrize(
    "ev",
    [
        evidence(low="99", high="102"),
        evidence(low=None),
        evidence(high=None),
        evidence(volume="0"),
    ],
)
def test_complete_range_and_zero_or_unknown_data_do_not_create_optimistic_fill(context, ev):
    assert run(context, ev={"QQQ": ev})["fills"] == []


def test_missing_window_never_fabricates_fill(context):
    assert run(context, ev={})["fills"] == []


def test_limit_gap_has_no_fill_even_when_prior_mark_was_affordable(context):
    result = run(context, ev={"QQQ": evidence(price="110", low="109", high="111", quote="110")})
    assert result["fills"] == []


def test_one_percent_actual_volume_cap_whole_shares_and_no_mutation(context):
    p = proposal(context, quantity="10")
    original = copy.deepcopy(p)
    result = run(context, p, {"QQQ": evidence(volume="250")})
    assert sum(D(fill_value(f, "quantity")) for f in result["fills"]) == D("2")
    assert D(result["actions"][0]["original_quantity"]) == D("10")
    assert D(result["actions"][0]["cumulative_fills"]) == D("2")
    assert D(result["actions"][0]["remaining_quantity"]) == D("8")
    assert p == original
    assert context.positions == (("QQQ", D("10")),)


def test_less_than_one_share_actual_capacity_produces_no_fractional_buy(context):
    assert run(context, ev={"QQQ": evidence(volume="99")})["fills"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: replace(x, observed_at=END - dt.timedelta(microseconds=1)),
        lambda x: replace(x, source_id="different-source"),
        lambda x: replace(x, bar=replace(x.bar, window_start=START + dt.timedelta(seconds=1))),
    ],
)
def test_source_and_time_identity_fail_closed(context, mutation):
    try:
        result = run(context, ev={"QQQ": mutation(evidence())})
    except (ContractError, ValueError):
        return
    assert result["fills"] == []


def test_expired_proposal_has_no_fill(context):
    ctx = replace(context, expires_at=START + dt.timedelta(seconds=1))
    try:
        result = run(ctx)
    except (ContractError, ValueError):
        return
    assert result["fills"] == []


def test_plan_is_frozen_and_tampered_proposal_identity_rejected(context):
    plan = compile_adapter(proposal(context), context)
    with pytest.raises(FrozenInstanceError):
        plan.plan_id = "mutated"
    with pytest.raises((ContractError, ValueError)):
        execute_adapter(
            replace(plan, plan_id="0" * 64), {"QQQ": evidence()}, action_coverage=coverage()
        )


def test_previous_receipt_refuses_duplicate_or_restart_fill(context):
    plan = compile_adapter(proposal(context), context)
    receipt = execute_adapter(plan, {"QQQ": evidence()}, action_coverage=coverage())
    assert len(receipt["fills"]) == 1
    with pytest.raises((ContractError, ValueError)):
        execute_adapter(
            plan, {"QQQ": evidence()}, previous_receipt=receipt, action_coverage=coverage()
        )


def test_prior_native_ledger_must_match_bound_actual_account(context):
    prior = LedgerSnapshot(
        DECISION.date(), D("9000"), {"QQQ": Position("QQQ", D("10"), D("100"), DECISION.date())}
    )
    result = run(context, priorledger=prior)
    assert len(result["fills"]) == 1
    changed = replace(prior, cash=D("8999"))
    with pytest.raises((ContractError, ValueError)):
        run(context, priorledger=changed)


def test_original_partial_intent_and_pending_cash_are_not_reauthorized(context):
    intent = AuthorizedIntent(
        "synthetic-existing",
        "SPY",
        "buy",
        D("10"),
        D("3"),
        D("7"),
        D("700"),
        context.expires_at,
        context.account_id,
        context.policy.identity,
    )
    ctx = replace(context, authorized_intents=(intent,))
    before = copy.deepcopy(ctx.authorized_intents)
    result = run(ctx, proposal(ctx, side="sell", limit="99"))
    assert len(result["fills"]) == 1
    assert D(result["original_pending_cash"]) == D("700")
    assert result["original_authorized_intents"][0]["intent_id"] == "synthetic-existing"
    assert ctx.authorized_intents == before
    assert (
        intent.original_quantity,
        intent.cumulative_fills,
        intent.remaining_quantity,
        intent.pending_cash,
    ) == (D("10"), D("3"), D("7"), D("700"))


@pytest.mark.parametrize("bps", ["2", "6", "10"])
def test_fee_inclusive_actual_confirmed_cash_boundary_cannot_overdraft(context, bps):
    cash = D("100") * (D("1") + D(bps) / D("10000")) - D("0.000001")
    policy = replace(
        context.policy, one_way_bps=D(bps), cash_reserve_fraction=D("0"), max_symbol=D("1")
    )
    ctx = replace(context, confirmed_cash=cash, equity=cash + D("1000"), policy=policy)
    with pytest.raises((ContractError, ValueError)):
        run(ctx, proposal(ctx, limit="100"))
    assert ctx.confirmed_cash == cash


def test_pending_sell_does_not_supply_purchase_cash(context):
    policy = replace(context.policy, max_symbol=D("1"), cash_reserve_fraction=D("0"))
    ctx = replace(context, policy=policy, confirmed_cash=D("50"), equity=D("1050"))
    intent = AuthorizedIntent(
        "synthetic-pending-sell",
        "QQQ",
        "sell",
        D("10"),
        D("3"),
        D("7"),
        D("0"),
        ctx.expires_at,
        ctx.account_id,
        ctx.policy.identity,
    )
    ctx = replace(ctx, authorized_intents=(intent,))
    p = proposal(ctx, kind="open", symbol="SPY", quantity="1", limit="100")
    with pytest.raises((ContractError, ValueError)):
        run(ctx, p, {"QQQ": evidence(), "SPY": evidence("SPY")})
    assert intent.remaining_quantity == D("7")


def test_proposed_sale_cannot_fund_simultaneous_buy(context):
    policy = replace(context.policy, max_symbol=D("1"), cash_reserve_fraction=D("0"))
    ctx = replace(context, policy=policy, confirmed_cash=D("50"), equity=D("1050"))
    p = proposal(ctx, kind="close", side="sell", quantity="10", limit="99")
    p["actions"].extend(proposal(ctx, kind="open", symbol="SPY", limit="100")["actions"])
    with pytest.raises((ContractError, ValueError)):
        run(ctx, p, {"QQQ": evidence(), "SPY": evidence("SPY")})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: {**x, "actions_sha256": "0" * 64},
        lambda x: {**x, "from_session": "2026-10-24"},
        lambda x: {**x, "to_session": "2026-10-25"},
        lambda x: {**x, "source_id": ""},
    ],
)
def test_corporate_action_coverage_is_bound_not_a_boolean(context, mutation):
    plan = compile_adapter(proposal(context), context)
    with pytest.raises((ContractError, ValueError)):
        execute_adapter(plan, {"QQQ": evidence()}, action_coverage=mutation(coverage()))


def test_missing_corporate_action_proof_is_refused(context):
    plan = compile_adapter(proposal(context), context)
    with pytest.raises((ContractError, ValueError)):
        execute_adapter(plan, {"QQQ": evidence()})


def test_stop_without_actual_opening_quote_has_no_trigger(context):
    p = proposal(context, kind="stop_loss", side="sell", quantity="10", limit="90", trigger="95")
    assert run(context, p, {"QQQ": evidence(low="90", high="110", quote=None)})["fills"] == []


def test_triggered_stop_limit_gap_can_remain_unfilled(context):
    p = proposal(context, kind="stop_loss", side="sell", quantity="10", limit="94", trigger="95")
    result = run(context, p, {"QQQ": evidence(price="92", low="91", high="93", quote="92")})
    assert result["fills"] == []


def test_range_complete_must_be_explicit(context):
    assert run(context, ev={"QQQ": replace(evidence(), range_complete=False)})["fills"] == []


def test_mutated_serialized_proposal_is_rejected(context):
    plan = compile_adapter(proposal(context), context)
    mutated = plan.proposal_json.replace('"quantity":"1"', '"quantity":"2"')
    if mutated == plan.proposal_json:
        mutated = plan.proposal_json.replace('"quantity": "1"', '"quantity": "2"')
    assert mutated != plan.proposal_json
    with pytest.raises((ContractError, ValueError)):
        execute_adapter(
            replace(plan, proposal_json=mutated), {"QQQ": evidence()}, action_coverage=coverage()
        )


@pytest.mark.parametrize("bps", ["2", "6", "10"])
def test_sell_delta_and_native_fee_cash_conservation(context, bps):
    ctx = replace(context, policy=replace(context.policy, one_way_bps=D(bps)))
    result = run(ctx, proposal(ctx, side="sell", quantity="1", limit="99"))
    fee = D("100") * D(bps) / D("10000")
    assert len(result["fills"]) == 1
    assert D(result["fills"][0]["quantity"]) == D("1")
    assert D(result["fills"][0]["cost_cash"]) == fee
    assert D(result["ledger"]["cash"]) == D("9100") - fee
    assert D(result["ledger"]["positions"]["QQQ"]["quantity"]) == D("9")
    assert D(result["ledger"]["equity"]) == D("10000") - fee
    assert D(result["accounting"]["fees"]) == fee


def test_export_is_deep_and_input_proposal_changes_do_not_change_bound_plan(context):
    p = proposal(context)
    plan = compile_adapter(p, context)
    before = plan.as_dict()
    p["actions"][0]["quantity"] = "9"
    exported = plan.as_dict()
    exported["proposal"]["actions"][0]["quantity"] = "8"
    assert plan.as_dict() == before
    result = execute_adapter(plan, {"QQQ": evidence()}, action_coverage=coverage())
    assert D(result["fills"][0]["quantity"]) == D("1")


@pytest.mark.parametrize("flag", ["allow_direct_short", "allow_inverse", "allow_intraday"])
def test_advanced_policy_capabilities_are_not_silently_supported(context, flag):
    ctx = replace(context, policy=replace(context.policy, **{flag: True}))
    with pytest.raises((ContractError, ValueError)):
        compile_adapter(proposal(ctx), ctx)


def test_price_basis_adjusted_is_not_native_raw_execution(context):
    ev = replace(evidence(), price_basis="adjusted")
    try:
        result = run(context, ev={"QQQ": ev})
    except (ContractError, ValueError):
        return
    assert result["fills"] == []


def test_nonempty_corporate_actions_are_not_silently_ignored(context):
    plan = compile_adapter(proposal(context), context)
    actions = [
        {"kind": "split", "symbol": "QQQ", "ratio": "2", "session": START.date().isoformat()}
    ]
    proof = {**coverage(), "actions": actions, "actions_sha256": canonical_sha(actions)}
    with pytest.raises((ContractError, ValueError)):
        execute_adapter(plan, {"QQQ": evidence()}, action_coverage=proof)


def test_late_quote_cannot_trigger_protective_action(context):
    p = proposal(context, kind="stop_loss", side="sell", quantity="10", limit="90", trigger="95")
    ev = replace(
        evidence(price="94", low="93", high="95", quote="94"),
        opening_quote_observed_at=START + dt.timedelta(seconds=1),
    )
    assert run(context, p, {"QQQ": ev})["fills"] == []


def test_explicit_full_exit_preserves_legacy_fractional_quantity(context):
    ctx = replace(context, positions=(("QQQ", D("5.5")),), equity=D("9550"))
    p = proposal(ctx, kind="close", side="sell", quantity="5.5", limit="99")
    result = run(ctx, p)
    assert sum(D(fill_value(f, "quantity")) for f in result["fills"]) == D("5.5")
    assert "QQQ" not in result["ledger"]["positions"]
    assert D(result["ledger"]["cash"]) == D("9549.45")


def test_invalid_complete_range_cannot_authorize_native_fill(context):
    try:
        result = run(context, ev={"QQQ": evidence(low="101", high="99")})
    except (ContractError, ValueError):
        return
    assert result["fills"] == []


def test_zero_volume_window_never_advances_valuation_from_unproved_vwap(context):
    result = run(context, ev={"QQQ": evidence(price="110", volume="0", low="109", high="111")})
    assert result["fills"] == []
    assert result["valuation_advanced"] is False
    assert result["accounting"] is None
    assert D(result["ledger"]["cash"]) == D("9000")
    assert D(result["ledger"]["positions"]["QQQ"]["mark"]) == D("100")
    assert D(result["ledger"]["equity"]) == D("10000")
    assert result["ledger"]["session_date"] == DECISION.date().isoformat()


def test_unknown_extra_execution_symbol_is_refused(context):
    with pytest.raises((ContractError, ValueError)):
        run(context, ev={"QQQ": evidence(), "UNKNOWN": evidence("UNKNOWN")})


def test_missing_window_consumes_one_attempt_and_cannot_be_retried(context):
    plan = compile_adapter(proposal(context), context)
    receipt = execute_adapter(plan, {}, action_coverage=coverage())
    assert receipt["fills"] == []
    assert receipt["valuation_advanced"] is False
    with pytest.raises((ContractError, ValueError)):
        execute_adapter(
            plan, {"QQQ": evidence()}, previous_receipt=receipt, action_coverage=coverage()
        )


@pytest.mark.parametrize("bps", ["2", "6", "10"])
def test_fully_invested_account_can_close_without_new_cash_budget(context, bps):
    policy = replace(context.policy, one_way_bps=D(bps), max_symbol=D("1"))
    ctx = replace(context, confirmed_cash=D("0"), equity=D("1000"), policy=policy)
    result = run(ctx, proposal(ctx, kind="close", side="sell", quantity="10", limit="99"))
    fee = D("1000") * D(bps) / D("10000")
    assert sum(D(f["quantity"]) for f in result["fills"]) == D("10")
    assert result["ledger"]["positions"] == {}
    assert D(result["ledger"]["cash"]) == D("1000") - fee
    assert D(result["accounting"]["fees"]) == fee
