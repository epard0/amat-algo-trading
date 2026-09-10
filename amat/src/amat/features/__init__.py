"""Feature stage: log returns, exponentially weighted volatility, horizon trend.

Contract for this package:

* features are computed only from completed sessions available at the decision
  timestamp; the EWMA convention, minimum-period behaviour, null policy, and
  timestamp alignment are frozen in configuration, not chosen at call time;
* the fully adjusted close is the only price series used here; raw prices
  belong to execution and valuation;
* every formula is verified against hand-calculated fixtures before use.

Normative formulas live in ``.claude/references/strategy-contract.md``.

Frozen EWMA convention
----------------------
The contract says ``EWMAStd(x, half_life=h)`` without naming a convention, and
several mutually incompatible ones are in common use. This package freezes the
following and never varies it:

* decay ``alpha = 1 - 0.5 ** (1 / half_life)``;
* *adjusted* weights, so observation ``i`` at time ``t`` carries weight
  ``(1 - alpha) ** (t - i)`` -- no initialization bias from a seed value;
* the mean is itself exponentially weighted, not assumed zero;
* the variance is **bias-corrected** by ``S0**2 / (S0**2 - Q)`` where ``S0`` is
  the sum of weights and ``Q`` the sum of squared weights, giving a sample
  rather than population statistic.

This is exactly ``pandas.Series.ewm(halflife=h, adjust=True).std()``, chosen
because it is the most widely reproduced reading and is therefore independently
checkable. ``tests/unit/test_features.py`` pins it against hand-computed values.
"""

from __future__ import annotations

import numpy as np

from amat.config.models import TrendConfig, VolatilityConfig
from amat.errors import DataIntegrityError
from amat.features.structure import (
    donchian_position,
    downside_variance_share,
    relative_strength_index,
    structure_multiplier,
    variance_ratio,
)

#: A market panel is always 2-D: (sessions, symbols).
_PANEL_NDIM = 2

__all__ = [
    "annualized_volatility",
    "donchian_position",
    "downside_variance_share",
    "ewma_std",
    "horizon_log_returns",
    "horizon_trend_scores",
    "log_returns",
    "long_scores",
    "relative_strength_index",
    "structure_multiplier",
    "trend_scores",
    "variance_ratio",
]


def log_returns(prices: np.ndarray) -> np.ndarray:
    """Single-session log returns of an adjusted-close panel.

    Args:
        prices: ``(T, N)`` panel of strictly positive adjusted closes, ordered
            oldest session first.

    Returns:
        ``(T, N)`` array whose first row is ``nan`` -- a return needs two closes,
        and emitting zero there would fabricate an observation.

    Raises:
        DataIntegrityError: if the panel is not 2-D or contains a non-positive
            or non-finite price.
    """
    _require_price_panel(prices)
    out = np.full_like(prices, np.nan, dtype=float)
    out[1:] = np.log(prices[1:] / prices[:-1])
    return out


def horizon_log_returns(prices: np.ndarray, horizon: int) -> np.ndarray:
    """Trailing ``horizon``-session log returns, ``ln(P[t] / P[t-h])``.

    The first ``horizon`` rows are ``nan``: they have no base price.

    Raises:
        DataIntegrityError: on an invalid panel or a non-positive horizon.
    """
    _require_price_panel(prices)
    if horizon <= 0:
        message = f"horizon must be strictly positive, received {horizon}"
        raise DataIntegrityError(message)
    out = np.full_like(prices, np.nan, dtype=float)
    if prices.shape[0] > horizon:
        out[horizon:] = np.log(prices[horizon:] / prices[:-horizon])
    return out


def ewma_std(values: np.ndarray, *, half_life: int, min_periods: int) -> np.ndarray:
    """Exponentially weighted sample standard deviation, computed causally.

    Implements the frozen convention documented in this module's docstring. The
    recursion is ``O(T)`` per column and, critically, each row depends only on
    rows at or before it -- the leakage tests mutate future rows and assert the
    output through ``t`` is bit-identical.

    Leading ``nan`` values (for example the first row of a return panel) are
    skipped rather than propagated, so a volatility estimate begins as soon as
    ``min_periods`` real observations exist.

    Args:
        values: ``(T, N)`` panel, typically log returns.
        half_life: Sessions over which a weight decays by half.
        min_periods: Observations required before a value is emitted.

    Returns:
        ``(T, N)`` array, ``nan`` until ``min_periods`` observations exist.

    Raises:
        DataIntegrityError: on a non-2-D input or a non-positive parameter.
    """
    if values.ndim != _PANEL_NDIM:
        message = f"values must be a 2-D (T, N) panel, received shape {values.shape}"
        raise DataIntegrityError(message)
    if half_life <= 0:
        message = f"half_life must be strictly positive, received {half_life}"
        raise DataIntegrityError(message)
    if min_periods <= 1:
        message = f"min_periods must exceed 1 for a sample std, received {min_periods}"
        raise DataIntegrityError(message)

    decay = 0.5 ** (1.0 / half_life)
    rows, cols = values.shape
    out = np.full((rows, cols), np.nan, dtype=float)

    for col in range(cols):
        weight_sum = 0.0
        weighted_x = 0.0
        weighted_xx = 0.0
        squared_weight_sum = 0.0
        count = 0
        for row in range(rows):
            x = values[row, col]
            if not np.isfinite(x):
                # A gap contributes nothing and does not advance the decay:
                # treating it as a zero return would invent a quiet session.
                continue
            weight_sum = 1.0 + decay * weight_sum
            weighted_x = x + decay * weighted_x
            weighted_xx = x * x + decay * weighted_xx
            squared_weight_sum = 1.0 + decay * decay * squared_weight_sum
            count += 1
            if count < min_periods:
                continue
            denominator = weight_sum * weight_sum - squared_weight_sum
            if denominator <= 0:
                continue
            mean = weighted_x / weight_sum
            biased_variance = weighted_xx / weight_sum - mean * mean
            variance = biased_variance * (weight_sum * weight_sum) / denominator
            out[row, col] = np.sqrt(variance) if variance > 0 else 0.0
    return out


