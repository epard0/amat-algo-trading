"""Causal v6 per-asset regime classification and entry-quality handling.

The linked four-quadrant approach is retained, but two safeguards are added:
ATR and Bollinger-width percentiles must agree on volatility, and a CUSUM
transition detector plus label confirmation prevents one noisy bar from being
treated as a durable regime.  The output can only reduce allocator targets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import DecisionOverlay
from amat.config.v6 import AssetRegimeConfig, LiquidityConfig
from amat.errors import DataIntegrityError

__all__ = ["AssetRegimeHistory", "build_asset_regime_history"]


@dataclass(frozen=True, slots=True)
class AssetRegimeHistory:
    """Every auditable input and v6 decision on the panel's ``(T, N)`` grid."""

    labels: np.ndarray
    volatility_percentile: np.ndarray
    adx: np.ndarray
    efficiency: np.ndarray
    trend_direction: np.ndarray
    volume_percentile: np.ndarray
    dollar_volume: np.ndarray
    transitions: np.ndarray
    overlay: DecisionOverlay


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1.0)
    out = np.full_like(values, np.nan, dtype=float)
    out[0] = values[0]
    for row in range(1, len(values)):
        out[row] = alpha * values[row] + (1.0 - alpha) * out[row - 1]
    return out


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    rows, columns = values.shape
    out = np.full((rows, columns), np.nan, dtype=float)
    for row in range(window - 1, rows):
        block = values[row - window + 1 : row + 1]
        usable = np.isfinite(block).all(axis=0)
        out[row, usable] = block[:, usable].mean(axis=0)
    return out


def _rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    rows, columns = values.shape
    out = np.full((rows, columns), np.nan, dtype=float)
    for row in range(window - 1, rows):
        block = values[row - window + 1 : row + 1]
        usable = np.isfinite(block).all(axis=0)
        out[row, usable] = block[:, usable].std(axis=0, ddof=1)
    return out


def _rolling_percentile(values: np.ndarray, window: int) -> np.ndarray:
    """Percentile rank of the current value inside its trailing window."""
    rows, columns = values.shape
    out = np.full((rows, columns), np.nan, dtype=float)
    for row in range(window - 1, rows):
        block = values[row - window + 1 : row + 1]
        current = values[row]
        usable = np.isfinite(block).all(axis=0) & np.isfinite(current)
        if np.any(usable):
            out[row, usable] = np.mean(block[:, usable] <= current[usable], axis=0)
    return out


