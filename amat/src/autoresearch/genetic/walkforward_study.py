"""Fresh fold-local MTF research with separate historical outer evaluations."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import multiprocessing as mp
import os
import re
import tempfile
from collections import Counter, OrderedDict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, get_args

import numpy as np
from pydantic import ValidationError

from amat.backtest.panel import MarketPanel
from amat.data.snapshot import load_panel
from amat.errors import ConfigurationError
from autoresearch.contracts import Candidate, Program
from autoresearch.experiments import Simulation
from autoresearch.genetic import parameter_study as prior
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import FeatureName, PatternDefinition
from autoresearch.genetic.mtf_replay import align_closed_values
from autoresearch.genetic.multitimeframe import aggregate_completed
from autoresearch.genetic.native_intraday import load_native_intraday
from autoresearch.genetic.native_views import native_panels, native_view_metadata
from autoresearch.genetic.shadow_inference import calibrate_values
from autoresearch.genetic.symbolic import evaluate_symbolic
from autoresearch.genetic.symbolic_study import _source_hashes
from autoresearch.market_scenarios import panel_digest

TIMEFRAMES = prior.TIMEFRAMES
MIN_TRAINING_ROWS = 20
FEATURE_CACHE_LIMIT = 4
MATRIX_DIMENSIONS = 2
EXPOSURE_PROFILES = (0.25, 0.5, 0.75, 1.0)
_STATE: dict[str, Any] = {}


def _read(path: Path) -> dict[str, Any]:
    envelope = json.loads(path.read_text())
    if (
        set(envelope) != {"payload", "payload_sha256"}
        or digest_json(envelope["payload"]) != envelope["payload_sha256"]
    ):
        raise ValueError("walk-forward receipt checksum mismatch")
    return dict(envelope["payload"])


def _write(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        if _read(path) != payload:
            raise ValueError("immutable walk-forward receipt changed")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    prior._atomic_json(path, {"payload": payload, "payload_sha256": digest_json(payload)})


def validate_folds(folds: list[dict[str, str]], sessions: tuple[Any, ...]) -> list[dict[str, str]]:
    """Allow rolling training overlap, never overlapping outer return windows."""
    if not folds:
        raise ValueError("at least one walk-forward fold is required")
    result, names = [], set()
    previous_test: dt.date | None = None
    for fold in folds:
        if set(fold) != {"name", "measured_start", "train_end", "validation_end", "test_end"}:
            raise ValueError("unexpected fold fields")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", fold["name"]) or fold["name"] in names:
            raise ValueError("fold names must be unique path-safe identifiers")
        dates = [
            dt.date.fromisoformat(fold[key])
            for key in ("measured_start", "train_end", "validation_end", "test_end")
        ]
        if (
            any(day not in sessions for day in dates)
            or not sessions[0] < dates[0] < dates[1] < dates[2] < dates[3]
        ):
            raise ValueError("fold dates must be ordered observed sessions with prior warmup")
        if previous_test is not None and dates[2] < previous_test:
            raise ValueError("outer fold return windows overlap or are out of order")
        previous_test = dates[3]
        names.add(fold["name"])
        result.append(dict(fold))
    return result


def fold_feature_metadata(
    panel: MarketPanel,
    view: MarketPanel,
    raw: np.ndarray,
    aligned: np.ndarray,
    fold: dict[str, str],
) -> dict[str, Any]:
    """Freeze availability from that fold's training observations only."""
    lower, upper = (dt.date.fromisoformat(fold[key]) for key in ("measured_start", "train_end"))
    decisions = np.array([lower <= day <= upper for day in panel.sessions])
    counts = np.isfinite(aligned[decisions]).sum(axis=0)
    dates = [stamp.date() if isinstance(stamp, dt.datetime) else stamp for stamp in view.sessions]
    source_mask = np.array([lower <= day <= upper for day in dates])
    reference = raw[source_mask]
    return {
        "training_finite_daily_decision_counts": counts.tolist(),
        "training_eligible_assets": (counts >= MIN_TRAINING_ROWS).tolist(),
        "minimum_training_daily_decisions": MIN_TRAINING_ROWS,
        "training_source_bar_count": len(reference),
        "training_finite_source_bar_counts": np.isfinite(reference).sum(axis=0).tolist(),
        "training_reference_stats": calibrate_values(reference, view.symbols)
        if len(reference)
        else {},
        "reference_scope": "actual timeframe bars; carried daily values are not independent bars",
        "missing_weight_policy": "neutral; no weight renormalization for ineligible assets/views",
    }


