"""Training adaptation, sealed selection phases, and deterministic recovery."""

from __future__ import annotations

import fcntl
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.parameter_search import run_parameter_search


def spec(**updates: Any) -> dict[str, Any]:
    return {
        "context_sha256": "a" * 64,
        "seed": 42,
        "global_trials": 12,
        "local_trials": 8,
        "top_k": 3,
        "batch_size": 4,
        "numeric_domains": {"tp": [1.0, 5.0], "sl": [0.5, 3.0]},
        "categorical_domains": {"direction": ["long", "short"]},
        "baselines": [{"tp": 2.0, "sl": 1.0, "direction": "long"}],
        **updates,
    }


def evaluator(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
    score = recipe["tp"] - recipe["sl"]
    return {"score": -score if phase == "validation" else score, "phase": phase}


def test_phases_freeze_training_finalists_and_test_does_not_select(tmp_path: Path) -> None:
    calls = []

    def callback(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        calls.append((phase, recipe))
        return evaluator(recipe, phase)

    result = run_parameter_search(spec(), callback, tmp_path)
    phases = [phase for phase, _ in calls]
    assert phases == sorted(phases, key={"train": 0, "validation": 1, "test": 2}.get)
    train_top = [record["recipe_sha256"] for record in result["training_ranking"][:3]]
    baseline = digest_json(spec()["baselines"][0])
    assert set(result["finalist_recipe_sha256"]) == {*train_top, baseline}
    validation = result["validation"]
    expected = max(validation, key=lambda record: record["metrics"]["score"])
    assert result["selected_recipe_sha256"] == expected["recipe_sha256"]
    assert {record["recipe_sha256"] for record in result["test"]} == {
        baseline,
        expected["recipe_sha256"],
    }
    assert result["execution_authority"] == result["promotion_authority"] == "none"


def test_completed_resume_never_calls_callback(tmp_path: Path) -> None:
    expected = run_parameter_search(spec(), evaluator, tmp_path)

    def forbidden(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        pytest.fail(f"re-evaluated {phase} {recipe}")

    assert run_parameter_search(spec(), forbidden, tmp_path) == expected


@pytest.mark.parametrize(
    "interrupt_phase,interrupt_count", [("train", 3), ("train", 16), ("validation", 2), ("test", 1)]
)
def test_interrupted_resume_matches_clean_run(
    tmp_path: Path, interrupt_phase: str, interrupt_count: int
) -> None:
    calls: Counter[tuple[str, str]] = Counter()
    phase_counts: Counter[str] = Counter()

    def interrupted(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        phase_counts[phase] += 1
        if phase == interrupt_phase and phase_counts[phase] == interrupt_count:
            raise RuntimeError("interrupted")
        calls[phase, digest_json(recipe)] += 1
        return evaluator(recipe, phase)

    path = tmp_path / "resume"
    with pytest.raises(RuntimeError):
        run_parameter_search(spec(), interrupted, path)

    def resumed(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        calls[phase, digest_json(recipe)] += 1
        return evaluator(recipe, phase)

    result = run_parameter_search(spec(), resumed, path)
    assert result == run_parameter_search(spec(), evaluator, tmp_path / "clean")
    assert max(calls.values()) == 1


def test_batching_matches_scalar_evaluation_and_recipe_mutation_is_detached(tmp_path: Path) -> None:
    batches = []

    def batch(recipes: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
        batches.append((phase, len(recipes)))
        values = [evaluator(recipe, phase) for recipe in recipes]
        for recipe in recipes:
            recipe["tp"] = 999
        return values

    result = run_parameter_search(spec(), evaluator, tmp_path / "batch", evaluate_many=batch)
    assert result == run_parameter_search(spec(), evaluator, tmp_path / "scalar")
    assert max(size for _, size in batches) <= 4


def test_seeded_local_proposals_stay_in_domains(tmp_path: Path) -> None:
    result = run_parameter_search(spec(global_trials=20, local_trials=64), evaluator, tmp_path)
    assert result["train_proposals"] == 85
    for path in (tmp_path / "trials").glob("train-*.json"):
        recipe = json.loads(path.read_text())["payload"]["recipe"]
        assert 1 <= recipe["tp"] <= 5
        assert 0.5 <= recipe["sl"] <= 3
        assert recipe["direction"] in {"long", "short"}


def test_local_proposals_do_not_depend_on_holdout_metrics(tmp_path: Path) -> None:
    run_parameter_search(spec(), evaluator, tmp_path / "a")

    def changed(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        return evaluator(recipe, "train") if phase == "train" else {"score": 999}

    run_parameter_search(spec(), changed, tmp_path / "b")
    for path in (tmp_path / "a" / "trials").glob("train-*.json"):
        assert path.read_bytes() == (tmp_path / "b" / "trials" / path.name).read_bytes()


def test_exact_duplicates_recorded_without_repeated_evaluation(tmp_path: Path) -> None:
    contract = spec(
        numeric_domains={"x": [1.0, 1.0]},
        categorical_domains={},
        baselines=[{"x": 1.0}, {"x": 1.0}],
    )
    calls = []

    def callback(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        calls.append(phase)
        return {"score": recipe["x"]}

    result = run_parameter_search(contract, callback, tmp_path)
    assert calls == ["train", "validation", "test"]
    assert result["train_unique_evaluated"] == 1
    assert result["train_duplicates"] == 21


def test_inactive_and_invalid_trials_do_not_rank(tmp_path: Path) -> None:
    contract = spec(
        numeric_domains={},
        categorical_domains={"mode": ["inactive", "invalid"]},
        baselines=[{"mode": "inactive"}, {"mode": "invalid"}],
    )

    def callback(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        assert phase in {"train", "validation", "test"}
        if recipe["mode"] == "invalid":
            raise ValueError("no valid trade")
        return {"score": None, "trades": 0}

    result = run_parameter_search(contract, callback, tmp_path)
    assert result["train_invalid"] == result["train_inactive"] == 1
    assert result["training_ranking"] == []
    assert result["selected_by_validation"] is None
    assert len(result["test"]) == 2


@pytest.mark.parametrize(
    "update", [{"context_sha256": "b" * 64}, {"seed": 43}, {"global_trials": 13}, {"batch_size": 2}]
)
def test_changed_spec_refuses_existing_output(tmp_path: Path, update: dict[str, Any]) -> None:
    run_parameter_search(spec(), evaluator, tmp_path)
    with pytest.raises(DataIntegrityError, match="immutable"):
        run_parameter_search(spec(**update), evaluator, tmp_path)


@pytest.mark.parametrize(
    "metrics",
    [
        {"score": float("nan")},
        {"score": 1, "nested": [float("inf")]},
        {"score": True},
        {"wrong": 1},
    ],
)
def test_nonfinite_or_bad_metrics_abort_without_invalid_relabel(
    tmp_path: Path, metrics: dict[str, Any]
) -> None:
    with pytest.raises(DataIntegrityError):
        run_parameter_search(spec(), lambda _recipe, _phase: metrics, tmp_path)
    assert (
        not list((tmp_path / "trials").glob("*.json")) if (tmp_path / "trials").exists() else True
    )


def test_exclusive_writer_lock(tmp_path: Path) -> None:
    with (tmp_path / ".lock").open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(DataIntegrityError, match="active writer"):
            run_parameter_search(spec(), evaluator, tmp_path)


def test_tampered_trial_checksum_refused(tmp_path: Path) -> None:
    run_parameter_search(spec(), evaluator, tmp_path)
    path = next((tmp_path / "trials").glob("train-*.json"))
    envelope = json.loads(path.read_text())
    envelope["payload"]["metrics"]["score"] += 1
    path.write_text(json.dumps(envelope))
    with pytest.raises(DataIntegrityError, match="checksum"):
        run_parameter_search(spec(), evaluator, tmp_path)


def test_thousands_of_proposals_are_supported(tmp_path: Path) -> None:
    result = run_parameter_search(
        spec(global_trials=2048, local_trials=256, batch_size=32), evaluator, tmp_path
    )
    assert result["train_proposals"] == 2305
    assert result["train_unique_evaluated"] > 2000