def _true_range(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    prior = np.vstack([np.full((1, closes.shape[1]), np.nan), closes[:-1]])
    return np.nanmax(np.stack([highs - lows, np.abs(highs - prior), np.abs(lows - prior)]), axis=0)


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    up = np.vstack([np.zeros((1, highs.shape[1])), np.diff(highs, axis=0)])
    down = np.vstack([np.zeros((1, lows.shape[1])), -np.diff(lows, axis=0)])
    plus_dm = np.where((up > down) & (up > 0.0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0.0), down, 0.0)
    atr = _ema(_true_range(highs, lows, closes), period)
    plus = 100.0 * _ema(plus_dm, period) / np.where(atr > 0.0, atr, np.nan)
    minus = 100.0 * _ema(minus_dm, period) / np.where(atr > 0.0, atr, np.nan)
    dx = 100.0 * np.abs(plus - minus) / np.where(plus + minus > 0.0, plus + minus, np.nan)
    return _ema(np.nan_to_num(dx, nan=0.0), period)


def _efficiency_ratio(closes: np.ndarray, window: int) -> np.ndarray:
    rows, columns = closes.shape
    out = np.full((rows, columns), np.nan, dtype=float)
    changes = np.abs(np.diff(closes, axis=0))
    for row in range(window, rows):
        direction = np.abs(closes[row] - closes[row - window])
        path = changes[row - window : row].sum(axis=0)
        out[row] = np.divide(direction, path, out=np.zeros(columns), where=path > 0.0)
    return out


def _cusum_transitions(closes: np.ndarray, lookback: int, threshold: float) -> np.ndarray:
    returns = np.full_like(closes, np.nan, dtype=float)
    returns[1:] = np.log(closes[1:] / closes[:-1])
    rows, columns = closes.shape
    out = np.zeros((rows, columns), dtype=bool)
    positive = np.zeros(columns)
    negative = np.zeros(columns)
    for row in range(lookback + 1, rows):
        history = returns[row - lookback : row]
        mean = np.nanmean(history, axis=0)
        std = np.nanstd(history, axis=0, ddof=1)
        z = np.divide(
            returns[row] - mean,
            std,
            out=np.zeros(columns),
            where=np.isfinite(std) & (std > 0.0),
        )
        positive = np.maximum(0.0, positive + z - 0.5)
        negative = np.maximum(0.0, negative - z - 0.5)
        out[row] = (positive > threshold) | (negative > threshold)
        positive[out[row]] = 0.0
        negative[out[row]] = 0.0
    return out


def _confirmed_labels(raw: np.ndarray, confirmation: int) -> np.ndarray:
    rows, columns = raw.shape
    stable = np.full(raw.shape, "unknown", dtype="U24")
    current = np.full(columns, "unknown", dtype="U24")
    candidate = np.full(columns, "unknown", dtype="U24")
    count = np.zeros(columns, dtype=int)
    for row in range(rows):
        for column in range(columns):
            label = raw[row, column]
            if label == current[column]:
                candidate[column] = label
                count[column] = 0
            elif label == candidate[column]:
                count[column] += 1
            else:
                candidate[column] = label
                count[column] = 1
            if count[column] >= confirmation:
                current[column] = candidate[column]
                count[column] = 0
            stable[row, column] = current[column]
    return stable


def build_asset_regime_history(
    panel: MarketPanel,
    regime: AssetRegimeConfig,
    liquidity: LiquidityConfig,
) -> AssetRegimeHistory:
    """Build v6's causal regime history and asymmetric liquidity overlay."""
    closes, highs, lows, volumes = panel.context_inputs()
    if np.any(closes <= 0.0) or np.any(highs <= 0.0) or np.any(lows <= 0.0):
        raise DataIntegrityError("v6 regime inputs must be strictly positive")

    tr = _true_range(highs, lows, closes)
    atr = _rolling_mean(tr, regime.atr_period)
    atr_pct = _rolling_percentile(atr / closes, regime.percentile_lookback)

    middle = _rolling_mean(closes, regime.ema_period)
    width = 4.0 * _rolling_std(closes, regime.ema_period) / middle
    bb_pct = _rolling_percentile(width, regime.percentile_lookback)
    volatility = np.fmax(atr_pct, bb_pct)

    adx = _adx(highs, lows, closes, regime.adx_period)
    ema = _ema(closes, regime.ema_period)
    slope = np.full_like(closes, np.nan)
    slope[regime.slope_sessions :] = (
        ema[regime.slope_sessions :] / ema[: -regime.slope_sessions] - 1.0
    )
    direction = np.zeros(closes.shape, dtype=np.int8)
    direction[(slope > 0.0) & (closes > ema)] = 1
    direction[(slope < 0.0) & (closes < ema)] = -1
    efficiency = _efficiency_ratio(closes, regime.efficiency_lookback)
    transitions = _cusum_transitions(closes, regime.percentile_lookback, regime.cusum_threshold)

    volume_pct = _rolling_percentile(volumes, liquidity.lookback_sessions)
    dollar_volume = volumes * panel.raw_closes
    liquid = (volume_pct >= liquidity.min_volume_percentile) & (
        dollar_volume >= liquidity.min_dollar_volume
    )

    raw = np.full(closes.shape, "unknown", dtype="U24")
    usable = np.isfinite(volatility) & np.isfinite(adx) & np.isfinite(efficiency)
    trending = (adx >= regime.trend_adx) & (efficiency >= regime.min_efficiency)
    ranging = adx <= regime.range_adx
    up = direction > 0
    down = direction < 0
    low_vol = volatility < regime.low_vol_percentile
    high_vol = volatility > regime.high_vol_percentile

    raw[usable & trending & up & low_vol] = "quiet_uptrend"
    raw[usable & trending & up & ~low_vol & ~high_vol] = "normal_uptrend"
    raw[usable & trending & up & high_vol] = "volatile_uptrend"
    raw[usable & trending & down] = "downtrend"
    raw[usable & ranging & ~high_vol] = "quiet_range"
    raw[usable & ranging & high_vol] = "volatile_range"
    transitional = usable & ~(trending | ranging)
    raw[transitional | transitions] = "transition"
    labels = _confirmed_labels(raw, regime.confirmation_sessions)

    scalar_by_label = {
        "unknown": regime.transition_scalar,
        "quiet_uptrend": regime.quiet_trend_scalar,
        "normal_uptrend": regime.normal_trend_scalar,
        "volatile_uptrend": regime.volatile_trend_scalar,
        "downtrend": regime.downtrend_scalar,
        "quiet_range": regime.quiet_range_scalar,
        "volatile_range": regime.volatile_range_scalar,
        "transition": regime.transition_scalar,
    }
    risk = np.zeros(closes.shape, dtype=float)
    for label, scalar in scalar_by_label.items():
        risk[labels == label] = scalar
    # A detected change-point acts immediately even while label hysteresis holds.
    risk[transitions] = np.minimum(risk[transitions], regime.transition_scalar)

    # Volume prioritizes the *increment*, not the whole holding. A hard
    # above-median gate erased nearly 90% of eligible sessions in the first v6
    # measurement and recreated v2's under-deployment defect. Absolute
    # liquidity remains a hard gate; relative volume softly ranks additions.
    relative_liquid = volume_pct >= liquidity.min_volume_percentile
    hostile = np.isin(labels, ("downtrend", "volatile_range"))
    allow_increase = np.asarray(liquid & relative_liquid & ~hostile & ~transitions, dtype=bool)
    volume_priority = liquidity.minimum_increase_scalar + (
        1.0 - liquidity.minimum_increase_scalar
    ) * np.nan_to_num(volume_pct, nan=0.0)
    return AssetRegimeHistory(
        labels=labels,
        volatility_percentile=volatility,
        adx=adx,
        efficiency=efficiency,
        trend_direction=direction,
        volume_percentile=volume_pct,
        dollar_volume=dollar_volume,
        transitions=transitions,
        overlay=DecisionOverlay(
            risk_multipliers=risk,
            allow_increase=allow_increase,
            increase_multipliers=volume_priority,
        ),
    )
