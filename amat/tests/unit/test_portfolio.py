"""Allocation, constraint order, and the cash-residual invariant."""

from __future__ import annotations

import numpy as np
import pytest

from amat.config.models import ConstraintConfig, CovarianceConfig
from amat.domain.instruments import Cluster, Universe
from amat.errors import DataIntegrityError
from amat.portfolio import (
    apply_constraints,
    forecast_volatility,
    ledoit_wolf_covariance,
    raw_exposure,
    target_weights,
)


@pytest.fixture
def universe() -> Universe:
    return Universe.from_clusters(
        [
            Cluster(name="us_equity", gross_cap=0.40, symbols=("SPY", "QQQ", "IWM")),
            Cluster(name="rates", gross_cap=0.35, symbols=("TLT", "IEF")),
        ]
    )


@pytest.fixture
def identity_covariance() -> np.ndarray:
    return np.eye(5) * 0.04


class TestConstraints:
    def test_symbol_cap_binds(self, universe: Universe) -> None:
        weights = np.array([0.9, 0.0, 0.0, 0.0, 0.0])
        config = ConstraintConfig()
        constrained, binding = apply_constraints(weights, universe, config)
        assert constrained[0] == pytest.approx(0.20)
        assert "symbol_cap" in binding

    def test_cluster_cap_binds_and_preserves_relative_conviction(self, universe: Universe) -> None:
        # Three US equities each at the 20% symbol cap sum to 60% > the 40% cap.
        weights = np.array([0.20, 0.20, 0.10, 0.0, 0.0])
        constrained, binding = apply_constraints(weights, universe, ConstraintConfig())
        equity_gross = constrained[:3].sum()
        assert equity_gross == pytest.approx(0.40)
        assert any(name.startswith("cluster_cap:us_equity") for name in binding)
        # Ratios inside the cluster are unchanged: 2 : 2 : 1
        assert constrained[0] / constrained[2] == pytest.approx(2.0)

    def test_gross_cap_binds(self, universe: Universe) -> None:
        weights = np.array([0.20, 0.20, 0.0, 0.20, 0.20])
        config = ConstraintConfig(max_gross_exposure=0.5)
        constrained, binding = apply_constraints(weights, universe, config)
        assert constrained.sum() == pytest.approx(0.5)
        assert "gross_cap" in binding

    def test_constraints_only_reduce_exposure(self, universe: Universe) -> None:
        rng = np.random.default_rng(4)
        for _ in range(50):
            weights = rng.uniform(0, 0.5, size=5)
            constrained, _ = apply_constraints(weights, universe, ConstraintConfig())
            assert np.all(constrained <= weights + 1e-12)
            assert np.all(constrained >= 0.0)

    def test_negative_weights_are_clipped_to_zero(self, universe: Universe) -> None:
        constrained, _ = apply_constraints(
            np.array([-0.5, 0.1, 0.0, 0.0, 0.0]), universe, ConstraintConfig()
        )
        assert constrained[0] == 0.0


