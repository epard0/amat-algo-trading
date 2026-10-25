"""Asset conditions are independent, causal, unit invariant and risk bounded."""

from pathlib import Path

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.portfolio import asset_sizing_scores
from autoresearch.improvements.asset_sizing_study import CONDITIONS, recipes


def config(mode="weak_trend", reduce=True):
    base = load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy
    return base.model_copy(
        update={
            "allocation": base.allocation.model_copy(
                update={"asset_condition": mode, "asset_sizing_reduce_only": reduce}
            )
        }
    )


@pytest.mark.parametrize("reduce", [True, False])
def test_only_triggered_selected_asset_score_changes(reduce):
    cfg = config(reduce=reduce)
    prices = np.ones((300, 11)) * 100
    prices[250:, 0] = 80
    scores = np.ones(11)
    scores[2] = 0
    prices[250:, 2] = 50
    sigma = np.ones(11) * 0.4
    actual = asset_sizing_scores(cfg, prices, 299, scores, sigma)
    assert actual[0] == pytest.approx(0.5)
    assert actual[2] == 0
    np.testing.assert_array_equal(actual[1:], scores[1:])
    np.testing.assert_array_equal(scores, np.array([1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1]))
    sigma[0] = 0.1
    assert asset_sizing_scores(cfg, prices, 299, scores, sigma)[0] == (1 if reduce else 2)


@pytest.mark.parametrize("mode", ["weak_trend", "volatility", "either"])
def test_asset_sizing_prefix_causality_and_price_scale_invariance(mode):
    cfg = config(mode)
    rng = np.random.default_rng(221)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, (400, 11)), axis=0))
    scores = np.arange(11.0)
    sigma = np.linspace(0.1, 0.5, 11)
    expected = asset_sizing_scores(cfg, prices, 299, scores, sigma)
    np.testing.assert_array_equal(
        expected, asset_sizing_scores(cfg, prices[:300], 299, scores, sigma)
    )
    changed = prices.copy()
    changed[300:] *= 10
    np.testing.assert_array_equal(expected, asset_sizing_scores(cfg, changed, 299, scores, sigma))
    np.testing.assert_array_equal(
        expected, asset_sizing_scores(cfg, prices * np.arange(1, 12), 299, scores, sigma)
    )
    assert np.all(expected <= scores)


def test_recipe_round_trip_isolation_and_baseline_identity():
    cfg = config("none")
    assert "asset_condition" not in cfg.model_dump(mode="json")["allocation"]
    assert "asset_sizing_reduce_only" not in cfg.model_dump(mode="json")["allocation"]
    values = recipes(cfg)
    assert len(values) == 7
    for arm, value in values.items():
        restored = StrategyConfig.model_validate(value)
        if arm != "core_daily":
            assert restored.allocation.asset_condition == CONDITIONS[arm.split("_")[0]]
            assert restored.allocation.asset_sizing_reduce_only == arm.endswith("_reduce")
        actual = dict(value)
        expected = dict(values["core_daily"])
        actual.pop("allocation")
        expected.pop("allocation")
        assert actual == expected
