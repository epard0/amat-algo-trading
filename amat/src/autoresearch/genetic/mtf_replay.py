"""Offline multi-timeframe score experiments through the authoritative daily runner."""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from itertools import pairwise
from typing import Any

import exchange_calendars as xcals  # type: ignore[import-untyped]
import numpy as np

from amat.backtest.exits import ExitPolicy, ExitRules
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult, DecisionOverlay, run_backtest
from autoresearch.contracts import Candidate
from autoresearch.experiments import ENTRY_PARTICIPATION_CAP, Simulation, measured, shifted
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.multitimeframe import _bounds
from autoresearch.market_scenarios import panel_digest
from autoresearch.signals import SignalTrace

TIMEFRAMES = ("15m", "1h", "1d", "1w", "1mo")
MAX_EFFECT = 0.25


def align_closed_values(  # noqa: PLR0912 - explicit causal alignment validation
    panel: MarketPanel,
    timeframe_panel: MarketPanel,
    values: np.ndarray,
    *,
    timeframe: str | None = None,
) -> np.ndarray:
    """Use the latest closed grid row, preserving its missing values.

    Intraday rows are interval-end timestamps; no earlier session's intraday row
    is reused. Daily/weekly/monthly rows are completed-period final session dates
    and remain available until the next supplied closed period. Callers must
    supply the complete expected period grid, including rows with missing values.
    """
    if not panel.sessions or not timeframe_panel.sessions:
        raise ValueError("MTF alignment requires nonempty daily and timeframe grids")
    if panel.symbols != timeframe_panel.symbols:
        raise ValueError("MTF alignment requires identical ordered symbols")
    if values.shape != timeframe_panel.adjusted_closes.shape or np.isinf(values).any():
        raise ValueError("MTF values must match timeframe shape without infinities")
    if any(isinstance(day, dt.datetime) for day in panel.sessions):
        raise ValueError("MTF replay target must use daily session dates")
    days = panel.sessions
    if any(a >= b for a, b in pairwise(days)):
        raise ValueError("daily sessions must increase strictly")
    source = timeframe_panel.sessions
    intraday = isinstance(source[0], dt.datetime)
    if any(isinstance(day, dt.datetime) != intraday for day in source):
        raise ValueError("MTF source timestamps cannot mix dates and datetimes")
    frame = timeframe or ("15m" if intraday else "1d")
    if frame not in TIMEFRAMES or intraday != (frame in ("15m", "1h")):
        raise ValueError("MTF timeframe must match source date semantics")
    source_days = [day.date() if isinstance(day, dt.datetime) else day for day in source]
    calendar_start, calendar_end = min(days[0], source_days[0]), max(days[-1], source_days[-1])
    if frame in ("1w", "1mo"):
        calendar_start = _bounds(calendar_start, frame)[0]
        calendar_end = _bounds(calendar_end, frame)[1]
    schedule = xcals.get_calendar("XNYS", start=str(calendar_start), end=str(calendar_end)).schedule
    closes = {day.date(): row["close"].to_pydatetime() for day, row in schedule.iterrows()}
    if any(day not in closes for day in (*days, *source_days)):
        raise ValueError("MTF rows must belong to XNYS sessions")
    stamps = []
    for day in source:
        if isinstance(day, dt.datetime):
            if day.tzinfo is None or day.utcoffset() is None:
                raise ValueError("MTF interval ends must be timezone-aware")
            stamps.append(day.astimezone(dt.UTC))
        else:
            stamps.append(closes[day])
    if any(a >= b for a, b in pairwise(stamps)):
        raise ValueError("MTF source rows must increase strictly")
    periods: dict[tuple[dt.date, dt.date], dt.date] = {}
    if frame in ("1w", "1mo"):
        for day in closes:
            periods[_bounds(day, frame)] = day
    result = np.full(panel.adjusted_closes.shape, np.nan)
    index = -1
    for row, day in enumerate(days):
        cutoff = closes[day]
        while index + 1 < len(stamps) and stamps[index + 1] <= cutoff:
            index += 1
        expected = (
            max((last for last in periods.values() if last <= day), default=None)
            if periods
            else day
        )
        if index >= 0 and stamps[index].date() == expected:
            result[row] = values[index]
    result.flags.writeable = False
    return result


def blend_mtf(
    base: np.ndarray,
    scores_by_tf: Mapping[str, np.ndarray],
    weights: Mapping[str, float],
    max_effect: float,
) -> np.ndarray:
    """Fixed-weight bounded contribution; unavailable views contribute zero."""
    if not np.isfinite(max_effect) or not 0 <= max_effect <= MAX_EFFECT:
        raise ValueError("MTF max_effect must be within [0, 0.25]")
    if set(weights) - set(TIMEFRAMES) or set(weights) - set(scores_by_tf):
        raise ValueError("MTF weights require supported supplied timeframes")
    if any(not np.isfinite(weight) or weight < 0 for weight in weights.values()):
        raise ValueError("MTF weights must be finite nonnegative values")
    if sum(weights.values()) > 1 + 1e-12:
        raise ValueError("MTF weights may sum to at most one")
    contribution = np.zeros_like(base, dtype=float)
    for frame, weight in weights.items():
        values = scores_by_tf[frame]
        if values.shape != base.shape or np.isinf(values).any():
            raise ValueError("MTF matrix shape or finite-value contract mismatch")
        finite = np.isfinite(values)
        if np.any(np.abs(values[finite]) > 1 + 1e-12):
            raise ValueError("MTF values must lie in [-1, 1]")
        contribution += weight * np.where(finite, values, 0)
    result = base.copy()
    active = np.isfinite(base) & (base > 0)
    result[active] = np.clip(base[active] + max_effect * contribution[active], 0, 1)
    return result


