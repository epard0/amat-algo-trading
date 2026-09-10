"""Causal technical structure features for soft cross-asset allocation.

These features do not generate trades independently. They describe whether an
already-positive trend has persistent, non-exhausted, high-in-range, and
downside-contained structure. The composite is bounded away from zero so it
reallocates risk without recreating the rejected hard-selection experiments.
"""

from __future__ import annotations

import numpy as np

from amat.config.models import StructureFilterConfig
from amat.errors import DataIntegrityError

__all__ = [
    "donchian_position",
    "downside_variance_share",
    "relative_strength_index",
    "structure_multiplier",
    "variance_ratio",
]

_PANEL_DIMENSIONS = 2
_MINIMUM_WINDOW = 2


def _require_panel(values: np.ndarray, name: str) -> None:
    if values.ndim != _PANEL_DIMENSIONS:
        raise DataIntegrityError(f"{name} must be a 2-D (sessions, symbols) panel")


def relative_strength_index(prices: np.ndarray, period: int) -> np.ndarray:
    """Wilder RSI on adjusted closes, unavailable until ``period`` returns."""
    _require_panel(prices, "prices")
    if period < _MINIMUM_WINDOW:
        raise DataIntegrityError("RSI period must be at least 2")
    if not np.all(np.isfinite(prices)) or np.any(prices <= 0.0):
        raise DataIntegrityError("RSI prices must be finite and strictly positive")

    rows, columns = prices.shape
    out = np.full((rows, columns), np.nan, dtype=float)
    if rows <= period:
        return out
    differences = np.diff(prices, axis=0)
    gains = np.maximum(differences, 0.0)
    losses = np.maximum(-differences, 0.0)
    average_gain = gains[:period].mean(axis=0)
    average_loss = losses[:period].mean(axis=0)

    def record(row: int) -> None:
        relative_strength = np.divide(
            average_gain,
            average_loss,
            out=np.full(columns, np.inf),
            where=average_loss > 0.0,
        )
        values = 100.0 - 100.0 / (1.0 + relative_strength)
        values[(average_gain == 0.0) & (average_loss == 0.0)] = 50.0
        out[row] = values

    record(period)
    for row in range(period + 1, rows):
        source = row - 1
        average_gain = ((period - 1) * average_gain + gains[source]) / period
        average_loss = ((period - 1) * average_loss + losses[source]) / period
        record(row)
    return out


def variance_ratio(returns: np.ndarray, *, lookback: int, horizon: int) -> np.ndarray:
    """Overlapping ``q``-return variance divided by ``q`` times one-step variance.

    This is a descriptive persistence feature, not a Lo-MacKinlay hypothesis
    test. Values above one indicate that multi-session returns varied more than
    an iid scaling of one-session returns over the same trailing sample.
    """
    _require_panel(returns, "returns")
    if horizon < _MINIMUM_WINDOW:
        raise DataIntegrityError("variance-ratio horizon must be at least 2")
    if lookback <= horizon:
        raise DataIntegrityError("variance-ratio lookback must exceed its horizon")

    rows, columns = returns.shape
    out = np.full((rows, columns), np.nan, dtype=float)
    for row in range(lookback, rows):
        block = returns[row - lookback + 1 : row + 1]
        usable = np.isfinite(block).all(axis=0)
        if not np.any(usable):
            continue
        one_variance = block[:, usable].var(axis=0, ddof=1)
        cumulative = np.vstack(
            [np.zeros((1, int(usable.sum()))), np.cumsum(block[:, usable], axis=0)]
        )
        horizon_returns = cumulative[horizon:] - cumulative[:-horizon]
        horizon_variance = horizon_returns.var(axis=0, ddof=1)
        out[row, usable] = np.divide(
            horizon_variance,
            horizon * one_variance,
            out=np.zeros_like(horizon_variance),
            where=one_variance > 0.0,
        )
    return out


