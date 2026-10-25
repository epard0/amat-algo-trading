"""Completed exchange-period pattern views, independent of daily score bindings."""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import replace
from pathlib import Path
from typing import Any

import exchange_calendars as xcals  # type: ignore[import-untyped]
import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.evaluator import evaluate_candles
from autoresearch.genetic.models import CandleGenome, PatternDefinition, canonical_genome_digest
from autoresearch.genetic.shadow_inference import calibrate_values
from autoresearch.genetic.symbolic import evaluate_symbolic, symbolic_required_history
from autoresearch.market_scenarios import panel_digest

TIMEFRAMES = ("1d", "1w", "1mo")


def _bounds(day: dt.date, timeframe: str) -> tuple[dt.date, dt.date]:
    if timeframe == "1w":
        first = day - dt.timedelta(days=day.weekday())
        return first, first + dt.timedelta(days=6)
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def aggregate_completed(panel: MarketPanel, timeframe: str) -> MarketPanel:
    """Input daily bars must already be closed; omit partial first/last periods.

    Weekly/monthly timestamps are the final exchange session, including holiday
    weeks. The calendar is extended to the period end to prevent treating a
    Tuesday prefix as a completed week. No forward fill or gap compression.
    """
    if timeframe not in TIMEFRAMES or not panel.sessions:
        raise ValueError("supported timeframes are 1d, 1w, 1mo on nonempty daily panels")
    first, last = panel.sessions[0], panel.sessions[-1]
    start, _ = _bounds(first, timeframe)
    _, end = _bounds(last, timeframe)
    schedule = xcals.get_calendar("XNYS", start=str(start), end=str(end)).sessions
    days = [stamp.date() for stamp in schedule]
    if tuple(day for day in days if first <= day <= last) != panel.sessions:
        raise ValueError("daily panel has duplicate, missing, or non-exchange sessions")
    panel.candle_inputs()
    if timeframe == "1d":
        return panel
    periods: dict[tuple[dt.date, dt.date], list[dt.date]] = {}
    for day in days:
        periods.setdefault(_bounds(day, timeframe), []).append(day)
    index = {day: i for i, day in enumerate(panel.sessions)}
    groups = [
        [index[day] for day in group]
        for group in periods.values()
        if all(day in index for day in group)
    ]
    width = len(panel.symbols)

    def reduced(name: str, operation: str) -> np.ndarray:
        array = getattr(panel, name)
        if array is None:
            return np.full((len(groups), width), np.nan)
        rows = []
        for group in groups:
            values = array[group]
            if operation == "first":
                row = values[0]
            elif operation == "last":
                row = values[-1]
            elif operation == "max":
                row = values.max(axis=0)
            elif operation == "min":
                row = values.min(axis=0)
            else:
                row = values.sum(axis=0)
            # Missing observations anywhere in a period remain unavailable.
            rows.append(np.where(np.isfinite(values).all(axis=0), row, np.nan))
        return np.asarray(rows).reshape(len(groups), width)

    return replace(
        panel,
        sessions=tuple(panel.sessions[group[-1]] for group in groups),
        adjusted_opens=reduced("adjusted_opens", "first"),
        adjusted_highs=reduced("adjusted_highs", "max"),
        adjusted_lows=reduced("adjusted_lows", "min"),
        adjusted_closes=reduced("adjusted_closes", "last"),
        raw_closes=reduced("raw_closes", "last"),
        session_volumes=reduced("session_volumes", "sum"),
        # Execution-window features have no defined coarse-period equivalent.
        window_vwaps=np.full((len(groups), width), np.nan),
        window_volumes=np.full((len(groups), width), np.nan),
        source=f"completed-XNYS-{timeframe}({panel.source})",
    )


def _values(pattern: PatternDefinition, panel: MarketPanel) -> np.ndarray:
    if isinstance(pattern.genome, CandleGenome):
        return evaluate_candles(
            pattern, dict(zip(("open", "high", "low", "close"), panel.candle_inputs(), strict=True))
        )
    return evaluate_symbolic(pattern, panel)


