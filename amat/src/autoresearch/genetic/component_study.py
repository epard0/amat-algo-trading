"""Frozen matched component removals and execution-cost stress for P11 recipes."""

from __future__ import annotations

import datetime as dt
import fcntl
import math
import multiprocessing as mp
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from amat.data.snapshot import load_panel
from autoresearch.contracts import Candidate, Program
from autoresearch.experiments import Simulation
from autoresearch.genetic import parameter_study as prior
from autoresearch.genetic import walkforward_study as p11
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.symbolic_study import _source_hashes
from autoresearch.market_scenarios import panel_digest

CONTROLS = ("full", "no_mtf", "no_exits", "neither", "daily_only", "no_intraday")
PARITY_COST = 2.0
DEFAULT_COSTS = (PARITY_COST, 5.0, 10.0, 20.0)
_STATE: dict[str, Any] = {}


def component_recipe(recipe: dict[str, Any], control: str) -> dict[str, Any]:
    """Remove components while preserving all retained timeframe coefficients."""
    if control not in CONTROLS:
        raise ValueError("unknown component control")
    result = dict(recipe)
    if control in ("no_mtf", "neither"):
        result["max_effect"] = 0.0
    if control in ("no_exits", "neither"):
        result["exit_mode"] = "none"
    if control in ("daily_only", "no_intraday"):
        total = sum(recipe[f"weight_{frame}"] for frame in prior.TIMEFRAMES)
        retained = {"1d"} if control == "daily_only" else {"1d", "1w", "1mo"}
        for frame in prior.TIMEFRAMES:
            if frame not in retained:
                result[f"weight_{frame}"] = 0.0
        remaining = sum(result[f"weight_{frame}"] for frame in prior.TIMEFRAMES)
        # The existing evaluator normalizes weights. Shrink effect by removed mass
        # so the retained weight * effect is unchanged, including missing views.
        result["max_effect"] = recipe["max_effect"] * (remaining / total) if total else 0.0
    return result


def freeze_jobs(
    summary: dict[str, Any], baseline: dict[str, Any], costs: tuple[float, ...]
) -> list[dict[str, Any]]:
    """Use only already selected P11 outer recipes; never optimize on controls."""
    if (
        not costs
        or len(set(costs)) != len(costs)
        or PARITY_COST not in costs
        or any(not math.isfinite(cost) or cost < 0 for cost in costs)
    ):
        raise ValueError("costs must be unique finite nonnegative values including parity cost 2")
    jobs = []
    baseline_sha = digest_json(baseline)
    for item in summary["folds"]:
        search = item["search"]
        recipes = {baseline_sha: baseline}
        aliases: dict[str, list[str]] = {baseline_sha: ["baseline"]}
        for policy, recipe in search["selected_by_policy"].items():
            if recipe is None:
                raise ValueError("P11 selection incomplete")
            key = digest_json(recipe)
            if search["selected_recipe_sha256_by_policy"][policy] != key:
                raise ValueError("P11 selected recipe identity mismatch")
            recipes[key] = recipe
            aliases.setdefault(key, []).append(policy)
        expected = {
            row["recipe_sha256"]: row["metrics"]
            for row in search["test"]
            if row["status"] == "evaluated"
        }
        for key, recipe in recipes.items():
            if key not in expected:
                raise ValueError("P11 selected recipe lacks outer parity receipt")
            controls = ("full",) if key == baseline_sha else CONTROLS
            for control in controls:
                for cost in costs:
                    job = {
                        "fold": item["fold"],
                        "parent_recipe_sha256": key,
                        "profile_aliases": aliases[key],
                        "control": control,
                        "cost_bps": cost,
                        "recipe": component_recipe(recipe, control),
                        "p11_expected": expected[key]
                        if control == "full" and cost == PARITY_COST
                        else None,
                    }
                    jobs.append({**job, "job_sha256": digest_json(job)})
    return jobs


