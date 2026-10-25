"""Factor isolation, parent-specific gating and strict interaction retention."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from amat.config.loading import load_strategy_config
from autoresearch.improvements.all_groups_switching_study import (
    CANDIDATES,
    LABELS,
    REFERENCES,
    mask_panels,
    recipes,
    retain,
)
from autoresearch.improvements.study import digest


def test_recipes_isolate_group_admission_and_switch_hurdle():
    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    values = recipes(base)
    for value in values.values():
        cfg = deepcopy(value)
        r = cfg["adaptive_rotation"]
        r.pop("switch_cost_bps", None)
        r.pop("switch_cost_multiple", None)
        r.pop("max_active_groups", None)
        expected = base.model_dump(mode="json")
        expected["adaptive_rotation"].pop("max_active_groups", None)
        assert cfg == expected
    assert values["conditional_reference"]["adaptive_rotation"]["max_active_groups"] == 2
    assert values["all_combined_double"]["adaptive_rotation"]["max_active_groups"] == 3
    assert values["all_combined_double"]["adaptive_rotation"]["switch_cost_multiple"] == 2


def test_churn_mask_uses_each_ungated_parent(monkeypatch):
    from autoresearch.improvements import all_groups_switching_study as module

    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    prices = np.full((100, 11), 100.0)
    panel = SimpleNamespace(
        adjusted_closes=prices,
        window_vwaps=prices,
        window_volumes=np.full_like(prices, 1_000_000.0),
        sessions=range(100),
    )
    core = np.zeros_like(prices, dtype=bool)
    core[::2, 0] = True
    monkeypatch.setattr(
        module,
        "precompute_decision_inputs",
        lambda *_a, **_k: SimpleNamespace(adaptive_scores=np.zeros_like(prices)),
    )
    masks = mask_panels(panel, core, base)
    assert masks["conditional_reference"][80, 0]
    assert not masks["all_combined"][80, 0]
    np.testing.assert_array_equal(masks["all_combined"], masks["all_combined_double"])
    assert "all_always" not in masks


def test_retention_checks_both_parents_costs_drawdown_and_2023():
    def record(value):
        return {
            "sessions": [],
            "metrics": {
                p: {"net_return": value, "max_drawdown": -0.1}
                for p in ["full", "walk_forward", "2023"]
            },
            "monthly": [{"month": f"2017-{m:02d}", "net_return": 0.01} for m in range(1, 13)],
        }

    records = {a: {c: record(0.2 if a in REFERENCES else 0.21) for c in [2, 6, 10]} for a in LABELS}
    protocol = {
        "parent_records": {
            f"{a}-{c}": {"sessions_sha256": digest([])} for a in REFERENCES for c in [2, 6, 10]
        }
    }
    records["all_volatility"][10]["metrics"]["walk_forward"]["net_return"] = 0.19
    records["all_combined"][6]["metrics"]["full"]["max_drawdown"] = -0.11
    records["all_always"][2]["metrics"]["2023"]["net_return"] = 0.19
    decision = retain(records, protocol)
    for arm in ["all_volatility", "all_combined", "all_always"]:
        assert decision[arm]["status"] == "rejected"
    assert decision["all_combined_double"]["status"] == "retained"
    assert decision["all_always_double"]["status"] == "retained"
    assert set(decision) == set(REFERENCES) | set(CANDIDATES)


def test_interaction_masks_are_prefix_causal_for_both_parent_preferences():
    from dataclasses import fields, replace

    from amat.data.synthetic import generate_panel
    from amat.portfolio import precompute_decision_inputs

    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    all_groups = base.model_copy(
        update={
            "adaptive_rotation": base.adaptive_rotation.model_copy(update={"max_active_groups": 3})
        }
    )
    panel = generate_panel(base.to_universe(), sessions=390)
    panel = replace(
        panel,
        window_vwaps=panel.raw_closes.copy(),
        window_volumes=np.full_like(panel.raw_closes, 1_000_000),
    )

    def compute(panel):
        preferred = (
            precompute_decision_inputs(
                base, panel.adjusted_closes, sessions=panel.sessions
            ).adaptive_scores
            > 0
        )
        return mask_panels(panel, preferred, all_groups)

    whole = compute(panel)
    arrays = {
        f.name: getattr(panel, f.name)[:340]
        for f in fields(panel)
        if isinstance(getattr(panel, f.name), np.ndarray)
    }
    prefix = replace(panel, sessions=panel.sessions[:340], **arrays)
    short = compute(prefix)
    changed = panel.adjusted_closes.copy()
    changed[340:] *= 4
    future = compute(replace(panel, adjusted_closes=changed))
    for arm in whole:
        np.testing.assert_array_equal(whole[arm][:340], short[arm])
        np.testing.assert_array_equal(whole[arm][:340], future[arm][:340])
