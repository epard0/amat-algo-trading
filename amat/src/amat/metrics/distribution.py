"""Sample return shapes and exact empirical lower-tail losses.

VaR/CVaR are nonnegative simple-return losses per session, with no Gaussian
assumption or annualization. CVaR averages exactly the worst alpha fraction
of empirical probability mass, including a fractional boundary observation.
Shape moments use population divisors and describe only the supplied sample.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _tail_loss(ordered: np.ndarray, alpha: float) -> tuple[float, float]:
    mass = alpha * len(ordered)
    whole = int(np.floor(mass))
    fractional = mass - whole
    tail = float(ordered[:whole].sum())
    if fractional:
        tail += fractional * float(ordered[whole])
    # Inverse empirical CDF, consistent with the mass used by expected shortfall.
    threshold = float(ordered[max(0, int(np.ceil(mass)) - 1)])
    return max(0.0, -threshold), max(0.0, -tail / mass)


def return_distribution_summary(returns: np.ndarray) -> dict[str, Any]:
    """Summarize one finite simple-return series; constant shape is undefined."""
    values = np.asarray(returns, dtype=float)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("return diagnostics require a finite nonempty one-dimensional series")
    centered = values - values.mean()
    scale = float(np.sqrt(np.mean(centered**2))) if np.any(values != values[0]) else 0.0
    standardized = centered / scale if scale > 0 else None
    squared = centered**2
    autocorrelation = None
    if scale > 0 and len(squared) > 2 and squared[:-1].std() > 0 and squared[1:].std() > 0:  # noqa: PLR2004
        autocorrelation = float(np.corrcoef(squared[:-1], squared[1:])[0, 1])
    ordered = np.sort(values)
    var95, cvar95 = _tail_loss(ordered, 0.05)
    var99, cvar99 = _tail_loss(ordered, 0.01)
    return {
        "observations": len(values),
        "mean": float(values.mean()),
        "standard_deviation": scale,
        "skewness": float(np.mean(standardized**3)) if standardized is not None else None,
        "excess_kurtosis": (
            float(np.mean(standardized**4) - 3) if standardized is not None else None
        ),
        "squared_return_autocorrelation_lag1": autocorrelation,
        "worst_session_return": float(ordered[0]),
        "best_session_return": float(ordered[-1]),
        "quantiles": {
            f"p{percentile:02d}": float(np.percentile(values, percentile))
            for percentile in (1, 5, 50, 95, 99)
        },
        "loss_var_95": var95,
        "loss_cvar_95": cvar95,
        "loss_var_99": var99,
        "loss_cvar_99": cvar99,
        "tail_observations_95": 0.05 * len(values),
        "tail_observations_99": 0.01 * len(values),
        "interpretation": (
            "empirical simple returns per session; positive loss VaR/CVaR; "
            "population shape moments; null shape for constant samples; not a forecast"
        ),
    }
