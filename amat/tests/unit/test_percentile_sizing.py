"""Prior-only percentile timing, native cache parity and legacy serialization."""

from pathlib import Path

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.synthetic import generate_panel
from amat.portfolio import (
    asset_percentile_gate,
    asset_sizing_scores,
    decide,
    precompute_decision_inputs,
)
from autoresearch.improvements.percentile_sizing_study import recipes


def base():
    return load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy


def test_quantile_matches_explicit_prior_only_reference_and_startup():
    rng = np.random.default_rng(28)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, (390, 3)), axis=0))
    gate = asset_percentile_gate(prices, 0.9)
    assert not gate[:272].any()
    returns = np.diff(np.log(prices), axis=0)
    for t in [272, 280, 350, 389]:
        prior = np.stack(
            [np.std(returns[j - 20 : j], axis=0, ddof=1) * np.sqrt(252) for j in range(t - 252, t)]
        )
        current = np.std(returns[t - 20 : t], axis=0, ddof=1) * np.sqrt(252)
        np.testing.assert_array_equal(
            gate[t], current > np.quantile(prior, 0.9, axis=0, method="linear")
        )
    assert not asset_percentile_gate(np.ones((400, 2)) * 100, 0.9).any()


@pytest.mark.parametrize("q", [0.8, 0.9, 0.95])
def test_percentile_is_prefix_causal_and_asset_independent(q):
    rng = np.random.default_rng(81)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, (400, 3)), axis=0))
    gate = asset_percentile_gate(prices, q)
    np.testing.assert_array_equal(gate[:330], asset_percentile_gate(prices[:330], q))
    changed = prices.copy()
    changed[330:] *= 4
    np.testing.assert_array_equal(gate[:330], asset_percentile_gate(changed, q)[:330])
    changed = prices.copy()
    changed[:, 1] = 100
    np.testing.assert_array_equal(gate[:, 0], asset_percentile_gate(changed, q)[:, 0])
    np.testing.assert_array_equal(gate, asset_percentile_gate(prices * np.arange(1, 4), q))


def test_cached_gate_and_direct_prefix_give_identical_native_allocations():
    cfg = base()
    cfg = cfg.model_copy(
        update={
            "allocation": cfg.allocation.model_copy(
                update={"asset_condition": "percentile", "asset_volatility_percentile": 0.8}
            )
        }
    )
    panel = generate_panel(cfg.to_universe(), sessions=390)
    cache = precompute_decision_inputs(cfg, panel.adjusted_closes, sessions=panel.sessions)
    assert cache.asset_percentile_gate is not None
    for index in [270, 300, 389]:
        actual = decide(
            cfg, panel.adjusted_closes, cfg.to_universe(), decision_index=index, precomputed=cache
        )
        prefix_cache = precompute_decision_inputs(
            cfg, panel.adjusted_closes[: index + 1], sessions=panel.sessions[: index + 1]
        )
        direct = decide(
            cfg,
            panel.adjusted_closes[: index + 1],
            cfg.to_universe(),
            decision_index=index,
            precomputed=prefix_cache,
        )
        np.testing.assert_array_equal(actual.weights, direct.weights)
        scores = np.arange(11.0)
        sigma = np.linspace(0.1, 0.5, 11)
        a = asset_sizing_scores(
            cfg,
            panel.adjusted_closes,
            index,
            scores,
            sigma,
            percentile_gate=cache.asset_percentile_gate,
        )
        b = asset_sizing_scores(cfg, panel.adjusted_closes, index, scores, sigma)
        np.testing.assert_array_equal(a, b)
        assert np.all(a <= scores)


def test_six_fixed_recipes_round_trip_and_baseline_fields_omitted():
    cfg = base()
    assert "asset_volatility_percentile" not in cfg.allocation.model_dump(mode="json")
    values = recipes(cfg)
    assert len(values) == 7
    for arm, value in values.items():
        restored = StrategyConfig.model_validate(value)
        if arm != "core_daily":
            assert restored.allocation.asset_condition == "percentile"
            assert (
                restored.allocation.asset_volatility_percentile == int(arm.split("_")[0][1:]) / 100
            )
            assert restored.allocation.asset_sizing_reduce_only == arm.endswith("_reduce")
