"""The v4 stance selector: trend breadth mapped to a blend weight.

What it measures
----------------
For each session ``t``, the fraction of universe instruments whose adjusted
close sits above their own close ``horizon`` sessions earlier -- computed only
from rows at or before ``t``, the same causality the trend signal itself obeys.
Broad participation is the environment the trend stance was designed for; a
collapse in breadth is the classic risk-off signature under which the
defensive stance (rates, metals, dollar) takes over.

Why this is deliberately not a model
------------------------------------
The prior v3 candidate learned regimes with a causally fitted KMeans and was
rejected by the falsification controls -- a static mean beat it. This selector
is the opposite design point: one observable, two declared thresholds, an EWMA
to damp flapping, zero fitted parameters. If even this cannot beat its own
shuffled and static-mean controls, the honest conclusion is that stance
switching adds nothing here, and the registry records that.

Causality note: the EWMA is a purely recursive filter (state at ``t`` depends
on breadth at ``<= t``), and breadth at ``t`` reads closes at ``t`` and
``t - horizon`` only. The decision using ``lambda_t`` executes no earlier than
session ``t + 1``, exactly like every other decision input.
"""

from __future__ import annotations

import numpy as np

from amat.errors import DataIntegrityError

__all__ = ["breadth_blend_weights"]

#: Fewer symbols than this makes a breadth fraction too coarse to mean anything.
_MIN_SYMBOLS = 4

#: A market panel is always 2-D: (sessions, symbols).
_PANEL_NDIM = 2


def breadth_blend_weights(
    adjusted_closes: np.ndarray,
    *,
    horizon_sessions: int,
    smoothing_half_life_sessions: int,
    floor_breadth: float,
    full_breadth: float,
) -> np.ndarray:
    """Blend weight toward the trend stance, per session.

    Args:
        adjusted_closes: ``(T, N)`` fully adjusted closes, features basis.
        horizon_sessions: Lookback for the above/below comparison.
        smoothing_half_life_sessions: EWMA half-life applied to raw breadth.
        floor_breadth: Smoothed breadth at or below this maps to 0.0 (fully
            defensive).
        full_breadth: Smoothed breadth at or above this maps to 1.0 (fully
            trend). Linear in between.

    Returns:
        ``(T,)`` array in ``[0, 1]``. Sessions before ``horizon_sessions``
        have no breadth reading and return 1.0 (pure trend stance) -- they all
        fall inside the strategy warm-up, where no decision is formed anyway.

    Raises:
        DataIntegrityError: on a malformed panel or an unusable parameterization.
    """
    if adjusted_closes.ndim != _PANEL_NDIM or adjusted_closes.shape[1] < _MIN_SYMBOLS:
        message = (
            f"breadth needs a (sessions, symbols) panel with at least {_MIN_SYMBOLS} "
            f"symbols, received shape {adjusted_closes.shape}"
        )
        raise DataIntegrityError(message)
    if not np.all(np.isfinite(adjusted_closes)) or np.any(adjusted_closes <= 0):
        message = "breadth requires finite, positive adjusted closes"
        raise DataIntegrityError(message)
    if horizon_sessions < 1:
        message = "horizon_sessions must be at least 1"
        raise DataIntegrityError(message)
    if smoothing_half_life_sessions < 1:
        message = "smoothing_half_life_sessions must be at least 1"
        raise DataIntegrityError(message)
    if not 0.0 <= floor_breadth < full_breadth <= 1.0:
        message = (
            f"breadth thresholds must satisfy 0 <= floor < full <= 1, received "
            f"floor={floor_breadth} full={full_breadth}"
        )
        raise DataIntegrityError(message)

    total = adjusted_closes.shape[0]
    weights = np.ones(total, dtype=float)
    if total <= horizon_sessions:
        return weights

    above = adjusted_closes[horizon_sessions:] > adjusted_closes[:-horizon_sessions]
    breadth = above.mean(axis=1)

    alpha = 1.0 - 0.5 ** (1.0 / smoothing_half_life_sessions)
    smoothed = np.empty_like(breadth)
    state = breadth[0]
    for position, value in enumerate(breadth):
        state = alpha * value + (1.0 - alpha) * state
        smoothed[position] = state

    span = full_breadth - floor_breadth
    weights[horizon_sessions:] = np.clip((smoothed - floor_breadth) / span, 0.0, 1.0)
    return weights
