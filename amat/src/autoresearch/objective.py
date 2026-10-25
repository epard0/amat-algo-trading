"""Training-only consistency score and explicit rejection gates."""

from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal
from typing import Any

import numpy as np

from amat.backtest.runner import BacktestResult
from amat.domain.ledgers import PeriodAttribution
from amat.metrics import summarize
from autoresearch.contracts import Program

MIN_FOLD_SESSIONS = 60
MIN_FOLDS = 2
INCREMENTAL_SCORE_VERSION = "exposure-matched-incremental/1"
LEGACY_SCORE_VERSION = "annual-fold-consistency/1"


def score_description(program: Program) -> str:
    if program.schema_version == "strategy-development/5":
        return (
            "Yearly risk-adjusted performance less weak-year, drawdown, cost "
            "and complexity penalties."
        )
    return (
        "Exposure-matched candidate Sharpe minus fixed original baseline Sharpe, less "
        "0.01 per added complexity unit. Baseline score is zero. Matching scales the more "
        "exposed strategy down retrospectively at 2 bps costs; it is a development control, "
        "not a tradeable benchmark. Incumbent comparisons use this same fixed reference. "
        "Quality gates and incumbent uncertainty checks remain mandatory."
    )


def apply_incremental_score(
    result: dict[str, Any],
    controls: dict[str, Any],
    program: Program,
    complexity: int,
    baseline_complexity: int,
) -> None:
    """Finalize the v6 objective from the same exposure control used for eligibility.

    Never replace historical v5 semantics or fall back to the absolute score when
    matched evidence is unavailable. The fixed reference makes frontier and
    incumbent scores comparable across research parents.
    """
    if program.schema_version == "strategy-development/5":
        return
    penalty = 0.01 * max(0, complexity - baseline_complexity)
    components: dict[str, Any] = {
        "reference_candidate": program.baseline.digest(),
        "incremental_complexity_penalty": penalty,
        "exposure_tolerance": program.evaluation.exposure_tolerance,
        "cost_bps": 2.0,
        "formula": "matched_sharpe_difference - incremental_complexity_penalty",
        "interpretation": "retrospective development exposure match; not a tradeable benchmark",
    }
    result.update(
        score=None,
        score_version=INCREMENTAL_SCORE_VERSION,
        absolute_performance_score=result.get("score"),
        score_components=components,
    )
    if controls.get("baseline") is True:
        result["score"] = 0.0
        components.update(matched_sharpe_difference=0.0, incremental_complexity_penalty=0.0)
        return
    match = controls.get("exposure_match", {})
    try:
        candidate, reference = match["candidate"], match["control"]
        values = [
            float(candidate["sharpe"]),
            float(reference["sharpe"]),
            float(candidate["average_gross_exposure"]),
            float(reference["average_gross_exposure"]),
            float(match["sharpe_difference"]),
            float(match["exposure_error"]),
        ]
        left, right, left_exposure, right_exposure, delta, error = values
        valid = (
            match.get("matched") is True
            and all(math.isfinite(value) for value in values)
            and min(left_exposure, right_exposure) > 0
            and 0 <= error <= program.evaluation.exposure_tolerance
            and abs(left_exposure - right_exposure) <= program.evaluation.exposure_tolerance
            and math.isclose(error, abs(left_exposure - right_exposure), abs_tol=1e-12)
            and math.isclose(delta, left - right, rel_tol=1e-9, abs_tol=1e-12)
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        result["failed_gates"].append("incremental_score_unavailable")
        result["eligible"] = False
        components["reason"] = "missing, nonfinite, inconsistent or unresolved exposure match"
        return
    components.update(
        matched_candidate_sharpe=left,
        matched_baseline_sharpe=right,
        matched_sharpe_difference=delta,
        candidate_exposure=left_exposure,
        baseline_exposure=right_exposure,
        exposure_error=error,
        scaled=match.get("scaled"),
        scalar=match.get("scalar"),
        matched_annualized_return_difference=match.get("annualized_return_difference"),
    )
    result["score"] = delta - penalty


def anchored_slice(result: BacktestResult, start: int, end: int) -> BacktestResult:
    """Keep the preceding close so the first measured session's return is included."""
    if not 1 <= start < end <= len(result.sessions):
        raise ValueError("evaluation slice needs a preceding valuation and measured sessions")
    anchor = replace(
        result.sessions[start - 1],
        fills=(),
        missed=(),
        instructions=(),
        attribution=PeriodAttribution(*(Decimal(0) for _ in range(5))),
    )
    return replace(
        result, sessions=(anchor, *result.sessions[start:end]), initial_equity=anchor.equity
    )


def score_run(
    result: BacktestResult, stress: BacktestResult, program: Program, complexity: int
) -> dict[str, Any]:
    summary, stressed = summarize(result), summarize(stress)
    folds: list[dict[str, Any]] = []
    for year in sorted({row.session.year for row in result.sessions}):
        indices = [i for i, row in enumerate(result.sessions) if row.session.year == year]
        if len(indices) >= MIN_FOLD_SESSIONS:
            start, end = indices[0], indices[-1] + 1
            fold = (
                anchored_slice(result, start, end)
                if start > 0
                else replace(result, sessions=result.sessions[:end])
            )
            folds.append({"year": year, **summarize(fold).as_dict()})
    sharpes = [float(fold["sharpe"]) for fold in folds]
    positive = sum(float(fold["total_return"]) > 0 for fold in folds) / max(1, len(folds))
    gates = []
    if len(folds) < MIN_FOLDS:
        gates.append("insufficient_year_folds")
    if positive < program.min_positive_folds:
        gates.append("positive_fold_fraction")
    if summary.max_drawdown < -program.max_drawdown:
        gates.append("drawdown")
    if stressed.sharpe <= 0:
        gates.append("stress_sharpe")
    if summary.average_gross_exposure < program.min_exposure:
        gates.append("exposure")
    if summary.total_fills == 0:
        gates.append("no_fills")
    if result.warnings or stress.warnings:
        gates.append("accounting_or_runner_warning")
    expected = (program.measured_start.isoformat(), program.train_end.isoformat())
    if (summary.first_session, summary.last_session) != expected:
        gates.append("measured_window")
    if result.session_dates != stress.session_dates:
        gates.append("stress_window")
    value = (
        (
            float(np.median(sharpes))
            - 0.5 * max(0, 0.15 - min(sharpes))
            - 2 * max(0, abs(summary.max_drawdown) - 0.12)
            - max(0, summary.cost_drag_annualized - 0.006)
            - 0.01 * complexity
        )
        if sharpes
        else None
    )
    return {
        "score": value,
        "eligible": not gates,
        "failed_gates": gates,
        "folds": folds,
        "positive_fold_fraction": positive,
        "train": summary.as_dict(),
        "stress": stressed.as_dict(),
        "complexity_penalty": 0.01 * complexity,
    }
