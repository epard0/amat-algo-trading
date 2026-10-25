"""Broad, resumable research search through the bot's daily portfolio engine.

Native intraday views inform completed daily decisions. Exit rules retain the
existing close-trigger/next-window semantics; this is not a bracket fill engine.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import multiprocessing as mp
import os
import platform
import tempfile
from concurrent.futures import ProcessPoolExecutor
from importlib.metadata import version
from pathlib import Path
from typing import Any, get_args

import numpy as np

from amat.backtest.exits import ExitRules
from amat.data.snapshot import load_panel
from autoresearch.contracts import Candidate, Program
from autoresearch.experiments import Simulation
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import FeatureName, PatternDefinition
from autoresearch.genetic.mtf_replay import align_closed_values, prepare_replay, replay_prepared
from autoresearch.genetic.multitimeframe import aggregate_completed
from autoresearch.genetic.native_intraday import load_native_intraday
from autoresearch.genetic.native_views import native_panels, native_view_metadata
from autoresearch.genetic.shadow_inference import calibrate_values
from autoresearch.genetic.symbolic import evaluate_symbolic
from autoresearch.genetic.symbolic_study import _source_hashes
from autoresearch.intraday.engine import performance
from autoresearch.market_scenarios import panel_digest

TIMEFRAMES = ("15m", "1h", "1d", "1w", "1mo")
_CONTEXT: dict[str, Any] = {}
MIN_FILLS = 20
FOLD_COUNT = 4
MIN_RETURNS = 2


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pattern(method: str, pattern: PatternDefinition, timeframe: str) -> PatternDefinition:
    value = pattern.model_dump(mode="json", by_alias=True)
    value["timeframe"] = timeframe
    if method != "genetic_pattern":
        name, lookback = method.rsplit(":", 1)
        value["genome"] = {
            "kind": "symbolic_indicator",
            "outputTransform": "tanh",
            "root": {
                "node": "feature",
                "name": name,
                "source": "self",
                "lookback": int(lookback),
                "lag": 0,
                "transform": "rolling_zscore",
            },
        }
    return PatternDefinition.model_validate(value)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".unpublished-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, sort_keys=True, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _build_method(method: str) -> str:
    context = _CONTEXT
    destination = context["output"] / "features" / f"{method.replace(':', '-')}.npz"
    binding = destination.with_suffix(".json")
    if destination.exists():
        with np.load(destination, allow_pickle=False) as archive:
            metadata = json.loads(str(archive["_metadata"]))
            if (
                metadata["context_sha256"] != context["context_sha256"]
                or metadata["method"] != method
            ):
                raise ValueError("feature cache context/method mismatch")
        record = {**metadata, "sha256": _file_sha(destination)}
        if binding.exists() and json.loads(binding.read_text()) != record:
            raise ValueError("feature cache context/hash mismatch")
        if not binding.exists():
            # NPZ contains its binding; complete an interrupted sidecar publication.
            _atomic_json(binding, record)
        return method
    if binding.exists():
        raise ValueError("published feature binding has no array file")
    values: dict[str, Any] = {}
    availability = {}
    training = np.array(
        [
            context["measured_start"] <= day <= context["train_end"]
            for day in context["panel"].sessions
        ]
    )
    for timeframe, view in context["views"].items():
        pattern = _pattern(method, context["pattern"], timeframe)
        raw = evaluate_symbolic(pattern, view)
        source_days = [
            stamp.date() if isinstance(stamp, dt.datetime) else stamp for stamp in view.sessions
        ]
        reference_mask = np.array(
            [context["measured_start"] <= day <= context["train_end"] for day in source_days]
        )
        reference = raw[reference_mask]
        reference_stats = calibrate_values(reference, view.symbols) if len(reference) else {}
        aligned = align_closed_values(context["panel"], view, raw, timeframe=timeframe)
        counts = np.isfinite(aligned[training]).sum(axis=0)
        eligible = counts >= MIN_FILLS
        values[timeframe] = aligned.copy()
        values[timeframe][:, ~eligible] = np.nan
        availability[timeframe] = {
            "training_source_bar_count": len(reference),
            "training_finite_source_bar_counts": np.isfinite(reference).sum(axis=0).tolist(),
            "training_reference_stats": reference_stats,
            "reference_scope": "actual completed timeframe bars; no daily carry duplication",
            "finite_values": int(np.isfinite(values[timeframe]).sum()),
            "total_values": int(values[timeframe].size),
            "training_finite_daily_decision_counts": counts.tolist(),
            "training_eligible_assets": eligible.tolist(),
            "minimum_training_daily_decisions": MIN_FILLS,
            "eligibility_scope": "train only; daily rows are not independent timeframe bars",
            "unavailable_weight_policy": "zero contribution; no renormalization",
        }
    metadata = {
        "method": method,
        "context_sha256": context["context_sha256"],
        "availability": availability,
    }
    values["_metadata"] = np.asarray(json.dumps(metadata, sort_keys=True))
    descriptor, name = tempfile.mkstemp(prefix=".unpublished-", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            np.savez(handle, **values)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    _atomic_json(binding, {**metadata, "sha256": _file_sha(destination)})
    return method


def _dependencies() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        **{
            name: version(name)
            for name in (
                "numpy",
                "pandas",
                "pydantic",
                "exchange-calendars",
                "scipy",
                "scikit-learn",
                "polars",
            )
        },
    }


def _method_scope() -> dict[str, Any]:
    return {
        "searched": [
            "retained symbolic GP expression",
            "all registered symbolic feature terminals at 21/63 bars",
            "both feature polarities",
            "five-timeframe weights",
            "trend/volatility/rebalance/target-risk settings",
            "fixed stop/profit/RR/embargo overlays",
        ],
        "search_methods": ["seeded global random", "training-only adaptive local refinement"],
        "separate_protocol_required": [
            "new candle/symbolic GP evolution",
            "ridge learners and routers",
            "entry models",
            "contextual-bandit risk learning",
            "intraday bracket fills",
            "joint price-path Monte Carlo",
        ],
        "monte_carlo": (
            "paired realized-return block bootstrap only; "
            "no strategy re-execution or selection-adjusted confidence"
        ),
        "reason": (
            "Distinct fitting, candidate-schema, episode, or resampling contracts "
            "are not silently combined with this feature/exit search."
        ),
    }


def _baseline_recipe(baseline: Candidate) -> dict[str, Any]:
    strategy = baseline.config.strategy
    return {
        "method": "genetic_pattern",
        "polarity": 1,
        "exit_mode": "none",
        "stop_fraction": 0.05,
        "reward_risk": 2.0,
        "embargo": 21,
        "max_effect": 0.0,
        "risk_scalar": 1.0,
        "deadband": strategy.trend.deadband_abs,
        "trend_clip": strategy.trend.clip_abs,
        "horizons": list(strategy.trend.horizons_sessions),
        "fast_vol": strategy.volatility.fast_half_life_sessions,
        "slow_vol": strategy.volatility.slow_half_life_sessions,
        "slow_weight": strategy.volatility.slow_weight,
        "rebalance_delta": strategy.rebalance.min_weight_delta,
        "target_vol": strategy.constraints.target_annualized_volatility,
        **{f"weight_{frame}": float(frame == "1d") for frame in TIMEFRAMES},
    }


def _candidate(recipe: dict[str, Any], baseline: Candidate) -> Candidate:
    value = baseline.model_dump(mode="json")
    strategy = value["config"]["strategy"]
    strategy["trend"].update(
        deadband_abs=recipe["deadband"],
        clip_abs=recipe["trend_clip"],
        horizons_sessions=recipe["horizons"],
    )
    strategy["volatility"].update(
        fast_half_life_sessions=recipe["fast_vol"],
        slow_half_life_sessions=recipe["slow_vol"],
        slow_weight=recipe["slow_weight"],
    )
    strategy["rebalance"]["min_weight_delta"] = recipe["rebalance_delta"]
    strategy["constraints"]["target_annualized_volatility"] = recipe["target_vol"]
    return Candidate.model_validate(value)


def _exit_rules(recipe: dict[str, Any]) -> ExitRules | None:
    mode = recipe.get("exit_mode", "none")
    if mode not in {"none", "stop", "profit", "both"}:
        raise ValueError("unsupported exit mode")
    if mode == "none":
        return None
    stop = recipe["stop_fraction"]
    return ExitRules(
        stop_loss=stop if mode in {"stop", "both"} else None,
        take_profit=stop * recipe["reward_risk"] if mode in {"profit", "both"} else None,
        reentry_embargo_sessions=round(recipe["embargo"]),
    )


def _evaluate_task(task: tuple[dict[str, Any], str]) -> dict[str, Any]:
    return evaluate_recipe(*task)


def evaluate_recipe(recipe: dict[str, Any], phase: str, *, cost: float = 2) -> dict[str, Any]:
    """Evaluate a frozen recipe; explicit cost stress never changes the search objective."""
    context = _CONTEXT
    simulation = context["simulations"][phase]
    candidate = _candidate(recipe, context["baseline"])
    prepared = prepare_replay(simulation, candidate)
    values = {}
    weights = {}
    if recipe["max_effect"] > 0:
        method = recipe["method"]
        if method not in context["feature_cache"]:
            with np.load(
                context["output"] / "features" / f"{method.replace(':', '-')}.npz"
            ) as loaded:
                context["feature_cache"][method] = {tf: loaded[tf] for tf in TIMEFRAMES}
        values = {
            tf: array[: len(simulation.panel)] * recipe["polarity"]
            for tf, array in context["feature_cache"][method].items()
        }
        total = sum(recipe[f"weight_{tf}"] for tf in TIMEFRAMES)
        weights = {tf: recipe[f"weight_{tf}"] / total for tf in TIMEFRAMES} if total else {}
    result = replay_prepared(
        prepared,
        scores_by_tf=values,
        weights=weights,
        max_effect=recipe.get("max_effect", 0),
        exit_rules=_exit_rules(recipe),
        scalar=recipe.get("risk_scalar", 1),
        cost=cost,
    )
    equity = np.array([float(x) for x in result.equity_curve])
    dates = [str(x) for x in result.session_dates]
    returns = equity[1:] / equity[:-1] - 1
    dates = dates[1:]
    lower = context["phase_starts"][phase]
    selected = np.array([day > lower for day in dates])
    returns = returns[selected]
    metrics = performance(returns)
    fills = sum(len(row.fills) for row in result.sessions if str(row.session) > lower)
    folds = [
        performance(part)["sharpe"] for part in np.array_split(returns, FOLD_COUNT) if len(part) > 1
    ]
    # Fixed training consistency objective, including inactive configurations as ineligible.
    objective = (
        float(np.mean(folds) - 0.5 * np.std(folds) - 2 * abs(metrics["max_drawdown"]))
        if fills >= MIN_FILLS and len(folds) == FOLD_COUNT and not result.warnings
        else None
    )
    metrics.update(
        score=objective,
        cost_bps=cost,
        fills=fills,
        fold_sharpes=folds,
        phase=phase,
        warnings=list(result.warnings),
        eligibility_reason="eligible"
        if objective is not None
        else "insufficient fills/folds or runner warnings",
        returns=returns.tolist(),
        dates=[day for day, keep in zip(dates, selected, strict=True) if keep],
    )
    # Cache only reusable data, not thousands of candidate-specific full trace panels.
    simulation.traces.clear()
    simulation.overlays.clear()
    simulation.selections.clear()
    return metrics


def paired_return_bootstrap(
    candidate: list[float], baseline: list[float], *, seed: int, paths: int = 5000, block: int = 20
) -> dict[str, Any]:
    """Paired circular block bootstrap of realized returns, not synthetic price replay."""
    left, right = np.asarray(candidate), np.asarray(baseline)
    if (
        left.shape != right.shape
        or left.ndim != 1
        or len(left) < MIN_RETURNS
        or block < 1
        or paths < 1
    ):
        raise ValueError("paired bootstrap needs aligned nonempty daily returns")
    if (
        not np.isfinite(left).all()
        or not np.isfinite(right).all()
        or min(left.min(), right.min()) <= -1
    ):
        raise ValueError("bootstrap returns must be finite and above minus one")
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(paths):
        starts = rng.integers(len(left), size=int(np.ceil(len(left) / block)))
        indices = ((starts[:, None] + np.arange(block)) % len(left)).ravel()[: len(left)]
        deltas.append(float(np.prod(1 + left[indices]) - np.prod(1 + right[indices])))
    return {
        "schema_version": "paired-realized-return-bootstrap/1",
        "seed": seed,
        "paths": paths,
        "block_sessions": block,
        "return_difference_quantiles": dict(
            zip(
                ("p05", "p50", "p95"),
                map(float, np.quantile(deltas, [0.05, 0.5, 0.95])),
                strict=True,
            )
        ),
        "fraction_above_baseline": float(np.mean(np.asarray(deltas) > 0)),
        "interpretation": (
            "conditional resampling of selected realized returns; "
            "not market-path strategy replay or selection-adjusted confidence"
        ),
    }


def _run_study(
    artifact: Path,
    snapshot: Path,
    native_data: Path,
    *,
    output: Path,
    measured_start: dt.date,
    train_end: dt.date,
    validation_end: dt.date,
    global_trials: int = 2048,
    local_trials: int = 256,
    workers: int = 6,
    seed: int = 20260929,
) -> dict[str, Any]:
    """Freeze data/source identity and run broad historical research, never promotion."""
    from autoresearch.genetic.parameter_search import run_parameter_search  # noqa: PLC0415

    if not measured_start < train_end < validation_end or workers < 1:
        raise ValueError("ordered dates and positive worker count required")
    output.mkdir(parents=True, exist_ok=True)
    old = json.loads(artifact.read_text())
    if digest_json(old["payload"]) != old["payload_sha256"]:
        raise ValueError("source artifact checksum mismatch")
    payload = old["payload"]
    # Explicit expression reuse: current research contract has no inherited approval.
    pattern = PatternDefinition.model_validate(payload["candidate"]["genetic_pattern"]["pattern"])
    baseline = Candidate.model_validate({"config": payload["candidate"]["config"]})
    panel, manifest = load_panel(snapshot, payload["universe"], with_context=True)
    if any(day not in panel.sessions for day in (measured_start, train_end, validation_end)):
        raise ValueError("study boundaries must be observed exchange sessions")
    if not panel.sessions[0] < measured_start or not validation_end < panel.sessions[-1]:
        raise ValueError("snapshot must cover warmup, training, validation and later test")
    native = load_native_intraday(native_data)
    if (
        native["symbols"] != payload["universe"]
        or dt.date.fromisoformat(native["end"]) < panel.sessions[-1]
    ):
        raise ValueError("native universe/coverage does not cover the study")
    if dt.date.fromisoformat(native["start"]) >= measured_start:
        raise ValueError("native history must precede measurement")
    as_of = dt.datetime.combine(panel.sessions[-1] + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC)
    methods = [
        "genetic_pattern",
        *(f"{name}:{lookback}" for name in get_args(FeatureName) for lookback in (21, 63)),
    ]
    metadata = native_view_metadata(native, as_of)
    context = {
        "schema_version": "mtf-parameter-study/1",
        "source_sha256": _source_hashes(),
        "artifact_file_sha256": _file_sha(artifact),
        "snapshot_panel_sha256": panel_digest(panel),
        "snapshot_manifest_sha256": _file_sha(snapshot / "manifest.json"),
        "snapshot_partitions": manifest.partition_sha256,
        "dependencies": _dependencies(),
        "native_data_sha256": _file_sha(native_data),
        "native_contract": metadata,
        "measured_start": str(measured_start),
        "train_end": str(train_end),
        "validation_end": str(validation_end),
        "test_end": str(panel.sessions[-1]),
        "methods": methods,
        "cost_bps": 2,
        "execution": "daily close trigger; next-session window",
        "validation_status": "previously accessed history; retrospective research only",
        "postselection_limits": (
            "global and adaptive trials use train only; "
            "validation selects finalists; test opened after selection; "
            "all periods historically accessed, not pristine prospective evidence"
        ),
        "objective": (
            "mean four chronological fold Sharpes minus half their standard deviation "
            "minus twice absolute drawdown; at least 20 fills, "
            "four measurable folds and no runner warnings"
        ),
        "method_scope": _method_scope(),
        "execution_authority": "none",
        "promotion_authority": "none",
    }
    context_sha = digest_json(context)
    context_path = output / "context.json"
    if context_path.exists() and json.loads(context_path.read_text()) != context:
        raise ValueError("study context changed; use a new output directory")
    context_path.write_text(json.dumps(context, indent=2) + "\n")
    (output / "features").mkdir(exist_ok=True)
    views = {
        **native_panels(native, as_of),
        **{tf: aggregate_completed(panel, tf) for tf in ("1d", "1w", "1mo")},
    }
    _CONTEXT.clear()
    _CONTEXT.update(
        output=output,
        panel=panel,
        pattern=pattern,
        views=views,
        context_sha256=context_sha,
        train_end=train_end,
        measured_start=measured_start,
    )
    fork = mp.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=fork) as pool:
        for index, method in enumerate(pool.map(_build_method, methods)):
            print(f"feature bank {index + 1}/{len(methods)}: {method}", flush=True)  # noqa: T201
    program = Program(
        schema_version="strategy-development/9",
        name="mtf-parameter-study",
        load_start=panel.sessions[0],
        measured_start=measured_start,
        train_end=train_end,
        validation_end=validation_end,
        holdout_after=validation_end,
        baseline=baseline,
        frozen_files={},
        dependencies={},
    )
    simulations = {}
    for phase, end in (
        ("train", train_end),
        ("validation", validation_end),
        ("test", panel.sessions[-1]),
    ):
        partial, _ = load_panel(snapshot, payload["universe"], end=end, with_context=True)
        simulations[phase] = Simulation(partial, program, snapshot)
    _CONTEXT.update(
        baseline=baseline,
        simulations=simulations,
        feature_cache={},
        phase_starts={
            "train": str(measured_start),
            "validation": str(train_end),
            "test": str(validation_end),
        },
    )
    spec = {
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
            **{f"weight_{tf}": [0, 1] for tf in TIMEFRAMES},
        },
        "categorical_domains": {
            "method": methods,
            "polarity": [-1, 1],
            "exit_mode": ["none", "stop", "profit", "both"],
            "fast_vol": [5, 10, 20, 40],
            "slow_vol": [45, 60],
            "horizons": [[5, 21, 63], [21, 63, 126], [21, 63, 252], [63, 126, 252]],
        },
        "baselines": [_baseline_recipe(baseline)],
    }
    count = 0
    with ProcessPoolExecutor(max_workers=workers, mp_context=fork) as pool:

        def evaluate_many(recipes: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
            nonlocal count
            results = list(pool.map(_evaluate_task, [(recipe, phase) for recipe in recipes]))
            count += len(results)
            print(f"{phase}: {count} completed evaluations this run", flush=True)  # noqa: T201
            return results

        return run_parameter_search(
            spec,
            lambda recipe, phase: _evaluate_task((recipe, phase)),
            output / "search",
            evaluate_many=evaluate_many,
        )


def run_study(
    artifact: Path,
    snapshot: Path,
    native_data: Path,
    *,
    output: Path,
    measured_start: dt.date,
    train_end: dt.date,
    validation_end: dt.date,
    global_trials: int = 2048,
    local_trials: int = 256,
    workers: int = 6,
    seed: int = 20260929,
) -> dict[str, Any]:
    """Serialize context, feature publication, and adaptive trial recording."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".study.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("study already has an active writer") from exc
        try:
            return _run_study(
                artifact,
                snapshot,
                native_data,
                output=output,
                measured_start=measured_start,
                train_end=train_end,
                validation_end=validation_end,
                global_trials=global_trials,
                local_trials=local_trials,
                workers=workers,
                seed=seed,
            )
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
