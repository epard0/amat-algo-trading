"""Frozen missing-aware dimensionless terminal semantics for symbolic programs.

Raw legacy formulas agree with causal-features/6 on complete observations. This
registry additionally requires every declared raw dependency window to be valid;
Wilder RSI resets and reseeds after a missing price. ATR is a simple trailing
mean of true range (including previous close). Body is signed close minus open;
wick fractions are trailing lookback means. Zero ATR/range is unavailable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, get_args

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.features import donchian_position, downside_variance_share, variance_ratio
from autoresearch.genetic.models import FeatureName, FeatureNode
from autoresearch.registry import rolling

SYMBOLIC_REGISTRY_VERSION = "genetic-symbolic-features/1"
SECONDARY = 5
NORMALIZATION_WINDOW = 63
FEATURE_CLIP = 3.0
EPSILON = 1e-12
DIVISION_FLOOR = 1e-8
INTERMEDIATE_BOUND = 1e6
MIN_BREADTH_ASSETS = 3
CANDLE_FEATURES = {"body_atr", "range_atr", "upper_wick_fraction", "lower_wick_fraction"}


def symbolic_registry_manifest() -> dict[str, Any]:
    """Semantic settings plus actual source hashes bind imported formula primitives."""
    root = Path(__file__).parents[2]
    paths = (
        "autoresearch/genetic/symbolic_registry.py",
        "autoresearch/genetic/symbolic.py",
        "autoresearch/genetic/models.py",
        "autoresearch/registry.py",
        "amat/features/structure.py",
        "amat/features/__init__.py",
    )
    return {
        "version": SYMBOLIC_REGISTRY_VERSION,
        "features": sorted(get_args(FeatureName)),
        "secondary_lookback": SECONDARY,
        "smoothing": 1,
        "normalization_window": NORMALIZATION_WINDOW,
        "clip": FEATURE_CLIP,
        "benchmark": "SPY",
        "safe_div_floor": DIVISION_FLOOR,
        "intermediate_bound": INTERMEDIATE_BOUND,
        "missing": "strict contiguous raw dependency window; RSI resets after price gap",
        "normalization": "rank midpoint ties; sample std zscore zero variance unavailable",
        "candle": "signed body/SMA true range ATR; range/ATR; SMA wick fractions",
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in paths
        },
    }


def symbolic_registry_digest() -> str:
    raw = json.dumps(symbolic_registry_manifest(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def shifted(values: np.ndarray, lag: int) -> np.ndarray:
    if lag == 0:
        return values.copy()
    out = np.full_like(values, np.nan, dtype=float)
    if lag < len(values):
        out[lag:] = values[:-lag]
    return out


def raw_history(node: FeatureNode) -> int:
    history = node.lookback
    if node.name in {
        "donchian_position",
        "close_location",
        "window_markout",
        "upper_wick_fraction",
        "lower_wick_fraction",
    }:
        history -= 1
    if node.name in {"trend_acceleration", "breakout_persistence"}:
        history += SECONDARY
    if node.name == "volatility_of_volatility":
        history += SECONDARY - 1
    return history


def complete_window(valid: np.ndarray, history: int) -> np.ndarray:
    """History is a zero-based lookback: require history+1 complete observations."""
    out = np.zeros_like(valid, dtype=bool)
    for row in range(history, len(valid)):
        out[row] = valid[row - history : row + 1].all(axis=0)
    return out


def _positive(values: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(values) & (values > 0), values, np.nan)


def _optional(panel: MarketPanel, name: str) -> np.ndarray:
    values = getattr(panel, name)
    return np.full_like(panel.adjusted_closes, np.nan) if values is None else _positive(values)


def _rsi(closes: np.ndarray, period: int) -> np.ndarray:
    """Wilder recursion, restarted using period contiguous returns after every gap."""
    out = np.full_like(closes, np.nan)
    for col in range(closes.shape[1]):
        gain = loss = 0.0
        count = 0
        for row in range(1, len(closes)):
            if not np.isfinite(closes[row - 1 : row + 1, col]).all():
                gain = loss = 0.0
                count = 0
                continue
            move = closes[row, col] - closes[row - 1, col]
            up, down = max(move, 0.0), max(-move, 0.0)
            if count < period:
                gain += up / period
                loss += down / period
                count += 1
            else:
                gain = ((period - 1) * gain + up) / period
                loss = ((period - 1) * loss + down) / period
            if count >= period:
                out[row, col] = gain / (gain + loss) if gain + loss else 0.5
    return out


def _candle(panel: MarketPanel, node: FeatureNode) -> tuple[np.ndarray, np.ndarray]:
    c = _positive(panel.adjusted_closes)
    o, h, low = (
        _optional(panel, name) for name in ("adjusted_opens", "adjusted_highs", "adjusted_lows")
    )
    valid = np.isfinite(o + h + low + c) & (low <= np.minimum(o, c)) & (h >= np.maximum(o, c))
    spread = np.where(valid, h - low, np.nan)
    previous = shifted(c, 1)
    tr = np.maximum(spread, np.maximum(np.abs(h - previous), np.abs(low - previous)))
    atr = rolling(tr, node.lookback, "mean")
    if node.name in {"body_atr", "range_atr"}:
        numerator = c - o if node.name == "body_atr" else spread
        values = np.divide(numerator, atr, out=np.full_like(c, np.nan), where=atr > 0)
    else:
        wick = (
            h - np.maximum(o, c) if node.name == "upper_wick_fraction" else np.minimum(o, c) - low
        )
        fraction = np.divide(wick, spread, out=np.full_like(c, np.nan), where=spread > 0)
        values = rolling(fraction, node.lookback, "mean")
    return values, valid


def raw_feature(panel: MarketPanel, node: FeatureNode) -> np.ndarray:  # noqa: PLR0912, PLR0915
    """Frozen raw feature, invalidating its entire declared dependency window."""
    c = _positive(panel.adjusted_closes)
    h, short = node.lookback, SECONDARY
    previous = shifted(c, 1)
    returns = np.log(c / previous)
    momentum = np.log(c / shifted(c, h))
    sigma = rolling(returns, short, "std")
    normalized = momentum / np.maximum(sigma * np.sqrt(h), EPSILON)
    valid = np.isfinite(c)
    name = node.name
    if name in CANDLE_FEATURES:
        values, valid = _candle(panel, node)
    elif name == "momentum":
        values = c / shifted(c, h) - 1
    elif name == "volatility":
        values = rolling(returns, h, "std") * np.sqrt(252)
    elif name == "drawdown":
        values = c / rolling(c, h + 1, "max") - 1
    elif name == "efficiency":
        distance = rolling(np.abs(c - previous), h, "mean") * h
        values = np.divide(
            np.abs(c - shifted(c, h)), distance, out=np.zeros_like(c), where=distance > EPSILON
        )
    elif name == "normalized_momentum":
        values = normalized
    elif name == "trend_agreement":
        values = (np.sign(momentum) + np.sign(np.log(c / shifted(c, short)))) / 2
    elif name == "trend_acceleration":
        values = normalized - shifted(normalized, short)
    elif name == "donchian_position":
        values = donchian_position(c, h)
    elif name == "breakout_distance":
        values = np.log(c / shifted(rolling(c, h, "max"), 1)) / np.maximum(sigma, EPSILON)
    elif name == "breakout_persistence":
        levels = shifted(rolling(c, h, "max"), short)
        values = np.full_like(c, np.nan)
        for row in range(h + short, len(c)):
            values[row] = (c[row - short + 1 : row + 1] >= levels[row]).mean(axis=0)
    elif name == "relative_strength":
        symbols = [str(symbol) for symbol in panel.symbols]
        if "SPY" not in symbols:
            raise ValueError("symbolic relative_strength requires authorized benchmark SPY")
        index = symbols.index("SPY")
        values = momentum - momentum[:, index, None]
        valid &= valid[:, index, None]
    elif name in {"volume_ratio", "volume_price", "illiquidity"}:
        volume = _optional(panel, "session_volumes")
        valid &= np.isfinite(volume)
        ratio = volume / shifted(rolling(volume, h, "mean"), 1)
        if name == "volume_ratio":
            values = ratio
        elif name == "volume_price":
            values = np.log(np.maximum(ratio, EPSILON)) * np.tanh(
                returns / np.maximum(sigma, EPSILON)
            )
        else:
            raw = _positive(panel.raw_closes)
            valid &= np.isfinite(raw)
            values = np.log1p(rolling(np.abs(returns) * 1e8 / (raw * volume), h, "mean"))
    elif name == "variance_ratio":
        values = variance_ratio(returns, lookback=h, horizon=short)
    elif name == "downside_variance_share":
        values = downside_variance_share(returns, h)
    elif name == "rsi":
        values = _rsi(c, h)
    elif name == "breadth":
        # Strict missing propagation across the entire authorized universe.
        valid &= valid.all(axis=1)[:, None]
        if c.shape[1] < MIN_BREADTH_ASSETS:
            valid[:] = False
        values = np.broadcast_to((momentum > 0).mean(axis=1)[:, None], c.shape).copy()
    elif name == "volatility_shock":
        values = np.log(
            np.maximum(sigma, EPSILON) / np.maximum(rolling(returns, h, "std"), EPSILON)
        )
    elif name == "volatility_of_volatility":
        values = rolling(np.log(np.maximum(rolling(returns, h, "std"), EPSILON)), short, "std")
    elif name == "close_location":
        high, low = _optional(panel, "adjusted_highs"), _optional(panel, "adjusted_lows")
        valid &= np.isfinite(high + low) & (low <= c) & (c <= high)
        location = np.divide(2 * c - high - low, high - low, out=np.zeros_like(c), where=high > low)
        values = rolling(np.where(valid, location, np.nan), h, "mean")
    elif name in {"opening_gap", "window_markout"}:
        raw, price, volume = (
            _positive(getattr(panel, field))
            for field in ("raw_closes", "window_vwaps", "window_volumes")
        )
        valid &= np.isfinite(raw + price + volume)
        window = price / raw * c
        values = (
            np.log(window / previous) / np.maximum(rolling(returns, h, "std"), EPSILON)
            if name == "opening_gap"
            else rolling(np.log(c / window), h, "mean")
        )
    else:
        raise ValueError(f"unregistered symbolic feature: {name}")
    return np.where(complete_window(valid, raw_history(node)), values, np.nan)


def terminal_values(panel: MarketPanel, node: FeatureNode) -> np.ndarray:
    """Apply frozen causal rolling normalization, clipping, source resolution and lag."""
    symbols = [str(symbol) for symbol in panel.symbols]
    if node.source != "self" and node.source not in symbols:
        raise ValueError(f"unauthorized symbolic source: {node.source}")
    # Suppress only arithmetic warnings; all nonfinite results become unavailable.
    with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
        values = raw_feature(panel, node)
        values = np.where(np.isfinite(values), values, np.nan)
        if node.transform == "time_series_rank":
            values = rolling(values, NORMALIZATION_WINDOW, "rank")
        else:
            mean = rolling(values, NORMALIZATION_WINDOW, "mean")
            scale = rolling(values, NORMALIZATION_WINDOW, "std")
            span = rolling(values, NORMALIZATION_WINDOW, "max") + rolling(
                -values, NORMALIZATION_WINDOW, "max"
            )
            values = np.divide(
                values - mean,
                scale,
                out=np.full_like(values, np.nan),
                where=(scale > 0) & (span > 0),
            )
        values = np.where(np.isfinite(values), np.clip(values, -FEATURE_CLIP, FEATURE_CLIP), np.nan)
    if node.source != "self":
        values = np.broadcast_to(values[:, symbols.index(node.source), None], values.shape).copy()
    return shifted(values, node.lag)
