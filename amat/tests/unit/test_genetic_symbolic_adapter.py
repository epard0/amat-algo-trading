"""Numeric signal intervention is bounded, causal, registry-bound and opt-in."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from amat.data.synthetic import generate_panel
from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate, Program, apply_proposal
from autoresearch.genetic.adapter import GeneticSymbolicAdapterSpec, apply_genetic_pattern
from autoresearch.genetic.models import canonical_genome_digest, parse_pattern
from autoresearch.genetic.replay import attach_pattern
from autoresearch.genetic.symbolic import SYMBOLIC_REGISTRY_VERSION, symbolic_registry_digest
from autoresearch.signals import build_signal
from autoresearch.variants import ablations, proposal_for
from autoresearch.worker import complexity

ROOT = Path(__file__).parents[2]


def pattern(root=None):
    payload = json.loads((ROOT / "docs/genetic-patterns/symbolic.example.json").read_text())
    payload["genome"]["root"] = root or {"node": "constant", "value": 1.0}
    return parse_pattern(payload)


def feature(**overrides):
    return {
        "node": "feature",
        "name": "momentum",
        "source": "self",
        "lookback": 5,
        "lag": 0,
        "transform": "time_series_rank",
        **overrides,
    }


def bind(value):
    return GeneticSymbolicAdapterSpec(
        pattern=value,
        genome_sha256=canonical_genome_digest(value),
        registry_version=SYMBOLIC_REGISTRY_VERSION,
        registry_sha256=symbolic_registry_digest(),
    )


@pytest.fixture(scope="module")
def base():
    return Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    )


@pytest.fixture(scope="module")
def panel(base):
    return replace(
        generate_panel(base.config.strategy.to_universe(), sessions=180),
        source="scenario(symbolic-adapter-test)",
    )


def program(base, panel, version):
    return Program(
        schema_version=version,
        name="symbolic-adapter-test",
        baseline=base,
        load_start=panel.sessions[0],
        measured_start=panel.sessions[90],
        train_end=panel.sessions[-2],
        validation_end=panel.sessions[-1],
        frozen_files={},
        dependencies={},
    )


def test_numeric_binding_is_not_boolean_recentered_and_is_bounded(panel):
    spec = bind(pattern())
    scores = np.full_like(panel.adjusted_closes, 0.5)
    scores[0] = 0
    scores[1] = 0.99
    changed, values = apply_genetic_pattern(panel, scores, spec)
    np.testing.assert_allclose(values, np.tanh(1.0))
    np.testing.assert_allclose(changed[2:], 0.5 + spec.pattern.binding.max_effect * np.tanh(1.0))
    np.testing.assert_array_equal(changed[0], 0)
    np.testing.assert_array_equal(changed[1], 1)
    assert np.max(abs(changed - scores)) <= spec.pattern.binding.max_effect
    neutral, _ = apply_genetic_pattern(
        panel, scores, bind(pattern({"node": "constant", "value": 0.0}))
    )
    np.testing.assert_array_equal(neutral, scores)


def test_missing_features_abstain_and_future_mutation_does_not_change_history(panel):
    spec = bind(pattern(feature()))
    scores = np.full_like(panel.adjusted_closes, 0.5)
    original, values = apply_genetic_pattern(panel, scores, spec)
    assert np.isnan(values[:5]).all()
    np.testing.assert_array_equal(original[:5], scores[:5])
    closes = panel.adjusted_closes.copy()
    closes[130:] *= 10
    revised, future = apply_genetic_pattern(replace(panel, adjusted_closes=closes), scores, spec)
    np.testing.assert_array_equal(revised[:130], original[:130])
    np.testing.assert_array_equal(future[:130], values[:130])


def test_registry_hash_and_version_reject_substitution():
    spec = bind(pattern())
    for field, value in (
        ("registry_sha256", "a" * 64),
        ("registry_version", "changed"),
        ("genome_sha256", "b" * 64),
    ):
        payload = spec.model_dump()
        payload[field] = value
        with pytest.raises(ValidationError, match="mismatch"):
            GeneticSymbolicAdapterSpec.model_validate(payload)


def test_candidate_history_universe_and_implicit_benchmark_checks(base):
    with pytest.raises(ValidationError, match="outside authorized"):
        attach_pattern(base, pattern(feature(source="UNKNOWN")))
    with pytest.raises(ValidationError, match="warm-up"):
        attach_pattern(base, pattern(feature(lookback=252, lag=63)))
    payload = base.model_dump(mode="json")
    payload["config"]["strategy"]["universe"]["clusters"][0]["symbols"].remove("SPY")
    without_spy = Candidate.model_validate(payload)
    with pytest.raises(ValidationError, match="outside authorized"):
        attach_pattern(without_spy, pattern(feature(name="relative_strength")))
    candidate = attach_pattern(base, pattern(feature(source="SPY")))
    assert candidate.genetic_pattern is not None


def test_program_version_and_legacy_digest_are_preserved(base, panel):
    candidate = attach_pattern(base, pattern(feature()))
    old = program(base, panel, "strategy-development/8")
    with pytest.raises(ValueError, match="strategy-development/9"):
        build_signal(panel, candidate, old)
    with pytest.raises(ValidationError, match="strategy-development/9"):
        Program.model_validate({**old.model_dump(), "baseline": candidate})
    fresh = program(base, panel, "strategy-development/9")
    trace = build_signal(panel, candidate, fresh)
    assert trace.genetic_scores is not None
    assert "genetic_pattern" not in base.model_dump(mode="json")
    assert build_signal(panel, base, old).scores.shape == trace.scores.shape


def test_complexity_counts_tree_nodes_and_roundtrips_through_proposals(base):
    formula = pattern(
        {
            "node": "binary",
            "op": "add",
            "left": feature(),
            "right": {"node": "unary", "op": "neg", "arg": {"node": "constant", "value": 1.0}},
        }
    )
    candidate = attach_pattern(base, formula)
    assert complexity(candidate) - complexity(base) == 4
    proposal = proposal_for(
        base, candidate, hypothesis="Test bounded symbolic contribution.", stage="manual"
    )
    assert apply_proposal(base, proposal) == candidate
    assert dict(ablations(candidate))["genetic_pattern_disabled"].digest() == base.digest()
    roundtrip = Candidate.model_validate_json(candidate.model_dump_json())
    assert roundtrip.digest() == candidate.digest()
