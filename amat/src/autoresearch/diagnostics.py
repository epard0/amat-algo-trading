"""Feature information, dependence-aware uncertainty and executable-horizon diagnostics."""

from __future__ import annotations

from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.contracts import Program
from autoresearch.labels import HORIZONS, forward_returns
from autoresearch.registry import EPSILON
from autoresearch.signals import SignalTrace

MIN_PAIRS = 20
MIN_CROSS_SECTION = 3
BUCKET_EDGES = 4


def rankdata(values: np.ndarray) -> np.ndarray:
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    return np.asarray((np.cumsum(counts) - (counts - 1) / 2)[inverse])


def correlation(left: np.ndarray, right: np.ndarray, *, rank: bool = False) -> float | None:
    if left.shape != right.shape:
        raise ValueError("correlation requires aligned arrays")
    valid = np.isfinite(left) & np.isfinite(right)
    a, b = left[valid], right[valid]
    if len(a) < MIN_CROSS_SECTION:
        return None
    if rank:
        a, b = rankdata(a), rankdata(b)
    if a.std() <= EPSILON or b.std() <= EPSILON:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def block_indices(length: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Resample entire contiguous date blocks; every asset shares these indices."""
    block = min(length, block)
    starts = rng.integers(0, length - block + 1, size=(length + block - 1) // block)
    return np.concatenate([np.arange(start, start + block) for start in starts])[:length]


def block_mean_interval(values: np.ndarray, program: Program) -> dict[str, Any]:
    if np.isfinite(values).sum() < program.evaluation.block_sessions * 2:
        return {"mean": None, "lower": None, "upper": None, "available": False}
    rng = np.random.default_rng(20260911)
    means = []
    for _ in range(program.evaluation.bootstrap_resamples):
        sample = values[block_indices(len(values), program.evaluation.block_sessions, rng)]
        sample = sample[np.isfinite(sample)]
        if len(sample):
            means.append(float(sample.mean()))
    tail = (1 - program.evaluation.confidence) / 2
    return {
        "mean": float(np.nanmean(values)),
        "lower": float(np.quantile(means, tail)),
        "upper": float(np.quantile(means, 1 - tail)),
        "available": True,
        "block_sessions": program.evaluation.block_sessions,
        "interpretation": "adaptive development diagnostic; not independent significance",
    }


def horizon_diagnostics(
    values: np.ndarray,
    labels: np.ndarray,
    panel: MarketPanel,
    edges: list[float],
    program: Program,
    *,
    horizon: int,
) -> dict[str, Any]:
    valid = np.isfinite(values) & np.isfinite(labels)
    daily_ic = np.full(len(values), np.nan)
    spread = np.full(len(values), np.nan)
    buckets = np.digitize(values, edges) if edges else np.zeros(values.shape, dtype=int)
    for index in range(len(values)):
        value = correlation(values[index], labels[index], rank=True)
        if value is not None:
            daily_ic[index] = value
        if len(set(edges)) == BUCKET_EDGES:
            high, low = (
                valid[index] & (buckets[index] == BUCKET_EDGES),
                valid[index] & (buckets[index] == 0),
            )
            if high.any() and low.any():
                spread[index] = labels[index, high].mean() - labels[index, low].mean()
    bucket_returns = []
    for bucket in range(5):
        sample = labels[valid & (buckets == bucket)]
        bucket_returns.append(
            {
                "bucket": bucket,
                "observations": len(sample),
                "mean_net_return": float(sample.mean()) if len(sample) else None,
            }
        )
    assets = {}
    for column, symbol in enumerate(panel.symbols):
        count = int(valid[:, column].sum())
        assets[str(symbol)] = {
            "observations": count,
            "rank_ic": correlation(values[:, column], labels[:, column], rank=True)
            if count >= MIN_PAIRS
            else None,
        }
    years = {}
    for year in sorted({session.year for session in panel.sessions}):
        # A fold diagnostic never uses a label that exits in a later year.
        mask = np.array([session.year == year for session in panel.sessions])
        mature = np.array(
            [
                i + horizon + 1 < len(panel) and panel.sessions[i + horizon + 1].year == year
                for i in range(len(panel))
            ]
        )
        take = mask & mature
        if take.any():
            years[str(year)] = {
                "observations": int(valid[take].sum()),
                "rank_ic": correlation(values[take], labels[take], rank=True),
            }
    return {
        "horizon": horizon,
        "observations": int(valid.sum()),
        "mean_daily_rank_ic": float(np.nanmean(daily_ic)) if np.isfinite(daily_ic).any() else None,
        "bucket_edges_from_initial_training": edges,
        "buckets": bucket_returns,
        "high_minus_low": block_mean_interval(spread, program),
        "assets": assets,
        "years": years,
    }


def feature_diagnostics(panel: MarketPanel, trace: SignalTrace, program: Program) -> dict[str, Any]:
    first = next(i for i, date in enumerate(panel.sessions) if date >= program.measured_start)
    result: dict[str, Any] = {
        "schema_version": "feature-diagnostics/2",
        "label_contract": (
            "next-session adjusted VWAP to horizon-end adjusted VWAP; net 2 bps each way"
        ),
        "evaluation_use": "adaptive_development",
        "features": [],
        "redundancy": [],
    }
    labels = {h: forward_returns(panel, h) for h in HORIZONS}
    for index, metadata in enumerate(trace.features):
        values = trace.predictors[:, :, index].copy()
        values[:first] = np.nan
        edges = trace.fits[0]["feature_quantiles"][index] if trace.fits else []
        horizons = [
            horizon_diagnostics(values, labels[h], panel, edges, program, horizon=h)
            for h in HORIZONS
        ]
        if metadata["recipe"]["kind"] == "pattern":
            for horizon in horizons:
                target = labels[horizon["horizon"]]
                horizon["pattern_cohorts"] = {}
                for name, match in (("matched", 1), ("unmatched", 0)):
                    sample = target[(values == match) & np.isfinite(target)]
                    horizon["pattern_cohorts"][name] = {
                        "observations": len(sample),
                        "mean_net_return": float(sample.mean()) if len(sample) else None,
                    }
        result["features"].append(
            {
                **metadata,
                "coverage": float(np.isfinite(values[first:]).mean()),
                "lag_one_correlation": correlation(values[first + 1 :], values[first:-1]),
                "baseline_rank_correlation": correlation(
                    values[first:], trace.baseline[first:], rank=True
                ),
                "horizons": horizons,
            }
        )
    for left in range(len(trace.features)):
        for right in range(left + 1, len(trace.features)):
            result["redundancy"].append(
                {
                    "left": left,
                    "right": right,
                    "rank_correlation": correlation(
                        trace.predictors[first:, :, left],
                        trace.predictors[first:, :, right],
                        rank=True,
                    ),
                }
            )
    scores = trace.scores[first:]
    result["signal"] = {
        "active_fraction": float(np.mean(scores > 0)),
        "mean_score": float(scores.mean()),
        "mean_daily_score_change": float(np.abs(np.diff(scores, axis=0)).mean()),
        "lag_one_correlation": correlation(scores[1:], scores[:-1]),
    }
    return result


def sharpe(returns: np.ndarray) -> float:
    scale = float(returns.std(ddof=1)) * np.sqrt(252)
    if scale <= EPSILON or np.any(returns <= -1):
        return 0.0
    annual = float(np.expm1(np.log1p(returns).mean() * 252))
    return float(annual / scale)


def paired_uncertainty(
    candidate: np.ndarray, control: np.ndarray, program: Program
) -> dict[str, Any]:
    if (
        candidate.ndim != 1
        or candidate.shape != control.shape
        or not np.isfinite(candidate).all()
        or not np.isfinite(control).all()
    ):
        raise ValueError("paired bootstrap requires aligned finite daily returns")
    if len(candidate) < 2 * program.evaluation.block_sessions:
        return {"available": False, "passes": False, "reason": "insufficient_date_blocks"}
    rng = np.random.default_rng(20260911)
    differences = []
    for _ in range(program.evaluation.bootstrap_resamples):
        indices = block_indices(len(candidate), program.evaluation.block_sessions, rng)
        differences.append(sharpe(candidate[indices]) - sharpe(control[indices]))
    # Declared maximum trial count determines the tail, never the observed
    # winning trial number. This is a conservative heuristic selection guard,
    # not a guarantee under adaptive search or arbitrary market dependence.
    tail = (1 - program.evaluation.confidence) / program.max_experiments
    lower = float(np.quantile(differences, tail))
    return {
        "available": True,
        "passes": lower > 0,
        "lower_sharpe_difference": lower,
        "observed_sharpe_difference": sharpe(candidate) - sharpe(control),
        "tail_probability": tail,
        "block_sessions": program.evaluation.block_sessions,
        "resamples": program.evaluation.bootstrap_resamples,
        "interpretation": (
            "trial-budget-adjusted bootstrap research guard; not a calibrated p-value"
        ),
    }
