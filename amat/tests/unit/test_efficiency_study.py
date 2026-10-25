"""Causal group-local filters, isolated trading changes and conservative retention."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from autoresearch.improvements.efficiency_study import (
    LABELS,
    group_risk_multipliers,
    recipes,
    retention,
)


@pytest.mark.parametrize("kind", ["trend", "shock", "stress"])
def test_group_filter_prefix_invariance_and_no_cross_group_contamination(kind):
    rng = np.random.default_rng(4)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, (300, 3)), axis=0))
    groups = [SimpleNamespace(symbols=["A", "B"]), SimpleNamespace(symbols=["C"])]
    expected = group_risk_multipliers(prices[:220], ["A", "B", "C"], groups, kind)
    altered = prices.copy()
    altered[220:] *= 0.01
    np.testing.assert_equal(
        group_risk_multipliers(altered, ["A", "B", "C"], groups, kind)[:220], expected
    )
    altered = prices.copy()
    altered[:, 2] *= np.exp(np.linspace(0, -8, 300))
    np.testing.assert_equal(
        group_risk_multipliers(altered, ["A", "B", "C"], groups, kind)[:, :2],
        group_risk_multipliers(prices, ["A", "B", "C"], groups, kind)[:, :2],
    )
    assert np.isin(expected, [0.5, 1]).all()
    assert np.all(expected[:63] == 1)


def test_weak_group_does_not_reduce_healthy_group():
    prices = np.ones((120, 3)) * 100
    prices[80:, :2] *= np.exp(-np.arange(40)[:, None] * 0.01)
    groups = [SimpleNamespace(symbols=["A", "B"]), SimpleNamespace(symbols=["C"])]
    result = group_risk_multipliers(prices, ["A", "B", "C"], groups, "trend")
    np.testing.assert_equal(result[-1], [0.5, 0.5, 1])
    with pytest.raises(ValueError):
        group_risk_multipliers(prices, ["A", "B", "C"], groups, "unknown")
    prices[0, 0] = np.nan
    with pytest.raises(ValueError):
        group_risk_multipliers(prices, ["A", "B", "C"], groups, "trend")


def test_trade_band_recipes_only_change_the_rebalance_threshold():
    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    values = recipes(base)
    for name in ["band3", "band4", "band5", "band3_trend", "band4_trend"]:
        cfg = deepcopy(values[name])
        cfg["rebalance"]["min_weight_delta"] = 0.02
        assert cfg == values["core_daily"]
    for name in ["group_trend", "group_shock", "group_stress"]:
        assert values[name] == values["core_daily"]


def test_retention_requires_all_costs_and_windows_and_drawdown_guard():
    def record():
        return {
            "metrics": {
                p: {"net_return": 0.2, "max_drawdown": -0.1} for p in ["full", "walk_forward"]
            },
            "monthly": [{"month": f"2017-{m:02d}", "net_return": 0.01} for m in range(1, 13)],
        }

    records = {arm: {cost: record() for cost in [2, 6, 10]} for arm in LABELS}
    records["band3"][10]["metrics"]["walk_forward"]["net_return"] = 0.19
    records["band4"][6]["metrics"]["full"]["max_drawdown"] = -0.11
    result = retention(records)
    assert result["core_daily"]["status"] == "incumbent"
    assert result["band3"]["status"] == "rejected"
    assert result["band4"]["status"] == "rejected"
    assert result["band5"]["status"] == "retained"
    assert result["band5"]["annualized_log_return_delta_ci95"] == [0.0, 0.0]


def test_default_cli_preserves_prior_context_and_uses_current_volatility_default():
    import datetime as dt

    from amat.cli.main import _BACKTEST_VERSION_CONFIGS, _measurement_execution_start, build_parser

    args = build_parser().parse_args(["backtest"])
    assert args.strategy_version == "daily-volatility"
    assert _BACKTEST_VERSION_CONFIGS[args.strategy_version].name == (
        "amat_group_selection_volatility.toml"
    )
    reference = build_parser().parse_args(["backtest", "--strategy-version", "daily-core"])
    assert _BACKTEST_VERSION_CONFIGS[reference.strategy_version].name == (
        "amat_group_selection_daily.toml"
    )
    base = load_strategy_config(_BACKTEST_VERSION_CONFIGS[args.strategy_version]).strategy
    sessions = tuple(dt.date(2016, 1, 1) + dt.timedelta(days=i) for i in range(500))
    assert (
        _measurement_execution_start(base, SimpleNamespace(sessions=sessions), sessions[400])
        == sessions[400]
    )
    assert (
        _measurement_execution_start(base, SimpleNamespace(sessions=sessions), sessions[0])
        == sessions[252]
    )
