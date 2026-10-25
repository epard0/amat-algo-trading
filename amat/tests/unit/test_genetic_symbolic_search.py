"""Typed variation, immutable grammar, context binding and exact search replay."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from autoresearch.genetic.models import canonical_genome_digest, parse_pattern
from autoresearch.genetic.search import EvaluationResult
from autoresearch.genetic.symbolic_search import (
    SymbolicSearchProtocol,
    SymbolicTerminal,
    node_paths,
    run_symbolic_search,
    tree_size,
    validate_symbolic_pattern,
)


@pytest.fixture
def template():
    return json.loads(
        (Path(__file__).parents[2] / "docs/genetic-patterns/symbolic.example.json").read_text()
    )


def score(pattern):
    root = pattern.genome.root.model_dump(mode="json")
    return EvaluationResult(float(tree_size(root)[1]), {"scope": "synthetic syntax test"})


def search(template, protocol=None, evaluator=score, **kwargs):
    return run_symbolic_search(
        protocol or SymbolicSearchProtocol(population_size=8, generations=2, seed=81),
        evaluator,
        pattern_template=template,
        evaluator_hash="symbolic-evaluator1",
        data_hash="training-data1",
        **kwargs,
    )


def test_seeded_replay_and_json_resume_exact_equality(template):
    full = search(template)
    partial = search(template, max_slots=11)
    resumed = search(template, checkpoint=json.loads(json.dumps(partial)))
    assert resumed == full == search(template)
    assert full["next_slot"] == len(full["ledger"]) == 24
    assert search(template, checkpoint=full, max_slots=0) == full
    assert search(template, checkpoint=search(template, max_slots=0)) == full


def test_every_offspring_respects_depth_nodes_and_frozen_grammar(template):
    protocol = SymbolicSearchProtocol(
        population_size=24,
        generations=12,
        max_depth=3,
        max_nodes=13,
        crossover_probability=1.0,
        mutation_probability=1.0,
        fresh_probability=0.0,
    )
    state = search(template, protocol)
    seen = set()
    for row in state["ledger"]:
        if row["pattern"] is not None:
            pattern = parse_pattern(row["pattern"])
            validate_symbolic_pattern(pattern, protocol)
            depth, count = tree_size(row["pattern"]["genome"]["root"])
            assert depth <= 3 and count <= 13
            assert canonical_genome_digest(pattern) == row["genome_hash"]
            if "mutation" in row["operator_details"]:
                seen.add(row["operator_details"]["mutation"])
        if "subtree_crossover" in row["operator"]:
            assert len(row["parents"]) == 2
            assert "donor_path" in row["operator_details"]["crossover"]
    assert seen == {"subtree", "operator", "constant"}


def test_genetic_and_random_count_identical_slot_budgets(template):
    protocol = SymbolicSearchProtocol(population_size=5, generations=3)
    genetic = search(template, protocol)
    random = search(template, replace(protocol, method="random"))
    assert len(genetic["ledger"]) == len(random["ledger"]) == 20
    assert all(row["operator"] in {"random", "generation_exhausted"} for row in random["ledger"])


def test_elites_and_failures_cached_and_fully_recorded(template):
    protocol = SymbolicSearchProtocol(
        population_size=2,
        generations=2,
        elite_count=2,
        max_depth=0,
        constants=(),
        feature_terminals=(SymbolicTerminal("efficiency"),),
    )
    calls = 0

    def failed(_pattern):
        nonlocal calls
        calls += 1
        return EvaluationResult(None, {"support": 0}, "insufficient_support")

    state = search(template, protocol, failed)
    assert calls == 1
    assert len(state["ledger"]) == 6
    assert all(
        row["status"] == "failed" and row["failure"] == "insufficient_support"
        for row in state["ledger"]
    )
    assert state["champion"] is None
    successful = search(template, protocol)
    assert all(row["operator"] == "elite" and row["cached"] for row in successful["ledger"][2:])


def test_generation_exhaustion_is_bounded_and_recorded(template, monkeypatch):
    monkeypatch.setattr(
        "autoresearch.genetic.symbolic_search._grow", lambda *_: {"node": "constant", "value": 1.0}
    )
    protocol = SymbolicSearchProtocol(population_size=2, generations=1, max_generation_attempts=3)
    state = search(template, protocol)
    assert len(state["ledger"]) == 4
    assert all(row["operator"] == "generation_exhausted" for row in state["ledger"])
    assert all(
        row["retry_count"] == 3 and "constant-only" in row["failure"] for row in state["ledger"]
    )
    assert not state["cache"]


def test_protocol_is_deeply_frozen_and_roundtrips_json():
    protocol = SymbolicSearchProtocol()
    assert SymbolicSearchProtocol.from_dict(json.loads(json.dumps(protocol.to_dict()))) == protocol
    with pytest.raises(FrozenInstanceError):
        protocol.max_depth = 2
    with pytest.raises(FrozenInstanceError):
        protocol.feature_terminals[0].lag = 2
    assert protocol.terminals()[0].node == "feature"
    for kwargs in (
        {"feature_terminals": ()},
        {"feature_terminals": []},
        {"constants": [0.0]},
        {"constants": (float("nan"),)},
        {"constants": (False,)},
        {"constants": (11.0,)},
        {"max_depth": 5},
        {"max_depth": True},
        {"max_nodes": 32},
        {"max_nodes": 0},
        {"unary_ops": ("eval",)},
        {"binary_ops": ("add", "add")},
        {"feature_terminals": (SymbolicTerminal("efficiency"),) * 2},
    ):
        with pytest.raises(ValueError):
            SymbolicSearchProtocol(**kwargs)


def test_context_hash_binds_grammar_data_evaluator_and_bounds(template):
    state = search(template, max_slots=3)
    base = SymbolicSearchProtocol(population_size=8, generations=2, seed=81)
    for changed in (
        replace(base, constants=(0.0,)),
        replace(base, max_depth=2),
        replace(base, feature_terminals=(SymbolicTerminal("momentum"),)),
    ):
        with pytest.raises(ValueError, match="context"):
            search(template, changed, checkpoint=state)
    with pytest.raises(ValueError, match="context"):
        run_symbolic_search(
            base,
            score,
            pattern_template=template,
            evaluator_hash="changed",
            data_hash="training-data1",
            checkpoint=state,
        )
    with pytest.raises(ValueError, match="context"):
        run_symbolic_search(
            base,
            score,
            pattern_template=template,
            evaluator_hash="symbolic-evaluator1",
            data_hash="changed",
            checkpoint=state,
        )
    state["cache"][next(iter(state["cache"]))]["fitness"] = 1e50
    with pytest.raises(ValueError, match="checksum"):
        search(template, checkpoint=state)


def test_unsupported_metadata_and_grammar_rejected_before_search(template):
    with pytest.raises(ValueError, match="daily adjusted"):
        search(dict(template, timeframe="1h"))
    protocol = SymbolicSearchProtocol(feature_terminals=(SymbolicTerminal("momentum"),))
    with pytest.raises(ValueError, match="outside frozen grammar"):
        validate_symbolic_pattern(parse_pattern(template), protocol)
    payload = dict(
        template,
        genome={
            "kind": "symbolic_indicator",
            "root": {"node": "constant", "value": 1.0},
            "outputTransform": "tanh",
        },
    )
    with pytest.raises(ValueError, match="constant-only"):
        validate_symbolic_pattern(parse_pattern(payload), SymbolicSearchProtocol())


def test_reference_hash_preserves_constant_value_and_commutative_order(template):
    feature = SymbolicTerminal("efficiency").feature_node().model_dump(mode="json")
    payload = dict(
        template,
        genome={
            "kind": "symbolic_indicator",
            "root": {
                "node": "binary",
                "op": "add",
                "left": feature,
                "right": {"node": "constant", "value": 0.5},
            },
            "outputTransform": "tanh",
        },
    )
    pattern = parse_pattern(payload)
    digest = canonical_genome_digest(pattern)
    assert digest == "2c231d9c9825af17815f10f8f962333e84097949b43c969b8168f674d448ca6a"
    root = payload["genome"]["root"]
    root["left"], root["right"] = root["right"], root["left"]
    assert canonical_genome_digest(parse_pattern(payload)) == digest
    root["left"]["value"] = -0.5
    assert canonical_genome_digest(parse_pattern(payload)) != digest
    assert node_paths(root) == [(), ("left",), ("right",)]
