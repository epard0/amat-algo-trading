"""Documented joint-domain repair; preserve the original registered campaign.

This amendment is exploratory because original outer outcomes have been opened.
It changes only proposal validity, never the objective or candidate budgets.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nested_strategy_campaign as study


def proposal(
    slot: int,
    seed: int,
    *,
    parents: list[dict[str, Any]] | tuple = (),
    progress: float = 0.0,
    joint_seeds: list[dict[str, Any]] | tuple = (),
) -> dict[str, Any]:
    rng = random.Random(seed + 104729 * slot)  # noqa: S311 - deterministic research sampling.
    methods = [v for values in study.FAMILIES.values() for v in values if v != "none"]
    if parents:
        recipe = json.loads(json.dumps(rng.choice(parents)))
        scale = 0.2 * (1 - progress) + 0.02 * progress
        for key, (lo, hi) in study.NUMERIC.items():
            recipe[key] = min(hi, max(lo, rng.gauss(recipe[key], scale * (hi - lo))))
        for key, values in study.CATEGORICAL.items():
            if rng.random() < study.MUTATION_CHANCE:
                recipe[key] = rng.choice(values)
        if rng.random() < study.MUTATION_CHANCE:
            recipe["feature_a"] = rng.choice(methods)
    else:
        recipe = {key: rng.uniform(lo, hi) for key, (lo, hi) in study.NUMERIC.items()}
        recipe.update({key: rng.choice(values) for key, values in study.CATEGORICAL.items()})
        recipe.update(
            polarity=(-1, 1)[(slot // len(methods)) % 2],
            exit_mode=("none", "stop", "both")[(slot // 4) % 3],
        )
        if joint_seeds and slot < len(joint_seeds):
            inherited = joint_seeds[slot]
            # A single-family identity must not erase the sampled joint weights.
            recipe.update(
                {
                    k: v
                    for k, v in inherited.items()
                    if k not in {"feature_a", "feature_b", "mix", "effect"}
                }
            )
            if inherited["effect"] > 0:
                recipe["effect"] = inherited["effect"]
        recipe["feature_a"] = rng.choice(methods)
        recipe["feature_b"] = rng.choice([m for m in methods if m != recipe["feature_a"]])
    if recipe["feature_b"] in ("none", recipe["feature_a"]):
        recipe["feature_b"] = rng.choice([m for m in methods if m != recipe["feature_a"]])
    recipe = study.canonical(recipe)
    validate(recipe)
    return recipe


def validate(recipe: dict[str, Any]) -> None:
    if recipe["feature_a"] == "none" or recipe["feature_b"] in ("none", recipe["feature_a"]):
        raise ValueError("Joint proposal requires two distinct active features")
    for key, (lo, hi) in study.NUMERIC.items():
        if not lo <= recipe[key] <= hi:
            raise ValueError(f"Joint proposal outside numeric domain: {key}={recipe[key]}")
    for key, choices in study.CATEGORICAL.items():
        if recipe[key] not in choices:
            raise ValueError(f"Joint proposal outside categorical domain: {key}={recipe[key]}")


def select(
    root: Path,
    pool: ProcessPoolExecutor,
    original: dict[str, Any],
    *,
    no_exits: bool = False,
) -> dict[str, Any]:
    fold, world = original["fold"], original["world"]
    folder = root / "joint-correction" / world / fold["name"]
    if no_exits:
        folder = folder / "no-exits"
    if (folder / "selection.json").exists():
        return study.read(folder / "selection.json")
    protocol = study.read(root / "protocol.json")
    seed = protocol["optimizer_seed"] + (90000 if no_exits else 7000) + int(fold["name"][-4:])
    reference = study.job(study.task(root, fold, study.baseline_recipe(), "train", world=world))
    inherited = [
        study.read(root / "search" / world / fold["name"] / f"{family}-training.json")[0]["recipe"]
        for family in study.FAMILIES
    ]
    broad = [proposal(i, seed, joint_seeds=inherited) for i in range(12)]
    if no_exits:
        broad = [study.canonical({**r, "exit_mode": "none"}) for r in broad]
    study.write(folder / "global-proposals.json", broad)
    runs = study.batch(pool, [study.task(root, fold, r, "train", world=world) for r in broad])
    parents = [r["recipe"] for r in study.ranked(runs, reference)[:2]]
    local = [proposal(12 + i, seed, parents=parents, progress=i / 3) for i in range(4)]
    if no_exits:
        local = [study.canonical({**r, "exit_mode": "none"}) for r in local]
    study.write(folder / "local-proposals.json", local)
    runs += study.batch(pool, [study.task(root, fold, r, "train", world=world) for r in local])
    ranking = study.ranked(runs, reference)
    study.write(folder / "training.json", ranking)
    finalists = [study.baseline_recipe()]
    for row in ranking:
        if row["eligible"] and row["recipe"] not in finalists:
            finalists.append(row["recipe"])
        if len(finalists) == study.SHORTLIST_SIZE + 1:
            break
    study.write(folder / "finalists.json", finalists)
    by_cost = {
        cost: study.batch(
            pool,
            [study.task(root, fold, r, "selection", cost=cost, world=world) for r in finalists],
        )
        for cost in (2, 10)
    }
    eligible = []
    for row in study.ranked(by_cost[2], by_cost[2][0]):
        stressed = next(r for r in by_cost[10] if r["task"]["recipe"] == row["recipe"])
        stress_score = study.score(stressed["metrics"], by_cost[10][0]["metrics"])
        if row["recipe"] == study.baseline_recipe() or (
            row["eligible"]
            and row["annual_log_growth_difference"] >= study.MIN_SELECTION_IMPROVEMENT
            and stress_score["eligible"]
            and stress_score["annual_log_growth_difference"] >= 0
        ):
            eligible.append(row)
    winner = sorted(eligible, key=lambda r: (-r["score"], study.digest_json(r["recipe"])))[0]
    result = {
        "fold": fold,
        "world": world,
        "selected": winner,
        "training_slots": 16,
        "selection_runs": [
            study.digest_json(r["task"]) for records in by_cost.values() for r in records
        ],
    }
    study.write(folder / "selection.json", result)
    return result


def diagnostics(
    root: Path,
    pool: ProcessPoolExecutor,
    selections: list[dict[str, Any]],
    originals: list[dict[str, Any]],
) -> None:
    links, tasks = [], {}
    for s in selections:
        if s["world"] != "historical":
            continue
        recipe, fold = s["selected"]["recipe"], s["fold"]
        parent = study.digest_json(recipe)
        original = next(o for o in originals if o["world"] == s["world"] and o["fold"] == fold)
        interventions = {
            "fixed_remove_all_features_original_cadence": study.canonical(
                {**recipe, "effect": 0.0}
            ),
            "fixed_remove_exits_original_cadence": study.canonical({**recipe, "exit_mode": "none"}),
            "retuned_remove_all_features": original["families"]["portfolio"]["selected"]["recipe"],
        }
        if recipe["effect"] > 0:
            for removed, kept, fraction in (
                ("a", "feature_b", 1 - recipe["mix"]),
                ("b", "feature_a", recipe["mix"]),
            ):
                interventions[f"fixed_remove_feature_{removed}"] = study.canonical(
                    {
                        **recipe,
                        "feature_a": recipe[kept],
                        "feature_b": "none",
                        "effect": recipe["effect"] * fraction,
                    }
                )
                family = next(f for f, methods in study.FAMILIES.items() if recipe[kept] in methods)
                interventions[f"retuned_remove_feature_{removed}"] = original["families"][family][
                    "selected"
                ]["recipe"]
            if recipe["exit_mode"] != "none":
                interventions["retuned_remove_exits"] = select(root, pool, original, no_exits=True)[
                    "selected"
                ]["recipe"]
        for feature in (False, True):
            for exits in (False, True):
                for cadence in (1, 5):
                    interventions[f"feature{int(feature)}-exits{int(exits)}-cadence{cadence}"] = (
                        study.canonical(
                            {
                                **recipe,
                                "effect": recipe["effect"] if feature else 0.0,
                                "exit_mode": recipe["exit_mode"] if exits else "none",
                                "cadence": cadence,
                            }
                        )
                    )
        for parameter in ("effect", "rebalance", "mix"):
            for factor in (0.9, 1.1):
                lo, hi = study.NUMERIC[parameter]
                value = min(hi, max(lo, recipe[parameter] * factor))
                if recipe["effect"] == 0 and parameter == "effect":
                    value = 0.0
                interventions[f"{parameter}-x{factor}"] = study.canonical(
                    {**recipe, parameter: value}
                )
        for name, alternative in interventions.items():
            t = study.task(root, fold, alternative, "test")
            identity = study.digest_json(t)
            tasks[identity] = t
            links.append(
                {"fold": fold["name"], "parent": parent, "intervention": name, "run": identity}
            )
        selected = study.job(study.task(root, fold, recipe, "selection"))["metrics"]
        reference = study.job(study.task(root, fold, study.baseline_recipe(), "selection"))[
            "metrics"
        ]
        scalar = min(1.0, selected["annualized_volatility"] / reference["annualized_volatility"])
        t = study.task(root, fold, study.baseline_recipe(), "test", risk_scalar=scalar)
        identity = study.digest_json(t)
        tasks[identity] = t
        links.append(
            {
                "fold": fold["name"],
                "parent": parent,
                "intervention": "ex_ante_risk_match",
                "run": identity,
                "scalar": scalar,
            }
        )
    study.write(
        root / "joint-correction" / "diagnostic-plan.json",
        {"tasks": list(tasks.values()), "links": links},
    )
    study.batch(pool, list(tasks.values()))
    study.write(root / "joint-correction" / "diagnostic-complete.json", {"links": links})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    protocol = study.verify(root)
    amendment = {
        "parent_protocol_sha256": study.digest_json(protocol),
        "script_sha256": study.sha(__file__),
        "issue": (
            "Single-family inheritance can set joint mix=1 or effect=0; "
            "a local collision can collapse a pair."
        ),
        "repair": (
            "Preserve sampled blend weight, inherit positive effect only, and require two "
            "distinct active features. Validate every numeric and categorical domain."
        ),
        "unchanged": (
            "Objective, costs, accounting, baseline, seeds, folds, 12+4 budgets, "
            "training-only parent selection, and fitted features."
        ),
        "scope": (
            "Replace only joint-family proposals for three historical and two whole-process "
            "folds. Freeze all five selections before corrected outer replay. "
            "Independent-family results remain original."
        ),
        "evidence_status": (
            "Exploratory implementation correction after original historical "
            "outer outcomes were opened; not a pristine holdout."
        ),
        "selection_changes_from_outer_outcomes": False,
    }
    study.write(root / "joint-correction" / "amendment.json", amendment)
    if args.plan_only:
        return
    started = time.monotonic()
    originals = study.read(root / "historical-selections.json") + study.read(
        root / "process-selections.json"
    )
    with ProcessPoolExecutor(max_workers=2, mp_context=mp.get_context("spawn")) as pool:
        selections = [select(root, pool, original) for original in originals]
        study.write(root / "joint-correction" / "selections.json", selections)
        tasks = [
            study.task(root, s["fold"], recipe, "test", cost=cost, world=s["world"])
            for s in selections
            for recipe in (study.baseline_recipe(), s["selected"]["recipe"])
            for cost in (2, 10)
        ]
        study.write(root / "joint-correction" / "evaluation-plan.json", tasks)
        runs = study.batch(pool, tasks)
        study.write(
            root / "joint-correction" / "evaluation.json",
            {"runs": [study.digest_json(r["task"]) for r in runs]},
        )
        diagnostics(root, pool, selections, originals)
        # Apply the corrected first-fold recipe to the same registered final markets.
        first = selections[0]
        tasks = []
        for world, definition in protocol["worlds"].items():
            if definition["role"] != "final_evaluation_only":
                continue
            for cost in (2, 10):
                tasks.append(
                    study.task(
                        root,
                        first["fold"],
                        first["selected"]["recipe"],
                        "test",
                        cost=cost,
                        world=world,
                        fit_world="historical",
                        upper=protocol["folds"][-1]["test_end"],
                    )
                )
        study.write(root / "joint-correction" / "market-plan.json", tasks)
        runs = study.batch(pool, tasks)
        study.write(
            root / "joint-correction" / "market-evaluation.json",
            {"runs": [study.digest_json(r["task"]) for r in runs]},
        )
    study.write(
        root / "joint-correction" / "complete.json",
        {"amendment_sha256": study.digest_json(amendment), "folds": len(selections)},
    )
    study.write(
        root / "phase-times" / f"joint-correction-{time.time_ns()}.json",
        {"phase": "joint-correction", "seconds": time.monotonic() - started},
    )


if __name__ == "__main__":
    main()
