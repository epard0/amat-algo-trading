from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.exits import ExitRules
from amat.backtest.runner import ScheduledDecisionContext, _prepare_schedule, run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.domain.types import Side
from amat.errors import DataIntegrityError


@pytest.fixture(scope="module")
def market():
    strategy = load_strategy_config(
        Path(__file__).resolve().parents[2] / "configs/strategy/amat_v1.toml"
    ).strategy
    panel = generate_panel(strategy.to_universe(), sessions=340, seed=13, trend_strength=0.5)
    prices = np.empty_like(panel.adjusted_closes)
    prices[0] = 100
    for i in range(1, len(panel)):
        prices[i, 0] = prices[i - 1, 0] * (1.004 + 0.001 * ((i % 3) - 1))
        prices[i, 1:] = prices[i - 1, 1:] * (1 + 0.0005 * (1 if i % 2 == 0 else -1))
    panel = replace(
        panel,
        adjusted_closes=prices,
        raw_closes=prices.copy(),
        window_vwaps=prices.copy(),
        adjusted_opens=prices.copy(),
        adjusted_highs=prices * 1.001,
        adjusted_lows=prices * 0.999,
    )
    return strategy, panel


def replay(strategy, panel, **kwargs):
    return run_backtest(
        strategy,
        panel,
        strategy.to_universe(),
        max_entry_window_participation=0.01,
        whole_share_buys=True,
        **kwargs,
    )


@pytest.mark.parametrize("cost", [2, 6, 10])
def test_identical_context_is_economically_identical(market, cost):
    strategy, panel = market
    context = ScheduledDecisionContext("annual", panel.sessions[280], panel.sessions[279], strategy)
    base = replay(strategy, panel, one_way_bps=cost)
    scheduled = replay(strategy, panel, one_way_bps=cost, decision_schedule=[context])
    assert base.equity_curve == scheduled.equity_curve
    for a, b in zip(base.sessions, scheduled.sessions, strict=True):
        assert (a.cash, a.fills, a.missed, a.instructions, a.attribution) == (
            b.cash,
            b.fills,
            b.missed,
            b.instructions,
            b.attribution,
        )
    assert scheduled.sessions[280 - 251].activated_context
    assert all(
        f.quantity == f.quantity.to_integral_value()
        for r in scheduled.sessions
        for f in r.fills
        if f.side == Side.BUY
    )


def test_pending_instructions_settle_before_new_context(market):
    strategy, panel = market
    context = ScheduledDecisionContext(
        "cash",
        panel.sessions[280],
        panel.sessions[279],
        strategy,
        weight_scalars=np.zeros(len(panel)),
    )
    base = replay(strategy, panel)
    result = replay(strategy, panel, decision_schedule=[context])
    before = {r.session: r for r in base.sessions}
    activation = next(r for r in result.sessions if r.session == panel.sessions[280])
    assert activation.instructions == before[activation.session].instructions
    assert activation.fills == before[activation.session].fills
    assert result.equity_curve[:30] == base.equity_curve[:30]
    assert next(r for r in result.sessions if r.session == panel.sessions[281]).gross_exposure == 0


def test_exit_basis_and_embargo_survive_policy_change(market):
    strategy, panel = market
    contexts = [
        ScheduledDecisionContext(
            "profit",
            panel.sessions[280],
            panel.sessions[279],
            strategy,
            exit_rules=ExitRules(take_profit=0.03, reentry_embargo_sessions=10),
        ),
        ScheduledDecisionContext(
            "no-new-exits", panel.sessions[281], panel.sessions[280], strategy
        ),
    ]
    result = replay(strategy, panel, decision_schedule=contexts)
    byday = {r.session: r for r in result.sessions}
    assert any(f.side == Side.SELL for f in byday[panel.sessions[281]].fills)
    assert not any(
        f.side == Side.BUY for i in range(282, 291) for f in byday[panel.sessions[i]].fills
    )
    previous = result.initial_equity
    for row in result.sessions:
        a = row.attribution
        assert abs(
            row.equity
            - previous
            - (a.market_pnl + a.distributions + a.cash_yield - a.fees - a.slippage)
        ) < Decimal(".00000001")
        previous = row.equity


@pytest.mark.parametrize(
    "change", [{"version": 2}, {"account_identity": "different"}, {"policy_identity": "different"}]
)
def test_unknown_contract_identity_refused(market, change):
    strategy, panel = market
    context = ScheduledDecisionContext("annual", panel.sessions[280], panel.sessions[279], strategy)
    with pytest.raises(DataIntegrityError):
        replay(strategy, panel, decision_schedule=[replace(context, **change)])


