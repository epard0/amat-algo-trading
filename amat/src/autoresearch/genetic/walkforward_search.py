"""Diversified, resumable single-fold parameter selection with sealed test phase."""

from __future__ import annotations

import fcntl
import math
import random
from pathlib import Path
from typing import Any

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.parameter_search import (
    BatchEvaluator,
    Evaluator,
    _evaluate_batch,
    _json,
    _local,
    _ranking,
    _trial,
    _uniform,
    _validate_recipe,
    _validate_spec,
    _write,
)

SCHEMA = "walkforward-search/1"
PORTFOLIO_SLOT_STRIDE = 8
FAMILIES = ("portfolio_only", "genetic", "trend", "momentum", "volatility", "liquidity", "candle")
FEATURE_FAMILIES = {
    "trend": {
        "efficiency",
        "trend_agreement",
        "trend_acceleration",
        "donchian_position",
        "breakout_distance",
        "breakout_persistence",
    },
    "momentum": {"momentum", "normalized_momentum", "relative_strength", "breadth", "rsi"},
    "volatility": {
        "volatility",
        "drawdown",
        "variance_ratio",
        "downside_variance_share",
        "volatility_shock",
        "volatility_of_volatility",
    },
    "liquidity": {"volume_ratio", "volume_price", "illiquidity"},
    "candle": {
        "close_location",
        "opening_gap",
        "window_markout",
        "body_atr",
        "range_atr",
        "upper_wick_fraction",
        "lower_wick_fraction",
    },
}


def method_family(recipe: dict[str, Any]) -> str:
    """Classify effective method: a disabled feature overlay is portfolio-only."""
    if recipe.get("max_effect", 0) == 0:
        return "portfolio_only"
    method = recipe.get("method")
    if method == "genetic_pattern":
        return "genetic"
    if isinstance(method, str):
        name = method.split(":", 1)[0]
        for family, features in FEATURE_FAMILIES.items():
            if name in features:
                return family
    raise DataIntegrityError(f"unclassified walk-forward method: {method}")


def sampling_contract(spec: dict[str, Any]) -> dict[str, Any]:
    bounds = spec["numeric_domains"].get("max_effect")
    enabled = bounds is not None and bounds[0] <= 0 <= bounds[1]
    return {
        "global": (
            "uniform independent slot RNG; each eighth slot reserved for portfolio-only "
            "when max_effect domain permits zero"
        ),
        "portfolio_only_reservation_enabled": enabled,
        "reserved_global_indices": list(range(0, spec["global_trials"], PORTFOLIO_SLOT_STRIDE))
        if enabled
        else [],
        "reservation_changes": (
            "force max_effect=0; all other parameters retain their uniform draws; "
            "no extra evaluations"
        ),
        "fallback": "no reserved slots when numeric max_effect domain does not contain zero",
    }


def selection_contract() -> dict[str, Any]:
    return {
        "training": (
            "per-family best score plus highest return and smallest absolute drawdown "
            "on return/drawdown Pareto front; union with baselines"
        ),
        "validation": {
            "balanced": "maximum score",
            "return": "maximum annualized return",
            "defensive": (
                "minimum absolute drawdown with annualized return at least "
                "the highest eligible baseline return"
            ),
        },
        "ties": "ascending recipe SHA256",
        "eligibility": "evaluated with finite non-null score, annualized_return and max_drawdown",
        "test": "union of frozen policy winners and baselines; reporting only",
        "local": "training score elites only; top_k sets local elite count, not shortlist size",
        "global": "each eighth slot portfolio-only if numeric max_effect domain permits zero",
        "scope": "single fold; no prior-outcome imports or fold stitching",
    }


