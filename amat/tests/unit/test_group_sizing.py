"""Group conditions cannot leak future prices or alter unaffected group budgets."""

from pathlib import Path

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.portfolio import group_condition_prices, group_sizing_scores
from autoresearch.improvements.group_sizing_study import recipes


def config(mode="weak_trend"):
    base = load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy
    return base.model_copy(
        update={"allocation": base.allocation.model_copy(update={"group_condition": mode})}
    )


def test_group_trigger_changes_only_its_members_and_preserves_group_mass():
    cfg = config()
    symbols = list(map(str, cfg.universe.symbols))
    prices = np.full((300, len(symbols)), 100.0)
    growth = [symbols.index(s) for s in cfg.adaptive_rotation.groups[0].symbols]
    prices[250:, growth] = 80
    scores = np.arange(1.0, len(symbols) + 1)
    sigma = np.linspace(0.1, 0.4, len(symbols))
    actual = group_sizing_scores(cfg, prices, 299, scores, sigma)
    other = [i for i in range(len(symbols)) if i not in growth]
    np.testing.assert_array_equal(actual[other], scores[other])
    assert actual[growth].sum() == pytest.approx(scores[growth].sum())
    expected = scores[growth] / sigma[growth]
    np.testing.assert_allclose(actual[growth], expected * scores[growth].sum() / expected.sum())
    assert not np.array_equal(actual[growth], scores[growth])
    scores[growth[0]] = 0
    assert group_sizing_scores(cfg, prices, 299, scores, sigma)[growth[0]] == 0


@pytest.mark.parametrize("mode", ["weak_trend", "volatility", "either"])
def test_group_sizing_is_prefix_causal_and_price_unit_invariant(mode):
    cfg = config(mode)
    rng = np.random.default_rng(42)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, (400, 11)), axis=0))
    scores = np.arange(11.0)
    sigma = np.linspace(0.1, 0.4, 11)
    expected = group_sizing_scores(cfg, prices, 299, scores, sigma)
    np.testing.assert_array_equal(
        expected, group_sizing_scores(cfg, prices[:300], 299, scores, sigma)
    )
    changed = prices.copy()
    changed[300:] *= 10
    np.testing.assert_array_equal(expected, group_sizing_scores(cfg, changed, 299, scores, sigma))
    np.testing.assert_allclose(
        group_condition_prices(prices, [0, 1]),
        group_condition_prices(prices * np.arange(1, 12), [0, 1]),
    )


def test_recipe_round_trip_and_baseline_identity():
    cfg = config("none")
    assert "group_condition" not in cfg.model_dump(mode="json")["allocation"]
    values = recipes(cfg)
    for arm, value in values.items():
        restored = StrategyConfig.model_validate(value)
        assert restored.allocation.group_condition == ("none" if arm == "core_daily" else arm)
        assert restored.allocation.volatility_power == 0
        a = dict(value)
        b = dict(values["core_daily"])
        a.pop("allocation")
        b.pop("allocation")
        assert a == b