class TestTargetWeights:
    def test_all_zero_scores_give_full_cash(
        self, universe: Universe, identity_covariance: np.ndarray
    ) -> None:
        result = target_weights(
            np.zeros(5), np.full(5, 0.2), identity_covariance, universe, ConstraintConfig()
        )
        assert result.gross_exposure == 0.0
        assert result.cash_weight == 1.0
        assert "all_scores_zero" in result.binding

    def test_residual_stays_in_cash_and_is_never_relevered(
        self, universe: Universe, identity_covariance: np.ndarray
    ) -> None:
        result = target_weights(
            np.full(5, 1.0), np.full(5, 0.2), identity_covariance, universe, ConstraintConfig()
        )
        assert result.gross_exposure <= 1.0 + 1e-12
        assert result.cash_weight == pytest.approx(1.0 - result.gross_exposure)
        # Clipping reduced risk below target; nothing scales it back up.
        assert result.forecast_volatility_post <= result.forecast_volatility_pre + 1e-12

    def test_weights_are_non_negative(
        self, universe: Universe, identity_covariance: np.ndarray
    ) -> None:
        result = target_weights(
            np.array([0.0, 0.5, 1.0, 0.2, 0.0]),
            np.full(5, 0.2),
            identity_covariance,
            universe,
            ConstraintConfig(),
        )
        assert np.all(result.weights >= 0.0)

    def test_unconstrained_portfolio_hits_the_volatility_target(self, universe: Universe) -> None:
        """With caps slack, the scaling step should land on the target exactly."""
        covariance = np.eye(5) * 0.04
        config = ConstraintConfig(max_symbol_weight=1.0, max_gross_exposure=1.0)
        loose = Universe.from_clusters(
            [
                Cluster(name="us_equity", gross_cap=1.0, symbols=("SPY", "QQQ", "IWM")),
                Cluster(name="rates", gross_cap=1.0, symbols=("TLT", "IEF")),
            ]
        )
        result = target_weights(
            np.array([1.0, 0.0, 0.0, 0.0, 0.0]), np.full(5, 0.2), covariance, loose, config
        )
        assert result.forecast_volatility_post == pytest.approx(
            config.target_annualized_volatility, rel=1e-9
        )

    def test_as_mapping_follows_universe_order(
        self, universe: Universe, identity_covariance: np.ndarray
    ) -> None:
        result = target_weights(
            np.array([1.0, 0.0, 0.0, 0.0, 0.0]),
            np.full(5, 0.2),
            identity_covariance,
            universe,
            ConstraintConfig(),
        )
        assert tuple(result.as_mapping(universe)) == universe.symbols


class TestCovariance:
    def test_is_symmetric_and_positive_semidefinite(self) -> None:
        rng = np.random.default_rng(9)
        returns = rng.normal(0, 0.01, size=(200, 5))
        covariance = ledoit_wolf_covariance(returns, CovarianceConfig(lookback_sessions=120))
        np.testing.assert_allclose(covariance, covariance.T, atol=1e-15)
        assert np.all(np.linalg.eigvalsh(covariance) > -1e-12)

    def test_uses_only_the_trailing_lookback(self) -> None:
        """Rows before the lookback window must not influence the estimate."""
        rng = np.random.default_rng(13)
        returns = rng.normal(0, 0.01, size=(300, 4))
        config = CovarianceConfig(lookback_sessions=120)
        baseline = ledoit_wolf_covariance(returns, config)

        mutated = returns.copy()
        mutated[:100] = 5.0
        np.testing.assert_allclose(baseline, ledoit_wolf_covariance(mutated, config), atol=1e-15)

    def test_short_window_fails_closed(self) -> None:
        with pytest.raises(DataIntegrityError, match="covariance needs"):
            ledoit_wolf_covariance(np.zeros((50, 3)), CovarianceConfig(lookback_sessions=120))

    def test_non_finite_window_fails_closed(self) -> None:
        returns = np.zeros((150, 3))
        returns[10, 1] = np.nan
        with pytest.raises(DataIntegrityError, match="non-finite"):
            ledoit_wolf_covariance(returns, CovarianceConfig(lookback_sessions=120))


class TestRawExposure:
    def test_inverse_volatility_scaling(self) -> None:
        result = raw_exposure(np.array([0.5, 1.0]), np.array([0.1, 0.5]))
        np.testing.assert_allclose(result, np.array([5.0, 2.0]))

    @pytest.mark.parametrize("sigma", [0.0, -0.1, np.nan])
    def test_invalid_sigma_fails_closed(self, sigma: float) -> None:
        with pytest.raises(DataIntegrityError, match="sigma"):
            raw_exposure(np.array([1.0]), np.array([sigma]))

    def test_forecast_volatility_of_zero_weights_is_zero(self) -> None:
        assert forecast_volatility(np.zeros(3), np.eye(3)) == 0.0


@pytest.fixture(scope="module")
def strategy():
    from amat.config.loading import load_strategy_config

    return load_strategy_config("configs/strategy/amat_v1.toml").strategy


@pytest.fixture(scope="module")
def panel(strategy):
    from amat.data.synthetic import generate_panel

    return generate_panel(strategy.to_universe(), sessions=420, seed=11, trend_strength=0.5)


