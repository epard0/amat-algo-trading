import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from autoresearch.genetic.search import EvaluationResult, SearchProtocol, run_search


@pytest.fixture
def template():
    return json.loads(
        (Path(__file__).parents[2] / "docs/genetic-patterns/candle.example.json").read_text()
    )


def score(pattern):
    return EvaluationResult(float(len(pattern.genome.rules)), {"support": 20})


def search(template, protocol=None, evaluator=score, **kwargs):
    return run_search(
        protocol or SearchProtocol(population_size=5, generations=2),
        evaluator,
        pattern_template=template,
        evaluator_hash="evaluator1",
        data_hash="data1",
        **kwargs,
    )


def test_resume_exactly_matches_uninterrupted(template):
    expected = search(template)
    partial = search(template, max_slots=7)
    resumed = search(template, checkpoint=json.loads(json.dumps(partial)))
    assert resumed == expected
    assert len(resumed["ledger"]) == 15
    assert search(template) == expected
    assert partial["next_slot"] == 7


def test_failures_preserved_and_budget_equal(template):
    def fail(_pattern):
        raise RuntimeError("support floor")

    protocol = SearchProtocol(population_size=4, generations=2)
    genetic = search(template, protocol, fail)
    control = search(template, replace(protocol, method="random"), fail)
    assert len(genetic["ledger"]) == len(control["ledger"]) == 12
    assert all(row["failure"] == "RuntimeError: support floor" for row in genetic["ledger"])
    assert genetic["champion"] is None


def test_elites_cached_but_recorded(template):
    count = 0

    def counted(pattern):
        nonlocal count
        count += 1
        return score(pattern)

    state = search(
        template, SearchProtocol(population_size=2, generations=2, elite_count=2), counted
    )
    assert len(state["ledger"]) == 6
    assert all(row["cached"] for row in state["ledger"][2:])
    assert count == len(state["cache"]) <= 2


def test_context_changes_rejected(template):
    state = search(template, max_slots=2)
    with pytest.raises(ValueError, match="context"):
        run_search(
            SearchProtocol(population_size=5, generations=2),
            score,
            pattern_template=template,
            evaluator_hash="changed",
            data_hash="data1",
            checkpoint=state,
        )
    changed = dict(template, timeframe="1h")
    with pytest.raises(ValueError, match="context"):
        search(changed, checkpoint=state)
    with pytest.raises(ValueError, match="context"):
        search(template, SearchProtocol(population_size=5, generations=3), checkpoint=state)


def test_bounded_invalid_generation(template, monkeypatch):
    def invalid(_candidate):
        raise ValueError("invalid candidate")

    monkeypatch.setattr("autoresearch.genetic.search.parse_pattern", invalid)
    protocol = SearchProtocol(population_size=3, generations=1, max_generation_attempts=2)
    state = search(template, protocol)
    assert len(state["ledger"]) == 6
    assert all(row["operator"] == "generation_exhausted" for row in state["ledger"])
    assert not state["cache"]


def test_explicit_failure_and_nonfinite_score(template):
    state = search(template, evaluator=lambda _: EvaluationResult(100, failure="rejected"))
    assert all(row["fitness"] is None for row in state["ledger"])
    state = search(template, evaluator=lambda _: EvaluationResult(float("nan")))
    assert all(row["status"] == "failed" for row in state["ledger"])
    json.dumps(state, allow_nan=False)


def test_protocol_frozen_and_settings_validated():
    protocol = SearchProtocol()
    with pytest.raises(FrozenInstanceError):
        protocol.seed = 2
    for kwargs in (
        {"population_size": 0},
        {"generations": -1},
        {"max_generation_attempts": 0},
        {"mutation_probability": float("nan")},
        {"max_rules": 9},
        {"max_lag": -1},
        {"method": "unknown"},
        {"seed": True},
    ):
        with pytest.raises(ValueError):
            SearchProtocol(**kwargs)


def test_checkpoint_payload_corruption_rejected(template):
    state = search(template, max_slots=3)
    state["champion"]["fitness"] = 1e50
    with pytest.raises(ValueError, match="checksum"):
        search(template, checkpoint=state)


def test_zero_work_and_completed_resume(template):
    initial = search(template, max_slots=0)
    assert initial["ledger"] == []
    complete = search(template, checkpoint=initial)
    assert search(template, checkpoint=complete) == complete


def test_hard_budget_limits():
    with pytest.raises(ValueError, match="budget"):
        SearchProtocol(population_size=1000, generations=1000)
    with pytest.raises(ValueError, match="budget"):
        SearchProtocol(max_generation_attempts=10001)
    with pytest.raises(ValueError, match="bounds"):
        SearchProtocol(max_lag=21)
