"""Resumable seeded parameter research with isolated selection phases."""

from __future__ import annotations

import fcntl
import json
import math
import os
import random
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json

Evaluator = Callable[[dict[str, Any], str], dict[str, Any]]
BatchEvaluator = Callable[[list[dict[str, Any]], str], list[dict[str, Any]]]
SCHEMA = "parameter-search/1"
SHA256_LENGTH = 64
BOUND_COUNT = 2
CATEGORY_MUTATION_PROBABILITY = 0.25


def _json(value: Any) -> Any:
    """Detach mutable callbacks/specs and refuse nonfinite metrics recursively."""
    try:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DataIntegrityError("parameter search requires finite JSON values") from exc


def _write(path: Path, payload: dict[str, Any]) -> None:
    envelope = {"payload": payload, "payload_sha256": digest_json(payload)}
    if path.exists():
        if _read(path) != payload:
            raise DataIntegrityError(f"immutable parameter receipt mismatch: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def _read(path: Path) -> dict[str, Any]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if (
            set(envelope) != {"payload", "payload_sha256"}
            or not isinstance(envelope["payload"], dict)
            or digest_json(envelope["payload"]) != envelope["payload_sha256"]
        ):
            raise DataIntegrityError("parameter receipt checksum mismatch")
        result: dict[str, Any] = _json(envelope["payload"])
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise DataIntegrityError("malformed parameter receipt") from exc


def _validate_spec(spec: dict[str, Any]) -> None:  # noqa: PLR0912
    required = {
        "context_sha256",
        "seed",
        "global_trials",
        "local_trials",
        "top_k",
        "numeric_domains",
        "categorical_domains",
        "baselines",
    }
    if set(spec) - required - {"batch_size"} or not required <= set(spec):
        raise DataIntegrityError("invalid parameter search spec fields")
    context = spec["context_sha256"]
    if (
        not isinstance(context, str)
        or len(context) != SHA256_LENGTH
        or any(c not in "0123456789abcdef" for c in context)
    ):
        raise DataIntegrityError("context_sha256 must bind source and data")
    for key in ("seed", "global_trials", "local_trials", "top_k", "batch_size"):
        value = spec.get(key, 32)
        if type(value) is not int or value < (1 if key in {"top_k", "batch_size"} else 0):
            raise DataIntegrityError(f"invalid parameter search {key}")
    numeric, categorical = spec["numeric_domains"], spec["categorical_domains"]
    if (
        not isinstance(numeric, dict)
        or not isinstance(categorical, dict)
        or set(numeric) & set(categorical)
    ):
        raise DataIntegrityError("parameter domains must have disjoint names")
    for name in (*numeric, *categorical):
        if not isinstance(name, str) or not name:
            raise DataIntegrityError("parameter names must be nonempty strings")
    for bounds in numeric.values():
        if (
            not isinstance(bounds, list)
            or len(bounds) != BOUND_COUNT
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds)
            or bounds[0] > bounds[1]
        ):
            raise DataIntegrityError("numeric domains require finite ordered [lo, hi]")
    for choices in categorical.values():
        if (
            not isinstance(choices, list)
            or not choices
            or len({digest_json(choice) for choice in choices}) != len(choices)
        ):
            raise DataIntegrityError("categorical domains require distinct choices")
    if not isinstance(spec["baselines"], list):
        raise DataIntegrityError("baselines must be a recipe list")
    for recipe in spec["baselines"]:
        _validate_recipe(recipe, spec)


def _validate_recipe(recipe: Any, spec: dict[str, Any]) -> None:
    numeric, categorical = spec["numeric_domains"], spec["categorical_domains"]
    if not isinstance(recipe, dict) or set(recipe) != set(numeric) | set(categorical):
        raise DataIntegrityError("recipe must supply exactly the declared parameters")
    for name, (low, high) in numeric.items():
        value = recipe[name]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise DataIntegrityError("recipe numeric parameter outside domain")
    for name, choices in categorical.items():
        if digest_json(recipe[name]) not in {digest_json(choice) for choice in choices}:
            raise DataIntegrityError("recipe categorical parameter outside domain")


def _metrics(value: Any) -> dict[str, Any]:
    result = _json(value)
    if not isinstance(result, dict) or "score" not in result:
        raise DataIntegrityError("evaluator must return a JSON object with score")
    score = result["score"]
    if score is not None and (type(score) not in (int, float) or not math.isfinite(score)):
        raise DataIntegrityError("evaluator score must be finite or null")
    return result


