"""Feature mathematics, pinned against hand-computed values.

The strategy contract requires the EWMA convention to be "frozen in configuration
and verified with hand-calculated fixtures". These tests are that verification:
the brute-force reference below recomputes the frozen convention directly from
its weight definition, independently of the recursion the implementation uses.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from amat.config.models import TrendConfig, VolatilityConfig
from amat.errors import DataIntegrityError
from amat.features import (
    annualized_volatility,
    ewma_std,
    horizon_log_returns,
    log_returns,
    long_scores,
    trend_scores,
)


def brute_force_ewma_std(column: np.ndarray, half_life: int, min_periods: int) -> np.ndarray:
    """Reference implementation built straight from the weight definition.

    Deliberately ``O(T^2)`` and written from the docstring rather than from the
    implementation, so agreement is evidence rather than a restatement.
    """
    decay = 0.5 ** (1.0 / half_life)
    out = np.full(column.shape, np.nan)
    for end in range(len(column)):
        observations = column[: end + 1]
        observations = observations[np.isfinite(observations)]
        if len(observations) < min_periods:
            continue
        last = len(observations) - 1
        weights = np.array([decay ** (last - i) for i in range(len(observations))])
        weight_sum = weights.sum()
        squared_weight_sum = (weights**2).sum()
        denominator = weight_sum**2 - squared_weight_sum
        if denominator <= 0:
            continue
        mean = (weights * observations).sum() / weight_sum
        biased = (weights * (observations - mean) ** 2).sum() / weight_sum
        out[end] = math.sqrt(biased * weight_sum**2 / denominator)
    return out


class TestLogReturns:
    def test_first_row_is_nan_not_zero(self) -> None:
        prices = np.array([[100.0], [110.0]])
        assert np.isnan(log_returns(prices)[0, 0])

    def test_known_value(self) -> None:
        prices = np.array([[100.0], [110.0], [121.0]])
        result = log_returns(prices)
        assert result[1, 0] == pytest.approx(math.log(1.1), abs=1e-12)
        assert result[2, 0] == pytest.approx(math.log(1.1), abs=1e-12)

    def test_horizon_return_spans_the_whole_window(self) -> None:
        prices = np.array([[100.0], [110.0], [121.0]])
        result = horizon_log_returns(prices, 2)
        assert np.isnan(result[0, 0]) and np.isnan(result[1, 0])
        assert result[2, 0] == pytest.approx(math.log(1.21), abs=1e-12)

    def test_non_positive_price_is_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="strictly positive"):
            log_returns(np.array([[100.0], [0.0]]))


class TestEwmaStd:
    @pytest.mark.parametrize("half_life", [2, 5, 20])
    def test_matches_brute_force_reference(self, half_life: int) -> None:
        rng = np.random.default_rng(7)
        values = rng.normal(0.0, 0.01, size=(60, 3))
        actual = ewma_std(values, half_life=half_life, min_periods=5)
        for column in range(values.shape[1]):
            expected = brute_force_ewma_std(values[:, column], half_life, 5)
            np.testing.assert_allclose(
                actual[:, column], expected, rtol=1e-12, atol=1e-15, equal_nan=True
            )

    def test_is_causal_under_future_mutation(self) -> None:
        """The core leakage property: later rows cannot change earlier output."""
        rng = np.random.default_rng(11)
        values = rng.normal(0.0, 0.01, size=(40, 2))
        baseline = ewma_std(values, half_life=5, min_periods=5)

        mutated = values.copy()
        mutated[25:] = 999.0
        after = ewma_std(mutated, half_life=5, min_periods=5)

        np.testing.assert_array_equal(baseline[:25], after[:25])

    def test_min_periods_withholds_early_values(self) -> None:
        values = np.array([[0.01], [0.02], [0.03], [0.04]])
        result = ewma_std(values, half_life=2, min_periods=3)
        assert np.isnan(result[0, 0]) and np.isnan(result[1, 0])
        assert np.isfinite(result[2, 0])

    def test_leading_gap_is_skipped_not_treated_as_zero(self) -> None:
        with_gap = np.array([[np.nan], [0.01], [-0.02], [0.015]])
        without_gap = np.array([[0.01], [-0.02], [0.015]])
        gapped = ewma_std(with_gap, half_life=2, min_periods=2)
        clean = ewma_std(without_gap, half_life=2, min_periods=2)
        np.testing.assert_allclose(gapped[1:, 0], clean[:, 0], equal_nan=True)

    @pytest.mark.parametrize(("half_life", "min_periods"), [(0, 5), (-1, 5), (5, 1), (5, 0)])
    def test_invalid_parameters_are_rejected(self, half_life: int, min_periods: int) -> None:
        with pytest.raises(DataIntegrityError):
            ewma_std(np.zeros((10, 1)), half_life=half_life, min_periods=min_periods)


class TestAnnualizedVolatility:
    def test_floor_applies(self) -> None:
        config = VolatilityConfig(
            fast_half_life_sessions=2,
            slow_half_life_sessions=3,
            min_periods_sessions=3,
            floor_annualized=5.0,
        )
        values = np.full((10, 1), 0.0001)
        sigma = annualized_volatility(values, config)
        assert np.nanmax(sigma) == pytest.approx(5.0)

    def test_floor_does_not_manufacture_an_unavailable_estimate(self) -> None:
        """A symbol without enough history stays ``nan`` rather than taking the floor."""
        config = VolatilityConfig()  # min_periods_sessions defaults to 60
        values = np.full((10, 1), 0.01)
        assert np.all(np.isnan(annualized_volatility(values, config)))


class TestTrendScores:
    def _flat_sigma(self, shape: tuple[int, int], value: float = 0.2) -> np.ndarray:
        return np.full(shape, value)

    def test_scores_are_bounded_by_tanh(self) -> None:
        rng = np.random.default_rng(3)
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, size=(300, 4)), axis=0))
        config = TrendConfig()
        scores = trend_scores(
            prices, self._flat_sigma(prices.shape), config, annualization_sessions=252
        )
        finite = scores[np.isfinite(scores)]
        assert finite.size > 0
        assert np.all(np.abs(finite) <= 1.0)

    def test_deadband_zeroes_small_scores(self) -> None:
        rng = np.random.default_rng(5)
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, size=(300, 3)), axis=0))
        config = TrendConfig(deadband_abs=0.99)
        scores = trend_scores(
            prices, self._flat_sigma(prices.shape), config, annualization_sessions=252
        )
        finite = scores[np.isfinite(scores)]
        assert np.all(finite == 0.0)

    def test_missing_horizon_withholds_the_score(self) -> None:
        prices = 100 * np.ones((100, 1))
        config = TrendConfig()
        scores = trend_scores(
            prices, self._flat_sigma(prices.shape), config, annualization_sessions=252
        )
        # The 252-session horizon cannot exist in a 100-session panel.
        assert np.all(np.isnan(scores))

    def test_long_scores_clamp_negatives_to_zero(self) -> None:
        scores = np.array([[-0.8, 0.0, 0.4]])
        result = long_scores(scores, TrendConfig())
        np.testing.assert_array_equal(result, np.array([[0.0, 0.0, 0.4]]))

    def test_long_scores_rejects_a_non_long_only_config(self) -> None:
        config = TrendConfig.model_construct(long_only=False)
        with pytest.raises(DataIntegrityError, match="long_only"):
            long_scores(np.zeros((1, 1)), config)