def _view(
    pattern: PatternDefinition, reference: MarketPanel, current: MarketPanel, timeframe: str
) -> dict[str, Any]:
    derived = PatternDefinition.model_validate(
        {**pattern.model_dump(by_alias=True), "timeframe": timeframe}
    )
    history = (
        pattern.genome.max_lag
        if isinstance(pattern.genome, CandleGenome)
        else symbolic_required_history(derived)
    )
    latest = np.full(len(current.symbols), np.nan)
    stats = None
    if len(current) > history:
        latest = _values(derived, current)[-1]
    if len(reference) > history:
        stats = calibrate_values(_values(derived, reference)[history:], reference.symbols)
    ready = stats is not None and all(
        stats[str(symbol)]["mean"] is not None and np.isfinite(latest[i])
        for i, symbol in enumerate(current.symbols)
    )
    return {
        "pattern": derived.model_dump(mode="json", by_alias=True),
        "pattern_sha256": canonical_genome_digest(derived),
        "status": "ready" if ready else "unavailable_or_insufficient_history",
        "required_bars": history + 1,
        "calibration_bars": len(reference),
        "available_bars": len(current),
        "latest_closed_session": current.sessions[-1].isoformat() if len(current) else None,
        "calibration_last_session": reference.sessions[-1].isoformat() if len(reference) else None,
        "reference_stats": stats,
        "assets": [
            {
                "symbol": str(symbol),
                "pattern_value": float(latest[i]) if np.isfinite(latest[i]) else None,
                "calibrated": stats is not None and stats[str(symbol)]["mean"] is not None,
            }
            for i, symbol in enumerate(current.symbols)
        ],
        "historical_validation_transferred": False,
    }


def multi_timeframe_report(
    pattern: PatternDefinition,
    calibration_panel: MarketPanel,
    panel: MarketPanel,
    *,
    timeframes: tuple[str, ...] = TIMEFRAMES,
) -> dict[str, Any]:
    """Spot the frozen expression at each scale; never transfer validation claims.

    Each derived pattern explicitly records its timeframe. Lookbacks and lags
    count bars of that timeframe. Calibration ends at the original frozen cutoff.
    These views cannot alter the daily adapter, fit the genome, or produce orders.
    """
    if pattern.timeframe != "1d" or pattern.price_basis != "corporate_action_adjusted":
        raise ValueError("multi-timeframe views require an adjusted daily source pattern")
    if not timeframes or len(set(timeframes)) != len(timeframes):
        raise ValueError("timeframes must be nonempty and unique")
    views = {}
    for timeframe in timeframes:
        current = aggregate_completed(panel, timeframe)
        reference = aggregate_completed(calibration_panel, timeframe)
        views[timeframe] = _view(pattern, reference, current, timeframe)
    protocol = {
        "schema_version": "genetic-multi-timeframe/1",
        "timeframes": list(timeframes),
        "calendar": "XNYS",
        "partial_periods": "omit",
        "lookback_units": "bars_of_each_timeframe",
        "execution_window_features": "unavailable_on_weekly_and_monthly",
        "source_pattern_sha256": canonical_genome_digest(pattern),
    }
    return {
        "protocol": protocol,
        "protocol_sha256": digest_json(protocol),
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "views": views,
    }


def freeze_intraday_reference(path: Path, cutoff: dt.date) -> dict[str, Any]:
    """Bind separate intraday calibration; no daily validation claims are inherited."""
    from autoresearch.genetic.shadow_intraday import (  # noqa: PLC0415
        aggregate_intraday,
        load_intraday,
    )

    payload = load_intraday(path)
    if (
        not dt.date.fromisoformat(payload["start"])
        <= cutoff
        <= dt.date.fromisoformat(payload["end"])
    ):
        raise ValueError("intraday calibration cutoff must be inside source coverage")
    as_of = dt.datetime.combine(cutoff + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC)
    panels = {tf: aggregate_intraday(payload, tf, as_of=as_of) for tf in ("15m", "1h")}
    return {
        "schema_version": "shadow-intraday-reference/1",
        "universe": payload["symbols"],
        "source_start": payload["start"],
        "calibration_end": cutoff.isoformat(),
        "calibration_sha256": {tf: panel_digest(panel) for tf, panel in panels.items()},
        "calibration_bars": {tf: len(panel) for tf, panel in panels.items()},
    }