def _ranking(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {
        record["recipe_sha256"]: record
        for record in records
        if record["status"] == "evaluated" and record["metrics"]["score"] is not None
    }
    return sorted(
        unique.values(), key=lambda record: (-record["metrics"]["score"], record["recipe_sha256"])
    )


def _evaluate_batch(
    requests: list[tuple[Path, dict[str, Any]]],
    phase: str,
    evaluate: Evaluator,
    evaluate_many: BatchEvaluator | None,
) -> list[dict[str, Any]]:
    """Write each completed callback immediately; unexpected failures abort."""
    if not requests:
        return []
    if evaluate_many is not None:
        try:
            values: Any = evaluate_many(
                [_json(request[1]["recipe"]) for request in requests], phase
            )
        except ValueError as exc:
            values = [{"score": None, "invalid_reason": str(exc)} for _ in requests]
            statuses = ["invalid"] * len(requests)
        else:
            if not isinstance(values, list) or len(values) != len(requests):
                raise DataIntegrityError("batch evaluator must return one metric object per recipe")
            statuses = ["evaluated"] * len(requests)
        result = []
        for (path, record), metrics, status in zip(requests, values, statuses, strict=True):
            completed = {**record, "status": status, "metrics": _metrics(metrics)}
            _write(path, completed)
            result.append(completed)
        return result
    result = []
    for path, record in requests:
        try:
            metrics = evaluate(_json(record["recipe"]), phase)
        except ValueError as exc:
            metrics, status = {"score": None, "invalid_reason": str(exc)}, "invalid"
        else:
            status = "evaluated"
        completed = {**record, "status": status, "metrics": _metrics(metrics)}
        _write(path, completed)
        result.append(completed)
    return result


def _trial(
    output: Path,
    recipe: dict[str, Any],
    phase: str,
    trial_id: str,
    spec_hash: str,
) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    path = output / "trials" / f"{trial_id}.json"
    record = {
        "phase": phase,
        "trial_id": trial_id,
        "spec_sha256": spec_hash,
        "recipe": recipe,
        "recipe_sha256": digest_json(recipe),
    }
    existing = _read(path) if path.exists() else None
    if existing is not None and any(existing.get(key) != value for key, value in record.items()):
        raise DataIntegrityError("cached parameter proposal disagrees with deterministic replay")
    if existing is not None:
        if existing.get("status") not in {"evaluated", "invalid", "duplicate"}:
            raise DataIntegrityError("invalid cached parameter status")
        if existing["status"] != "duplicate":
            _metrics(existing.get("metrics"))
    return path, record, existing


def _uniform(spec: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    return {
        **{name: rng.uniform(*bounds) for name, bounds in sorted(spec["numeric_domains"].items())},
        **{
            name: rng.choice(choices)
            for name, choices in sorted(spec["categorical_domains"].items())
        },
    }


def _local(
    spec: dict[str, Any], rng: random.Random, ranked: list[dict[str, Any]], progress: float
) -> dict[str, Any]:
    if not ranked:
        return _uniform(spec, rng)
    recipe: dict[str, Any] = _json(rng.choice(ranked[: spec["top_k"]])["recipe"])
    scale = 0.20 * (1 - progress) + 0.02 * progress
    for name, (low, high) in sorted(spec["numeric_domains"].items()):
        recipe[name] = min(high, max(low, rng.gauss(recipe[name], scale * (high - low))))
    for name, choices in sorted(spec["categorical_domains"].items()):
        if rng.random() < CATEGORY_MUTATION_PROBABILITY:
            recipe[name] = rng.choice(choices)
    return recipe


def run_parameter_search(
    spec: dict[str, Any],
    evaluate: Evaluator,
    output: Path,
    *,
    evaluate_many: BatchEvaluator | None = None,
) -> dict[str, Any]:
    """Train globally and locally, freeze finalists, validate, then test once.

    Larger scores rank first. Null scores remain unranked. Trial counts are
    proposal budgets; exact duplicates are recorded without another evaluation.
    Local batches adapt exclusively to earlier training results. Test metrics
    never choose a recipe, and cached phase receipts are never re-evaluated.
    """
    spec = _json(spec)
    _validate_spec(spec)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DataIntegrityError("parameter search already has an active writer") from exc
        return _run(spec, evaluate, output, evaluate_many)


def _run(  # noqa: PLR0915
    spec: dict[str, Any], evaluate: Evaluator, output: Path, evaluate_many: BatchEvaluator | None
) -> dict[str, Any]:
    _write(output / "spec.json", {"schema_version": SCHEMA, "spec": spec})
    spec_hash = digest_json(spec)
    train: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    batch_size = spec.get("batch_size", 32)
    proposals = [
        ("baseline", len(spec["baselines"])),
        ("global", spec["global_trials"]),
        ("local", spec["local_trials"]),
    ]
    for origin, count in proposals:
        for first in range(0, count, batch_size):
            # Freeze the elite for this batch, including during resume replay.
            ranked = _ranking(train)
            batch: list[dict[str, Any] | None] = []
            pending = []
            for index in range(first, min(first + batch_size, count)):
                rng = random.Random(f"{SCHEMA}:{spec['seed']}:{origin}:{index}")  # noqa: S311
                recipe = (
                    _json(spec["baselines"][index])
                    if origin == "baseline"
                    else _uniform(spec, rng)
                    if origin == "global"
                    else _local(spec, rng, ranked, index / max(1, count - 1))
                )
                _validate_recipe(recipe, spec)
                trial_id = f"train-{origin}-{index:08d}"
                path, base, cached = _trial(output, recipe, "train", trial_id, spec_hash)
                recipe_hash = base["recipe_sha256"]
                if recipe_hash in seen:
                    record = {**base, "status": "duplicate", "duplicate_of": seen[recipe_hash]}
                    _write(path, record)
                    batch.append(record)
                else:
                    seen[recipe_hash] = trial_id
                    if cached is not None and cached["status"] == "duplicate":
                        raise DataIntegrityError("unexpected cached duplicate")
                    batch.append(cached)
                    if cached is None:
                        pending.append((path, base))
            completed = iter(_evaluate_batch(pending, "train", evaluate, evaluate_many))
            train.extend(record if record is not None else next(completed) for record in batch)
    ranked = _ranking(train)
    baseline_hashes = list(dict.fromkeys(digest_json(recipe) for recipe in spec["baselines"]))
    by_hash = {
        record["recipe_sha256"]: record for record in train if record["status"] != "duplicate"
    }
    finalist_hashes = list(
        dict.fromkeys(
            [record["recipe_sha256"] for record in ranked[: spec["top_k"]]] + baseline_hashes
        )
    )
    frozen = {
        "spec_sha256": spec_hash,
        "selection_phase": "train",
        "ranked_train_recipes": [record["recipe_sha256"] for record in ranked],
        "finalist_recipe_sha256": finalist_hashes,
        "baseline_recipe_sha256": baseline_hashes,
    }
    _write(output / "training-selection.json", frozen)

    def phase_records(hashes: list[str], phase: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any] | None] = []
        requests = []
        for recipe_hash in hashes:
            path, base, cached = _trial(
                output, by_hash[recipe_hash]["recipe"], phase, f"{phase}-{recipe_hash}", spec_hash
            )
            if cached is not None and cached["status"] == "duplicate":
                raise DataIntegrityError("holdout receipts cannot be duplicate placeholders")
            records.append(cached)
            if cached is None:
                requests.append((path, base))
        completed_records = []
        for offset in range(0, len(requests), batch_size):
            completed_records.extend(
                _evaluate_batch(
                    requests[offset : offset + batch_size], phase, evaluate, evaluate_many
                )
            )
        completed = iter(completed_records)
        return [record if record is not None else next(completed) for record in records]

    validation = phase_records(finalist_hashes, "validation")
    validation_rank = _ranking(validation)
    winner_hash = validation_rank[0]["recipe_sha256"] if validation_rank else None
    test_hashes = list(dict.fromkeys(([winner_hash] if winner_hash else []) + baseline_hashes))
    selected = {
        "spec_sha256": spec_hash,
        "selection_phase": "validation",
        "selected_recipe_sha256": winner_hash,
        "test_recipe_sha256": test_hashes,
    }
    _write(output / "validation-selection.json", selected)
    test = phase_records(test_hashes, "test")
    summary = {
        "schema_version": SCHEMA,
        "spec_sha256": spec_hash,
        "context_sha256": spec["context_sha256"],
        "execution_authority": "none",
        "promotion_authority": "none",
        "selection_rule": (
            "training top-k plus baselines; validation selects one; test is reporting only"
        ),
        "train_proposals": len(train),
        "train_unique_evaluated": sum(record["status"] == "evaluated" for record in train),
        "train_invalid": sum(record["status"] == "invalid" for record in train),
        "train_inactive": sum(
            record["status"] == "evaluated" and record["metrics"]["score"] is None
            for record in train
        ),
        "train_duplicates": sum(record["status"] == "duplicate" for record in train),
        "training_ranking": ranked,
        "finalist_recipe_sha256": finalist_hashes,
        "selected_by_validation": by_hash[winner_hash]["recipe"] if winner_hash else None,
        "selected_recipe_sha256": winner_hash,
        "validation": validation,
        "test": test,
        "trials_directory": "trials",
    }
    _write(output / "summary.json", summary)
    return summary
