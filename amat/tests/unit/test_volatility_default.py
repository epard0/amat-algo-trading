"""Native promoted gate preserves research semantics and close-time causality."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.portfolio import precompute_decision_inputs
from amat.portfolio.adaptive import volatility_switch_gate
from autoresearch.improvements.conditional_switching_study import condition_masks
from autoresearch.improvements.volatility_next_study import recipes


def test_native_gate_exact_research_parity_and_prefix_causality():
    rng = np.random.default_rng(15)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, (400, 11)), axis=0))
    panel = SimpleNamespace(
        adjusted_closes=prices, window_vwaps=prices, window_volumes=np.ones_like(prices) * 1e6
    )
    expected = condition_masks(panel, np.ones_like(prices, dtype=bool))["volatility"]
    np.testing.assert_array_equal(volatility_switch_gate(prices), expected)
    np.testing.assert_array_equal(volatility_switch_gate(prices[:300]), expected[:300])
    changed = prices.copy()
    changed[300:] *= 2
    np.testing.assert_array_equal(volatility_switch_gate(changed)[:300], expected[:300])


def test_promoted_profile_matches_research_gate_selection():
    config = load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy
    panel = generate_panel(config.to_universe(), sessions=400)
    native = precompute_decision_inputs(config, panel.adjusted_closes, sessions=panel.sessions)
    original = config.model_copy(
        update={
            "adaptive_rotation": config.adaptive_rotation.model_copy(
                update={"switch_condition": "always"}
            )
        }
    )
    # Recompute through the same native cache with explicit historical gate.
    from amat.backtest.runner import _precompute_run_inputs

    expected, _ = _precompute_run_inputs(
        original,
        None,
        panel,
        signal_override=None,
        switch_cost_gate=volatility_switch_gate(panel.adjusted_closes),
    )
    np.testing.assert_array_equal(native.adaptive_scores, expected.adaptive_scores)
    assert config.adaptive_rotation.max_active_groups == 3
    assert config.adaptive_rotation.switch_condition == "volatility"


def test_exactly_five_independent_ablations_and_historical_serialization():
    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    assert "switch_condition" not in base.model_dump(mode="json")["adaptive_rotation"]
    configs = recipes(base)
    assert len(configs) == 6
    parent = configs.pop("core_daily")
    for candidate in configs.values():
        assert candidate != parent
        assert candidate["adaptive_rotation"]["switch_condition"] == "volatility"


def test_cost_stress_binds_native_conditional_hurdle(monkeypatch):
    from amat.backtest import runner

    config = load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy
    panel = generate_panel(config.to_universe(), sessions=300)
    observed = []
    original = runner._precompute_run_inputs

    def capture(strategy, *args, **kwargs):
        observed.append(strategy.adaptive_rotation.switch_cost_bps)
        return original(strategy, *args, **kwargs)

    monkeypatch.setattr(runner, "_precompute_run_inputs", capture)
    runner.run_backtest(config, panel, config.to_universe(), one_way_bps=10)
    assert observed == [10]
    assert config.adaptive_rotation.switch_cost_bps == 2  # caller identity is immutable