def intraday_timeframe_report(
    pattern: PatternDefinition,
    reference: dict[str, Any],
    path: Path,
    *,
    as_of: dt.datetime,
) -> dict[str, Any]:
    """Verify calibration parity and evaluate 15-minute/hourly closed intervals."""
    from autoresearch.genetic.shadow_intraday import (  # noqa: PLC0415
        aggregate_intraday,
        aggregation_metadata,
        load_intraday,
    )

    if pattern.price_basis != "corporate_action_adjusted":
        raise ValueError("intraday views require an adjusted source pattern")
    cutoff = dt.date.fromisoformat(reference["calibration_end"])
    checked = freeze_intraday_reference(path, cutoff)
    if checked != reference:
        raise ValueError("intraday frozen calibration changed")
    end = dt.datetime.combine(cutoff + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC)
    if as_of.tzinfo is None or as_of < end:
        raise ValueError("intraday observation must follow frozen calibration")
    payload = load_intraday(path)
    views = {}
    for tf in ("15m", "1h"):
        current = aggregate_intraday(payload, tf, as_of=as_of)
        calibration = aggregate_intraday(payload, tf, as_of=end)
        views[tf] = {
            **_view(pattern, calibration, current, tf),
            "aggregation": aggregation_metadata(payload, tf, as_of=as_of),
        }
        schedule = xcals.get_calendar(
            "XNYS", start=payload["start"], end=as_of.date().isoformat()
        ).schedule
        width = dt.timedelta(minutes=15 if tf == "15m" else 60)
        expected = None
        for _, row in schedule.iterrows():
            opened = row["open"].to_pydatetime()
            closed = min(row["close"].to_pydatetime(), as_of)
            count = int((closed - opened) // width)
            if count > 0:
                expected = opened + count * width
        stale = current.sessions[-1] != expected
        views[tf]["expected_latest_close"] = expected.isoformat() if expected else None
        views[tf]["stale"] = stale
        if stale:
            views[tf]["status"] = "stale_source"
    protocol = {
        "schema_version": "genetic-intraday-views/1",
        "source_pattern_sha256": canonical_genome_digest(pattern),
        "calibration": reference,
        "timeframes": ["15m", "1h"],
        "lookback_units": "completed regular-session bars across sessions",
        "raw_price_and_execution_features": "unavailable",
    }
    return {
        "protocol": protocol,
        "protocol_sha256": digest_json(protocol),
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "views": views,
    }


def spot_intraday(
    artifact_path: Path,
    data: Path,
    calibration_end: dt.date,
    *,
    as_of: dt.datetime,
    output: Path,
) -> dict[str, Any]:
    """Manual historical diagnostic; prospective evidence requires a campaign."""
    from autoresearch.contracts import Candidate  # noqa: PLC0415
    from autoresearch.genetic.shadow_artifact import load_shadow_artifact  # noqa: PLC0415
    from autoresearch.genetic.shadow_campaign import _immutable  # noqa: PLC0415
    from autoresearch.genetic.shadow_intraday import load_intraday  # noqa: PLC0415

    if output.resolve() in {artifact_path.resolve(), data.resolve()} or output.suffix != ".json":
        raise ValueError("intraday spot output must be a distinct JSON file")
    if as_of.tzinfo is None or as_of > dt.datetime.now(dt.UTC):
        raise ValueError("intraday as_of must be timezone-aware and not in the future")
    artifact = load_shadow_artifact(artifact_path)
    candidate = Candidate.model_validate(artifact["payload"]["candidate"])
    if candidate.genetic_pattern is None:
        raise ValueError("intraday spotting requires a frozen pattern")
    reference = freeze_intraday_reference(data, calibration_end)
    if reference["universe"] != artifact["payload"]["universe"]:
        raise ValueError("intraday universe differs from frozen artifact")
    report = intraday_timeframe_report(
        candidate.genetic_pattern.pattern, reference, data, as_of=as_of
    )
    report.update(
        {
            "artifact_sha256": artifact["payload_sha256"],
            "data_sha256": digest_json(load_intraday(data)),
            "as_of": as_of.isoformat(),
            "evidence_level": "historical_intraday_shadow_diagnostic",
        }
    )
    envelope = {"payload": report, "payload_sha256": digest_json(report)}
    _immutable(output, envelope)
    return envelope
