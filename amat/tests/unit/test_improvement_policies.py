"""Economic invariants for opt-in risk and cash sizing."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.execution.budget import CashBudgetPolicy, budget_buys
from amat.risk.active import ActiveRiskConfig, apply_active_risk, initial_state
from autoresearch.improvements.study import recipes


def test_proportional_budget_is_order_independent_and_reserves_equity():
    requested = {"A": Decimal(10), "B": Decimal(20)}
    prices = {"A": Decimal(10), "B": Decimal(5)}
    policy = CashBudgetPolicy(price_buffer_fraction=Decimal(0))
    args = {"confirmed_cash": Decimal(100), "equity": Decimal(1000), "policy": policy}
    q, residual = budget_buys(requested, prices, **args)
    reverse, _ = budget_buys(dict(reversed(list(requested.items()))), prices, **args)
    assert q == reverse
    assert sum(q[s] * prices[s] * Decimal("1.0002") for s in q) <= Decimal(90)
    assert abs(q["B"] - 2 * q["A"]) <= Decimal(".000002")
    assert all(q[s] + residual[s] == requested[s] for s in q)


def test_pending_cash_is_not_spendable():
    q, _ = budget_buys(
        {"A": Decimal(10)},
        {"A": Decimal(10)},
        confirmed_cash=Decimal(100),
        equity=Decimal(100),
        committed_cash=Decimal(100),
    )
    assert q["A"] == 0


def test_deposit_does_not_reset_drawdown_or_create_profit():
    t = dt.datetime(2026, 1, 2, 12, tzinfo=dt.UTC)
    state = initial_state("account", "policy", t, 100)
    loss = state.observe(t + dt.timedelta(minutes=1), 90)
    deposit = loss.observe(t + dt.timedelta(minutes=2), 190, external_flow=100)
    assert deposit.nav == pytest.approx(0.9)
    assert deposit.nav / deposit.high_water - 1 == pytest.approx(-0.1)
    with pytest.raises(ValueError, match="backwards"):
        deposit.observe(t, 100)


def test_risk_reduces_stress_and_does_not_repeatedly_halve_holdings():
    t = dt.datetime(2026, 1, 2, 12, tzinfo=dt.UTC)
    state = initial_state("account", "policy", t, 100).observe(t + dt.timedelta(minutes=1), 89)
    targets = {"A": 0.5, "B": 0.4}
    held = targets.copy()
    shocks = ({"A": -0.3, "B": -0.1},)
    config = ActiveRiskConfig(stress_budget=0.5)
    first = apply_active_risk(targets, held, state, config, shocks)
    second = apply_active_risk(targets, first.targets, first.state, config, shocks)
    assert first.targets == second.targets
    assert first.targets == {"A": 0.25, "B": 0.2}
    stress = apply_active_risk(
        targets, held, initial_state("a", "p", t, 100), ActiveRiskConfig(stress_budget=0.04), shocks
    )
    assert stress.stress_loss_after <= 0.04 + 1e-12
    assert all(stress.targets[s] <= targets[s] for s in targets)


def test_variance_share_fail_closed_when_constraint_infeasible():
    t = dt.datetime(2026, 1, 2, 12, tzinfo=dt.UTC)
    result = apply_active_risk(
        {"A": 0.9},
        {"A": 0.9},
        initial_state("a", "p", t, 100),
        ActiveRiskConfig(max_positive_variance_share=0.4),
        ({"A": -0.01},),
        covariance=np.eye(1),
    )
    assert result.targets["A"] == 0


def test_fixed_recipes_preserve_baseline_and_isolate_parameters():
    root = Path(__file__).resolve().parents[2]
    s = load_strategy_config(root / "configs/strategy/amat_v13_return_target.toml").strategy
    arms = recipes(s)
    assert arms["baseline"] == s.model_dump(mode="json")
    from amat.config.models import StrategyConfig

    assert StrategyConfig.model_validate(arms["A3"]).allocation.volatility_power == 1
    assert arms["C1"]["warmup"] == arms["baseline"]["warmup"]
    assert arms["B1"]["cross_sectional"].get("selection_interval_sessions", 1) == 1


def test_risk_checkpoint_cannot_be_moved_to_another_account():
    from amat.risk.active import restore_risk_checkpoint, risk_checkpoint

    t = dt.datetime(2026, 1, 2, 12, tzinfo=dt.UTC)
    state = initial_state("actual-account", "policy", t, 100)
    value = risk_checkpoint(state)
    assert restore_risk_checkpoint(value, "actual-account", "policy") == state
    with pytest.raises(ValueError, match="identity"):
        restore_risk_checkpoint(value, "another-account", "policy")


@pytest.mark.parametrize("price", ["NaN", "sNaN", "Infinity", "-Infinity", "0", "-1"])
def test_cash_budget_rejects_invalid_prices_as_validation_errors(price):
    with pytest.raises(ValueError, match="reference price"):
        budget_buys(
            {"A": Decimal(1)},
            {"A": Decimal(price)},
            confirmed_cash=Decimal(100),
            equity=Decimal(100),
        )
