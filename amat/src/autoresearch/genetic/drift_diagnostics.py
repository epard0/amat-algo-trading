"""Pure explanations of frozen prevalence monitoring, without policy changes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from autoresearch.genetic.shadow_inference import MATRIX_DIMENSIONS, ShadowMonitorPolicy


def explain_prevalence_drift(
    values: np.ndarray,
    symbols: Sequence[str],
    reference_stats: dict[str, Any],
    policy: ShadowMonitorPolicy,
    *,
    reference_window_sessions: int | None = None,
) -> dict[str, Any]:
    """Explain the monitor's strict absolute positive-fraction shift threshold.

    Only finite observations enter the positive fraction, matching the monitor.
    Counts of reference positives cannot be recovered reliably from a rounded
    fraction, so they are not inferred. This report grants no suspension reset,
    calibration, execution, or promotion authority.
    """
    if (
        values.ndim != MATRIX_DIMENSIONS
        or not len(values)
        or values.shape[1] != len(symbols)
        or len(set(symbols)) != len(symbols)
    ):
        raise ValueError("diagnostics need nonempty aligned values and unique symbols")
    if reference_window_sessions is not None and (
        type(reference_window_sessions) is not int or reference_window_sessions <= 0
    ):
        raise ValueError("reference window sessions must be a positive integer")
    rows: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols):
        if symbol not in reference_stats:
            raise ValueError(f"missing reference statistics: {symbol}")
        previous = reference_stats[symbol].get("positive_fraction")
        if previous is not None and (
            type(previous) not in (int, float)
            or not math.isfinite(previous)
            or not 0 <= previous <= 1
        ):
            raise ValueError("reference positive fraction must be null or finite in [0, 1]")
        column = values[:, index]
        finite = column[np.isfinite(column)]
        positives = int(np.sum(finite > 0))
        now = positives / len(finite) if len(finite) else None
        signed = now - previous if now is not None and previous is not None else None
        absolute = abs(signed) if signed is not None else None
        margin = absolute - policy.max_prevalence_shift if absolute is not None else None
        rows.append(
            {
                "symbol": str(symbol),
                "window_rows": len(column),
                "finite_count": len(finite),
                "unavailable_count": len(column) - len(finite),
                "positive_count": positives,
                "available_fraction": len(finite) / len(column),
                "reference_positive_fraction": previous,
                "observed_positive_fraction": now,
                "signed_shift": signed,
                "absolute_shift": absolute,
                "threshold": policy.max_prevalence_shift,
                "threshold_margin": margin,
                "threshold_exceeded": margin > 0 if margin is not None else False,
                "comparison_available": absolute is not None,
                "latest_available": bool(np.isfinite(column[-1])),
            }
        )
    return {
        "schema_version": "genetic-prevalence-diagnostics/1",
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "suspension_reset_authority": "none",
        "reference_window_sessions": reference_window_sessions,
        "comparison": "abs(observed positive fraction - reference positive fraction) > threshold",
        "positive_definition": "finite pattern value strictly greater than zero",
        "interpretation": (
            "Frozen distribution-shift heuristic, not a significance test or profitability claim. "
            "Rolling windows may overlap and observations may be serially dependent. "
            "Unavailable comparisons do not establish healthy monitoring."
        ),
        "assets": rows,
    }
