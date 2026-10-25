"""Causal group rotation: completed weeks for ranking, completed days for risk.

The prior week's last observed session becomes available only when a new exchange
week starts. This conservative delay never mistakes a partial week for a completed
one, including holiday weeks. Calendar labels never read the next price row.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from itertools import pairwise

import numpy as np

from amat.config.models import StrategyConfig
from amat.errors import DataIntegrityError
from amat.portfolio.cross_section import horizon_agreement


def _ratio(values: np.ndarray) -> float:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median))) * 1.4826
    return float(np.clip(np.mean(values) / max(mad, 1e-6), -20, 20))


def volatility_switch_gate(prices: np.ndarray) -> np.ndarray:
    """Completed-close volatility gate; exact retained research rule, no future data."""
    returns = np.diff(np.log(prices), axis=0)
    gate = np.zeros_like(prices, dtype=bool)
    for t in range(60, len(prices)):
        fast = np.std(returns[t - 20 : t], axis=0, ddof=1)
        slow = np.std(returns[t - 60 : t], axis=0, ddof=1)
        gate[t] = (fast * np.sqrt(252) > 0.25) | (fast > 1.5 * slow)  # noqa: PLR2004
    return gate


def rotation_inputs(  # noqa: PLR0912, PLR0915 - one explicit causal state pass
    strategy: StrategyConfig,
    prices: np.ndarray,
    scores: np.ndarray,
    sigma: np.ndarray,
    horizons: np.ndarray | None,
    *,
    sessions: Sequence[dt.date],
    switch_cost_gate: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    config = strategy.adaptive_rotation
    symbols = tuple(map(str, strategy.universe.symbols))
    if (
        prices.shape != scores.shape
        or sigma.shape != scores.shape
        or len(sessions) != len(prices)
        or prices.shape[1] != len(symbols)
        or any(b <= a for a, b in pairwise(sessions))
        or not np.all(np.isfinite(prices))
        or np.any(prices <= 0)
    ):
        raise DataIntegrityError(
            "adaptive rotation requires aligned finite prices and ordered sessions"
        )
    if switch_cost_gate is not None and (
        switch_cost_gate.shape != prices.shape or switch_cost_gate.dtype != np.bool_
    ):
        raise DataIntegrityError("switch_cost_gate requires an aligned boolean panel")
    if switch_cost_gate is None and config.switch_condition == "volatility":
        switch_cost_gate = volatility_switch_gate(prices)
    if horizons is not None and horizons.shape[1:] != prices.shape:
        raise DataIntegrityError("adaptive horizon panel is misaligned")
    columns = {symbol: i for i, symbol in enumerate(symbols)}
    groups = [[columns[s] for s in group.symbols] for group in config.groups]
    benchmark = columns[config.benchmark]
    result = np.zeros_like(scores)
    scalars = np.ones(len(prices))
    retained: set[int] = set()
    streaks: dict[int, int] = {}
    weekly: list[int] = []
    first = max(strategy.warmup.min_completed_sessions - 1, strategy.trend.max_horizon_sessions)
    blocked_until, healthy = -1, 0
    off = False
    for t, date in enumerate(sessions):
        if t and date.isocalendar()[:2] != sessions[t - 1].isocalendar()[:2]:
            weekly.append(t - 1)
        eligible = np.isfinite(scores[t]) & (scores[t] > 0) & np.isfinite(sigma[t]) & (sigma[t] > 0)
        if strategy.cross_sectional.min_horizon_agreement:
            if horizons is None:
                raise DataIntegrityError(
                    "adaptive rotation requires configured horizon confirmation"
                )
            eligible &= (
                horizon_agreement(horizons[:, t]) >= strategy.cross_sectional.min_horizon_agreement
            )
        if config.regime_overlay:
            if len(weekly) < config.slow_weeks:
                scalars[t] = 0
            else:
                slow = prices[weekly[-config.slow_weeks :], benchmark]
                weak = prices[t, benchmark] < float(np.mean(slow))
                off = weak or (off and healthy < config.recovery_sessions)
                healthy = 0 if weak else healthy + 1
                shock = (
                    t >= config.shock_sessions
                    and prices[t, benchmark] / prices[t - config.shock_sessions, benchmark] - 1
                    <= config.shock_return
                )
                if shock:
                    blocked_until = t + config.cooldown_sessions
                    healthy = 0
                scalars[t] = min(
                    config.risk_off_scalar if off else 1.0,
                    config.fast_scalar if t <= blocked_until else 1.0,
                )
        if t < first:
            continue
        if (t - first) % strategy.cross_sectional.selection_interval_sessions == 0:
            available = list(range(len(groups)))
            rank_values = scores[t].copy()
            if config.group_selection or config.residual_ranking:
                if len(weekly) < config.lookback_weeks + 1:
                    retained = set()
                    continue
                rows = weekly[-config.lookback_weeks - 1 :]
                returns = prices[rows[1:]] / prices[rows[:-1]] - 1
                strengths = []
                for indices in groups:
                    group_returns = returns[:, indices].mean(axis=1)
                    strengths.append(_ratio(group_returns - returns[:, benchmark]))
                    if config.residual_ranking:
                        for i in indices:
                            rank_values[i] = _ratio(returns[:, i] - group_returns)
                available = sorted(available, key=lambda g: (-strengths[g], g))
                available = [g for g in available if any(eligible[i] for i in groups[g])]
                if config.group_selection:
                    available = available[: config.max_active_groups]
            allowed = {i for g in available for i in groups[g]}
            ordered = sorted(
                (i for i in allowed if eligible[i]), key=lambda i: (-rank_values[i], i)
            )
            if config.hysteresis:
                rank = {i: n for n, i in enumerate(ordered)}
                ordered.sort(
                    key=lambda i: (
                        rank[i] - (config.incumbent_rank_bonus if i in retained else 0),
                        rank[i],
                        i,
                    )
                )
            selected: list[int] = []
            for i in ordered:
                group = next(g for g in groups if i in g)
                if (
                    config.group_selection
                    and sum(j in group for j in selected) >= config.max_per_group
                ):
                    continue
                selected.append(i)
                if len(selected) >= (strategy.cross_sectional.max_positions or len(symbols)):
                    break
            # Confirmation applies only to discretionary replacements. Invalid or
            # newly excluded incumbents exit immediately; empty slots fill normally.
            preferred = set(selected)
            streaks = {i: streaks.get(i, 0) + 1 for i in preferred}
            for incumbent in sorted(retained - preferred):
                if incumbent not in allowed or not eligible[incumbent]:
                    continue
                group = next(g for g in groups if incumbent in g)
                entrants = [i for i in reversed(selected) if i not in retained and i in group]
                if not entrants:
                    continue
                entrant = entrants[0]
                # Five-day projection of trailing 20-session mean log returns is
                # a fixed opportunity proxy, not a calibrated alpha forecast.
                edge = (
                    5
                    / 20
                    * float(
                        np.log(prices[t, entrant] / prices[t - 20, entrant])
                        - np.log(prices[t, incumbent] / prices[t - 20, incumbent])
                    )
                )
                hurdle = 2 * config.switch_cost_bps / 10000 * config.switch_cost_multiple
                cost_active = switch_cost_gate is None or bool(
                    switch_cost_gate[t, entrant] or switch_cost_gate[t, incumbent]
                )
                if streaks[entrant] < config.switch_confirmation_sessions or (
                    cost_active and config.switch_cost_bps > 0 and edge <= hurdle
                ):
                    selected[selected.index(entrant)] = incumbent
            retained = set(selected)
        result[t] = np.where(
            eligible & np.array([i in retained for i in range(len(symbols))]), scores[t], 0
        )
    return result, scalars
