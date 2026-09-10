"""Causal pair-structure measurements for statistical-arbitrage research."""

from __future__ import annotations

import numpy as np
import pytest

from amat.errors import DataIntegrityError
from amat.stat_arb import PairSelectionConfig, analyze_pair, scan_pairs


def _structured_pair(seed: int = 7, observations: int = 500) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    right_log = 4.0 + np.cumsum(rng.normal(0.0002, 0.008, observations))
    residual = np.zeros(observations)
    for index in range(1, observations):
        residual[index] = 0.72 * residual[index - 1] + rng.normal(0.0, 0.008)
    left_log = 0.2 + 1.15 * right_log + residual
    return np.exp(left_log), np.exp(right_log)


def test_cointegrated_structure_reports_hedge_ratio_stationarity_and_half_life() -> None:
    left, right = _structured_pair()
    result = analyze_pair(
        left,
        right,
        left_symbol="AAA",
        right_symbol="BBB",
        config=PairSelectionConfig(minimum_half_life_sessions=0.5),
    )

    assert result.hedge_ratio == pytest.approx(1.15, rel=0.04)
    assert result.adf_t_statistic < -2.86
    assert result.half_life_sessions is not None
    assert 0.5 < result.half_life_sessions < 10
    assert result.zero_crossings >= 4
    assert result.eligible


def test_unrelated_random_walks_fail_the_structure_gates() -> None:
    rng = np.random.default_rng(99)
    left = np.exp(4.0 + np.cumsum(rng.normal(0.0, 0.01, 500)))
    right = np.exp(4.0 + np.cumsum(rng.normal(0.0, 0.01, 500)))

    result = analyze_pair(left, right, left_symbol="AAA", right_symbol="BBB")

    assert not result.eligible
    assert result.rejection_reasons


def test_scan_uses_only_the_requested_trailing_window_and_ranks_eligible_first() -> None:
    left, right = _structured_pair(observations=300)
    unrelated = np.exp(3.0 + np.cumsum(np.random.default_rng(3).normal(0, 0.02, 300)))
    panel = np.column_stack([left, right, unrelated])

    results = scan_pairs(
        panel,
        ("AAA", "BBB", "CCC"),
        lookback_sessions=252,
        config=PairSelectionConfig(minimum_half_life_sessions=0.5),
    )

    assert len(results) == 3
    assert results[0].eligible
    assert {results[0].left_symbol, results[0].right_symbol} == {"AAA", "BBB"}
    assert all(result.observations == 252 for result in results)


def test_pair_analysis_rejects_nonpositive_or_short_history() -> None:
    with pytest.raises(DataIntegrityError, match="at least"):
        analyze_pair(
            np.ones(30),
            np.ones(30),
            left_symbol="AAA",
            right_symbol="BBB",
        )

    left, right = _structured_pair(observations=100)
    left[-1] = 0.0
    with pytest.raises(DataIntegrityError, match="strictly positive"):
        analyze_pair(left, right, left_symbol="AAA", right_symbol="BBB")
