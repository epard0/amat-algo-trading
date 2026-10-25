"""Diversified walk-forward single-fold selection and immutable phase boundaries."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, get_args

import pytest

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import FeatureName
from autoresearch.genetic.walkforward_search import (
    method_family,
    pareto_front,
    run_walkforward_search,
    training_shortlist,
    validation_policies,
)


def spec(**updates: Any) -> dict[str, Any]:
    return {
        "context_sha256": "a" * 64,
        "seed": 11,
        "global_trials": 30,
        "local_trials": 10,
        "top_k": 3,
        "batch_size": 4,
        "numeric_domains": {"x": [0, 1], "max_effect": [0, 0.25]},
        "categorical_domains": {
            "method": ["genetic_pattern", "momentum:21", "volume_ratio:21", "volatility:63"]
        },
        "baselines": [{"method": "genetic_pattern", "max_effect": 0, "x": 0.5}],
        **updates,
    }


def evaluator(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
    x = recipe["x"]
    return {
        "score": x if phase == "train" else 1 - x,
        "annualized_return": x * 0.2,
        "max_drawdown": -x * 0.1,
    }


def record(
    name: str, method: str, score: float | None, ret: float, dd: float, *, effect: float = 0.1
) -> dict[str, Any]:
    return {
        "recipe_sha256": name,
        "recipe": {"method": method, "max_effect": effect},
        "status": "evaluated",
        "metrics": {"score": score, "annualized_return": ret, "max_drawdown": dd},
    }


def test_all_registered_features_classified_and_disabled_method_is_portfolio_only() -> None:
    for name in get_args(FeatureName):
        assert method_family({"method": f"{name}:21", "max_effect": 0.1}) in {
            "trend",
            "momentum",
            "volatility",
            "liquidity",
            "candle",
        }
    assert method_family({"method": "genetic_pattern", "max_effect": 0.1}) == "genetic"
    assert method_family({"method": "unknown", "max_effect": 0}) == "portfolio_only"
    with pytest.raises(DataIntegrityError, match="unclassified"):
        method_family({"method": "unknown", "max_effect": 0.1})


def test_pareto_dominance_requires_strict_improvement_and_keeps_equal_points() -> None:
    rows = [
        record("a", "momentum:21", 1, 0.2, -0.1),
        record("b", "momentum:21", 2, 0.2, -0.1),
        record("c", "momentum:21", 3, 0.1, -0.2),
        record("d", "momentum:21", 4, 0.3, -0.2),
        record("e", "momentum:21", None, 0.9, 0),
    ]
    assert [row["recipe_sha256"] for row in pareto_front(rows)] == ["a", "b", "d"]
    assert pareto_front([]) == []


def test_training_shortlist_preserves_low_global_rank_families_and_pareto_extremes() -> None:
    rows = [
        record("g", "genetic_pattern", 100, 0.5, -0.05),
        record("m-score", "momentum:21", 3, 0.1, -0.2),
        record("m-return", "momentum:21", 2, 0.3, -0.1),
        record("m-defense", "momentum:21", 1, 0.15, -0.03),
        record("l", "volume_ratio:21", 0.1, 0.01, -0.01),
    ]
    shortlisted, coverage = training_shortlist(rows)
    assert {row["recipe_sha256"] for row in shortlisted} == {
        "g",
        "m-score",
        "m-return",
        "m-defense",
        "l",
    }
    assert coverage["momentum"]["shortlist_by_objective"] == {
        "balanced": "m-score",
        "return": "m-return",
        "defensive": "m-defense",
    }
    assert coverage["momentum"]["pareto_front_recipe_sha256"] == ["m-defense", "m-return"]
    assert coverage["candle"]["eligible"] == 0


def test_validation_policies_defensive_baseline_floor_and_hash_ties() -> None:
    rows = [
        record("base", "genetic_pattern", 1, 0.1, -0.1, effect=0),
        record("score", "momentum:21", 10, 0.15, -0.05),
        record("return", "momentum:21", 2, 0.3, -0.3),
        record("a-defense", "momentum:21", 1, 0.11, -0.01),
        record("z-defense", "momentum:21", 1, 0.11, -0.01),
        record("below-floor", "volume_ratio:21", 0, 0.09, -0.001),
    ]
    winners, floor = validation_policies(rows, ["base"])
    assert winners == {"balanced": "score", "return": "return", "defensive": "a-defense"}
    assert floor == 0.1
    rows[0]["metrics"]["score"] = None
    assert validation_policies(rows, ["base"])[0]["defensive"] is None


def test_stage_order_frozen_before_next_phase_and_test_union(tmp_path: Path) -> None:
    calls = []

    def callback(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        calls.append(phase)
        if phase == "validation":
            assert (tmp_path / "training-selection.json").exists()
        if phase == "test":
            assert (tmp_path / "validation-selection.json").exists()
        return evaluator(recipe, phase)

    result = run_walkforward_search(spec(), callback, tmp_path)
    assert calls == sorted(calls, key={"train": 0, "validation": 1, "test": 2}.get)
    baseline = digest_json(spec()["baselines"][0])
    expected = {key for key in result["selected_recipe_sha256_by_policy"].values() if key}
    assert {row["recipe_sha256"] for row in result["test"]} == expected | {baseline}
    assert result["execution_authority"] == result["promotion_authority"] == "none"


@pytest.mark.parametrize(
    "phase,count", [("train", 3), ("train", 34), ("validation", 2), ("test", 1)]
)
def test_resume_after_interruption_reuses_receipts_and_same_proposals(
    tmp_path: Path, phase: str, count: int
) -> None:
    observed: Counter[str] = Counter()
    called: Counter[tuple[str, str]] = Counter()

    def interrupted(recipe: dict[str, Any], current: str) -> dict[str, Any]:
        observed[current] += 1
        if current == phase and observed[current] == count:
            raise RuntimeError("stop")
        called[current, digest_json(recipe)] += 1
        return evaluator(recipe, current)

    with pytest.raises(RuntimeError):
        run_walkforward_search(spec(), interrupted, tmp_path / "resume")

    def resumed(recipe: dict[str, Any], current: str) -> dict[str, Any]:
        called[current, digest_json(recipe)] += 1
        return evaluator(recipe, current)

    result = run_walkforward_search(spec(), resumed, tmp_path / "resume")
    assert result == run_walkforward_search(spec(), evaluator, tmp_path / "clean")
    assert max(called.values()) == 1
    assert run_walkforward_search(spec(), resumed, tmp_path / "resume") == result
    assert max(called.values()) == 1


def test_holdout_mutation_cannot_change_training_proposals_or_shortlist(tmp_path: Path) -> None:
    run_walkforward_search(spec(), evaluator, tmp_path / "a")

    def mutated(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        metrics = evaluator(recipe, phase)
        if phase != "train":
            metrics.update(score=999, annualized_return=999, max_drawdown=-999)
        return metrics

    run_walkforward_search(spec(), mutated, tmp_path / "b")
    for path in (tmp_path / "a" / "trials").glob("train-*.json"):
        assert path.read_bytes() == (tmp_path / "b" / "trials" / path.name).read_bytes()
    assert (tmp_path / "a" / "training-selection.json").read_bytes() == (
        tmp_path / "b" / "training-selection.json"
    ).read_bytes()


def test_test_metrics_cannot_change_any_selected_policy(tmp_path: Path) -> None:
    baseline = run_walkforward_search(spec(), evaluator, tmp_path / "a")

    def mutated(recipe: dict[str, Any], phase: str) -> dict[str, Any]:
        result = evaluator(recipe, phase)
        if phase == "test":
            result["score"] *= -1000
        return result

    altered = run_walkforward_search(spec(), mutated, tmp_path / "b")
    assert (
        altered["selected_recipe_sha256_by_policy"] == baseline["selected_recipe_sha256_by_policy"]
    )


def test_batch_and_scalar_identical(tmp_path: Path) -> None:
    scalar = run_walkforward_search(spec(), evaluator, tmp_path / "scalar")
    batch = run_walkforward_search(
        spec(),
        evaluator,
        tmp_path / "batch",
        evaluate_many=lambda recipes, phase: [evaluator(recipe, phase) for recipe in recipes],
    )
    assert batch == scalar


def test_invalid_inactive_duplicates_stay_unranked_but_baseline_reported(tmp_path: Path) -> None:
    contract = spec(
        global_trials=2,
        local_trials=2,
        numeric_domains={"x": [0.5, 0.5], "max_effect": [0.0, 0.0]},
        categorical_domains={"method": ["genetic_pattern"]},
        baselines=[{"method": "genetic_pattern", "max_effect": 0.0, "x": 0.5}],
    )
    result = run_walkforward_search(
        contract, lambda _recipe, _phase: {"score": None}, tmp_path / "null"
    )
    assert result["train_duplicates"] == 4
    assert result["training_ranking"] == []
    assert result["selected_by_policy"] == {"balanced": None, "return": None, "defensive": None}
    assert len(result["test"]) == 1

    def invalid(_recipe: dict[str, Any], _phase: str) -> dict[str, Any]:
        raise ValueError("inactive")

    assert run_walkforward_search(contract, invalid, tmp_path / "invalid")["train_invalid"] == 1


@pytest.mark.parametrize("update", [{"seed": 99}, {"context_sha256": "b" * 64}, {"top_k": 4}])
def test_changed_spec_refused(tmp_path: Path, update: dict[str, Any]) -> None:
    run_walkforward_search(spec(), evaluator, tmp_path)
    with pytest.raises(DataIntegrityError, match="immutable"):
        run_walkforward_search(spec(**update), evaluator, tmp_path)


def test_corrupted_receipt_and_missing_eligible_metric_refused(tmp_path: Path) -> None:
    run_walkforward_search(spec(), evaluator, tmp_path / "corrupt")
    path = next((tmp_path / "corrupt" / "trials").glob("train-*.json"))
    envelope = json.loads(path.read_text())
    envelope["payload"]["metrics"]["score"] = 999
    path.write_text(json.dumps(envelope))
    with pytest.raises(DataIntegrityError, match="checksum"):
        run_walkforward_search(spec(), evaluator, tmp_path / "corrupt")
    with pytest.raises(DataIntegrityError, match="annualized_return"):
        run_walkforward_search(spec(), lambda _recipe, _phase: {"score": 1}, tmp_path / "missing")


def test_p10_output_is_not_reused(tmp_path: Path) -> None:
    from autoresearch.genetic.parameter_search import run_parameter_search

    run_parameter_search(spec(), evaluator, tmp_path)
    with pytest.raises(DataIntegrityError, match="immutable"):
        run_walkforward_search(spec(), evaluator, tmp_path)


def test_reserved_portfolio_global_slots_are_exactly_eighty_of_640(tmp_path: Path) -> None:
    result = run_walkforward_search(spec(global_trials=640, local_trials=0), evaluator, tmp_path)
    reserved = result["sampling"]["reserved_global_indices"]
    assert reserved == list(range(0, 640, 8))
    portfolio = []
    for index in range(640):
        path = tmp_path / "trials" / f"train-global-{index:08d}.json"
        recipe = json.loads(path.read_text())["payload"]["recipe"]
        if recipe["max_effect"] == 0:
            portfolio.append(index)
    assert portfolio == reserved
    assert len(portfolio) == 80
    assert result["family_coverage"]["portfolio_only"]["proposals"] == 81
    saved = json.loads((tmp_path / "spec.json").read_text())["payload"]
    assert saved["sampling"] == result["sampling"]


@pytest.mark.parametrize("bounds", [[0.01, 0.25], None])
def test_no_reserved_slots_when_zero_domain_not_declared(
    tmp_path: Path, bounds: list[float] | None
) -> None:
    numeric = {"x": [0, 1]}
    baseline: dict[str, Any] = {"method": "genetic_pattern", "x": 0.5}
    if bounds is not None:
        numeric["max_effect"] = bounds
        baseline["max_effect"] = 0.1
    result = run_walkforward_search(
        spec(numeric_domains=numeric, baselines=[baseline]), evaluator, tmp_path
    )
    assert result["sampling"]["reserved_global_indices"] == []
    assert result["sampling"]["portfolio_only_reservation_enabled"] is False


def test_batch_value_error_aborts_preserving_prior_receipts(tmp_path: Path) -> None:
    count = 0

    def batch(recipes: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("source integrity error")
        return [evaluator(recipe, phase) for recipe in recipes]

    with pytest.raises(DataIntegrityError, match="batch aborted"):
        run_walkforward_search(spec(), evaluator, tmp_path, evaluate_many=batch)
    receipts = list((tmp_path / "trials").glob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["payload"]["status"] == "evaluated"
    assert run_walkforward_search(spec(), evaluator, tmp_path)["train_invalid"] == 0
