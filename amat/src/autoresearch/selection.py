"""Auditable, causal asset eligibility and ranking before portfolio construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.registry import lag, rolling
from autoresearch.specs import SelectionSpec


@dataclass(frozen=True)
class SelectionTrace:
    scores: np.ndarray
    eligible: np.ndarray
    selected: np.ndarray
    rank: np.ndarray
    reasons: np.ndarray
    dollar_volume: np.ndarray
    relative_volume: np.ndarray
    window_dollar_volume: np.ndarray

    def report(self, panel: MarketPanel, first: int) -> dict[str, Any]:
        """Small JSON view; the complete session/asset trace is persisted separately."""
        if not 0 <= first < len(panel):
            raise ValueError("selection report requires a nonempty measurement interval")

        def number(value: float) -> float | None:
            return float(value) if np.isfinite(value) else None

        return {
            "interpretation": "research selection; turnover proxies, not quoted liquidity",
            "as_of_session": str(panel.sessions[-1]),
            "mean_selected_assets": float(self.selected[first:].sum(axis=1).mean()),
            "cash_only_sessions": int(np.sum(~self.selected[first:].any(axis=1))),
            "reason_counts": {
                str(reason): int(np.sum(self.reasons[first:] == reason))
                for reason in np.unique(self.reasons[first:])
            },
            "latest": [
                {
                    "symbol": str(symbol),
                    "selected": bool(self.selected[-1, column]),
                    "rank": int(self.rank[-1, column]),
                    "reason": str(self.reasons[-1, column]),
                    "score": number(self.scores[-1, column]),
                    "dollar_volume": number(self.dollar_volume[-1, column]),
                    "relative_volume": number(self.relative_volume[-1, column]),
                    "window_dollar_volume": number(self.window_dollar_volume[-1, column]),
                }
                for column, symbol in enumerate(panel.symbols)
            ],
            "execution_authority": "none",
        }


def select_assets(
    panel: MarketPanel,
    scores: np.ndarray,
    spec: SelectionSpec,
    *,
    clusters: tuple[str, ...],
    allow_increase: np.ndarray,
) -> SelectionTrace:
    """Select from information available at each close, with deterministic ties.

    The liquidity floors use the smaller of today's turnover and its preceding
    complete-window mean. Relative volume divides today's shares by the preceding
    mean. Neither uses tomorrow's execution bar. Missing/zero observations cannot
    authorize an entry even when a configured floor is zero. Assets outside the
    selection receive a zero preference; their exits remain executable through
    the runner's existing asymmetric entry gate.
    """
    shape = panel.adjusted_closes.shape
    if scores.shape != shape or allow_increase.shape != shape:
        raise ValueError("selection inputs must align with the market panel")
    if len(clusters) != len(panel.symbols) or any(not cluster for cluster in clusters):
        raise ValueError("selection requires a cluster for every asset")
    if allow_increase.dtype != np.dtype(bool):
        raise ValueError("entry eligibility must be boolean")
    if np.any(np.isfinite(scores) & ((scores < 0) | (scores > 1))):
        raise ValueError("selection scores must be bounded preferences in [0, 1]")
    _, _, _, volumes = panel.context_inputs()
    valid_daily = (
        np.isfinite(volumes)
        & (volumes > 0)
        & np.isfinite(panel.raw_closes)
        & (panel.raw_closes > 0)
    )
    valid_window = (
        np.isfinite(panel.window_volumes)
        & (panel.window_volumes > 0)
        & np.isfinite(panel.window_vwaps)
        & (panel.window_vwaps > 0)
    )
    volume = np.where(valid_daily, volumes, np.nan)
    dollars = np.where(valid_daily, volumes * panel.raw_closes, np.nan)
    windows = np.where(valid_window, panel.window_volumes * panel.window_vwaps, np.nan)
    prior_volume = lag(rolling(volume, spec.lookback, "mean"), 1)
    relative = volume / prior_volume
    dollars = np.minimum(dollars, lag(rolling(dollars, spec.lookback, "mean"), 1))
    windows = np.minimum(windows, lag(rolling(windows, spec.lookback, "mean"), 1))
    eligible = np.ones(shape, dtype=bool)
    reasons = np.full(shape, "below_top_k", dtype="<U24")
    gates = (
        ("no_signal", np.isfinite(scores) & (scores > 0)),
        ("existing_entry_gate", allow_increase),
        ("missing_liquidity", np.isfinite(dollars) & np.isfinite(relative) & np.isfinite(windows)),
        ("daily_liquidity", dollars >= spec.min_dollar_volume),
        (
            "relative_volume",
            (relative >= spec.min_relative_volume) & (relative <= spec.max_relative_volume),
        ),
        ("window_liquidity", windows >= spec.min_window_dollar_volume),
    )
    for reason, passed in gates:
        reasons[eligible & ~passed] = reason
        eligible &= passed
    selected = np.zeros(shape, dtype=bool)
    ranks = np.zeros(shape, dtype=int)
    retained = np.zeros(shape[1], dtype=bool)
    for index in range(len(panel)):
        priority = scores[index] + spec.retention_bonus * retained
        ordered = sorted(
            np.flatnonzero(eligible[index]),
            key=lambda column: (-priority[column], str(panel.symbols[column])),
        )
        counts: dict[str, int] = {}
        for rank, column in enumerate(ordered, start=1):
            ranks[index, column] = rank
            cluster = clusters[column]
            if counts.get(cluster, 0) >= spec.max_per_cluster:
                reasons[index, column] = "cluster_limit"
            elif selected[index].sum() < spec.top_k:
                selected[index, column] = True
                reasons[index, column] = "selected"
                counts[cluster] = counts.get(cluster, 0) + 1
        retained = selected[index]
    return SelectionTrace(
        np.where(selected, scores, 0.0),
        eligible,
        selected,
        ranks,
        reasons,
        dollars,
        relative,
        windows,
    )
