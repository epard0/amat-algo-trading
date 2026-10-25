"""Frozen historical validation and paired statistical primitives.

Signal-alignment nulls move complete feature rows within a single time split.
They are deliberately noncausal offline controls, never tradable market paths.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from itertools import pairwise
from typing import Any, Self

import numpy as np
from numpy.typing import NDArray

MIN_SEGMENT = 4
MAX_REPLICATES = 100_000
MAX_FOLDS = 32
MAX_SEEDS = 32
MAX_SHORTLIST = 32
MAX_SEARCH_RUNS = 100_000
YEAR_SESSIONS = 252


def _integer(value: object, name: str, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")


def _tuple(value: object) -> bool:
    return isinstance(value, tuple)


def _date(value: object) -> bool:
    return type(value) is date


@dataclass(frozen=True)
class WalkForwardFold:
    name: str
    fit_end: date
    selection_end: date
    evaluation_end: date

    def __post_init__(self) -> None:
        if (
            not self.name
            or not self.name.isascii()
            or not all(char.isalnum() or char in "_-" for char in self.name)
        ):
            raise ValueError("fold name must be a nonempty ASCII identifier")
        if not all(_date(day) for day in (self.fit_end, self.selection_end, self.evaluation_end)):
            raise ValueError("fold boundaries must be dates")
        if not self.fit_end < self.selection_end < self.evaluation_end:
            raise ValueError("fold requires fit_end < selection_end < evaluation_end")


@dataclass(frozen=True)
class ValidationProtocol:
    folds: tuple[WalkForwardFold, ...]
    seeds: tuple[int, ...] = (11, 29)
    shortlist_size: int = 2
    null_replicates: int = 19
    null_seed: int = 1729
    bootstrap_replicates: int = 1000
    block_sessions: int = 20
    min_effect_sessions: int = 30

    def __post_init__(self) -> None:
        if not _tuple(self.folds) or not 1 <= len(self.folds) <= MAX_FOLDS:
            raise ValueError("folds must be a nonempty bounded tuple")
        if len({fold.name for fold in self.folds}) != len(self.folds):
            raise ValueError("fold identifiers must be unique")
        for previous, following in pairwise(self.folds):
            if following.selection_end < previous.evaluation_end:
                raise ValueError("outer evaluation intervals must be ordered and nonoverlapping")
        if not _tuple(self.seeds) or not 1 <= len(self.seeds) <= MAX_SEEDS:
            raise ValueError("seeds must be a nonempty bounded tuple")
        for seed in self.seeds:
            _integer(seed, "seed", 0, 2**32 - 1)
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seeds must be unique")
        _integer(self.shortlist_size, "shortlist_size", 1, MAX_SHORTLIST)
        _integer(self.null_replicates, "null_replicates", 1, MAX_REPLICATES)
        _integer(self.null_seed, "null_seed", 0, 2**32 - 1)
        _integer(self.bootstrap_replicates, "bootstrap_replicates", 1, MAX_REPLICATES)
        _integer(self.block_sessions, "block_sessions", 1, 1000)
        _integer(self.min_effect_sessions, "min_effect_sessions", 1, 100_000)
        if len(self.folds) * len(self.seeds) * (self.null_replicates + 1) > MAX_SEARCH_RUNS:
            raise ValueError("validation search-run budget exceeds maximum")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["folds"] = [
            {
                "name": fold.name,
                **{
                    name: getattr(fold, name).isoformat()
                    for name in ("fit_end", "selection_end", "evaluation_end")
                },
            }
            for fold in self.folds
        ]
        payload["seeds"] = list(self.seeds)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        payload = dict(value)
        folds = []
        for fold in payload["folds"]:
            decoded: dict[str, Any] = {
                name: date.fromisoformat(item) if name.endswith("_end") else item
                for name, item in fold.items()
            }
            folds.append(WalkForwardFold(**decoded))
        payload["folds"] = tuple(folds)
        if "seeds" in payload:
            payload["seeds"] = tuple(payload["seeds"])
        return cls(**payload)


def make_null_mapping(
    sessions: tuple[date, ...],
    eligible: NDArray[np.bool_],
    fold: WalkForwardFold,
    rng: np.random.Generator,
    *,
    min_shift: int = 5,
) -> tuple[NDArray[np.int64], dict[str, Any]]:
    """Return target-to-source indices; apply identically to every feature/asset.

    Missingness travels with complete rows. Ineligible rows remain unchanged.
    The circular seam and future-to-past mappings make this an offline null only.
    """
    _integer(min_shift, "min_shift", 1, 1000)
    if any(not _date(day) for day in sessions) or any(
        right <= left for left, right in pairwise(sessions)
    ):
        raise ValueError("sessions must contain strictly increasing dates")
    mask = np.asarray(eligible)
    if mask.dtype != np.bool_ or mask.shape != (len(sessions),):
        raise ValueError("eligible must be a one-dimensional boolean mask aligned with sessions")
    mapping = np.arange(len(sessions), dtype=np.int64)
    records = []
    for name, lower, upper in (
        ("fit", date.min, fold.fit_end),
        ("selection", fold.fit_end, fold.selection_end),
        ("outer", fold.selection_end, fold.evaluation_end),
    ):
        indices = np.flatnonzero(mask & np.array([lower < day <= upper for day in sessions]))
        size = len(indices)
        if size < max(MIN_SEGMENT, 2 * min_shift):
            raise ValueError(f"{name} has too few eligible rows for the requested null shift")
        shift = int(rng.integers(min_shift, size - min_shift + 1))
        mapping[indices] = np.roll(indices, shift)
        records.append({"segment": name, "eligible_sessions": size, "shift": shift})
    return mapping, {
        "kind": "split-local-joint-feature-circular-shift/1",
        "segments": records,
        "min_shift": min_shift,
        "limitation": "Noncausal offline signal-alignment null; historical development only. "
        "Preserves joint feature rows and missingness, not a shuffled OHLC market path.",
    }


def _sharpe(values: NDArray[np.float64]) -> float | None:
    scale = float(np.std(values, ddof=1))
    if not math.isfinite(scale) or scale <= np.finfo(float).eps:
        return None
    return float(np.mean(values) / scale * math.sqrt(YEAR_SESSIONS))


def _interval(values: Sequence[float]) -> list[float]:
    return [float(value) for value in np.quantile(values, [0.025, 0.975])]


def paired_return_uncertainty(
    candidate: NDArray[np.float64],
    baseline: NDArray[np.float64],
    *,
    replicates: int,
    block_sessions: int,
    seed: int,
    segments: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Paired circular moving-block bootstrap, resampled separately per outer fold.

    The annualized mean is arithmetic mean daily return times 252, not CAGR.
    Blocks may wrap within a fold but never cross a fold boundary. Intervals are
    descriptive and conditional on the already selected pipeline and fixed folds.
    """
    _integer(replicates, "replicates", 1, MAX_REPLICATES)
    _integer(block_sessions, "block_sessions", 1, 1000)
    _integer(seed, "seed", 0, 2**32 - 1)
    left, right = np.asarray(candidate, dtype=float), np.asarray(baseline, dtype=float)
    if left.ndim != 1 or right.shape != left.shape or len(left) < MIN_SEGMENT:
        raise ValueError("paired returns must be aligned one-dimensional arrays with >=4 sessions")
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError(
            "paired returns must all be finite; observations cannot be silently dropped"
        )
    lengths = tuple(segments) if segments is not None else (len(left),)
    for length in lengths:
        _integer(length, "segment length", MIN_SEGMENT, len(left))
    if not lengths or sum(lengths) != len(left):
        raise ValueError("segment lengths must exactly partition the paired returns")
    if any(block_sessions > length for length in lengths):
        raise ValueError("block_sessions cannot exceed a segment length")
    rng = np.random.default_rng(seed)
    means, sharpes = [], []
    undefined = 0
    for _ in range(replicates):
        pieces = []
        offset = 0
        for length in lengths:
            starts = rng.integers(0, length, size=math.ceil(length / block_sessions))
            indices = ((starts[:, None] + np.arange(block_sessions)) % length).ravel()[:length]
            pieces.append(indices + offset)
            offset += length
        indices = np.concatenate(pieces)
        sampled_left, sampled_right = left[indices], right[indices]
        means.append(float(np.mean(sampled_left - sampled_right) * YEAR_SESSIONS))
        candidate_sharpe, baseline_sharpe = _sharpe(sampled_left), _sharpe(sampled_right)
        if candidate_sharpe is None or baseline_sharpe is None:
            undefined += 1
        else:
            sharpes.append(candidate_sharpe - baseline_sharpe)
    candidate_sharpe, baseline_sharpe = _sharpe(left), _sharpe(right)
    return {
        "sessions": len(left),
        "segment_lengths": list(lengths),
        "replicates": replicates,
        "block_sessions": block_sessions,
        "seed": seed,
        "approximate_independent_blocks": sum(length / block_sessions for length in lengths),
        "annualized_mean_difference": float(np.mean(left - right) * YEAR_SESSIONS),
        "annualized_mean_difference_ci95": _interval(means),
        "sharpe_difference": None
        if candidate_sharpe is None or baseline_sharpe is None
        else candidate_sharpe - baseline_sharpe,
        "sharpe_difference_ci95": None if undefined else _interval(sharpes),
        "undefined_sharpe_replicates": undefined,
        "limitations": [
            "Conditional intervals do not correct selection or provide independent confirmation.",
            "Circular blocks preserve paired dependence; block count is a heuristic, not ESS.",
            "Any undefined Sharpe replicate makes its interval unavailable; none are discarded.",
        ],
    }


