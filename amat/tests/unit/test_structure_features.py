"""Causality, formulas, and bounds for the soft structure allocator."""

from __future__ import annotations

import numpy as np
import pytest

from amat.config.models import StructureFilterConfig
from amat.errors import DataIntegrityError
from amat.features import log_returns
from amat.features.structure import (
    donchian_position,
    downside_variance_share,
    relative_strength_index,
    structure_multiplier,
    variance_ratio,
)


def test_wilder_rsi_handles_rise_fall_and_flat_prices() -> None:
    prices = np.column_stack(
        [
            np.arange(1.0, 31.0),
            np.arange(30.0, 0.0, -1.0),
            np.full(30, 10.0),
        ]
    )
    result = relative_strength_index(prices, period=14)

    np.testing.assert_allclose(result[-1], np.array([100.0, 0.0, 50.0]))
    assert np.all(np.isnan(result[:14]))


def test_variance_ratio_distinguishes_persistence_from_reversal() -> None:
    rng = np.random.default_rng(82)
    innovations = rng.normal(0.0, 0.01, size=(500, 2))
    returns = np.zeros_like(innovations)
    for row in range(1, len(returns)):
        returns[row, 0] = 0.75 * returns[row - 1, 0] + innovations[row, 0]
        returns[row, 1] = -0.75 * returns[row - 1, 1] + innovations[row, 1]

    result = variance_ratio(returns, lookback=126, horizon=5)

    assert result[-1, 0] > 1.0
    assert result[-1, 1] < 1.0


def test_donchian_position_and_downside_share_match_simple_examples() -> None:
    prices = np.array([[1.0], [2.0], [3.0], [4.0], [5.0]])
    position = donchian_position(prices, lookback=5)
    assert position[-1, 0] == pytest.approx(1.0)

    returns = np.array([[np.nan], [0.01], [-0.02], [0.03], [-0.04], [0.05]])
    share = downside_variance_share(returns, lookback=5)
    expected = (0.02**2 + 0.04**2) / (0.01**2 + 0.02**2 + 0.03**2 + 0.04**2 + 0.05**2)
    assert share[-1, 0] == pytest.approx(expected)


def test_structure_multiplier_is_bounded_and_causal() -> None:
    rng = np.random.default_rng(19)
    returns = rng.normal(0.0004, 0.01, size=(320, 3))
    prices = 100.0 * np.exp(np.cumsum(returns, axis=0))
    config = StructureFilterConfig(enabled=True, min_multiplier=0.35)

    baseline = structure_multiplier(prices, log_returns(prices), config)
    mutated = prices.copy()
    mutated[270:] *= 4.0
    after = structure_multiplier(mutated, log_returns(mutated), config)

    finite = baseline[np.isfinite(baseline)]
    assert np.all(finite >= 0.35)
    assert np.all(finite <= 1.0)
    np.testing.assert_array_equal(baseline[:270], after[:270])


def test_disabled_structure_filter_is_exactly_inert() -> None:
    prices = np.arange(1.0, 101.0).reshape(-1, 1)
    result = structure_multiplier(
        prices,
        log_returns(prices),
        StructureFilterConfig(),
    )
    np.testing.assert_array_equal(result, np.ones_like(prices))


def test_structure_configuration_rejects_inverted_thresholds() -> None:
    with pytest.raises(ValueError, match="floor < target < upper_target < ceiling"):
        StructureFilterConfig(rsi_floor=70.0, rsi_target=60.0)
    with pytest.raises(ValueError, match="shorter than"):
        StructureFilterConfig(
            variance_lookback_sessions=5,
            variance_ratio_horizon=5,
        )


def test_structure_features_reject_invalid_shapes() -> None:
    with pytest.raises(DataIntegrityError, match="2-D"):
        relative_strength_index(np.arange(10.0), period=5)
