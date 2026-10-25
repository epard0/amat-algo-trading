"""Conditional simultaneous intervals for a fixed family of paired monthly histories."""

from __future__ import annotations

import numpy as np

MATRIX_DIMENSIONS = 2
MIN_MONTHS = 2
MIN_DRAWS = 100
STANDARD_ERROR_FLOOR = 1e-14


def simultaneous_growth_intervals(
    differences: np.ndarray, *, block_months: int, draws: int, seed: int
) -> dict[str, object]:
    """Use shared circular blocks and a centered maximum absolute bootstrap statistic.

    This measures uncertainty conditional on supplied histories. It does not
    correct unobserved research choices or turn reused data into a holdout.
    Differences are paired monthly log returns; outputs are annual log units.
    """
    values = np.asarray(differences, dtype=float)
    if values.ndim != MATRIX_DIMENSIONS or min(values.shape) < 1 or not np.isfinite(values).all():
        raise ValueError("Finite nonempty month-by-history matrix required")
    months, histories = values.shape
    if months < MIN_MONTHS or not 1 <= block_months <= months or draws < MIN_DRAWS:
        raise ValueError("At least two months, valid block length and100 draws required")
    rng = np.random.default_rng(seed)
    sampled = np.empty((draws, histories))
    blocks = (months + block_months - 1) // block_months
    for offset in range(0, draws, 32):
        size = min(32, draws - offset)
        starts = rng.integers(0, months, size=(size, blocks))
        indices = ((starts[:, :, None] + np.arange(block_months)) % months).reshape(size, -1)
        sampled[offset : offset + size] = values[indices[:, :months]].mean(axis=1) * 12
    observed = values.mean(axis=0) * 12
    standard_error = sampled.std(axis=0, ddof=1)
    active = standard_error > STANDARD_ERROR_FLOOR
    maximum = (
        np.max(np.abs((sampled[:, active] - observed[active]) / standard_error[active]), axis=1)
        if np.any(active)
        else np.zeros(draws)
    )
    critical = float(np.quantile(maximum, 0.95))
    return {
        "annual_log_growth_difference": observed.tolist(),
        "simultaneous_lower": (observed - critical * standard_error).tolist(),
        "simultaneous_upper": (observed + critical * standard_error).tolist(),
        "bootstrap_standard_error": standard_error.tolist(),
        "critical_max_absolute_statistic": critical,
        "degenerate_columns": np.flatnonzero(~active).tolist(),
        "months": months,
        "histories": histories,
        "block_months": block_months,
        "draws": draws,
        "seed": seed,
        "confidence": 0.95,
        "conditional_on_measured_histories": True,
        "untouched_holdout": False,
    }