def _eligible(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = _ranking(records)
    for record in ranked:
        for field in ("annualized_return", "max_drawdown"):
            value = record["metrics"].get(field)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise DataIntegrityError(f"eligible walk-forward metrics require finite {field}")
    return ranked


def pareto_front(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return nondominated eligible recipes; exact objective ties all survive.

    Maximize annualized return while minimizing absolute drawdown. Dominance
    requires at least one strictly better objective; hash order resolves later
    policy ties without deleting equivalent points from the reported frontier.
    """
    ranked = _eligible(records)
    result = []
    for candidate in ranked:
        ret = candidate["metrics"]["annualized_return"]
        drawdown = abs(candidate["metrics"]["max_drawdown"])
        if not any(
            other["metrics"]["annualized_return"] >= ret
            and abs(other["metrics"]["max_drawdown"]) <= drawdown
            and (
                other["metrics"]["annualized_return"] > ret
                or abs(other["metrics"]["max_drawdown"]) < drawdown
            )
            for other in ranked
        ):
            result.append(candidate)
    return sorted(result, key=lambda record: record["recipe_sha256"])


def training_shortlist(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Diversify with training metrics only, including explicit absent families."""
    ranked = _eligible(records)
    coverage = {}
    selected = {}
    for family in FAMILIES:
        all_family = [record for record in records if method_family(record["recipe"]) == family]
        eligible = [record for record in ranked if method_family(record["recipe"]) == family]
        front = pareto_front(eligible)
        choices = {}
        if eligible:
            choices = {
                "balanced": eligible[0],
                "return": min(
                    front,
                    key=lambda record: (
                        -record["metrics"]["annualized_return"],
                        record["recipe_sha256"],
                    ),
                ),
                "defensive": min(
                    front,
                    key=lambda record: (
                        abs(record["metrics"]["max_drawdown"]),
                        record["recipe_sha256"],
                    ),
                ),
            }
            selected.update({record["recipe_sha256"]: record for record in choices.values()})
        coverage[family] = {
            "proposals": len(all_family),
            "unique_evaluated": sum(record["status"] == "evaluated" for record in all_family),
            "eligible": len(eligible),
            "pareto_front_recipe_sha256": [record["recipe_sha256"] for record in front],
            "shortlist_by_objective": {
                objective: record["recipe_sha256"] for objective, record in choices.items()
            },
        }
    return [selected[key] for key in sorted(selected)], coverage


def validation_policies(
    records: list[dict[str, Any]], baseline_hashes: list[str]
) -> tuple[dict[str, str | None], float | None]:
    eligible = _eligible(records)
    baselines = [record for record in eligible if record["recipe_sha256"] in baseline_hashes]
    floor = max((record["metrics"]["annualized_return"] for record in baselines), default=None)
    defensive = [
        record
        for record in eligible
        if floor is not None and record["metrics"]["annualized_return"] >= floor
    ]
    winners = {
        "balanced": eligible[0] if eligible else None,
        "return": min(
            eligible,
            key=lambda record: (-record["metrics"]["annualized_return"], record["recipe_sha256"]),
            default=None,
        ),
        "defensive": min(
            defensive,
            key=lambda record: (abs(record["metrics"]["max_drawdown"]), record["recipe_sha256"]),
            default=None,
        ),
    }
    return {
        policy: record["recipe_sha256"] if record else None for policy, record in winners.items()
    }, floor


def run_walkforward_search(
    spec: dict[str, Any],
    evaluate: Evaluator,
    output: Path,
    *,
    evaluate_many: BatchEvaluator | None = None,
) -> dict[str, Any]:
    """Run one independently seeded fold; outer test is reporting only.

    Family-diversified training finalists precede three fixed validation policies.
    Local adaptation uses training score only. Exact duplicates are recorded and
    skipped. Receipts resume deterministically without re-evaluating cached work.
    This engine neither reads prior-study outcomes nor stitches fold histories.
    """
    spec = _json(spec)
    _validate_spec(spec)
    if not spec["baselines"]:
        raise DataIntegrityError("walk-forward policies require at least one baseline")
    for recipe in spec["baselines"]:
        method_family(recipe)
    for method in spec["categorical_domains"].get("method", []):
        method_family({"method": method, "max_effect": 1})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DataIntegrityError("parameter search already has an active writer") from exc

        def checked_many(recipes: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
            if evaluate_many is None:
                raise DataIntegrityError("missing batch evaluator")
            try:
                return evaluate_many(recipes, phase)
            except ValueError as exc:
                # A batch exception cannot identify an invalid recipe. Do not
                # relabel valid neighbors or integrity failures as trial outcomes.
                raise DataIntegrityError(
                    "walk-forward batch aborted without invalid relabeling"
                ) from exc

        return _run(spec, evaluate, output, checked_many if evaluate_many is not None else None)


def _run(  # noqa: PLR0915
    spec: dict[str, Any], evaluate: Evaluator, output: Path, evaluate_many: BatchEvaluator | None
) -> dict[str, Any]:
    sampling = sampling_contract(spec)
    _write(output / "spec.json", {"schema_version": SCHEMA, "spec": spec, "sampling": sampling})
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
                if origin == "global" and index in sampling["reserved_global_indices"]:
                    recipe["max_effect"] = 0.0
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
    ranked = _eligible(train)
    baseline_hashes = list(dict.fromkeys(digest_json(recipe) for recipe in spec["baselines"]))
    by_hash = {
        record["recipe_sha256"]: record for record in train if record["status"] != "duplicate"
    }
    shortlist, coverage = training_shortlist(train)
    finalist_hashes = list(
        dict.fromkeys([record["recipe_sha256"] for record in shortlist] + baseline_hashes)
    )
    frozen = {
        "spec_sha256": spec_hash,
        "selection_phase": "train",
        "sampling": sampling,
        "policy": selection_contract(),
        "family_coverage": coverage,
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
    winners, baseline_floor = validation_policies(validation, baseline_hashes)
    test_hashes = list(
        dict.fromkeys([key for key in winners.values() if key is not None] + baseline_hashes)
    )
    selected = {
        "spec_sha256": spec_hash,
        "selection_phase": "validation",
        "selected_recipe_sha256_by_policy": winners,
        "defensive_baseline_return_floor": baseline_floor,
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
        "selection_rule": selection_contract(),
        "family_coverage": coverage,
        "sampling": sampling,
        "defensive_baseline_return_floor": baseline_floor,
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
        "selected_by_policy": {
            policy: by_hash[key]["recipe"] if key else None for policy, key in winners.items()
        },
        "selected_recipe_sha256_by_policy": winners,
        "validation": validation,
        "test": test,
        "trials_directory": "trials",
    }
    _write(output / "summary.json", summary)
    return summary