def _verify_archive(archive: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = json.loads(str(archive["_metadata"]))
    if set(metadata["matrix_sha256"]) != set(TIMEFRAMES):
        raise ValueError("unmasked cache must bind every timeframe")
    shapes = {archive[frame].shape for frame in TIMEFRAMES}
    if len(shapes) != 1 or len(next(iter(shapes))) != MATRIX_DIMENSIONS:
        raise ValueError("unmasked timeframe matrices must share a two-dimensional shape")
    for frame, expected in metadata["matrix_sha256"].items():
        actual = hashlib.sha256(np.asarray(archive[frame], dtype="<f8").tobytes()).hexdigest()
        if actual != expected:
            raise ValueError("unmasked matrix checksum mismatch")
    return metadata


def _build_method(method: str) -> str:
    state = _STATE
    target = state["output"] / "features" / f"{method.replace(':', '-')}.npz"
    binding = target.with_suffix(".json")
    if target.exists():
        with np.load(target, allow_pickle=False) as archive:
            metadata = _verify_archive(archive)
        if metadata["context_sha256"] != state["context_sha256"] or metadata["method"] != method:
            raise ValueError("unmasked feature cache context mismatch")
        _write(binding, {**metadata, "sha256": prior._file_sha(target)})
        return method
    if binding.exists():
        raise ValueError("published feature binding is missing its arrays")
    arrays: dict[str, Any] = {}
    eligibility: dict[str, dict[str, Any]] = {fold["name"]: {} for fold in state["folds"]}
    for frame, view in state["views"].items():
        pattern = prior._pattern(method, state["pattern"], frame)
        raw = evaluate_symbolic(pattern, view)
        aligned = align_closed_values(state["panel"], view, raw, timeframe=frame)
        arrays[frame] = aligned
        for fold in state["folds"]:
            eligibility[fold["name"]][frame] = fold_feature_metadata(
                state["panel"], view, raw, aligned, fold
            )
    metadata = {
        "schema_version": "walkforward-unmasked-features/1",
        "context_sha256": state["context_sha256"],
        "method": method,
        "unmasked": True,
        "eligibility_by_fold": eligibility,
        "matrix_sha256": {
            frame: hashlib.sha256(np.asarray(values, dtype="<f8").tobytes()).hexdigest()
            for frame, values in arrays.items()
        },
    }
    arrays["_metadata"] = np.asarray(json.dumps(metadata, sort_keys=True, allow_nan=False))
    descriptor, name = tempfile.mkstemp(prefix=".unpublished-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            np.savez(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    _write(binding, {**metadata, "sha256": prior._file_sha(target)})
    return method


def _load_method(method: str) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    cache: OrderedDict[str, Any] = _STATE["feature_cache"]
    if method not in cache:
        path = _STATE["output"] / "features" / f"{method.replace(':', '-')}.npz"
        binding = _read(path.with_suffix(".json"))
        if (
            binding["sha256"] != prior._file_sha(path)
            or binding["context_sha256"] != _STATE["context_sha256"]
        ):
            raise ValueError("unmasked feature cache hash mismatch")
        with np.load(path, allow_pickle=False) as archive:
            _verify_archive(archive)
            arrays = {frame: archive[frame] for frame in TIMEFRAMES}
        cache[method] = arrays, binding
        if len(cache) > FEATURE_CACHE_LIMIT:
            cache.popitem(last=False)
    cache.move_to_end(method)
    arrays, binding = cache[method]
    return arrays, binding


def _evaluate_task(task: tuple[dict[str, Any], str, str]) -> dict[str, Any]:
    recipe, fold_name, phase = task
    fold = _STATE["fold_by_name"][fold_name]
    try:
        prior._candidate(recipe, _STATE["baseline"])
        prior._exit_rules(recipe)
    except (ValidationError, ConfigurationError) as exc:
        return {
            "score": None,
            "status": "invalid_candidate",
            "reason": str(exc),
            "fold": fold_name,
            "phase": phase,
        }
    cache = {}
    if recipe["max_effect"] > 0:
        arrays, binding = _load_method(recipe["method"])
        masked = {}
        for frame, array in arrays.items():
            eligible = np.asarray(
                binding["eligibility_by_fold"][fold_name][frame]["training_eligible_assets"],
                dtype=bool,
            )
            masked[frame] = np.where(eligible[None, :], array, np.nan)
        cache[recipe["method"]] = masked
    # Each worker owns its module state; no P10 file or cached artifact is changed.
    original = prior._CONTEXT
    prior._CONTEXT = {
        "output": _STATE["output"],
        "baseline": _STATE["baseline"],
        "feature_cache": cache,
        "simulations": _STATE["simulations"][fold_name],
        "phase_starts": {
            "train": fold["measured_start"],
            "validation": fold["train_end"],
            "test": fold["validation_end"],
        },
    }
    try:
        metrics = prior.evaluate_recipe(recipe, phase)
        metrics["fold"] = fold_name
        metrics["replay_scope"] = (
            "full-history warm-state counterfactual; metrics restricted to fold phase"
        )
        return metrics
    finally:
        prior._CONTEXT = original


def _spec(  # noqa: PLR0917 - explicit frozen search inputs
    baseline: Candidate,
    methods: list[str],
    context_sha: str,
    seed: int,
    global_trials: int,
    local_trials: int,
) -> dict[str, Any]:
    return {
        "context_sha256": context_sha,
        "seed": seed,
        "global_trials": global_trials,
        "local_trials": local_trials,
        "top_k": 12,
        "batch_size": 32,
        "numeric_domains": {
            "stop_fraction": [0.005, 0.20],
            "reward_risk": [0.5, 8.0],
            "embargo": [1, 42],
            "max_effect": [0, 0.25],
            "risk_scalar": [0.25, 1],
            "deadband": [0.05, 0.5],
            "trend_clip": [0.5, 3],
            "slow_weight": [0.25, 1],
            "rebalance_delta": [0.005, 0.05],
            "target_vol": [0.08, 0.25],
            **{f"weight_{frame}": [0, 1] for frame in TIMEFRAMES},
        },
        "categorical_domains": {
            "method": methods,
            "polarity": [-1, 1],
            "exit_mode": ["none", "stop", "profit", "both"],
            "fast_vol": [5, 10, 20, 40],
            "slow_vol": [45, 60],
            "horizons": [[5, 21, 63], [21, 63, 126], [21, 63, 252], [63, 126, 252]],
        },
        "baselines": [prior._baseline_recipe(baseline)],
    }


def _range(values: list[float]) -> dict[str, float] | None:
    return {"min": min(values), "max": max(values)} if values else None


def summarize_folds(results: list[dict[str, Any]], baseline_sha: str) -> dict[str, Any]:
    """Compare separate folds; inactive placeholders never count as stable signals."""
    from autoresearch.genetic.walkforward_search import method_family  # noqa: PLC0415

    policies: dict[str, Any] = {}
    for policy in ("balanced", "return", "defensive"):
        observations, recipes, active, families = [], [], [], []
        omitted_folds = []
        coefficients: dict[str, list[float]] = {frame: [] for frame in TIMEFRAMES}
        baseline_count = 0
        for item in results:
            summary = item["search"]
            chosen = summary["selected_recipe_sha256_by_policy"][policy]
            records = {
                record["recipe_sha256"]: record
                for record in summary["test"]
                if record["status"] == "evaluated"
            }
            if chosen not in records or baseline_sha not in records:
                omitted_folds.append(
                    {"fold": item["name"], "reason": "no selected or baseline outer metrics"}
                )
                continue
            selected, baseline = records[chosen]["metrics"], records[baseline_sha]["metrics"]
            recipe = summary["selected_by_policy"][policy]
            recipes.append(recipe)
            baseline_count += chosen == baseline_sha
            eligibility = item.get("selected_feature_eligibility", {}).get(policy) or {}
            total = sum(recipe[f"weight_{frame}"] for frame in TIMEFRAMES)
            effective = {}
            for frame in TIMEFRAMES:
                eligible_count = sum(eligibility.get(frame, {}).get("training_eligible_assets", []))
                coefficient = (
                    recipe["max_effect"] * recipe[f"weight_{frame}"] / total if total else 0.0
                )
                effective[frame] = {
                    "nominal_coefficient": coefficient,
                    "eligible_assets": eligible_count,
                    "active_coefficient": coefficient if eligible_count else 0.0,
                }
                if coefficient > 0 and eligible_count:
                    coefficients[frame].append(coefficient)
            feature_active = any(row["active_coefficient"] > 0 for row in effective.values())
            if feature_active:
                active.append(recipe)
            family = method_family(recipe) if feature_active else "portfolio_only"
            families.append(family)
            observations.append(
                {
                    "fold": item["name"],
                    "selected_recipe_sha256": chosen,
                    "selected_baseline": chosen == baseline_sha,
                    "active_feature_family": family,
                    "feature_coefficients": effective,
                    "annualized_return_delta": selected["annualized_return"]
                    - baseline["annualized_return"],
                    "total_return_delta": selected["total_return"] - baseline["total_return"],
                    "drawdown_magnitude_delta": abs(selected["max_drawdown"])
                    - abs(baseline["max_drawdown"]),
                }
            )
        policies[policy] = {
            "fold_comparisons": observations,
            "evaluated_fold_count": len(observations),
            "registered_fold_count": len(results),
            "omitted_fold_count": len(omitted_folds),
            "omitted_folds": omitted_folds,
            "selected_baseline_count": baseline_count,
            "annualized_return_win_count": sum(
                row["annualized_return_delta"] > 0 for row in observations
            ),
            "lower_drawdown_count": sum(
                row["drawdown_magnitude_delta"] < 0 for row in observations
            ),
            "median_annualized_return_delta": float(
                np.median([row["annualized_return_delta"] for row in observations])
            )
            if observations
            else None,
            "median_drawdown_magnitude_delta": float(
                np.median([row["drawdown_magnitude_delta"] for row in observations])
            )
            if observations
            else None,
            "active_method_counts": dict(Counter(recipe["method"] for recipe in active)),
            "active_family_counts": dict(Counter(families)),
            "selected_exit_mode_counts": dict(Counter(recipe["exit_mode"] for recipe in recipes)),
            "active_timeframe_coefficient_ranges": {
                frame: _range(values) for frame, values in coefficients.items()
            },
            "active_exit_parameter_ranges": {
                "stop_threshold": _range(
                    [
                        recipe["stop_fraction"]
                        for recipe in recipes
                        if recipe["exit_mode"] in ("stop", "both")
                    ]
                ),
                "profit_threshold": _range(
                    [
                        recipe["stop_fraction"] * recipe["reward_risk"]
                        for recipe in recipes
                        if recipe["exit_mode"] in ("profit", "both")
                    ]
                ),
                "reward_risk": _range(
                    [recipe["reward_risk"] for recipe in recipes if recipe["exit_mode"] == "both"]
                ),
                "embargo": _range(
                    [
                        round(recipe["embargo"])
                        for recipe in recipes
                        if recipe["exit_mode"] != "none"
                    ]
                ),
            },
            "portfolio_parameter_ranges": {
                key: _range([recipe[key] for recipe in recipes])
                for key in (
                    "risk_scalar",
                    "target_vol",
                    "deadband",
                    "trend_clip",
                    "rebalance_delta",
                )
            },
        }
    return {
        "policies": policies,
        "interpretation": (
            "foldwise descriptive stability; no joined equity or pooled significance, "
            "or post-test policy selection. Return policy has no extra drawdown dominance gate. "
            "Inactive feature/exit placeholders and entirely unavailable views are excluded."
        ),
    }


def _verify_identity(
    context: dict[str, Any], artifact: Path, snapshot: Path, native_data: Path
) -> None:
    if (
        _source_hashes() != context["source_sha256"]
        or prior._dependencies() != context["dependencies"]
    ):
        raise ValueError("walk-forward runtime changed during execution")
    identities = (
        (artifact, context["artifact_sha256"]),
        (native_data, context["native_data_sha256"]),
        (snapshot / "manifest.json", context["snapshot_manifest_sha256"]),
    )
    identities += tuple(
        (snapshot / name, value) for name, value in context["snapshot_partitions"].items()
    )
    if any(prior._file_sha(path) != expected for path, expected in identities):
        raise ValueError("walk-forward data changed during execution")


def _run(  # noqa: PLR0915 - explicit immutable registration and fold phases
    artifact: Path,
    snapshot: Path,
    native_data: Path,
    *,
    output: Path,
    folds: list[dict[str, str]],
    global_trials: int,
    local_trials: int,
    workers: int,
    seed: int,
) -> dict[str, Any]:
    from autoresearch.genetic.walkforward_search import run_walkforward_search  # noqa: PLC0415

    if workers < 1 or global_trials < 0 or local_trials < 0:
        raise ValueError("positive workers and nonnegative trial budgets required")
    payload = _read(artifact)
    baseline = Candidate.model_validate({"config": payload["candidate"]["config"]})
    pattern = PatternDefinition.model_validate(payload["candidate"]["genetic_pattern"]["pattern"])
    panel, manifest = load_panel(snapshot, payload["universe"], with_context=True)
    folds = validate_folds(folds, panel.sessions)
    native = load_native_intraday(native_data)
    if (
        native["symbols"] != payload["universe"]
        or dt.date.fromisoformat(native["start"])
        >= min(dt.date.fromisoformat(fold["measured_start"]) for fold in folds)
        or dt.date.fromisoformat(native["end"])
        < max(dt.date.fromisoformat(fold["test_end"]) for fold in folds)
    ):
        raise ValueError(
            "native data must cover the ordered universe and every fold with prior history"
        )
    as_of = dt.datetime.combine(panel.sessions[-1] + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC)
    methods = [
        "genetic_pattern",
        *(f"{name}:{lookback}" for name in get_args(FeatureName) for lookback in (21, 63)),
    ]
    context = {
        "schema_version": "walkforward-mtf-study/1",
        "folds": folds,
        "source_sha256": _source_hashes(),
        "dependencies": prior._dependencies(),
        "artifact_sha256": prior._file_sha(artifact),
        "snapshot_manifest_sha256": prior._file_sha(snapshot / "manifest.json"),
        "snapshot_partitions": manifest.partition_sha256,
        "panel_sha256": panel_digest(panel),
        "native_data_sha256": prior._file_sha(native_data),
        "native_contract": native_view_metadata(native, as_of),
        "methods": methods,
        "global_trials_per_fold": global_trials,
        "local_trials_per_fold": local_trials,
        "seed": seed,
        "cost_bps": 2,
        "minimum_training_finite_decisions": MIN_TRAINING_ROWS,
        "fixed_exposure_profiles": list(EXPOSURE_PROFILES),
        "exposure_control_scope": "fixed scalars; not realized-volatility or exposure matching",
        "global_portfolio_control_slots": "every eighth global proposal has max_effect zero",
        "method_scope": prior._method_scope(),
        "replay_scope": "full-history warm-state counterfactuals; no strategy switching portfolio",
        "historical_access": "previously accessed retrospective history; no pristine holdout claim",
        "execution_authority": "none",
        "promotion_authority": "none",
    }
    _write(output / "context.json", context)
    context_sha = digest_json(context)
    (output / "features").mkdir(exist_ok=True)
    views = {
        **native_panels(native, as_of),
        **{frame: aggregate_completed(panel, frame) for frame in ("1d", "1w", "1mo")},
    }
    _STATE.clear()
    _STATE.update(
        output=output,
        panel=panel,
        pattern=pattern,
        views=views,
        folds=folds,
        context_sha256=context_sha,
    )
    fork = mp.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=fork) as pool:
        for index, method in enumerate(pool.map(_build_method, methods)):
            print(f"walk-forward feature bank {index + 1}/{len(methods)}: {method}", flush=True)  # noqa: T201
    simulations: dict[str, dict[str, Simulation]] = {}
    for fold in folds:
        program = Program(
            schema_version="strategy-development/9",
            name=f"walkforward-{fold['name']}",
            baseline=baseline,
            load_start=panel.sessions[0],
            measured_start=dt.date.fromisoformat(fold["measured_start"]),
            train_end=dt.date.fromisoformat(fold["train_end"]),
            validation_end=dt.date.fromisoformat(fold["validation_end"]),
            holdout_after=dt.date.fromisoformat(fold["validation_end"]),
            frozen_files={},
            dependencies={},
        )
        simulations[fold["name"]] = {}
        for phase, field in (
            ("train", "train_end"),
            ("validation", "validation_end"),
            ("test", "test_end"),
        ):
            partial, _ = load_panel(
                snapshot,
                payload["universe"],
                end=dt.date.fromisoformat(fold[field]),
                with_context=True,
            )
            simulations[fold["name"]][phase] = Simulation(partial, program, snapshot)
    _STATE.update(
        baseline=baseline,
        simulations=simulations,
        feature_cache=OrderedDict(),
        fold_by_name={fold["name"]: fold for fold in folds},
    )
    results = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=fork) as pool:
        for index, fold in enumerate(folds):
            name = fold["name"]

            def evaluate_many(
                recipes: list[dict[str, Any]], phase: str, current: str = name
            ) -> list[dict[str, Any]]:
                records = list(
                    pool.map(_evaluate_task, [(recipe, current, phase) for recipe in recipes])
                )
                print(f"{current} {phase}: {len(records)} completed", flush=True)  # noqa: T201
                return records

            spec = _spec(
                baseline,
                methods,
                digest_json({"context_sha256": context_sha, "fold": fold}),
                seed + index,
                global_trials,
                local_trials,
            )

            def evaluate(recipe: dict[str, Any], phase: str, current: str = name) -> dict[str, Any]:
                return _evaluate_task((recipe, current, phase))

            summary = run_walkforward_search(
                spec,
                evaluate,
                output / "folds" / name,
                evaluate_many=evaluate_many,
            )
            control_path = output / "folds" / name / "exposure-controls.json"
            if control_path.exists():
                controls = _read(control_path)
            else:
                recipes = [
                    {**prior._baseline_recipe(baseline), "risk_scalar": scalar}
                    for scalar in EXPOSURE_PROFILES
                ]
                metrics = evaluate_many(recipes, "test")
                controls = {
                    "reporting_only": True,
                    "selection_authority": "none",
                    "profiles": [
                        {"risk_scalar": scalar, "metrics": row}
                        for scalar, row in zip(EXPOSURE_PROFILES, metrics, strict=True)
                    ],
                }
                _write(control_path, controls)
            selected_eligibility: dict[str, Any] = {}
            for policy, recipe in summary["selected_by_policy"].items():
                if recipe is None or recipe["max_effect"] == 0:
                    selected_eligibility[policy] = None
                else:
                    binding_path = (
                        output / "features" / f"{recipe['method'].replace(':', '-')}.json"
                    )
                    selected_eligibility[policy] = _read(binding_path)["eligibility_by_fold"][name]
            results.append(
                {
                    "name": name,
                    "fold": fold,
                    "search": summary,
                    "fixed_exposure_controls": controls,
                    "selected_feature_eligibility": selected_eligibility,
                }
            )
    result = {
        "schema_version": "walkforward-mtf-results/1",
        "context_sha256": context_sha,
        "folds": results,
        "stability": summarize_folds(results, digest_json(prior._baseline_recipe(baseline))),
        "execution_authority": "none",
        "promotion_authority": "none",
    }
    _verify_identity(context, artifact, snapshot, native_data)
    _write(output / "summary.json", result)
    return result


def run_walkforward_study(
    artifact: Path,
    snapshot: Path,
    native_data: Path,
    *,
    output: Path,
    folds: list[dict[str, str]],
    global_trials: int = 640,
    local_trials: int = 128,
    workers: int = 6,
    seed: int = 20260929,
) -> dict[str, Any]:
    """Serialize fresh feature publication and all registered rolling-fold searches."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".study.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("walk-forward study already has an active writer") from exc
        try:
            return _run(
                artifact,
                snapshot,
                native_data,
                output=output,
                folds=folds,
                global_trials=global_trials,
                local_trials=local_trials,
                workers=workers,
                seed=seed,
            )
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