def test_noncausal_or_changed_execution_refused(market):
    strategy, panel = market
    context = ScheduledDecisionContext("annual", panel.sessions[280], panel.sessions[279], strategy)
    for declaration in [
        replace(context, selection_cutoff=context.activation_session),
        replace(
            context,
            strategy=strategy.model_copy(
                update={
                    "execution": strategy.execution.model_copy(
                        update={"signal_to_execution_lag_sessions": 2}
                    )
                }
            ),
        ),
    ]:
        with pytest.raises(DataIntegrityError):
            replay(strategy, panel, decision_schedule=[declaration])
    with pytest.raises(DataIntegrityError):
        replay(strategy, panel, decision_schedule=[context, context])


def test_prepared_arrays_do_not_alias_caller(market):
    strategy, panel = market
    scalars = np.ones(len(panel))
    context = ScheduledDecisionContext(
        "annual", panel.sessions[280], panel.sessions[279], strategy, weight_scalars=scalars
    )
    prepared = _prepare_schedule([context], strategy, panel, one_way_bps=6)
    scalars[:] = 0
    assert np.all(prepared[280].weight_scalars == 1)
    assert not prepared[280].weight_scalars.flags.writeable


@pytest.mark.parametrize("cost", [2, 6, 10])
def test_incumbent_context_binds_effective_cost_before_config_validation(cost):
    strategy = load_strategy_config(
        Path(__file__).resolve().parents[2]
        / "configs/strategy/amat_group_selection_volatility.toml"
    ).strategy
    panel = generate_panel(strategy.to_universe(), sessions=310, seed=31, trend_strength=0.5)
    context = ScheduledDecisionContext("annual", panel.sessions[280], panel.sessions[279], strategy)
    assert (
        replay(strategy, panel, one_way_bps=cost).equity_curve
        == replay(strategy, panel, one_way_bps=cost, decision_schedule=[context]).equity_curve
    )


def test_calendar_only_plan_matches_native_next_session_instructions(market):
    from amat.domain.ledgers import LedgerSnapshot
    from amat.domain.positions import Position
    from amat.planning.scheduled import plan_scheduled_decision
    from autoresearch.genetic.nested_replay import panel_prefix

    strategy, panel = market
    context = ScheduledDecisionContext("annual", panel.sessions[280], panel.sessions[279], strategy)
    result = replay(strategy, panel, decision_schedule=[context])
    quantities = {}
    for row in result.sessions:
        for fill in row.fills:
            quantities[fill.symbol] = quantities.get(fill.symbol, Decimal(0)) + (
                fill.quantity if fill.side == Side.BUY else -fill.quantity
            )
        if row.session == panel.sessions[280]:
            observed = row
            break
    marks = panel.marks_at(280)
    ledger = LedgerSnapshot(
        session_date=observed.session,
        cash=observed.cash,
        positions={
            symbol: Position(symbol, quantity, marks[symbol], observed.session)
            for symbol, quantity in quantities.items()
            if quantity
        },
    )
    assert ledger.equity == observed.equity
    pending = {}
    embargo = {}
    plan = plan_scheduled_decision(
        strategy,
        panel_prefix(panel, 281),
        ledger=ledger,
        schedule=[context],
        execution_session=panel.sessions[281],
        one_way_bps=2,
        entry_basis={},
        embargo_until=embargo,
        pending=pending,
    )
    executed = next(row for row in result.sessions if row.session == panel.sessions[281])
    assert plan.instructions == executed.instructions
    assert plan.execution_authority == "none"
    assert pending == {} and embargo == {}


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -1])
def test_scheduled_plan_rejects_invalid_cost(market, cost):
    from amat.domain.ledgers import LedgerSnapshot
    from amat.planning.scheduled import plan_scheduled_decision

    strategy, panel = market
    ledger = LedgerSnapshot(panel.sessions[-1], Decimal("100000"), {})
    with pytest.raises(DataIntegrityError, match="finite and nonnegative"):
        plan_scheduled_decision(
            strategy,
            panel,
            ledger=ledger,
            schedule=[],
            execution_session=panel.sessions[-1],
            one_way_bps=cost,
            entry_basis={},
            embargo_until={},
            pending={},
        )


def test_annual_schedule_cannot_enable_fractional_purchases(market):
    strategy, panel = market
    context = ScheduledDecisionContext("annual", panel.sessions[280], panel.sessions[279], strategy)
    with pytest.raises(DataIntegrityError, match="native whole-share cash policy"):
        run_backtest(
            strategy,
            panel,
            strategy.to_universe(),
            decision_schedule=[context],
            whole_share_buys=False,
        )
