"""Frozen inner selection and authoritative outer accounting for nested GP."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.data.synthetic import generate_panel
from autoresearch.contracts import Program
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.nested_replay import (
    FoldSimulation,
    _diagnostic_patterns,
    evaluate_fold,
    panel_prefix,
)
from autoresearch.genetic.replay import attach_pattern, load_incumbent

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def inputs():
    base = load_incumbent(ROOT / "configs/strategy/amat_v13_return_target.toml")
    panel = generate_panel(base.config.strategy.to_universe(), sessions=540)
    panel = replace(
        panel,
        source="scenario(nested-test)",
        adjusted_opens=panel.adjusted_closes * 0.999,
        adjusted_highs=panel.adjusted_closes * 1.01,
        adjusted_lows=panel.adjusted_closes * 0.99,
    )
    payload = json.loads((ROOT / "docs/genetic-patterns/symbolic.example.json").read_text())
    payload["genome"]["root"] = {
        "node": "feature",
        "name": "momentum",
        "lookback": 5,
        "lag": 0,
        "transform": "time_series_rank",
        "source": "self",
    }
    return base, panel, parse_pattern(payload)


def test_selection_freezes_before_outer_and_fallback_pairs_exactly(inputs, tmp_path, monkeypatch):
    base, panel, pattern = inputs
    from autoresearch.genetic import nested_replay

    original = nested_replay.FoldSimulation

    class Checked(original):
        def __init__(self, panel, *args, **kwargs):
            if len(panel) == 540:
                assert (tmp_path / "fold/selection.json").exists()
            super().__init__(panel, *args, **kwargs)

    monkeypatch.setattr(nested_replay, "FoldSimulation", Checked)
    result = evaluate_fold(
        base,
        panel,
        None,
        fit_end=panel.sessions[503],
        selection_end=panel.sessions[524],
        output=tmp_path / "fold",
        contract_hash="c" * 64,
        contenders={"genetic": [pattern], "random": [pattern]},
        diagnostics=False,
    )
    for method in ("genetic", "random"):
        assert result["methods"][method]["retained_incumbent"]
        paired = result["paired_returns"][method]
        assert paired["candidate"] == paired["incumbent"]
        assert len(paired["dates"]) == 15
        assert set(result["outer"][method]["warnings"]) == {"outer", "cost_6bps", "delay_1"}
        assert result["diagnostics"][method]["remove_pattern_exact_parity"]
    # Completed results can be reproduced from the same exact frozen selection.
    again = evaluate_fold(
        base,
        panel,
        None,
        fit_end=panel.sessions[503],
        selection_end=panel.sessions[524],
        output=tmp_path / "fold",
        contract_hash="c" * 64,
        contenders={"genetic": [pattern], "random": [pattern]},
        diagnostics=False,
    )
    assert again == result


@pytest.mark.parametrize("swap", [(510, 530), (500, 510), (100, 101)])
def test_null_mapping_does_not_cross_inner_outer_boundary(inputs, tmp_path, swap):
    base, panel, pattern = inputs
    mapping = np.arange(len(panel))
    if swap == (100, 101):
        mapping[100] = mapping[101]  # Duplicate indices are not a null permutation.
    else:
        mapping[swap[0]], mapping[swap[1]] = mapping[swap[1]], mapping[swap[0]]
    with pytest.raises(ValueError, match="boundary"):
        evaluate_fold(
            base,
            panel,
            None,
            fit_end=panel.sessions[503],
            selection_end=panel.sessions[524],
            output=tmp_path / "bad",
            contract_hash="c" * 64,
            contenders={"genetic": [pattern], "random": [pattern]},
            feature_mapping=mapping,
        )
    assert not (tmp_path / "bad").exists()


def test_joint_mapping_changes_features_only_and_mask_is_neutral(inputs):
    base, panel, pattern = inputs
    program = Program(
        schema_version="strategy-development/9",
        name="mapped-test",
        load_start=panel.sessions[0],
        measured_start=panel.sessions[503],
        train_end=panel.sessions[524],
        validation_end=panel.sessions[-1],
        baseline=base,
        frozen_files={},
        dependencies={},
    )
    mapping = np.arange(len(panel))
    mapping[100:200] = mapping[100:200][::-1]
    common = np.ones(len(panel), dtype=bool)
    common[300:310] = False
    sim = FoldSimulation(panel, program, None, mapping, common)
    candidate = attach_pattern(base, pattern)
    mapped = sim.trace(candidate)
    from autoresearch.genetic.symbolic import evaluate_symbolic

    np.testing.assert_allclose(
        mapped.genetic_scores[100:200], evaluate_symbolic(pattern, panel)[mapping][100:200]
    )
    np.testing.assert_array_equal(mapped.scores[300:310], sim.trace(base).scores[300:310])
    assert sim.panel is panel


def test_diagnostic_leaf_ablation_and_strength_remain_within_schema(inputs):
    _, _, pattern = inputs
    variants = _diagnostic_patterns(pattern)
    assert set(variants) == {"strength_0.8", "strength_1.2", "remove_leaf_0"}
    assert variants["remove_leaf_0"].genome.root.node == "constant"
    assert all(item.binding.max_effect <= 0.25 for item in variants.values())


def test_panel_prefix_copies_all_market_arrays(inputs):
    _, panel, _ = inputs
    prefix = panel_prefix(panel, 520)
    assert len(prefix) == 520
    assert not np.shares_memory(panel.adjusted_opens, prefix.adjusted_opens)


def test_selected_pattern_outer_diagnostics_are_not_used_to_reselect(inputs, tmp_path, monkeypatch):
    base, panel, pattern = inputs
    from autoresearch.genetic import nested_replay

    seen = []

    def fixed_inner(simulation, candidate):
        seen.append(simulation.panel.sessions[-1])
        return {
            "candidate_sha256": candidate.digest(),
            "objective": {"eligible": True, "score": 1.0, "failed_gates": []},
            "controls": {},
        }

    monkeypatch.setattr(nested_replay, "_inner", fixed_inner)
    result = evaluate_fold(
        base,
        panel,
        None,
        fit_end=panel.sessions[503],
        selection_end=panel.sessions[524],
        output=tmp_path / "selected",
        contract_hash="c" * 64,
        contenders={"genetic": [pattern], "random": [pattern]},
    )
    assert seen == [panel.sessions[524], panel.sessions[524]]
    assert not result["methods"]["genetic"]["retained_incumbent"]
    assert result["methods"]["genetic"]["selected_pattern"] == pattern.model_dump(
        mode="json", by_alias=True
    )
    assert "remove_leaf_0" in result["diagnostics"]["genetic"]
    assert "body_atr" in result["outer"]
    perturbation = result["diagnostics"]["genetic"]["strength_1.2"]["perturbation"]
    assert perturbation["requested_factor"] == 1.2
    assert perturbation["effective_max_effect"] <= 0.25
    assert perturbation["changed"] == (pattern.binding.max_effect < 0.25)
    assert not result["diagnostics_used_for_selection"]
    frozen = json.loads((tmp_path / "selected/selection.json").read_text())
    assert "outer" not in frozen


def test_mapping_must_preserve_common_row_availability(inputs, tmp_path):
    base, panel, pattern = inputs
    mapping = np.arange(len(panel))
    mapping[100], mapping[101] = mapping[101], mapping[100]
    common = np.ones(len(panel), dtype=bool)
    common[100] = False
    with pytest.raises(ValueError, match="common-row"):
        evaluate_fold(
            base,
            panel,
            None,
            fit_end=panel.sessions[503],
            selection_end=panel.sessions[524],
            output=tmp_path / "bad",
            contract_hash="c" * 64,
            contenders={"genetic": [pattern], "random": [pattern]},
            feature_mapping=mapping,
            common_rows=common,
        )
