"""Registered, finite research tests; no adaptive reuse of a final holdout."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from autoresearch.periods import period_reports

MAX_TRIALS = 1000
MIN_BLOCK = 2
MIN_BOOTSTRAPS = 100
MIN_FOLDS = 3


def calendar_check(actual: list[str], expected: list[str]) -> None:
    if not expected or expected != sorted(set(expected)):
        raise ValueError("calendar must be nonempty, unique and ordered")
    for value in expected:
        dt.date.fromisoformat(value)
    if actual != expected:
        raise ValueError("missing, duplicate, extra or reordered exchange sessions")


def causal_check(records: list[dict[str, str]]) -> None:
    for row in records:
        available, decision, execution = (
            dt.datetime.fromisoformat(row[key])
            for key in ("available_at", "decision_at", "execution_at")
        )
        if any(value.tzinfo is None for value in (available, decision, execution)):
            raise ValueError("causal timestamps must include timezones")
        if not available <= decision < execution:
            raise ValueError("information or execution precedes its permissible time")


def matched_periods(
    candidate: list[dict[str, Any]], control: list[dict[str, Any]], calendar: list[str]
) -> list[dict[str, Any]]:
    """Pair continuous histories exactly; never inner-join away missing sessions."""
    for rows in (candidate, control):
        calendar_check([row["session"] for row in rows], calendar)
    results = []
    for a, b in zip(period_reports(candidate), period_reports(control), strict=True):
        if (a["kind"], a["start"], a["end"]) != (b["kind"], b["start"], b["end"]):
            raise ValueError("period boundaries disagree")
        results.append(
            {
                "kind": a["kind"],
                "period": a["period"],
                "start": a["start"],
                "end": a["end"],
                "candidate_return": a["total_return"],
                "control_return": b["total_return"],
                "return_difference": a["total_return"] - b["total_return"],
                "candidate_exposure": a["mean_gross_exposure"],
                "control_exposure": b["mean_gross_exposure"],
                "calendar_completeness": "verified_against_frozen_calendar",
                "exposure_matching": "requires_recorded_matched_runs",
                "interpretation": "descriptive paired periods, not independent trials",
            }
        )
    return results


@dataclass(frozen=True)
class StatisticalProtocol:
    """A finite family with preallocated alpha, including unsuccessful trials.

    Bonferroni spending is valid for dependent tests when each input p-value is
    valid. It is not the invalid alpha/(trial+1) 'sequential Holm' rule. The
    bootstrap below is a diagnostic, not an anytime-valid inferential claim.
    """

    max_trials: int = 30
    alpha: float = 0.05
    block_sessions: int = 20
    bootstrap_samples: int = 2000
    seed: int = 20250921
    minimum_observations: int = 252
    minimum_folds: int = 3
    inference: str = "registered_block_bootstrap_diagnostic"

    def __post_init__(self) -> None:
        if not 1 <= self.max_trials <= MAX_TRIALS or not 0 < self.alpha < 1:
            raise ValueError("invalid trial family")
        if not MIN_BLOCK <= self.block_sessions <= self.minimum_observations:
            raise ValueError("invalid block length")
        if self.bootstrap_samples < MIN_BOOTSTRAPS or self.minimum_folds < MIN_FOLDS:
            raise ValueError("insufficient registered evaluation")

    def freeze(self, path: Path) -> None:
        payload = json.dumps(asdict(self), sort_keys=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as handle:
            handle.write(payload)
        path.with_suffix(".sha256").write_text(hashlib.sha256(payload.encode()).hexdigest())

    def compare(
        self, candidate: list[float], control: list[float], *, trial: int
    ) -> dict[str, Any]:
        if not 1 <= trial <= self.max_trials:
            raise ValueError("registered trial budget exhausted")
        a, b = np.asarray(candidate), np.asarray(control)
        if a.shape != b.shape or a.ndim != 1 or len(a) < self.minimum_observations:
            raise ValueError("insufficient aligned returns")
        if not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError("invalid returns")
        difference = a - b
        folds = np.array_split(difference, self.minimum_folds)
        consistent = all(float(fold.mean()) > 0 for fold in folds)
        rng = np.random.default_rng(self.seed + trial)
        centered = difference - difference.mean()
        exceed = 0
        blocks = (len(a) + self.block_sessions - 1) // self.block_sessions
        for _ in range(self.bootstrap_samples):
            starts = rng.integers(0, len(a), size=blocks)
            indices = (starts[:, None] + np.arange(self.block_sessions)) % len(a)
            exceed += float(centered[indices.ravel()[: len(a)]].mean()) >= float(difference.mean())
        p = (exceed + 1) / (self.bootstrap_samples + 1)
        threshold = self.alpha / self.max_trials
        return {
            "trial": trial,
            "family_size": self.max_trials,
            "p_value_diagnostic": p,
            "alpha_allocated": threshold,
            "folds_positive": consistent,
            "diagnostic_pass": consistent and p <= threshold,
            "promotion_authority": "none",
            "inference": self.inference,
            "bootstrap_resolution_sufficient": 1 / (self.bootstrap_samples + 1) <= threshold,
        }