def donchian_position(prices: np.ndarray, lookback: int) -> np.ndarray:
    """Current adjusted close's position in its trailing Donchian range."""
    _require_panel(prices, "prices")
    if lookback < _MINIMUM_WINDOW:
        raise DataIntegrityError("Donchian lookback must be at least 2")
    rows, columns = prices.shape
    out = np.full((rows, columns), np.nan, dtype=float)
    for row in range(lookback - 1, rows):
        block = prices[row - lookback + 1 : row + 1]
        usable = np.isfinite(block).all(axis=0)
        low = block[:, usable].min(axis=0)
        high = block[:, usable].max(axis=0)
        span = high - low
        out[row, usable] = np.divide(
            prices[row, usable] - low,
            span,
            out=np.full(int(usable.sum()), 0.5),
            where=span > 0.0,
        )
    return out


def downside_variance_share(returns: np.ndarray, lookback: int) -> np.ndarray:
    """Share of trailing squared returns contributed by negative sessions."""
    _require_panel(returns, "returns")
    if lookback < _MINIMUM_WINDOW:
        raise DataIntegrityError("downside lookback must be at least 2")
    rows, columns = returns.shape
    out = np.full((rows, columns), np.nan, dtype=float)
    for row in range(lookback, rows):
        block = returns[row - lookback + 1 : row + 1]
        usable = np.isfinite(block).all(axis=0)
        total = np.square(block[:, usable]).sum(axis=0)
        downside = np.square(np.minimum(block[:, usable], 0.0)).sum(axis=0)
        out[row, usable] = np.divide(
            downside,
            total,
            out=np.full(int(usable.sum()), 0.5),
            where=total > 0.0,
        )
    return out


def _ascending_quality(values: np.ndarray, floor: float, target: float) -> np.ndarray:
    return np.clip((values - floor) / (target - floor), 0.0, 1.0)


def _descending_quality(values: np.ndarray, target: float, ceiling: float) -> np.ndarray:
    return np.clip((ceiling - values) / (ceiling - target), 0.0, 1.0)


def structure_multiplier(
    prices: np.ndarray,
    returns: np.ndarray,
    config: StructureFilterConfig,
) -> np.ndarray:
    """Equal-weight four feature qualities mapped into ``[floor, 1]``."""
    if prices.shape != returns.shape:
        raise DataIntegrityError("structure price and return panels must have equal shape")
    if not config.enabled:
        return np.ones(prices.shape, dtype=float)

    ratio = variance_ratio(
        returns,
        lookback=config.variance_lookback_sessions,
        horizon=config.variance_ratio_horizon,
    )
    rsi = relative_strength_index(prices, config.rsi_period)
    channel = donchian_position(prices, config.channel_lookback_sessions)
    downside = downside_variance_share(returns, config.downside_lookback_sessions)

    ratio_quality = _ascending_quality(
        ratio, config.variance_ratio_floor, config.variance_ratio_target
    )
    # Reward a healthy momentum plateau rather than one exact RSI value. This
    # avoids turning small oscillator changes around a single peak into churn.
    rsi_rising = _ascending_quality(rsi, config.rsi_floor, config.rsi_target)
    rsi_not_exhausted = _descending_quality(rsi, config.rsi_upper_target, config.rsi_ceiling)
    rsi_quality = np.minimum(rsi_rising, rsi_not_exhausted)
    channel_quality = _ascending_quality(channel, config.channel_floor, config.channel_target)
    downside_quality = _descending_quality(
        downside, config.downside_target, config.downside_ceiling
    )

    stacked = np.stack([ratio_quality, rsi_quality, channel_quality, downside_quality])
    usable = np.isfinite(stacked).all(axis=0)
    quality = np.nan_to_num(stacked.mean(axis=0), nan=1.0)
    multiplier = config.min_multiplier + (1.0 - config.min_multiplier) * quality
    multiplier[~usable] = 1.0
    return np.asarray(multiplier, dtype=float)
