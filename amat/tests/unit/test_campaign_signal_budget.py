"""The registered budget uses past-only IR payoffs and keeps its cold-start control honest."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from amat.config.models import StrategyConfig
from amat.data.snapshot import load_panel
from amat.portfolio import decide, precompute_decision_inputs
from amat.portfolio.risk_budget import budget_multipliers
from autoresearch.improvements.campaign_audit import sealed

C = Path(__file__).resolve().parents[2] / "outputs/research-campaigns/2026-10-19-12h-performance"


@pytest.fixture(scope="module")
def budget() -> tuple[dict[str, Any], StrategyConfig, Any]:
    design = sealed(C / "signal-budget-design-022.json")
    strategy = StrategyConfig.model_validate(design["configs"]["signal_budget"])
    snapshot = Path(sealed(C / "protocol.json")["snapshot"])
    panel, _ = load_panel(snapshot, list(map(str, strategy.universe.symbols)), with_context=True)
    return design, strategy, panel


def test_registered_budget_ir_prefix_and_future_mutation_are_identical(
    budget: tuple[dict[str, Any], StrategyConfig, Any],
) -> None:
    _, strategy, panel = budget
    prices = panel.adjusted_closes[:700]
    dates = panel.sessions[:700]
    full = precompute_decision_inputs(strategy, prices, sessions=dates)
    prefix = precompute_decision_inputs(strategy, prices[:500], sessions=dates[:500])
    poisoned = prices.copy()
    poisoned[500:] *= np.linspace(0.4, 2.1, poisoned.shape[1])
    changed = precompute_decision_inputs(strategy, poisoned, sessions=dates)
    assert strategy.risk_budget.measure == "information_ratio"
    assert full.signal_quality is not None and full.adaptive_scores is not None
    for other in [prefix, changed]:
        assert other.signal_quality is not None and other.adaptive_scores is not None
        np.testing.assert_allclose(
            full.signal_quality[:500], other.signal_quality[:500], rtol=0, atol=0, equal_nan=True
        )
        np.testing.assert_allclose(
            full.adaptive_scores[:500], other.adaptive_scores[:500], rtol=0, atol=0, equal_nan=True
        )
    expected = decide(
        strategy, prices, strategy.to_universe(), decision_index=499, precomputed=full
    )
    actual = decide(
        strategy, prices[:500], strategy.to_universe(), decision_index=499, precomputed=prefix
    )
    np.testing.assert_allclose(expected.weights, actual.weights, rtol=0, atol=1e-12)
    multipliers = budget_multipliers(full.signal_quality[499], strategy.risk_budget)
    assert multipliers.min() >= strategy.risk_budget.min_multiplier
    assert multipliers.max() <= strategy.risk_budget.max_multiplier
    assert abs(multipliers.mean() - 1) < 0.05
    assert expected.gross_exposure <= 1
    assert max(expected.weights) <= 0.4 + 1e-12


def test_matched_control_changes_only_budget_enable_flag_and_retains_warmup(
    budget: tuple[dict[str, Any], StrategyConfig, Any],
) -> None:
    design, strategy, _ = budget
    parent = sealed(Path(design["parent_study"]) / "protocol.json")["configs"][design["parent_arm"]]
    diagnostic = design["configs"]["matched_warmup_control"]
    candidate = design["configs"]["signal_budget"]
    assert candidate["warmup"]["min_completed_sessions"] == 378
    assert diagnostic["warmup"] == candidate["warmup"]
    assert diagnostic["risk_budget"]["enabled"] is False
    changed = strategy.model_dump(mode="json")
    changed["risk_budget"]["enabled"] = False
    assert changed == diagnostic
    changed["warmup"] = parent["warmup"]
    assert changed == parent
    assert design["roles"]["matched_warmup_control"] == "diagnostic"
    assert design["roles"]["signal_budget"] == "candidate"


def test_saved_budget_and_matched_control_cannot_use_campaign_start(
    budget: tuple[dict[str, Any], StrategyConfig, Any],
) -> None:
    from datetime import date

    from amat.backtest.runner import _initial_decision
    from amat.errors import DataIntegrityError

    design, _, panel = budget
    for config in design["configs"].values():
        with pytest.raises(DataIntegrityError, match="insufficient signal warm-up"):
            _initial_decision(
                panel, config["warmup"]["min_completed_sessions"], 1, date(2017, 1, 3)
            )
    assert _initial_decision(panel, 252, 1, date(2017, 1, 3)) == 251
