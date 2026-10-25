"""Descriptive slices of one continuous run; never independent backtests."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np

MIN_DAILY_ROWS = 2


def period_reports(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude the equity anchor, retaining each boundary's first return.

    Blocks have equal observation counts to within one session. No positions,
    warmup, model fits, or selection clocks are reset at a reporting boundary.
    Completeness against an exchange calendar is deliberately not inferred.
    """
    if len(daily) < MIN_DAILY_ROWS:
        return []
    dates = [dt.date.fromisoformat(row["session"]) for row in daily]
    if dates != sorted(set(dates)):
        raise ValueError("period sessions must be unique and chronological")
    equity = np.asarray([float(row["equity"]) for row in daily])
    returns = np.asarray([float(row["return"]) for row in daily[1:]])
    if not np.isfinite(equity).all() or np.any(equity <= 0):
        raise ValueError("period equity must be finite and positive")
    if not np.isfinite(returns).all() or not np.allclose(
        returns, equity[1:] / equity[:-1] - 1, atol=1e-12, rtol=1e-10
    ):
        raise ValueError("period returns disagree with equity")
    quarters: dict[str, list[int]] = {}
    for index, date in enumerate(dates[1:], 1):
        quarters.setdefault(f"{date.year}-Q{(date.month - 1) // 3 + 1}", []).append(index)
    groups = [("full", "full", list(range(1, len(daily))))]
    groups.extend(("calendar_quarter", label, indices) for label, indices in quarters.items())
    groups.extend(
        ("historical_block", f"block-{index}", block.tolist())
        for index, block in enumerate(np.array_split(np.arange(1, len(daily)), 4), 1)
        if len(block)
    )
    result = []
    for kind, label, indices in groups:
        start, end = indices[0], indices[-1]
        sample = daily[start : end + 1]
        path = equity[start - 1 : end + 1]
        turnover = None
        if all("fills" in row for row in sample):
            turnover = sum(
                sum(abs(float(fill["price"]) * float(fill["quantity"])) for fill in row["fills"])
                / equity[index - 1]
                for index, row in zip(indices, sample, strict=True)
            )
        exposures = [float(row["gross_exposure"]) for row in sample if "gross_exposure" in row]
        mean_exposure = float(np.mean(exposures)) if len(exposures) == len(sample) else None
        if mean_exposure is not None and not math.isfinite(mean_exposure):
            raise ValueError("period exposure must be finite")
        result.append(
            {
                "kind": kind,
                "period": label,
                "start": str(dates[start]),
                "end": str(dates[end]),
                "observations": len(sample),
                "total_return": float(path[-1] / path[0] - 1),
                "cumulative_return": float(path[-1] / equity[0] - 1),
                "max_drawdown": float(np.min(path / np.maximum.accumulate(path) - 1)),
                "one_way_turnover": turnover,
                "mean_gross_exposure": mean_exposure,
                "calendar_completeness": "not_verified",
                "interpretation": "continuous-run descriptive slice; not independent validation",
            }
        )
    return result