@dataclass(frozen=True)
class PreparedReplay:
    simulation: Simulation
    candidate: Candidate
    trace: SignalTrace
    overlay: DecisionOverlay


def prepare_replay(simulation: Simulation, candidate: Candidate) -> PreparedReplay:
    """Cache candidate signal fitting and original regime/liquidity/entry overlays."""
    return PreparedReplay(
        simulation, candidate, simulation.trace(candidate), simulation.overlay(candidate)
    )


def replay_prepared(
    prepared: PreparedReplay,
    *,
    scores_by_tf: Mapping[str, np.ndarray] | None = None,
    weights: Mapping[str, float] | None = None,
    max_effect: float = 0,
    exit_rules: ExitPolicy | None = None,
    cost: float = 2,
    delay: int = 0,
    scalar: float = 1,
) -> BacktestResult:
    """Replay research recipes with unchanged daily execution and accounting."""
    if not np.isfinite(cost) or cost < 0 or delay < 0 or not isinstance(delay, int):
        raise ValueError("research cost and delay must be nonnegative")
    if not np.isfinite(scalar) or not 0 <= scalar <= 1:
        raise ValueError("research scalar must be within [0, 1]")
    simulation, candidate = prepared.simulation, prepared.candidate
    scores = blend_mtf(prepared.trace.scores, scores_by_tf or {}, weights or {}, max_effect)
    overlay = prepared.overlay
    selection = simulation.selection(candidate, scores=scores)
    if selection is not None:
        if (
            candidate.signal
            and candidate.signal.selection
            and candidate.signal.selection.mode == "entries"
        ):
            overlay = replace(overlay, allow_increase=overlay.allow_increase & selection.selected)
        else:
            scores = selection.scores
    overlay = DecisionOverlay(
        shifted(overlay.risk_multipliers, delay) * scalar,
        shifted(overlay.allow_increase, delay),
        shifted(overlay.increase_multipliers, delay)
        if overlay.increase_multipliers is not None
        else None,
    )
    result = run_backtest(
        candidate.config.strategy,
        simulation.panel,
        candidate.config.strategy.to_universe(),
        actions_by_session=simulation.actions,
        decision_overlay=overlay,
        one_way_bps=cost,
        max_entry_window_participation=ENTRY_PARTICIPATION_CAP
        if simulation.program.schema_version
        in ("strategy-development/7", "strategy-development/8", "strategy-development/9")
        else None,
        signal_override=shifted(scores, delay)
        if candidate.signal
        or candidate.genetic_pattern
        or delay
        or (max_effect and any((weights or {}).values()))
        else None,
        exit_rules=exit_rules,
    )
    return measured(result, simulation.program)


def recipe_digest(
    prepared: PreparedReplay,
    *,
    scores_by_tf: Mapping[str, np.ndarray],
    weights: Mapping[str, float],
    max_effect: float,
    contracts: Mapping[str, Any],
    exit_rules: ExitPolicy | None = None,
    cost: float = 2,
    delay: int = 0,
    scalar: float = 1,
) -> str:
    """Bind recipe identity to candidate, dates, matrix bytes, and data semantics."""
    blend_mtf(prepared.trace.scores, scores_by_tf, weights, max_effect)
    matrices = {}
    for frame, values in sorted(scores_by_tf.items()):
        normalized = np.asarray(values, dtype="<f8").copy()
        normalized[np.isnan(normalized)] = np.nan
        matrices[frame] = {
            "shape": list(values.shape),
            "sha256": hashlib.sha256(normalized.tobytes()).hexdigest(),
        }
    exits = (
        asdict(exit_rules)
        if isinstance(exit_rules, ExitRules)
        else {str(symbol): asdict(rules) for symbol, rules in sorted((exit_rules or {}).items())}
    )
    return digest_json(
        {
            "schema_version": "mtf-research-replay/1",
            "execution_authority": "none",
            "candidate": prepared.candidate.digest(),
            "program": prepared.simulation.program.model_dump(mode="json"),
            "panel": panel_digest(prepared.simulation.panel),
            "matrices": matrices,
            "contracts": dict(contracts),
            "weights": dict(weights),
            "max_effect": max_effect,
            "exits": exits,
            "cost": cost,
            "delay": delay,
            "scalar": scalar,
        }
    )