def verify_parity(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """Require exact daily returns and decision outcomes at the P11 cost."""
    keys = ("dates", "returns", "fills", "warnings", "annualized_return", "max_drawdown")
    if any(actual[key] != expected[key] for key in keys):
        raise ValueError("P11 full-recipe replay parity failed")


def _evaluate(job: dict[str, Any]) -> dict[str, Any]:
    recipe, fold = job["recipe"], job["fold"]
    cache = {}
    if recipe["max_effect"] > 0:
        arrays, binding = p11._load_method(recipe["method"])
        cache[recipe["method"]] = {
            frame: np.where(
                np.asarray(
                    binding["eligibility_by_fold"][fold["name"]][frame]["training_eligible_assets"],
                    dtype=bool,
                )[None, :],
                array,
                np.nan,
            )
            for frame, array in arrays.items()
        }
    original = prior._CONTEXT
    prior._CONTEXT = {
        "baseline": _STATE["baseline"],
        "output": _STATE["p11_study"],
        "feature_cache": cache,
        "simulations": {"test": _STATE["simulations"][fold["name"]]},
        "phase_starts": {"test": fold["validation_end"]},
    }
    try:
        metrics = prior.evaluate_recipe(recipe, "test", cost=job["cost_bps"])
    finally:
        prior._CONTEXT = original
    if job["p11_expected"] is not None:
        verify_parity(metrics, job["p11_expected"])
    return {
        "job_sha256": job["job_sha256"],
        "metrics": metrics,
        "p11_parity_verified": job["p11_expected"] is not None,
    }


def replay_key(job: dict[str, Any]) -> str:
    """Deduplicate effective recipes while retaining all reporting control labels."""
    recipe = dict(job["recipe"])
    if recipe["max_effect"] == 0:
        for key in ("method", "polarity", *(f"weight_{tf}" for tf in prior.TIMEFRAMES)):
            recipe.pop(key, None)
    if recipe["exit_mode"] == "none":
        for key in ("stop_fraction", "reward_risk", "embargo"):
            recipe.pop(key, None)
    return digest_json({"recipe": recipe, "fold": job["fold"], "cost": job["cost_bps"]})


def publish_record(
    output: Path, job: dict[str, Any], result: dict[str, Any], records: dict[str, Any]
) -> None:
    """Publish each immutable label receipt, sharing only identical replay outcomes."""
    if job["p11_expected"] is not None:
        verify_parity(result["metrics"], job["p11_expected"])
    record = {
        "job_sha256": job["job_sha256"],
        "metrics": result["metrics"],
        "p11_parity_verified": job["p11_expected"] is not None,
        "replay_sha256": replay_key(job),
    }
    p11._write(output / "receipts" / f"{job['job_sha256']}.json", record)
    records[job["job_sha256"]] = record


def _inputs(paths: dict[str, Path]) -> dict[str, str]:
    return {name: prior._file_sha(path) for name, path in sorted(paths.items())}


def _run(  # noqa: PLR0912, PLR0915 - ordered freeze, evaluation, and identity gates
    artifact: Path,
    snapshot: Path,
    native_data: Path,
    *,
    p11_study: Path,
    output: Path,
    workers: int,
    costs: tuple[float, ...],
) -> dict[str, Any]:
    context = p11._read(p11_study / "context.json")
    summary = p11._read(p11_study / "summary.json")
    if summary["context_sha256"] != digest_json(context):
        raise ValueError("P11 summary context mismatch")
    sources = _source_hashes()
    changed_sources = {
        name: {"p11": old, "current": sources.get(name)}
        for name, old in context["source_sha256"].items()
        if sources.get(name) != old
    }
    if set(changed_sources) - {"autoresearch/genetic/cli.py"}:
        raise ValueError("P11 numerical runtime changed")
    if prior._dependencies() != context["dependencies"]:
        raise ValueError("P11 numerical dependencies changed")
    payload = p11._read(artifact)
    baseline = Candidate.model_validate({"config": payload["candidate"]["config"]})
    baseline_recipe = prior._baseline_recipe(baseline)
    jobs = freeze_jobs(summary, baseline_recipe, costs)
    panel, manifest = load_panel(snapshot, payload["universe"], with_context=True)
    p11.validate_folds(context["folds"], panel.sessions)
    if (
        prior._file_sha(artifact) != context["artifact_sha256"]
        or prior._file_sha(native_data) != context["native_data_sha256"]
        or prior._file_sha(snapshot / "manifest.json") != context["snapshot_manifest_sha256"]
        or manifest.partition_sha256 != context["snapshot_partitions"]
        or panel_digest(panel) != context["panel_sha256"]
    ):
        raise ValueError("P11 input identity mismatch")
    if [item["fold"] for item in summary["folds"]] != context["folds"]:
        raise ValueError("P11 result fold registration mismatch")
    paths = {
        "artifact": artifact,
        "native_data": native_data,
        "snapshot_manifest": snapshot / "manifest.json",
        "p11_context": p11_study / "context.json",
        "p11_summary": p11_study / "summary.json",
        **{f"snapshot/{name}": snapshot / name for name in context["snapshot_partitions"]},
    }
    methods = sorted({job["recipe"]["method"] for job in jobs if job["recipe"]["max_effect"]})
    p11._STATE = {
        "output": p11_study,
        "context_sha256": digest_json(context),
        "feature_cache": OrderedDict(),
    }
    for method in methods:
        arrays, binding = p11._load_method(method)
        cache_path = p11_study / "features" / f"{method.replace(':', '-')}.npz"
        with np.load(cache_path, allow_pickle=False) as archive:
            embedded = p11._verify_archive(archive)
        if {key: value for key, value in binding.items() if key != "sha256"} != embedded:
            raise ValueError("P11 feature metadata disagrees with arrays")
        if binding["method"] != method or not binding["unmasked"]:
            raise ValueError("P11 feature binding mismatch")
        if any(array.shape != (len(panel), len(panel.symbols)) for array in arrays.values()):
            raise ValueError("P11 feature shape mismatch")
        stem = p11_study / "features" / method.replace(":", "-")
        paths[f"features/{method}/arrays"] = stem.with_suffix(".npz")
        paths[f"features/{method}/binding"] = stem.with_suffix(".json")
    registration = {
        "schema_version": "matched-component-study/1",
        "source_sha256": sources,
        "p11_source_differences": changed_sources,
        "dependencies": prior._dependencies(),
        "input_sha256": _inputs(paths),
        "p11_context_sha256": digest_json(context),
        "jobs": jobs,
        "controls": list(CONTROLS),
        "costs_bps": list(costs),
        "coefficient_policy": "removed weight mass stays neutral; retained coefficients unchanged",
        "scope": "frozen selections, full-history warm-state paired outer counterfactuals",
        "historical_access": "retrospective previously accessed history, overlapping training",
        "selection_authority": "none",
        "execution_authority": "none",
        "promotion_authority": "none",
    }
    p11._write(output / "registration.json", registration)
    simulations = {}
    for fold in context["folds"]:
        program = Program(
            schema_version="strategy-development/9",
            name=f"component-{fold['name']}",
            baseline=baseline,
            load_start=panel.sessions[0],
            measured_start=dt.date.fromisoformat(fold["measured_start"]),
            train_end=dt.date.fromisoformat(fold["train_end"]),
            validation_end=dt.date.fromisoformat(fold["validation_end"]),
            holdout_after=dt.date.fromisoformat(fold["validation_end"]),
            frozen_files={},
            dependencies={},
        )
        partial, _ = load_panel(
            snapshot,
            payload["universe"],
            end=dt.date.fromisoformat(fold["test_end"]),
            with_context=True,
        )
        simulations[fold["name"]] = Simulation(partial, program, snapshot)
    _STATE.update(baseline=baseline, p11_study=p11_study, simulations=simulations)
    records, pending = {}, []
    for job in jobs:
        path = output / "receipts" / f"{job['job_sha256']}.json"
        if path.exists():
            record = p11._read(path)
            if record["job_sha256"] != job["job_sha256"]:
                raise ValueError("component receipt job mismatch")
            if job["p11_expected"] is not None:
                verify_parity(record["metrics"], job["p11_expected"])
            records[job["job_sha256"]] = record
        else:
            pending.append(job)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for job in pending:
        grouped.setdefault(replay_key(job), []).append(job)
    known = {
        replay_key(job): records[job["job_sha256"]] for job in jobs if job["job_sha256"] in records
    }
    todo = [group[0] for key, group in grouped.items() if key not in known]
    if todo:
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork")) as pool:
            for index, (job, record) in enumerate(
                zip(todo, pool.map(_evaluate, todo), strict=True)
            ):
                known[replay_key(job)] = record
                for alias in grouped[replay_key(job)]:
                    publish_record(output, alias, record, records)
                print(f"component replay {index + 1}/{len(todo)} completed", flush=True)  # noqa: T201
    for group in grouped.values():
        for job in group:
            publish_record(output, job, known[replay_key(job)], records)
    if (
        _inputs(paths) != registration["input_sha256"]
        or _source_hashes() != registration["source_sha256"]
        or prior._dependencies() != registration["dependencies"]
    ):
        raise ValueError("component study inputs or runtime changed during execution")
    result = {
        "schema_version": "matched-component-results/1",
        "registration_sha256": digest_json(registration),
        "records": [{**job, **records[job["job_sha256"]]} for job in jobs],
        "job_count": len(jobs),
        "unique_replay_count": len({replay_key(job) for job in jobs}),
        "parity_count": sum(job["p11_expected"] is not None for job in jobs),
        "selection_authority": "none",
        "execution_authority": "none",
        "promotion_authority": "none",
    }
    p11._write(output / "summary.json", result)
    return result


def run_component_study(
    artifact: Path,
    snapshot: Path,
    native_data: Path,
    *,
    p11_study: Path,
    output: Path,
    workers: int = 6,
    costs: tuple[float, ...] = DEFAULT_COSTS,
) -> dict[str, Any]:
    """Run or exactly resume an independent immutable offline P12 study."""
    if workers < 1:
        raise ValueError("positive workers required")
    if (
        output.resolve() == p11_study.resolve()
        or p11_study.resolve() in output.resolve().parents
        or output.resolve() in p11_study.resolve().parents
    ):
        raise ValueError("output must be independent of P11 evidence")
    output.mkdir(parents=True, exist_ok=True)
    original = p11._STATE
    with (output / ".study.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("component study already has an active writer") from exc
        try:
            return _run(
                artifact,
                snapshot,
                native_data,
                p11_study=p11_study,
                output=output,
                workers=workers,
                costs=costs,
            )
        finally:
            p11._STATE = original
            _STATE.clear()
            fcntl.flock(lock, fcntl.LOCK_UN)
