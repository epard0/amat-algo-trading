"""Composition changes retain causal decisions and the original risk limits."""

from dataclasses import replace

import numpy as np
import pytest
from pydantic import ValidationError

from amat.config.models import AllocationConfig, ConstraintConfig
from amat.config.v6 import load_v6_config
from amat.domain.instruments import Cluster, Universe
from amat.errors import DataIntegrityError
from amat.portfolio import decide, precompute_decision_inputs, target_weights


def test_weighting_reduces_second_volatility_penalty_without_changing_risk_target():
    universe = Universe.from_clusters([Cluster(name="assets", gross_cap=1, symbols=("A", "B"))])
    sigma = np.array([0.1, 0.3])
    covariance = np.diag(sigma**2)
    limits = ConstraintConfig(target_annualized_volatility=0.06, max_symbol_weight=0.8)
    scores = np.ones(2)
    old = target_weights(scores, sigma, covariance, universe, limits)
    explicit = target_weights(
        scores, sigma, covariance, universe, limits, allocation=AllocationConfig()
    )
    equal = target_weights(
        scores,
        sigma,
        covariance,
        universe,
        limits,
        allocation=AllocationConfig(volatility_power=0),
    )
    np.testing.assert_array_equal(old.weights, explicit.weights)
    assert old.weights[0] / old.weights[1] == pytest.approx(3)
    assert equal.weights[0] == pytest.approx(equal.weights[1])
    assert equal.forecast_volatility_post == pytest.approx(0.06)
    assert equal.gross_exposure <= 1


@pytest.mark.parametrize("value", [-1, 1.1, np.nan, np.inf])
def test_invalid_weighting_is_rejected(value):
    with pytest.raises(ValidationError):
        AllocationConfig(volatility_power=value)


@pytest.mark.parametrize("value", [0.0, -1.0, np.nan, np.inf])
def test_power_zero_does_not_hide_invalid_volatility(value):
    universe = Universe.from_clusters([Cluster(name="assets", gross_cap=1, symbols=("A",))])
    with pytest.raises(DataIntegrityError, match="sigma"):
        target_weights(
            np.ones(1),
            np.array([value]),
            np.eye(1),
            universe,
            ConstraintConfig(),
            allocation=AllocationConfig(volatility_power=0),
        )


def test_new_weighting_preserves_prefix_causality_and_clipping(project_root):
    strategy = load_v6_config(project_root / "configs/strategy/amat_v6.toml").config.strategy
    strategy = strategy.model_copy(update={"allocation": AllocationConfig(volatility_power=0)})
    universe = strategy.to_universe()
    rng = np.random.default_rng(341)
    prices = 100 * np.exp(
        np.cumsum(rng.normal(0.0004, 0.012, (400, len(universe.symbols))), axis=0)
    )
    cache = precompute_decision_inputs(strategy, prices)
    actual = decide(strategy, prices, universe, decision_index=310, precomputed=cache)
    prefix = decide(strategy, prices[:311], universe, decision_index=310)
    np.testing.assert_array_equal(actual.weights, prefix.weights)
    changed = prices.copy()
    changed[311:] *= np.exp(rng.normal(0, 0.5, changed[311:].shape))
    mutated = decide(
        strategy,
        changed,
        universe,
        decision_index=310,
        precomputed=precompute_decision_inputs(strategy, changed),
    )
    np.testing.assert_array_equal(actual.weights, mutated.weights)
    assert actual.weights.max() <= 0.20
    assert actual.gross_exposure <= 1
    assert actual.forecast_volatility_post <= actual.forecast_volatility_pre + 1e-12
    all_cash = decide(
        strategy,
        prices,
        universe,
        decision_index=310,
        precomputed=replace(cache, long_scores=np.zeros_like(cache.long_scores)),
    )
    assert all_cash.cash_weight == 1
