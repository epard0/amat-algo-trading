"""Versioned feature formulas and point-in-time transformations, without trade actions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.features import (
    donchian_position,
    downside_variance_share,
    log_returns,
    relative_strength_index,
    variance_ratio,
)
from autoresearch.labels import adjusted_window_prices
from autoresearch.specs import FeatureSpec, required_history

REGISTRY_VERSION = "causal-features/6"
MIN_GROUP_SIZE = 3
EPSILON = 1e-12


@dataclass(frozen=True)
class Metadata:
    family: str
    units: str
    inputs: tuple[str, ...] = ("adjusted_closes",)
    available_at: str = "completed_session_close"
    missing_policy: str = "unavailable; signal abstains; diagnostics exclude"


REGISTRY = {
    "momentum": Metadata("trend", "fractional_return"),
    "volatility": Metadata("risk", "annualized_volatility"),
    "drawdown": Metadata("risk", "fraction_from_trailing_peak"),
    "volume_ratio": Metadata("participation", "ratio", ("session_volumes",)),
    "efficiency": Metadata("trend", "fraction"),
    "normalized_momentum": Metadata("trend", "volatility_units"),
    "trend_agreement": Metadata("trend", "signed_agreement"),
    "trend_acceleration": Metadata("trend", "change_in_volatility_units"),
    "donchian_position": Metadata("breakout", "range_fraction"),
    "breakout_distance": Metadata("breakout", "volatility_units"),
    "breakout_persistence": Metadata("breakout", "fraction_of_confirmed_breakout_sessions"),
    "relative_strength": Metadata("relative", "log_return_vs_benchmark"),
    "volume_price": Metadata(
        "participation", "signed_volume_surprise", ("adjusted_closes", "session_volumes")
    ),
    "variance_ratio": Metadata("structure", "variance_ratio"),
    "downside_variance_share": Metadata("structure", "fraction"),
    "rsi": Metadata("structure", "fraction"),
    "path_similarity": Metadata("pattern", "normalized_path_correlation"),
    "breadth": Metadata("market_structure", "fraction_of_frozen_universe_advancing"),
    "volatility_shock": Metadata("risk", "log_short_to_long_volatility_ratio"),
    "volatility_of_volatility": Metadata("risk", "dispersion_of_log_trailing_volatility"),
    "close_location": Metadata(
        "candle",
        "mean_signed_close_location",
        ("adjusted_closes", "adjusted_highs", "adjusted_lows"),
    ),
    "opening_gap": Metadata(
        "execution_proxy",
        "opening_window_gap_in_daily_volatility_units",
        ("adjusted_closes", "raw_closes", "window_vwaps", "window_volumes"),
    ),
    "window_markout": Metadata(
        "execution_proxy",
        "mean_log_opening_window_to_close_return",
        ("adjusted_closes", "raw_closes", "window_vwaps", "window_volumes"),
    ),
    "illiquidity": Metadata(
        "execution_proxy",
        "log1p_mean_absolute_return_per_100m_dollar_volume",
        ("adjusted_closes", "raw_closes", "session_volumes"),
    ),
}


def catalog() -> dict[str, Any]:
    return {
        "version": REGISTRY_VERSION,
        "features": {key: asdict(value) for key, value in REGISTRY.items()},
    }


def rolling(values: np.ndarray, window: int, operation: str) -> np.ndarray:
    out = np.full_like(values, np.nan, dtype=float)
    for index in range(window - 1, len(values)):
        block = values[index - window + 1 : index + 1]
        usable = np.isfinite(block).all(axis=0)
        if not usable.any():
            continue
        block = block[:, usable]
        if operation == "mean":
            row = block.mean(axis=0)
        elif operation == "std":
            row = block.std(axis=0, ddof=1)
        elif operation == "max":
            row = block.max(axis=0)
        elif operation == "rank":
            row = (
                np.sum(block < block[-1], axis=0) + 0.5 * np.sum(block == block[-1], axis=0)
            ) / window
        else:
            raise ValueError(f"unknown rolling operation: {operation}")
        out[index, usable] = row
    return out


def lag(values: np.ndarray, sessions: int) -> np.ndarray:
    out = np.full_like(values, np.nan)
    if sessions < len(values):
        out[sessions:] = values[:-sessions]
    return out


def cross_rank(values: np.ndarray) -> np.ndarray:
    out = np.full_like(values, np.nan)
    for index, row in enumerate(values):
        usable = np.isfinite(row)
        if usable.sum() < MIN_GROUP_SIZE:
            continue
        sample = row[usable]
        out[index, usable] = [
            (np.sum(sample < value) + 0.5 * np.sum(sample == value)) / len(sample)
            for value in sample
        ]
    return out


def raw_values(  # noqa: PLR0912, PLR0915 - explicit audited formula dispatch
    panel: MarketPanel, spec: FeatureSpec
) -> np.ndarray:
    closes = panel.adjusted_closes
    returns = log_returns(closes)
    h, short = spec.lookback, spec.secondary_lookback
    momentum = np.log(closes / lag(closes, h))
    sigma = rolling(returns, short, "std")
    normalized = momentum / np.maximum(sigma * np.sqrt(h), EPSILON)
    if spec.kind in ("opening_gap", "window_markout"):
        window = adjusted_window_prices(panel)
        if spec.kind == "opening_gap":
            values = np.log(window / lag(closes, 1)) / np.maximum(
                rolling(returns, h, "std"), EPSILON
            )
        else:
            values = rolling(np.log(closes / window), h, "mean")
    elif spec.kind == "illiquidity":
        _, _, _, volume = panel.context_inputs()
        valid = (
            np.isfinite(volume)
            & (volume > 0)
            & np.isfinite(panel.raw_closes)
            & (panel.raw_closes > 0)
        )
        dollars = np.multiply(
            volume, panel.raw_closes, out=np.full_like(closes, np.nan), where=valid
        )
        values = np.log1p(rolling(np.abs(returns) * 1e8 / dollars, h, "mean"))
    elif spec.kind == "close_location":
        _, highs, lows, _ = panel.context_inputs()
        valid = (
            np.isfinite(closes)
            & np.isfinite(highs)
            & np.isfinite(lows)
            & (lows > 0)
            & (highs >= closes)
            & (closes >= lows)
        )
        spread = highs - lows
        location = np.divide(
            2 * closes - highs - lows,
            spread,
            out=np.zeros_like(closes),
            where=valid & (spread > 0),
        )
        values = rolling(np.where(valid, location, np.nan), h, "mean")
    elif spec.kind == "momentum":
        values = closes / lag(closes, h) - 1
    elif spec.kind == "volatility":
        values = rolling(returns, h, "std") * np.sqrt(252)
    elif spec.kind == "breadth":
        # Require at least three assets and 80% coverage of the frozen universe.
        # The market-wide reading is not imputed into a missing asset's history.
        valid = np.isfinite(momentum) & (closes > 0) & (lag(closes, h) > 0)
        count = valid.sum(axis=1)
        coverage = count >= max(MIN_GROUP_SIZE, int(np.ceil(0.8 * closes.shape[1])))
        fraction = np.sum(valid & (momentum > 0), axis=1) / np.maximum(count, 1)
        values = np.where(valid & coverage[:, None], fraction[:, None], np.nan)
    elif spec.kind == "volatility_shock":
        long_sigma = rolling(returns, h, "std")
        values = np.log(np.maximum(sigma, EPSILON) / np.maximum(long_sigma, EPSILON))
    elif spec.kind == "volatility_of_volatility":
        trailing = rolling(returns, h, "std")
        values = rolling(np.log(np.maximum(trailing, EPSILON)), short, "std")
    elif spec.kind == "drawdown":
        values = closes / rolling(closes, h + 1, "max") - 1
    elif spec.kind == "efficiency":
        distance = rolling(np.abs(closes - lag(closes, 1)), h, "mean") * h
        values = np.divide(
            np.abs(closes - lag(closes, h)),
            distance,
            out=np.zeros_like(closes),
            where=distance > EPSILON,
        )
        values[:h] = np.nan
        values[~np.isfinite(distance)] = np.nan
    elif spec.kind == "normalized_momentum":
        values = normalized
    elif spec.kind == "trend_agreement":
        short_trend = np.log(closes / lag(closes, short))
        values = (np.sign(momentum) + np.sign(short_trend)) / 2
    elif spec.kind == "trend_acceleration":
        values = normalized - lag(normalized, short)
    elif spec.kind == "donchian_position":
        values = donchian_position(closes, h)
    elif spec.kind == "breakout_distance":
        prior_high = lag(rolling(closes, h, "max"), 1)
        values = np.log(closes / prior_high) / np.maximum(sigma, EPSILON)
    elif spec.kind == "breakout_persistence":
        # Hold each breakout level fixed for the confirmation window. Requiring
        # repeated new highs would measure a different and excessively narrow event.
        levels = lag(rolling(closes, h, "max"), short)
        values = np.full_like(closes, np.nan)
        for index in range(h + short, len(closes)):
            values[index] = np.mean(closes[index - short + 1 : index + 1] >= levels[index], axis=0)
    elif spec.kind == "relative_strength":
        symbols = [str(symbol) for symbol in panel.symbols]
        if spec.benchmark not in symbols:
            raise ValueError("relative-strength benchmark is absent from the frozen universe")
        values = momentum - momentum[:, symbols.index(spec.benchmark), None]
    elif spec.kind in ("volume_ratio", "volume_price"):
        if panel.session_volumes is None:
            raise ValueError(f"{spec.kind} requires full-session volume")
        prior_mean = lag(rolling(panel.session_volumes, h, "mean"), 1)
        ratio = np.divide(
            panel.session_volumes,
            prior_mean,
            out=np.full_like(closes, np.nan),
            where=prior_mean > 0,
        )
        values = (
            ratio
            if spec.kind == "volume_ratio"
            else np.log(np.maximum(ratio, EPSILON)) * np.tanh(returns / np.maximum(sigma, EPSILON))
        )
    elif spec.kind == "variance_ratio":
        values = variance_ratio(returns, lookback=h, horizon=short)
    elif spec.kind == "downside_variance_share":
        values = downside_variance_share(returns, h)
    elif spec.kind == "rsi":
        values = relative_strength_index(closes, h) / 100
    elif spec.kind == "path_similarity":
        if spec.template is None:
            raise ValueError("path similarity requires a template")
        values = path_similarity(closes, h, spec.template)
    else:
        raise ValueError(f"unregistered feature: {spec.kind}")
    return rolling(values, spec.smoothing, "mean") if spec.smoothing > 1 else values


def feature_values(
    panel: MarketPanel, spec: FeatureSpec, *, groups: tuple[tuple[int, ...], ...] = ()
) -> np.ndarray:
    """Fitted train_zscore is deliberately deferred to the chronological fitter."""
    values = raw_values(panel, spec)
    window = spec.normalization_window
    if spec.transform in ("time_series_rank", "group_rank"):
        historical = rolling(values, window, "rank")
        if spec.transform == "time_series_rank":
            values = historical
        else:
            if not groups or sorted(index for group in groups for index in group) != list(
                range(len(panel.symbols))
            ):
                raise ValueError("group rank requires a partition of the frozen universe")
            ranked = historical.copy()
            for group in groups:
                if len(group) >= MIN_GROUP_SIZE:
                    candidate = cross_rank(values[:, group])
                    ranked[:, group] = np.where(
                        np.isfinite(candidate), candidate, historical[:, group]
                    )
            values = ranked
    elif spec.transform == "rolling_zscore":
        mean, scale = rolling(values, window, "mean"), rolling(values, window, "std")
        values = (values - mean) / np.maximum(scale, EPSILON)
    elif spec.transform == "cross_sectional_rank":
        values = cross_rank(values)
    elif spec.transform == "volatility_scaled":
        scale = rolling(log_returns(panel.adjusted_closes), window, "std") * np.sqrt(spec.lookback)
        values = values / np.maximum(scale, EPSILON)
    if spec.lag_sessions:
        values = lag(values, spec.lag_sessions)
    return np.asarray(np.clip(values, -spec.clip, spec.clip), dtype=float)


def describe(spec: FeatureSpec) -> dict[str, Any]:
    return {
        "id": spec.digest(),
        "recipe": spec.model_dump(mode="json"),
        "metadata": asdict(REGISTRY[spec.kind]),
        "required_history_sessions": required_history(spec),
    }


def path_similarity(closes: np.ndarray, lookback: int, template: tuple[float, ...]) -> np.ndarray:
    """Correlation with an interpolated template, invariant to price level/scale.

    Flat paths are available but receive zero similarity. Missing or nonpositive
    closes make the entire trailing path unavailable.
    """
    target = np.interp(np.linspace(0, 1, lookback + 1), np.linspace(0, 1, len(template)), template)
    target -= target.mean()
    target /= np.linalg.norm(target)
    output = np.full_like(closes, np.nan)
    for index in range(lookback, len(closes)):
        block = closes[index - lookback : index + 1]
        usable = np.isfinite(block).all(axis=0) & (block > 0).all(axis=0)
        paths = np.log(block[:, usable])
        paths -= paths.mean(axis=0)
        norms = np.linalg.norm(paths, axis=0)
        output[index, usable] = (target @ paths) / np.maximum(norms, EPSILON)
    return np.asarray(np.clip(output, -1, 1))
