"""Inference-only pattern observations and frozen, report-only health diagnostics."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from time import perf_counter_ns
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.contracts import Candidate

MONITOR_WINDOW = 20
MATRIX_DIMENSIONS = 2
CONSTANT_TOLERANCE = 1e-12


@dataclass(frozen=True)
class ShadowMonitorPolicy:
    """Frozen thresholds; exceeding one suspends contribution in the report."""

    min_available_fraction: float = 0.8
    max_abs_mean_z: float = 4.0
    max_prevalence_shift: float = 0.35
    max_score_change: float = 0.5

    def __post_init__(self) -> None:
        for name in ("min_available_fraction", "max_prevalence_shift", "max_score_change"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be finite and in [0, 1]")
        if not math.isfinite(self.max_abs_mean_z) or self.max_abs_mean_z <= 0:
            raise ValueError("max_abs_mean_z must be finite and positive")


def _number(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def calibrate_values(values: np.ndarray, symbols: Sequence[str]) -> dict[str, Any]:
    """Summarize supplied calibration rows without fitting an expression."""
    if values.ndim != MATRIX_DIMENSIONS or values.shape[1] != len(symbols) or not len(values):
        raise ValueError("calibration needs nonempty aligned values and symbols")
    result: dict[str, Any] = {}
    for index, symbol in enumerate(symbols):
        column = values[:, index]
        available = column[np.isfinite(column)]
        result[str(symbol)] = {
            "available_fraction": float(len(available) / len(column)),
            "min": float(available.min()) if len(available) else None,
            "max": float(available.max()) if len(available) else None,
            "mean": float(available.mean()) if len(available) else None,
            "std": float(available.std()) if len(available) else None,
            "positive_fraction": float(np.mean(available > 0)) if len(available) else None,
        }
    return result


def monitor_values(
    values: np.ndarray,
    symbols: Sequence[str],
    reference_stats: dict[str, Any],
    policy: ShadowMonitorPolicy,
) -> dict[str, Any]:
    """Compare a trailing observation window with frozen per-asset summaries."""
    observed = calibrate_values(values, symbols)
    alerts: list[dict[str, str]] = []
    for index, symbol in enumerate(symbols):
        reference, current = reference_stats[str(symbol)], observed[str(symbol)]
        available = current["available_fraction"]
        if available < policy.min_available_fraction or not np.isfinite(values[-1, index]):
            alerts.append({"symbol": str(symbol), "code": "pattern_unavailable"})
        mean, std = reference["mean"], reference["std"]
        z_score: float | None = None
        if current["mean"] is not None and mean is not None and std is not None:
            difference = abs(current["mean"] - mean)
            # A constant calibration has no estimated spread. Any material change
            # is a drift alert; never divide by zero or write Infinity to JSON.
            drifted = (
                difference > policy.max_abs_mean_z * std
                if std > 0
                else difference > CONSTANT_TOLERANCE
            )
            z_score = difference / std if std > 0 else (0.0 if not drifted else None)
            if drifted:
                alerts.append({"symbol": str(symbol), "code": "pattern_mean_drift"})
        else:
            alerts.append({"symbol": str(symbol), "code": "calibration_unavailable"})
        current["absolute_mean_z"] = z_score
        current["constant_calibration"] = std == 0
        previous, now = reference["positive_fraction"], current["positive_fraction"]
        shift = abs(now - previous) if previous is not None and now is not None else None
        current["positive_fraction_shift"] = shift
        if shift is not None and shift > policy.max_prevalence_shift:
            alerts.append({"symbol": str(symbol), "code": "positive_prevalence_drift"})
    return {"window_sessions": len(values), "per_asset": observed, "alerts": alerts}


def _prefix(panel: MarketPanel, stop: int) -> MarketPanel:
    arrays: dict[str, Any] = {
        name: None if getattr(panel, name) is None else getattr(panel, name)[:stop]
        for name in (
            "adjusted_closes",
            "raw_closes",
            "window_vwaps",
            "window_volumes",
            "adjusted_highs",
            "adjusted_lows",
            "session_volumes",
            "adjusted_opens",
        )
    }
    return replace(panel, sessions=panel.sessions[:stop], **arrays)


def _probe_parity(artifact: dict[str, Any], panel: MarketPanel) -> bool:
    from autoresearch.genetic.shadow_artifact import verify_calibration  # noqa: PLC0415

    stop = next(
        (
            index + 1
            for index, date in enumerate(panel.sessions)
            if str(date) == artifact["payload"]["calibration"]["end"]
        ),
        None,
    )
    if stop is None:
        raise ValueError("panel must include the complete frozen calibration history")
    try:
        verify_calibration(artifact, _prefix(panel, stop))
    except ValueError:
        return False
    return True


def infer_shadow(artifact: dict[str, Any], panel: MarketPanel) -> dict[str, Any]:
    """Observe the latest completed input session; never produce an order or fit.

    The caller supplies completed sessions only. Dates are not interpreted as a
    market-clock claim here. Calibration-prefix probes catch changed historical
    inputs, while the artifact validator binds code, candidate and monitor policy.
    """
    from autoresearch.genetic.shadow_artifact import (  # noqa: PLC0415
        evaluate_shadow,
        validate_shadow_artifact,
    )

    started = perf_counter_ns()
    artifact = validate_shadow_artifact(artifact)
    payload = artifact["payload"]
    symbols = [str(symbol) for symbol in panel.symbols]
    if symbols != payload["universe"]:
        raise ValueError("shadow inference universe/order differs from frozen artifact")
    if not panel.sessions or str(panel.sessions[0]) != payload["calibration"]["start"]:
        raise ValueError("shadow inference must start at the frozen calibration start")
    candidate = Candidate.model_validate(payload["candidate"])
    policy = ShadowMonitorPolicy(**payload["monitor_policy"])
    parity = _probe_parity(artifact, panel)
    values, baseline, modified = evaluate_shadow(candidate, panel)
    first = next(
        (
            index
            for index, date in enumerate(panel.sessions)
            if str(date) > payload["calibration"]["end"]
        ),
        len(panel),
    )
    # A calibration-end observation can verify wiring but is not forward evidence.
    stop = max(first, len(panel) - MONITOR_WINDOW)
    window = values[stop:] if first < len(panel) else values[-1:]
    monitor = monitor_values(window, symbols, payload["reference_stats"], policy)
    alerts = monitor["alerts"]
    if not parity:
        alerts.append({"symbol": "*", "code": "reference_probe_mismatch"})
    score_change = float(np.mean(abs(modified[-1] - modified[-2]))) if len(modified) > 1 else 0.0
    if score_change > policy.max_score_change:
        alerts.append({"symbol": "*", "code": "score_change_exceeded"})
    suspended = bool(alerts)
    effective = baseline[-1] if suspended else modified[-1]
    rows = [
        {
            "symbol": symbol,
            "pattern_value": _number(values[-1, index]),
            "baseline_score": _number(baseline[-1, index]),
            "candidate_score": _number(modified[-1, index]),
            "reported_score": _number(effective[index]),
            "bounded_contribution": _number(effective[index] - baseline[-1, index]),
            "available": bool(np.isfinite(values[-1, index])),
        }
        for index, symbol in enumerate(symbols)
    ]
    return {
        "schema_version": "shadow-pattern-observation/1",
        "artifact_sha256": artifact["payload_sha256"],
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "mode": "report_only",
        "session": str(panel.sessions[-1]),
        "panel_rows": len(panel),
        "forward_sessions": len(panel) - first,
        "required_history": payload["required_history"],
        "warmup_complete": len(panel) > payload["required_history"],
        "reference_probe_parity": parity,
        "recommended_monitor_state": "suspended" if suspended else "observing",
        "monitor": monitor,
        "score_change_proxy": score_change,
        "score_change_proxy_definition": (
            "mean absolute daily candidate-score change; not trade or portfolio turnover"
        ),
        "assets": rows,
        "latency_ns": perf_counter_ns() - started,
    }
