"Matched-exposure incremental evaluation, paired uncertainty, and failure standpoints."

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from autoresearch.intraday.engine import performance
from autoresearch.intraday.specs import Recipe

MIN_EXPOSURE = 1e-8
EPSILON = 1e-12
MIN_TRADES = 50
DRAWDOWN_FLOOR = -0.20
POSITIVE_FOLD_FLOOR = 0.6
MARKET_MOVE = 0.003
WIDE_RANGE = 0.015
QUIET_RANGE = 0.005


def matched(candidate: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    if [r["session"] for r in candidate["daily"]] != [r["session"] for r in control["daily"]]:
        raise ValueError("matched controls require identical dates")
    a, b = (np.array([r["return"] for r in run["daily"]]) for run in (candidate, control))
    ea, eb = (run["metrics"]["average_gross_exposure"] for run in (candidate, control))
    if min(ea, eb) <= MIN_EXPOSURE:
        return {
            "available": False,
            "reason": "no_exposure_in_candidate_or_reference",
            "score": None,
        }
    target = min(ea, eb)
    a, b = a * (target / ea), b * (target / eb)
    delta = a - b
    std = float(delta.std(ddof=1))
    return {
        "available": True,
        "target_exposure": target,
        "candidate_scalar": target / ea,
        "control_scalar": target / eb,
        "candidate_sharpe": performance(a)["sharpe"],
        "control_sharpe": performance(b)["sharpe"],
        "incremental_annualized_mean_return": float(delta.mean() * 252),
        "incremental_information_ratio": float(delta.mean() / std * np.sqrt(252))
        if std > EPSILON
        else 0.0,
        "score": float(delta.mean() / std * np.sqrt(252)) if std > EPSILON else 0.0,
    }


def uncertainty(
    candidate: dict[str, Any], control: dict[str, Any], *, seed: int = 20260912
) -> dict[str, Any]:
    match = matched(candidate, control)
    if not match["available"]:
        return {"passes": False, "reason": match["reason"]}
    a, b = (np.array([r["return"] for r in run["daily"]]) for run in (candidate, control))
    delta = a * match["candidate_scalar"] - b * match["control_scalar"]
    rng = np.random.default_rng(seed)
    block = 5
    starts = rng.integers(0, len(delta), size=(500, (len(delta) + block - 1) // block))
    indices = (starts[:, :, None] + np.arange(block)) % len(delta)
    samples = delta[indices.reshape(500, -1)[:, : len(delta)]].mean(axis=1) * 252
    lo, hi = np.quantile(samples, [0.05, 0.95])
    return {
        "passes": bool(lo > 0),
        "annualized_mean_gain_p05": float(lo),
        "annualized_mean_gain_p95": float(hi),
        "samples": 500,
        "block_sessions": block,
        "seed": seed,
        "interpretation": (
            "Paired circular-block development guard; adaptive search is not an ind"
            "ependent significance test."
        ),
    }


def diagnostics(run: dict[str, Any], panel: Any) -> dict[str, Any]:
    trades = run["trades"]

    def group(field: str) -> dict[str, Any]:
        keys = sorted({str(t[field]) for t in trades})
        return {
            key: {
                "trades": sum(str(t[field]) == key for t in trades),
                "portfolio_return_sum": sum(
                    t["portfolio_return"] for t in trades if str(t[field]) == key
                ),
                "mean_net_bps": float(
                    np.mean([t["net_return"] for t in trades if str(t[field]) == key]) * 1e4
                ),
            }
            for key in keys
        }

    folds = []
    for year in sorted({r["session"][:4] for r in run["daily"]}):
        returns = np.array([r["return"] for r in run["daily"] if r["session"].startswith(year)])
        folds.append({"year": year, **performance(returns)})
    for t in trades:
        t["entry_hour_et"] = (570 + t["entry_minute"]) // 60
    # Ex-post condition analysis is diagnostic only, never a feature or routing input.
    market = panel.values[:, -1, 0, 3] / panel.values[:, 0, 0, 0] - 1
    ranges = (
        np.nanmax(panel.values[:, :, 0, 1], axis=1) - np.nanmin(panel.values[:, :, 0, 2], axis=1)
    ) / panel.values[:, 0, 0, 0]
    returns = np.array([r["return"] for r in run["daily"]])
    conditions = {}
    for name, mask in {
        "market_up": market > MARKET_MOVE,
        "market_down": market < -MARKET_MOVE,
        "market_flat": np.abs(market) <= MARKET_MOVE,
        "wide_range": ranges > WIDE_RANGE,
        "quiet_range": ranges < QUIET_RANGE,
    }.items():
        conditions[name] = performance(returns[mask])
    return {
        "folds": folds,
        "by_symbol": group("symbol"),
        "by_hour_et": group("entry_hour_et"),
        "by_direction": group("direction"),
        "exit_reasons": dict(Counter(t["exit_reason"] for t in trades)),
        "historical_conditions": conditions,
    }


def evaluate(  # noqa: PLR0917 - paired runs and fixed reference
    recipe: Recipe,
    reference_recipe: Recipe,
    primary: dict[str, Any],
    stress: dict[str, Any],
    delayed: dict[str, Any],
    reference: dict[str, Any],
    incumbent: dict[str, Any],
    incumbent_score: float,
    analysis: dict[str, Any],
) -> dict[str, Any]:
    comparison = matched(primary, reference)
    complexity = len(recipe.terms) + len(recipe.interactions)
    base_complexity = len(reference_recipe.terms) + len(reference_recipe.interactions)
    penalty = 0.01 * max(0, complexity - base_complexity)
    score = comparison["score"] - penalty if comparison["available"] else None
    gates = []
    m = primary["metrics"]
    if m["total_trades"] < MIN_TRADES:
        gates.append("insufficient_trades")
    if m["max_drawdown"] < DRAWDOWN_FLOOR or m["intraday_max_drawdown"] < DRAWDOWN_FLOOR:
        gates.append("drawdown")
    if m["sharpe"] <= 0:
        gates.append("negative_risk_adjusted_return")
    if stress["metrics"]["annualized_return"] <= 0:
        gates.append("cost_stress_not_profitable")
    if delayed["metrics"]["annualized_return"] <= 0:
        gates.append("delay_stress_not_profitable")
    if not comparison["available"] or comparison.get("incremental_annualized_mean_return", 0) <= 0:
        gates.append("no_matched_incremental_value")
    folds = analysis["folds"]
    if np.mean([f["total_return"] > 0 for f in folds]) < POSITIVE_FOLD_FLOOR:
        gates.append("inconsistent_years")
    guard = uncertainty(primary, incumbent)
    improved = score is not None and score > incumbent_score + 0.05
    if not improved:
        gates.append("improvement_margin")
    if not guard["passes"]:
        gates.append("paired_uncertainty")
    failure = {
        "objective": {
            "matched_comparison": comparison,
            "score": score,
            "incumbent_score": incumbent_score,
            "margin": 0.05,
            "complexity_penalty": penalty,
        },
        "execution": {
            "normal_cost_bps": 2,
            "stress_cost_bps": 6,
            "normal_return": m["annualized_return"],
            "stress_return": stress["metrics"]["annualized_return"],
            "delayed_return": delayed["metrics"]["annualized_return"],
            "liquidity_rejections": m["liquidity_rejections"],
        },
        "signal": {
            "gross_expectancy_bps": m["gross_expectancy_bps"],
            "net_expectancy_bps": m["expectancy_bps"],
            "trades": m["total_trades"],
            "win_rate": m["win_rate"],
        },
        "risk": {
            "drawdown": m["max_drawdown"],
            "intraday_drawdown": m["intraday_max_drawdown"],
            "exposure": m["average_gross_exposure"],
        },
        "conditions": analysis,
        "uncertainty": guard,
        "data": {
            "limits": (
                "Historical consolidated minute OHLCV; no quotes/order-book, point-in-t"
                "ime news, or borrow availability. Short entry fee is a fixed 1 bp prox"
                "y, not observed locate pricing."
            )
        },
    }
    return {
        "score": score,
        "score_version": "intraday-matched-incremental/1",
        "score_components": comparison,
        "complexity_penalty": penalty,
        "failed_gates": gates,
        "eligible": not gates,
        "decision": "keep" if not gates else "discard",
        "uncertainty": guard,
        "failure_analysis": failure,
        "train": m,
        "stress": stress["metrics"],
        "delayed": delayed["metrics"],
        "folds": folds,
    }