def search_null_test(observed: float, null_stats: Sequence[float]) -> dict[str, Any]:
    """One-sided upper-tail add-one test; ties count against the observed result."""
    values = np.asarray(null_stats, dtype=float)
    if not math.isfinite(observed) or values.ndim != 1 or not len(values):
        raise ValueError("observed and a nonempty one-dimensional null sample are required")
    if not np.isfinite(values).all():
        raise ValueError("failed or nonfinite null outcomes cannot be counted as zero")
    count = int(np.count_nonzero(values >= observed))
    total = len(values)
    fraction = count / total
    z = 1.959963984540054
    denominator = 1 + z * z / total
    center = (fraction + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(fraction * (1 - fraction) / total + z * z / (4 * total**2))
    radius /= denominator
    return {
        "observed": float(observed),
        "null_replicates": total,
        "exceedances_including_ties": count,
        "p_value": (count + 1) / (total + 1),
        "resolution": 1 / (total + 1),
        "tail_probability_wilson_ci95": [max(0.0, center - radius), min(1.0, center + radius)],
        "interpretation": "Add-one upper-tail Monte Carlo p-value under the specified alignment "
        "null. Wilson interval estimates the underlying exceedance probability, not the corrected "
        "p-value. Validity depends on null exchangeability; historical development only.",
    }
