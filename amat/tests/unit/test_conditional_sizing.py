"""Conditional sizing is causal, isolated from selection, and preserves legacy identity."""

from pathlib import Path

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.synthetic import generate_panel
from amat.portfolio import decide, precompute_decision_inputs, unfavorable_sizing_condition
from autoresearch.improvements.conditional_sizing_study import recipes


@pytest.mark.parametrize("mode", ["weak_trend", "volatility", "either"])
def test_condition_is_prefix_causal(mode):
    rng = np.random.default_rng(41)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.018, (400, 2)), axis=0))
    original = [unfavorable_sizing_condition(prices, t, 0, mode) for t in range(300)]
    changed = prices.copy()
    changed[300:] *= 100
    assert original == [unfavorable_sizing_condition(changed, t, 0, mode) for t in range(300)]
    assert original == [unfavorable_sizing_condition(prices[:300], t, 0, mode) for t in range(300)]


def test_fixed_thresholds_and_warmup():
    prices = np.ones((300, 1)) * 100
    assert not unfavorable_sizing_condition(prices, 250, 0, "either")
    prices[250, 0] = 90
    assert unfavorable_sizing_condition(prices, 250, 0, "weak_trend")
    prices = np.exp(np.cumsum(np.tile([0.05, -0.05], 150)))[:, None] * 100
    assert unfavorable_sizing_condition(prices, 250, 0, "volatility")
    assert not unfavorable_sizing_condition(prices, 59, 0, "volatility")


@pytest.mark.parametrize("mode", ["weak_trend", "volatility", "either"])
def test_shared_decision_equals_selected_fixed_allocator(mode):
    cfg = load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy
    conditional = cfg.model_copy(
        update={
            "allocation": cfg.allocation.model_copy(
                update={"volatility_power": 1.0, "unfavorable_condition": mode}
            )
        }
    )
    panel = generate_panel(cfg.to_universe(), sessions=400)
    cache = precompute_decision_inputs(cfg, panel.adjusted_closes, sessions=panel.sessions)
    benchmark = list(map(str, cfg.universe.symbols)).index("QQQ")
    for t in [260, 300, 350, 399]:
        active = unfavorable_sizing_condition(panel.adjusted_closes, t, benchmark, mode)
        expected = cfg.model_copy(
            update={
                "allocation": cfg.allocation.model_copy(update={"volatility_power": float(active)})
            }
        )
        actual = decide(
            conditional,
            panel.adjusted_closes,
            cfg.to_universe(),
            decision_index=t,
            precomputed=cache,
        )
        fixed = decide(
            expected, panel.adjusted_closes, cfg.to_universe(), decision_index=t, precomputed=cache
        )
        np.testing.assert_array_equal(actual.weights, fixed.weights)
        assert actual.gross_exposure <= 1


def test_recipe_changes_sizing_only_and_preserves_historical_identity():
    cfg = load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy
    assert "unfavorable_condition" not in cfg.allocation.model_dump(mode="json")
    values = recipes(cfg)
    base = values["core_daily"]
    for arm, value in values.items():
        assert {k: v for k, v in value.items() if k != "allocation"} == {
            k: v for k, v in base.items() if k != "allocation"
        }
        if arm != "core_daily":
            assert StrategyConfig.model_validate(value).allocation.unfavorable_condition == arm
            assert value["allocation"] == {"volatility_power": 1.0, "unfavorable_condition": arm}