class TestPrecomputedDecisionInputs:
    """The feature cache: bit-identical to recomputing, misalignment refused.

    The cache exists purely for speed -- the session loop was O(T^2) in
    feature work without it -- so the only property that matters is exact
    equality with the recompute-from-prefix path, not closeness.
    """

    def test_cached_decisions_are_bit_identical_to_recomputed_ones(self, strategy, panel) -> None:
        from amat.portfolio import decide, precompute_decision_inputs

        universe = strategy.to_universe()
        cache = precompute_decision_inputs(strategy, panel.adjusted_closes)
        first = strategy.warmup.min_completed_sessions - 1
        compared = []
        for index in (first, first + 1, (first + len(panel)) // 2, len(panel) - 1):
            fresh = decide(strategy, panel.adjusted_closes, universe, decision_index=index)
            cached = decide(
                strategy,
                panel.adjusted_closes,
                universe,
                decision_index=index,
                precomputed=cache,
            )
            assert np.array_equal(cached.weights, fresh.weights), index
            assert cached.forecast_volatility_pre == fresh.forecast_volatility_pre
            assert cached.forecast_volatility_post == fresh.forecast_volatility_post
            assert cached.gross_exposure == fresh.gross_exposure
            assert cached.cash_weight == fresh.cash_weight
            assert cached.binding == fresh.binding
            compared.append(fresh)
        # Vacuity guard: equality between two all-cash allocations proves
        # little. At least one compared decision must actually hold positions,
        # or a fixture change could silently reduce this test to 0 == 0.
        assert any(decision.gross_exposure > 0 for decision in compared)

    def test_every_cache_row_matches_its_prefix_computation(self, strategy, panel) -> None:
        # The property the docstrings actually claim, asserted directly on the
        # feature panels rather than one reduction away through decide(): row t
        # of the full-panel pass equals row t of a pass over [:t+1], for every
        # sampled t and all three cached panels. equal_nan compares the warm-up
        # region too.
        from amat.portfolio import precompute_decision_inputs

        full = precompute_decision_inputs(strategy, panel.adjusted_closes)
        total = len(panel)
        indices = [*range(1, total, 7), total - 1]
        for index in indices:
            prefix = precompute_decision_inputs(strategy, panel.adjusted_closes[: index + 1])
            for name in ("returns", "sigma_panel", "long_scores"):
                assert np.array_equal(
                    getattr(full, name)[index], getattr(prefix, name)[index], equal_nan=True
                ), (name, index)

    def test_the_cache_is_causal_under_future_mutation(self, strategy, panel) -> None:
        # The same guarantee the runner-level leakage tests police, asserted
        # directly at the cache boundary: rewriting rows after the decision
        # session must not change the cached decision at that session.
        from amat.portfolio import decide, precompute_decision_inputs

        universe = strategy.to_universe()
        index = strategy.warmup.min_completed_sessions + 40
        mutated = panel.adjusted_closes.copy()
        mutated[index + 1 :, :] *= 1.7
        original = decide(
            strategy,
            panel.adjusted_closes,
            universe,
            decision_index=index,
            precomputed=precompute_decision_inputs(strategy, panel.adjusted_closes),
        )
        rewritten = decide(
            strategy,
            mutated,
            universe,
            decision_index=index,
            precomputed=precompute_decision_inputs(strategy, mutated),
        )
        assert np.array_equal(original.weights, rewritten.weights)
        assert original.binding == rewritten.binding

    def test_a_misaligned_cache_is_refused(self, strategy, panel) -> None:
        from amat.portfolio import decide, precompute_decision_inputs

        universe = strategy.to_universe()
        cache = precompute_decision_inputs(strategy, panel.adjusted_closes[:-5])
        with pytest.raises(DataIntegrityError, match="misaligned cache"):
            decide(
                strategy,
                panel.adjusted_closes,
                universe,
                decision_index=len(panel) - 1,
                precomputed=cache,
            )

    def test_internally_inconsistent_panels_are_refused(self) -> None:
        from amat.portfolio import PrecomputedDecisionInputs

        with pytest.raises(DataIntegrityError, match="disagree in shape"):
            PrecomputedDecisionInputs(
                returns=np.zeros((10, 3)),
                sigma_panel=np.zeros((10, 3)),
                long_scores=np.zeros((9, 3)),
            )