def annualized_volatility(returns: np.ndarray, config: VolatilityConfig) -> np.ndarray:
    """Blended annualized volatility, ``max(sigma_fast, w * sigma_slow, floor)``.

    Both estimates are annualized by ``sqrt(annualization_sessions)`` before the
    maximum, so the floor is expressed in the same annualized units as the
    contract states it.

    Returns:
        ``(T, N)`` array. Entries stay ``nan`` until *both* estimates exist, so
        a symbol is never sized off a half-converged volatility.
    """
    scale = np.sqrt(config.annualization_sessions)
    fast = (
        ewma_std(
            returns,
            half_life=config.fast_half_life_sessions,
            min_periods=config.min_periods_sessions,
        )
        * scale
    )
    slow = (
        ewma_std(
            returns,
            half_life=config.slow_half_life_sessions,
            min_periods=config.min_periods_sessions,
        )
        * scale
    )

    blended = np.maximum(fast, config.slow_weight * slow)
    sigma = np.maximum(blended, config.floor_annualized)
    # Preserve unavailability: the floor must not manufacture a volatility for a
    # symbol that has no estimate yet.
    sigma[~np.isfinite(fast) | ~np.isfinite(slow)] = np.nan
    return np.asarray(sigma, dtype=float)


def trend_scores(
    prices: np.ndarray,
    sigma: np.ndarray,
    config: TrendConfig,
    *,
    annualization_sessions: int,
) -> np.ndarray:
    """Volatility-normalized, clipped, ``tanh``-combined, dead-banded trend score.

    Implements, for each horizon ``h`` in ``config.horizons_sessions``::

        z[h] = clip(ln(P[t]/P[t-h]) / (sigma * sqrt(h / annualization)), -c, +c)
        s    = tanh(mean over h of z[h])
        s    = 0 where abs(s) < deadband

    Returns:
        ``(T, N)`` array of scores in ``[-1, 1]``, ``nan`` where any horizon or
        the volatility estimate is unavailable.
    """
    stacked = horizon_trend_scores(
        prices, sigma, config, annualization_sessions=annualization_sessions
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        # Any missing horizon makes the average unrepresentative, so the whole
        # score is withheld rather than computed from the horizons that happen
        # to be available.
        combined = np.tanh(stacked.mean(axis=0))

    combined[np.abs(combined) < config.deadband_abs] = 0.0
    combined[~np.isfinite(stacked).all(axis=0)] = np.nan
    return np.asarray(combined, dtype=float)


def horizon_trend_scores(
    prices: np.ndarray,
    sigma: np.ndarray,
    config: TrendConfig,
    *,
    annualization_sessions: int,
) -> np.ndarray:
    """The per-horizon normalized trends that :func:`trend_scores` averages.

    Exposed because two consumers need to see the horizons *before* they are
    collapsed: the point-in-time scanner, which reports why an asset ranks where
    it does, and any rule conditioned on the horizons agreeing. Extracted from
    :func:`trend_scores` rather than reimplemented so the two cannot drift --
    the composite is defined as the ``tanh`` of this array's mean, and
    ``tests/unit/test_features.py`` pins that identity.

    Returns:
        ``(H, T, N)`` clipped, volatility-normalized trends, horizon-major in
        ``config.horizons_sessions`` order. ``nan`` wherever the underlying
        return or volatility estimate is unavailable.
    """
    if prices.shape != sigma.shape:
        message = f"price panel {prices.shape} and sigma panel {sigma.shape} must have equal shape"
        raise DataIntegrityError(message)

    with np.errstate(invalid="ignore", divide="ignore"):
        return np.stack(
            [
                np.clip(
                    horizon_log_returns(prices, horizon)
                    / (sigma * np.sqrt(horizon / annualization_sessions)),
                    -config.clip_abs,
                    config.clip_abs,
                )
                for horizon in config.horizons_sessions
            ]
        )


def long_scores(scores: np.ndarray, config: TrendConfig) -> np.ndarray:
    """Clamp scores to the long-or-cash mandate, ``max(score, 0)``.

    Raises:
        DataIntegrityError: if the configuration is not long-only, which would
            silently admit short exposure the mandate forbids.
    """
    if not config.long_only:
        message = "long_scores requires a long_only configuration; v1 is long-or-cash"
        raise DataIntegrityError(message)
    return np.asarray(np.maximum(scores, 0.0), dtype=float)


def _require_price_panel(prices: np.ndarray) -> None:
    if prices.ndim != _PANEL_NDIM:
        message = f"prices must be a 2-D (T, N) panel, received shape {prices.shape}"
        raise DataIntegrityError(message)
    finite = np.isfinite(prices)
    if np.any(finite & (prices <= 0)):
        message = "adjusted closes must be strictly positive"
        raise DataIntegrityError(message)
