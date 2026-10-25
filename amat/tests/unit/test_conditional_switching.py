"""Condition gates use observed prefixes and preserve the ungated strategy exactly."""

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from autoresearch.improvements.conditional_switching_study import condition_masks, recipes
from autoresearch.improvements.switching_study import retention


def panel_values(n=150):
    rng = np.random.default_rng(25)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n, 3)), axis=0))
    return SimpleNamespace(
        adjusted_closes=prices, window_vwaps=prices, window_volumes=np.full_like(prices, 100_000.0)
    )


def test_masks_ignore_future_prices_liquidity_and_membership():
    panel = panel_values()
    preferred = np.random.default_rng(5).random((150, 3)) > 0.5
    prefix = SimpleNamespace(**{k: v[:120] for k, v in vars(panel).items()})
    expected = condition_masks(prefix, preferred[:120])
    panel.window_volumes[120:] = 1
    panel.adjusted_closes[120:] *= 10
    preferred[120:] = False
    actual = condition_masks(panel, preferred)
    for key in actual:
        np.testing.assert_array_equal(actual[key][:120], expected[key])
        assert not actual[key][:60].any()


def test_observed_liquidity_shock_and_membership_churn():
    panel = panel_values()
    panel.window_vwaps[:] = 100
    panel.window_volumes[:] = 1_000_000
    panel.window_volumes[100, 0] = 100_000
    preferred = np.zeros((150, 3), dtype=bool)
    preferred[91:101:2, 1] = True
    masks = condition_masks(panel, preferred)
    assert masks["liquidity"][100, 0]
    assert not masks["liquidity"][100, 1]
    assert masks["churn"][100, 1]
    assert not masks["churn"][100, 0]
    np.testing.assert_array_equal(
        masks["combined"], masks["volatility"] | masks["liquidity"] | masks["churn"]
    )
    panel.window_volumes[0, 0] = np.nan
    with pytest.raises(ValueError):
        condition_masks(panel, preferred)


def test_false_gate_native_backtest_exactly_matches_core_and_true_matches_always_on():
    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    costly = base.model_copy(
        update={
            "adaptive_rotation": base.adaptive_rotation.model_copy(update={"switch_cost_bps": 6})
        }
    )
    panel = generate_panel(base.to_universe(), sessions=390)
    kwargs = {"one_way_bps": 6, "max_entry_window_participation": 0.01}
    core = run_backtest(base, panel, base.to_universe(), **kwargs)
    false = run_backtest(
        costly,
        panel,
        base.to_universe(),
        switch_cost_gate=np.zeros_like(panel.adjusted_closes, dtype=bool),
        **kwargs,
    )
    assert json.dumps(
        [asdict(r) for r in core.sessions], default=str, sort_keys=True
    ) == json.dumps([asdict(r) for r in false.sessions], default=str, sort_keys=True)
    always = run_backtest(costly, panel, base.to_universe(), **kwargs)
    true = run_backtest(
        costly,
        panel,
        base.to_universe(),
        switch_cost_gate=np.ones_like(panel.adjusted_closes, dtype=bool),
        **kwargs,
    )
    assert json.dumps(
        [asdict(r) for r in always.sessions], default=str, sort_keys=True
    ) == json.dumps([asdict(r) for r in true.sessions], default=str, sort_keys=True)
    with pytest.raises(DataIntegrityError):
        run_backtest(
            costly,
            panel,
            base.to_universe(),
            switch_cost_gate=np.ones_like(panel.adjusted_closes),
            **kwargs,
        )


def test_retention_uses_conditional_arm_names_and_rejects_any_failed_cost():
    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    arms = recipes(base)

    def record():
        return {
            "metrics": {
                p: {"net_return": 0.2, "max_drawdown": -0.1} for p in ["full", "walk_forward"]
            },
            "monthly": [{"month": "2017-01", "net_return": 0.01}],
        }

    records = {a: {c: record() for c in [2, 6, 10]} for a in arms}
    records["liquidity"][10]["metrics"]["walk_forward"]["net_return"] = 0.19
    result = retention(records, labels={a: a for a in arms})
    assert result["liquidity"]["status"] == "rejected"
    assert result["volatility"]["status"] == "retained"


def test_registered_gate_refuses_a_different_parent_preference_history():
    from autoresearch.improvements.study import digest
    from autoresearch.improvements.switching_study import verify_registered_switch_gates

    panel = panel_values()
    panel.adjusted_closes[:] = 100
    panel.window_vwaps[:] = 100
    panel.window_volumes[:] = 1_000_000
    original_parent = np.zeros((150, 3), dtype=bool)
    original_parent[::2, 0] = True
    other_parent = np.zeros_like(original_parent)
    correct = {"historical_conditional": condition_masks(panel, original_parent)["combined"]}
    wrong = {"historical_conditional": condition_masks(panel, other_parent)["combined"]}
    protocol = {
        "labels": {"historical_conditional": "Original conditional strategy"},
        "registered_switch_gate_sha256": {
            key: digest(value.tolist()) for key, value in correct.items()
        },
    }
    verify_registered_switch_gates(protocol, correct)
    with pytest.raises(ValueError, match="parent preferences"):
        verify_registered_switch_gates(protocol, wrong)
    with pytest.raises(ValueError, match="identities"):
        verify_registered_switch_gates(protocol, {})
