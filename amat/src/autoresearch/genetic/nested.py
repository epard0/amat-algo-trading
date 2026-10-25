"""Frozen whole-search historical walk-forward validation; never promotion authority."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.data.action_exceptions import default_ledger_path
from amat.data.snapshot import load_panel
from autoresearch.calendar_audit import audit_sessions
from autoresearch.contracts import Candidate
from autoresearch.genetic.data import digest_json, load_daily
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.nested_replay import evaluate_fold
from autoresearch.genetic.nested_search import discover_fold
from autoresearch.genetic.replay import _check_incumbent
from autoresearch.genetic.study import _runtime_versions, write_json
from autoresearch.genetic.symbolic import SymbolicEvaluator, symbolic_registry_manifest
from autoresearch.genetic.symbolic_search import SymbolicSearchProtocol
from autoresearch.genetic.symbolic_study import SymbolicScreenConfig, _source_hashes
from autoresearch.genetic.validation import (
    ValidationProtocol,
    make_null_mapping,
    paired_return_uncertainty,
    search_null_test,
)
from autoresearch.market_scenarios import panel_digest

VALIDATION_VERSION = "genetic-nested-validation/1"
METHODS = ("genetic", "random")
MAX_TOTAL_SLOTS = 2_000_000
MAX_MAPPING_CELLS = 20_000_000
ALPHA = 0.05
MIN_OUTER_FOLDS = 2
EFFECT_THRESHOLD = 1e-12
LIMITATIONS = [
    "Historical development data, not a pristine holdout; incumbent was already researched.",
    (
        "Each fold replays from load_start and inherits hypothetical "
        "pattern holdings; switching from incumbent holdings is unassessed."
    ),
    (
        "Synchronized circular feature-time null preserves prices and "
        "joint feature rows, but not feature/return or volatility-regime alignment."
    ),
    (
        "Null signals may use later rows within their own split and are "
        "offline counterfactuals, never executable strategies."
    ),
    (
        "Bootstrap uncertainty is conditional on selected patterns, not a "
        "substitute for whole-search nulls."
    ),
    (
        "Fixed incumbent, grammar, seeds and shortlist budget; broader "
        "researcher choices are outside this null family."
    ),
    (
        "Held-out asset-group discovery, a pristine final holdout and "
        "shadow inference remain unassessed."
    ),
]


def panel_prefix(panel: MarketPanel, end: dt.date) -> MarketPanel:
    """Copy every time-aligned input, preserving optional fields and symbol order."""
    stop = panel.sessions.index(end) + 1
    arrays: dict[str, Any] = {
        field.name: value[:stop].copy()
        for field in fields(panel)
        if isinstance(value := getattr(panel, field.name), np.ndarray)
    }
    return replace(panel, sessions=panel.sessions[:stop], **arrays)


def _file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _artifacts(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path != directory / "completed.json"
    }


def _completed(directory: Path) -> dict[str, Any] | None:
    path = directory / "completed.json"
    if not path.exists():
        return None
    wrapper = json.loads(path.read_text())
    if wrapper["sha256"] != digest_json(wrapper["result"]):
        raise ValueError("completed fold checksum mismatch")
    if wrapper["artifacts"] != _artifacts(directory):
        raise ValueError("completed fold evidence changed")
    return dict(wrapper["result"])


def _prepare(
    panel: MarketPanel,
    snapshot: Path,
    start: dt.date,
    validation: ValidationProtocol,
    search: SymbolicSearchProtocol,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    symbols = tuple(str(symbol) for symbol in panel.symbols)
    prepared, identity = [], {}
    for fold in validation.folds:
        if not start < fold.fit_end:
            raise ValueError("load start must precede every fit end")
        for end in (fold.fit_end, fold.selection_end, fold.evaluation_end):
            if end not in panel.sessions:
                raise ValueError(f"fold endpoint {end} is not an available session")
        outer_count = sum(fold.selection_end < day <= fold.evaluation_end for day in panel.sessions)
        if outer_count < max(4, validation.block_sessions):
            raise ValueError("outer fold is too short for the registered bootstrap block")
        full = panel_prefix(panel, fold.evaluation_end)
        fit = panel_prefix(full, fold.fit_end)
        dataset = load_daily(snapshot, symbols, start=start, end=fold.fit_end)
        evaluator = SymbolicEvaluator(full)
        common = np.ones(len(full), dtype=bool)
        for terminal in search.terminals():
            common &= np.isfinite(evaluator.feature(terminal)).all(axis=1)
        # No future label-validity mask is used for portfolio decisions.
        fit_stop = len(fit)
        prepared.append(
            {
                "fold": fold,
                "panel": full,
                "fit_panel": fit,
                "dataset": dataset,
                "common": common,
                "fit_stop": fit_stop,
            }
        )
        identity[fold.name] = {
            "fit_dataset_sha256": dataset.digest,
            "fit_panel_sha256": panel_digest(fit),
            "full_panel_sha256": panel_digest(full),
            "common_feature_calendar_sha256": digest_json(common.tolist()),
            "feature_available_dates": int(common.sum()),
            "label_policy": (
                "decision t, entry t+1, exit t+2; fit-only arrays purge every crossing label"
            ),
            "last_possible_fit_label_decision": fit.sessions[-3].isoformat(),
            "fit_end": fold.fit_end.isoformat(),
            "selection_end": fold.selection_end.isoformat(),
            "evaluation_end": fold.evaluation_end.isoformat(),
        }
    return prepared, identity


def _mappings(prepared: list[dict[str, Any]], validation: ValidationProtocol) -> dict[str, Any]:
    if (
        sum(len(item["panel"]) for item in prepared) * validation.null_replicates
        > MAX_MAPPING_CELLS
    ):
        raise ValueError("null mapping budget exceeds twenty million cells")
    maps: dict[str, Any] = {}
    for replicate in range(validation.null_replicates):
        for index, item in enumerate(prepared):
            rng = np.random.default_rng(
                np.random.SeedSequence([validation.null_seed, replicate, index])
            )
            indices, metadata = make_null_mapping(
                item["panel"].sessions, item["common"], item["fold"], rng
            )
            maps[f"null-{replicate:04d}/{item['fold'].name}"] = {
                "indices": indices.tolist(),
                "metadata": metadata,
                "sha256": digest_json(indices.tolist()),
            }
    return maps


def _assert_frozen(frozen: dict[str, Any], exception: Path) -> None:
    if _source_hashes() != frozen["source_sha256"] or _runtime_versions() != frozen["runtime"]:
        raise ValueError("validation source or runtime changed; report incomplete")
    if _file_hash(exception) != frozen["action_exceptions_sha256"]:
        raise ValueError("corporate-action exceptions changed; report incomplete")


def _run_job(  # noqa: PLR0917 - one frozen fold transaction
    item: dict[str, Any],
    incumbent: Candidate,
    snapshot: Path,
    validation: ValidationProtocol,
    search: SymbolicSearchProtocol,
    screen: SymbolicScreenConfig,
    directory: Path,
    contract_hash: str,
    mapping: np.ndarray | None,
    *,
    diagnostics: bool,
) -> dict[str, Any]:
    previous = _completed(directory)
    if previous is not None:
        return previous
    original_panel_hash = panel_digest(item["panel"])
    stop = item["fit_stop"]
    discovery = discover_fold(
        item["dataset"],
        item["fit_panel"],
        search,
        screen,
        seeds=validation.seeds,
        shortlist_size=validation.shortlist_size,
        output=directory / "discovery",
        contract_hash=contract_hash,
        feature_mapping=None if mapping is None else mapping[:stop],
        common_rows=item["common"][:stop],
    )
    contenders = {
        method: [parse_pattern(pattern) for pattern in discovery["contenders"][method]]
        for method in METHODS
    }
    fold = item["fold"]
    replay = evaluate_fold(
        incumbent,
        item["panel"],
        snapshot,
        fit_end=fold.fit_end,
        selection_end=fold.selection_end,
        output=directory / "portfolio",
        contract_hash=contract_hash,
        contenders=contenders,
        feature_mapping=mapping,
        common_rows=item["common"],
        diagnostics=diagnostics,
    )
    if panel_digest(item["panel"]) != original_panel_hash:
        raise ValueError("fold market panel changed during replay")
    result = {"fold": fold.name, "discovery": discovery, "portfolio": replay}
    write_json(
        directory / "completed.json",
        {
            "sha256": digest_json(result),
            "result": result,
            "artifacts": _artifacts(directory),
        },
    )
    return result


def _series(jobs: list[dict[str, Any]], method: str) -> tuple[np.ndarray, np.ndarray, list[int]]:
    candidates, baselines, lengths, dates = [], [], [], []
    for job in jobs:
        paired = job["portfolio"]["paired_returns"][method]
        candidate = np.asarray(paired["candidate"], dtype=float)
        baseline = np.asarray(paired["incumbent"], dtype=float)
        if candidate.shape != baseline.shape or candidate.ndim != 1 or not candidate.size:
            raise ValueError("outer paired returns require matching nonempty series")
        if not np.isfinite(candidate).all() or not np.isfinite(baseline).all():
            raise ValueError("outer returns contain nonfinite values")
        if len(paired["dates"]) != len(candidate):
            raise ValueError("outer return dates do not align")
        dates.extend(paired["dates"])
        candidates.append(candidate)
        baselines.append(baseline)
        lengths.append(len(candidate))
    if dates != sorted(set(dates)):
        raise ValueError("outer dates overlap or are not chronological")
    return np.concatenate(candidates), np.concatenate(baselines), lengths


def _aggregate(  # noqa: PLR0912 - explicit predeclared support gates
    observed: list[dict[str, Any]],
    nulls: list[list[dict[str, Any]]],
    validation: ValidationProtocol,
) -> dict[str, Any]:
    null_maxima = []
    for jobs in nulls:
        statistics = []
        for method in METHODS:
            candidate, baseline, _ = _series(jobs, method)
            statistics.append(float((candidate - baseline).mean() * 252))
        null_maxima.append(max(statistics))
    results: dict[str, Any] = {}
    for method in METHODS:
        candidate, baseline, lengths = _series(observed, method)
        statistic = float((candidate - baseline).mean() * 252)
        uncertainty = paired_return_uncertainty(
            candidate,
            baseline,
            replicates=validation.bootstrap_replicates,
            block_sessions=validation.block_sessions,
            seed=validation.null_seed,
            segments=lengths,
        )
        results[method] = {
            "annualized_mean_daily_excess": statistic,
            "outer_sessions": len(candidate),
            "fold_lengths": lengths,
            "paired_uncertainty": uncertainty,
            "family_adjusted_null": search_null_test(statistic, null_maxima),
        }
    for method, result in results.items():
        candidate, baseline, _ = _series(observed, method)
        effects = int(np.count_nonzero(np.abs(candidate - baseline) > EFFECT_THRESHOLD))
        selected = sum(
            not job["portfolio"]["methods"][method]["retained_incumbent"] for job in observed
        )
        stress_deltas = {
            role: [
                job["portfolio"]["outer"][method][role]["sharpe"]
                - job["portfolio"]["outer"]["incumbent"][role]["sharpe"]
                for job in observed
            ]
            for role in ("cost_6bps", "delay_1")
        }
        gates = []
        if not selected:
            gates.append("no_selected_incremental_policy")
        if len(observed) < MIN_OUTER_FOLDS:
            gates.append("insufficient_outer_folds")
        if effects < validation.min_effect_sessions:
            gates.append("insufficient_outer_effect_sessions")
        if result["annualized_mean_daily_excess"] <= 0:
            gates.append("nonpositive_outer_increment")
        if result["paired_uncertainty"]["annualized_mean_difference_ci95"][0] <= 0:
            gates.append("paired_interval_includes_zero")
        if result["family_adjusted_null"]["p_value"] > ALPHA:
            gates.append("alignment_null_not_rejected")
        for role, deltas in stress_deltas.items():
            if float(np.mean(deltas)) <= 0:
                gates.append(f"no_increment_under_{role}")
        if any(
            any(job["portfolio"]["outer"][name].get("warnings", {}).values())
            for job in observed
            for name in (method, "incumbent")
        ):
            gates.append("outer_accounting_warning")
        result.update(
            selected_folds=selected,
            outer_effect_sessions=effects,
            stress_sharpe_deltas=stress_deltas,
            failed_gates=gates,
            historical_support=not gates,
            interpretation="conditional historical alignment diagnostics; no promotion authority",
        )
    return {
        "methods": results,
        "null_max_statistics": null_maxima,
        "statistic": "252 * mean(candidate daily return minus incumbent daily return)",
        "family": (
            "maximum selected-policy statistic across genetic and random in each full null pipeline"
        ),
    }


def run_validation(  # noqa: PLR0912, PLR0915 - frozen orchestration transaction
    incumbent: Candidate,
    snapshot: Path,
    *,
    start: dt.date,
    validation: ValidationProtocol,
    search: SymbolicSearchProtocol,
    screen: SymbolicScreenConfig,
    output: Path,
    resume: bool = False,
) -> dict[str, Any]:
    _check_incumbent(incumbent)
    if validation.min_effect_sessions != screen.min_effect_sessions:
        raise ValueError("validation and screen support floors must match")
    if search.method != "genetic":
        raise ValueError("base search protocol method must be genetic; random is derived")
    slots = (
        search.budget
        * len(validation.seeds)
        * 2
        * len(validation.folds)
        * (validation.null_replicates + 1)
    )
    if slots > MAX_TOTAL_SLOTS:
        raise ValueError("nested candidate-slot budget exceeds two million")
    symbols = tuple(str(symbol) for symbol in incumbent.config.strategy.universe.symbols)
    end = validation.folds[-1].evaluation_end
    panel, manifest = load_panel(snapshot, symbols, start=start, end=end, with_context=True)
    if panel.sessions[0] != start or panel.sessions[-1] != end:
        raise ValueError("load boundaries must be available sessions")
    calendar = audit_sessions(list(panel.sessions), start=start, end=end)
    prepared, identities = _prepare(panel, snapshot, start, validation, search)
    mappings = _mappings(prepared, validation)
    exception = default_ledger_path(snapshot)
    frozen = {
        "schema_version": VALIDATION_VERSION,
        "start": start.isoformat(),
        "validation": validation.to_dict(),
        "search": search.to_dict(),
        "screen": asdict(screen),
        "incumbent": incumbent.model_dump(mode="json"),
        "snapshot": manifest.model_dump(mode="json"),
        "calendar": calendar,
        "panel_sha256": panel_digest(panel),
        "folds": identities,
        "source_sha256": _source_hashes(),
        "runtime": _runtime_versions(),
        "registry": symbolic_registry_manifest(),
        "action_exceptions_sha256": _file_hash(exception),
        "candidate_slot_budget": slots,
        "mappings_sha256": digest_json(mappings),
        "null_hypothesis": (
            "incremental pattern signal timing is unrelated to portfolio "
            "outcomes, conditional on frozen market paths and incumbent"
        ),
        "statistical_family": list(METHODS),
        "alpha": ALPHA,
        "support_gate": {
            "min_outer_folds": MIN_OUTER_FOLDS,
            "min_outer_effect_sessions": validation.min_effect_sessions,
            "effect_threshold": EFFECT_THRESHOLD,
            "required": [
                "selected_policy",
                "positive_mean_excess",
                "positive_paired_ci_lower",
                "family_null_p_at_most_alpha",
                "positive_mean_cost_delay_sharpe_delta",
                "no_outer_accounting_warnings",
            ],
        },
        "execution_authority": "none",
        "promotion_authority": "none",
        "limitations": LIMITATIONS,
    }
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("validation workspace already in use") from exc
        path = output / "protocol.json"
        if resume:
            if not path.exists() or digest_json(json.loads(path.read_text())) != digest_json(
                frozen
            ):
                raise ValueError("resume protocol, source or data differs")
            if json.loads((output / "null-mappings.json").read_text()) != mappings:
                raise ValueError("frozen null mappings changed")
        elif path.exists():
            raise ValueError("validation output exists; use resume or a new directory")
        else:
            write_json(path, frozen)
            write_json(output / "null-mappings.json", mappings)
        contract_hash = digest_json(frozen)
        observed: list[dict[str, Any]] = []
        nulls: list[list[dict[str, Any]]] = [[] for _ in range(validation.null_replicates)]
        active = "initialization"
        try:
            for replicate in range(-1, validation.null_replicates):
                for item in prepared:
                    name = "observed" if replicate == -1 else f"null-{replicate:04d}"
                    active = f"{name}/{item['fold'].name}"
                    mapping = (
                        None
                        if replicate == -1
                        else np.asarray(mappings[active]["indices"], dtype=int)
                    )
                    _assert_frozen(frozen, exception)
                    job = _run_job(
                        item,
                        incumbent,
                        snapshot,
                        validation,
                        search,
                        screen,
                        output / active,
                        contract_hash,
                        mapping,
                        diagnostics=replicate == -1,
                    )
                    (observed if replicate == -1 else nulls[replicate]).append(job)
                    write_json(
                        output / "progress.json",
                        {
                            "status": "running",
                            "last_completed": active,
                            "completed_folds": len(observed) + sum(map(len, nulls)),
                            "total_folds": len(prepared) * (validation.null_replicates + 1),
                        },
                    )
            _assert_frozen(frozen, exception)
            verified, _ = load_panel(snapshot, symbols, start=start, end=end, with_context=True)
            if panel_digest(verified) != frozen["panel_sha256"]:
                raise ValueError("market inputs changed during validation")
            report = {
                "schema_version": VALIDATION_VERSION,
                "status": "completed",
                "protocol_sha256": contract_hash,
                "candidate_slot_budget": slots,
                "observed": observed,
                "statistics": _aggregate(observed, nulls, validation),
                "completed_null_replicates": len(nulls),
                "limitations": LIMITATIONS,
                "execution_authority": "none",
                "promotion_authority": "none",
            }
            write_json(output / "report.json", report)
            _markdown(output / "report.md", report)
            write_json(
                output / "progress.json",
                {"status": "completed", "completed_null_replicates": len(nulls)},
            )
            return report
        except Exception as exc:
            write_json(
                output / "progress.json",
                {
                    "status": "failed",
                    "job": active,
                    "failure_type": type(exc).__name__,
                    "failure": str(exc),
                    "statistical_result": "unavailable; failed trials are never converted to zero",
                },
            )
            raise


def _markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Nested genetic validation",
        "",
        "Historical development evidence; no execution or promotion authority.",
        "",
        "| Method | Annualized mean daily excess | Family-adjusted null p |",
        "|---|---:|---:|",
    ]
    for method, result in report["statistics"]["methods"].items():
        lines.append(
            f"| {method} | {result['annualized_mean_daily_excess']:.6f} | "
            f"{result['family_adjusted_null']['p_value']:.4f} |"
        )
    lines.extend(
        [
            "",
            (
                "Full intervals, fold selections, failures and accounting "
                "artifacts are in report.json and the per-fold directories."
            ),
            "",
            *LIMITATIONS,
        ]
    )
    path.write_text("\n".join(lines) + "\n")
