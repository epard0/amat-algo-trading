"""Market context: multi-timeframe structure, location, volume, and patterns.

This package answers "where are we, and in what conditions?" -- the qualitative
judgement a discretionary trader applies, expressed as causal numeric features.

Causality is the binding constraint on everything here
------------------------------------------------------
Support/resistance, reversal patterns, and regime labels are exactly the places
where look-ahead creeps into a research codebase, because the natural way to
compute them ("find the swing highs") uses a centred window that peeks forward.
Every function in this package therefore uses **trailing windows only**, and
``tests/unit/test_context.py`` asserts that mutating future rows leaves earlier
output bit-identical.

A swing high confirmed with ``k`` bars of right-hand context is only *knowable*
``k`` bars later, so this module dates it accordingly rather than at the pivot.
That deliberately makes the feature less pretty and more honest.

Nothing here produces a trading decision on its own. These are inputs to
:mod:`amat.risk` scoring and :mod:`amat.regime` classification.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from amat.errors import DataIntegrityError

__all__ = [
    "ContextPanels",
    "ContextSnapshot",
    "TimeframeView",
    "build_context",
    "build_context_panels",
    "distance_to_level",
    "market_feature_history",
    "mta_alignment",
    "resistance_levels",
    "reversal_score",
    "rolling_extreme",
    "support_levels",
    "trend_state",
    "volume_context",
]

#: A market panel is always 2-D: (sessions, symbols).
_PANEL_NDIM = 2

#: Steepness of the tanh squash applied to the reversal measure.
_REVERSAL_GAIN = 4.0

#: Shortest window that can be split into two comparable halves.
_MIN_VOLUME_WINDOW = 4


@dataclass(frozen=True, slots=True)
class TimeframeView:
    """One timeframe's view of trend and location for every symbol.

    Attributes:
        horizon_sessions: Lookback this view summarizes.
        trend: Sign-and-strength of the trend in ``[-1, 1]``.
        location: Where price sits inside the trailing range, ``0`` at the low
            and ``1`` at the high. This is the "location" leg of location /
            trend / volume: buying strength at the top of a range and buying
            strength at the bottom are different trades.
        slope: Annualized log-price slope over the window.
    """

    horizon_sessions: int
    trend: np.ndarray
    location: np.ndarray
    slope: np.ndarray


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Full market context for one session, for every symbol.

    Every array is ``(N,)`` in universe order and is computed from completed
    sessions only.
    """

    views: tuple[TimeframeView, ...]
    alignment: np.ndarray
    support_distance: np.ndarray
    resistance_distance: np.ndarray
    volume_z: np.ndarray
    volume_trend: np.ndarray
    reversal: np.ndarray

    def as_matrix(self) -> np.ndarray:
        """Stack context into an ``(N, F)`` feature matrix for the regime model."""
        return np.column_stack(
            [
                self.alignment,
                self.support_distance,
                self.resistance_distance,
                self.volume_z,
                self.volume_trend,
                self.reversal,
                *[view.trend for view in self.views],
                *[view.location for view in self.views],
            ]
        )

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Column names matching :meth:`as_matrix`."""
        return (
            "alignment",
            "support_distance",
            "resistance_distance",
            "volume_z",
            "volume_trend",
            "reversal",
            *[f"trend_{view.horizon_sessions}" for view in self.views],
            *[f"location_{view.horizon_sessions}" for view in self.views],
        )


def rolling_extreme(values: np.ndarray, window: int, *, mode: str) -> np.ndarray:
    """Trailing rolling maximum or minimum, inclusive of the current row.

    Trailing rather than centred: a centred extreme would use future bars.

    Args:
        values: ``(T, N)`` panel.
        window: Lookback length in sessions.
        mode: ``"max"`` or ``"min"``.

    Returns:
        ``(T, N)`` array, ``nan`` until ``window`` rows exist.
    """
    _require_panel(values)
    if window <= 0:
        message = f"window must be strictly positive, received {window}"
        raise DataIntegrityError(message)
    if mode not in {"max", "min"}:
        message = f"mode must be 'max' or 'min', received {mode!r}"
        raise DataIntegrityError(message)

    rows, cols = values.shape
    out = np.full((rows, cols), np.nan, dtype=float)
    reducer = np.nanmax if mode == "max" else np.nanmin
    for row in range(window - 1, rows):
        block = values[row - window + 1 : row + 1]
        if np.all(np.isnan(block)):
            continue
        out[row] = reducer(block, axis=0)
    return out


def support_levels(lows: np.ndarray, window: int) -> np.ndarray:
    """Trailing support: the lowest low of the last ``window`` sessions."""
    return rolling_extreme(lows, window, mode="min")


def resistance_levels(highs: np.ndarray, window: int) -> np.ndarray:
    """Trailing resistance: the highest high of the last ``window`` sessions."""
    return rolling_extreme(highs, window, mode="max")


def distance_to_level(prices: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Signed fractional distance from price to a level.

    Positive means price is above the level. Expressed as a fraction of price so
    it is comparable across instruments at different nominal prices.
    """
    if prices.shape != levels.shape:
        message = f"prices {prices.shape} and levels {levels.shape} must match"
        raise DataIntegrityError(message)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.asarray((prices - levels) / prices, dtype=float)


def trend_state(prices: np.ndarray, horizon: int, *, annualization: int = 252) -> TimeframeView:
    """Trend, location, and slope over one trailing timeframe.

    ``trend`` is ``tanh`` of the window's log return scaled by its own realized
    dispersion, so it is comparable across instruments and bounded in
    ``[-1, 1]``. ``location`` is the percentile of the current close within the
    window's range.
    """
    _require_panel(prices)
    if horizon <= 1:
        message = f"horizon must exceed 1, received {horizon}"
        raise DataIntegrityError(message)

    rows, cols = prices.shape
    log_prices = np.log(prices)

    window_return = np.full((rows, cols), np.nan, dtype=float)
    if rows > horizon:
        window_return[horizon:] = log_prices[horizon:] - log_prices[:-horizon]

    dispersion = np.full((rows, cols), np.nan, dtype=float)
    steps = np.full((rows, cols), np.nan, dtype=float)
    steps[1:] = log_prices[1:] - log_prices[:-1]
    for row in range(horizon, rows):
        block = steps[row - horizon + 1 : row + 1]
        dispersion[row] = np.nanstd(block, axis=0, ddof=1) * np.sqrt(horizon)

    with np.errstate(invalid="ignore", divide="ignore"):
        trend = np.tanh(np.where(dispersion > 0, window_return / dispersion, np.nan))
        slope = window_return * (annualization / horizon)

    highest = rolling_extreme(prices, horizon, mode="max")
    lowest = rolling_extreme(prices, horizon, mode="min")
    span = highest - lowest
    with np.errstate(invalid="ignore", divide="ignore"):
        location = np.where(span > 0, (prices - lowest) / span, 0.5)
    location[~np.isfinite(highest) | ~np.isfinite(lowest)] = np.nan

    return TimeframeView(horizon_sessions=horizon, trend=trend, location=location, slope=slope)


def mta_alignment(views: tuple[TimeframeView, ...]) -> np.ndarray:
    """Agreement across timeframes, in ``[-1, 1]``.

    The multi-timeframe question is not "is the trend up?" but "do the
    timeframes agree?". A short-term rally against a long-term downtrend is a
    materially worse setup than the same rally with the higher timeframe behind
    it, and a plain average of trend scores hides that.

    This multiplies mean trend strength by the fraction of timeframes sharing the
    dominant sign, so unanimous-but-weak scores above mixed-but-strong ones.
    """
    if not views:
        message = "mta_alignment requires at least one timeframe view"
        raise DataIntegrityError(message)

    stacked = np.stack([view.trend for view in views])
    with np.errstate(invalid="ignore"):
        mean_trend = _nanmean(stacked, axis=0)
        signs = np.sign(stacked)
        dominant = np.sign(mean_trend)
        agreement = _nanmean(signs == dominant, axis=0)
    alignment = mean_trend * agreement
    alignment[~np.isfinite(stacked).all(axis=0)] = np.nan
    return np.asarray(alignment, dtype=float)


def volume_context(volumes: np.ndarray, window: int = 63) -> tuple[np.ndarray, np.ndarray]:
    """Volume z-score and volume trend over a trailing window.

    Volume is the confirmation leg of location / trend / volume: a breakout on
    below-average volume and the same breakout on a volume surge are different
    events, and only the second is confirmation.

    Returns:
        ``(volume_z, volume_trend)``. ``volume_z`` is the standardized current
        volume; ``volume_trend`` is the log ratio of the recent half-window mean
        to the older half-window mean.
    """
    _require_panel(volumes)
    if window < _MIN_VOLUME_WINDOW:
        message = f"volume window must be at least {_MIN_VOLUME_WINDOW}, received {window}"
        raise DataIntegrityError(message)

    rows, cols = volumes.shape
    z = np.full((rows, cols), np.nan, dtype=float)
    trend = np.full((rows, cols), np.nan, dtype=float)
    half = window // 2

    for row in range(window - 1, rows):
        block = volumes[row - window + 1 : row + 1]
        mean = np.nanmean(block, axis=0)
        std = np.nanstd(block, axis=0, ddof=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            z[row] = np.where(std > 0, (volumes[row] - mean) / std, 0.0)
            recent = np.nanmean(block[-half:], axis=0)
            older = np.nanmean(block[:half], axis=0)
            trend[row] = np.where((recent > 0) & (older > 0), np.log(recent / older), np.nan)
    return z, trend


def reversal_score(
    prices: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    window: int = 21,
) -> np.ndarray:
    """Trailing reversal evidence, in ``[-1, 1]``.

    Positive means evidence of a bullish reversal (a failed probe of the
    trailing low that closed back up); negative the bearish mirror. Both are
    computed from completed bars only, so the pattern is dated when it became
    *knowable*, not when the pivot occurred.

    This is a deliberately simple, auditable rejection measure rather than a
    catalogue of named candlestick patterns: named patterns carry a large
    parameter surface and weak evidence, and the contract prefers a small
    deterministic core.
    """
    for panel in (prices, highs, lows):
        _require_panel(panel)
    if not prices.shape == highs.shape == lows.shape:
        message = "prices, highs, and lows must share a shape"
        raise DataIntegrityError(message)

    trailing_low = rolling_extreme(lows, window, mode="min")
    trailing_high = rolling_extreme(highs, window, mode="max")
    span = trailing_high - trailing_low

    with np.errstate(invalid="ignore", divide="ignore"):
        # How far the session probed below the trailing low, then recovered.
        undercut = np.where(span > 0, (trailing_low - lows) / span, 0.0)
        recovery = np.where(span > 0, (prices - lows) / span, 0.0)
        bullish = np.clip(undercut, 0.0, None) * np.clip(recovery, 0.0, 1.0)

        overshoot = np.where(span > 0, (highs - trailing_high) / span, 0.0)
        fade = np.where(span > 0, (highs - prices) / span, 0.0)
        bearish = np.clip(overshoot, 0.0, None) * np.clip(fade, 0.0, 1.0)

    score = np.tanh(_REVERSAL_GAIN * (bullish - bearish))
    score[~np.isfinite(span)] = np.nan
    return np.asarray(score, dtype=float)


def build_context(
    prices: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    *,
    horizons: tuple[int, ...] = (21, 63, 252),
    level_window: int = 63,
    volume_window: int = 63,
    index: int = -1,
) -> ContextSnapshot:
    """Assemble the full context snapshot for one session.

    Args:
        prices: ``(T, N)`` adjusted closes.
        highs, lows: ``(T, N)`` raw highs and lows for level structure.
        volumes: ``(T, N)`` session volumes.
        horizons: Timeframes for the sliding-window multi-timeframe analysis.
        level_window: Lookback for support and resistance.
        volume_window: Lookback for volume statistics.
        index: Row to snapshot. Defaults to the last row.

    Returns:
        A :class:`ContextSnapshot` for that session.
    """
    views = tuple(trend_state(prices, horizon) for horizon in horizons)
    alignment = mta_alignment(views)
    support = support_levels(lows, level_window)
    resistance = resistance_levels(highs, level_window)
    volume_z, volume_trend = volume_context(volumes, volume_window)
    reversal = reversal_score(prices, highs, lows)

    return ContextSnapshot(
        views=tuple(
            TimeframeView(
                horizon_sessions=view.horizon_sessions,
                trend=view.trend[index],
                location=view.location[index],
                slope=view.slope[index],
            )
            for view in views
        ),
        alignment=alignment[index],
        support_distance=distance_to_level(prices, support)[index],
        resistance_distance=distance_to_level(prices, resistance)[index],
        volume_z=volume_z[index],
        volume_trend=volume_trend[index],
        reversal=reversal[index],
    )


@dataclass(frozen=True, slots=True)
class ContextPanels:
    """Full-panel context arrays, computed once and indexed per session.

    Every underlying function uses trailing windows only, so row ``t`` depends on
    no row after ``t``. Computing the panel once and indexing is therefore
    identical to rebuilding context on each truncated panel -- and turns an
    ``O(T^2)`` session loop into ``O(T)``. ``tests/unit/test_context_and_regime.py``
    asserts that equivalence rather than assuming it.
    """

    views: tuple[TimeframeView, ...]
    alignment: np.ndarray
    support_distance: np.ndarray
    resistance_distance: np.ndarray
    volume_z: np.ndarray
    volume_trend: np.ndarray
    reversal: np.ndarray

    def at(self, index: int) -> ContextSnapshot:
        """The context snapshot for one session."""
        return ContextSnapshot(
            views=tuple(
                TimeframeView(
                    horizon_sessions=view.horizon_sessions,
                    trend=view.trend[index],
                    location=view.location[index],
                    slope=view.slope[index],
                )
                for view in self.views
            ),
            alignment=self.alignment[index],
            support_distance=self.support_distance[index],
            resistance_distance=self.resistance_distance[index],
            volume_z=self.volume_z[index],
            volume_trend=self.volume_trend[index],
            reversal=self.reversal[index],
        )

    def market_history(self) -> np.ndarray:
        """``(T, 6)`` market-level condition history for the regime model."""
        with np.errstate(invalid="ignore"):
            return np.column_stack(
                [
                    _nanmean(panel, axis=1)
                    for panel in (
                        self.alignment,
                        self.support_distance,
                        self.resistance_distance,
                        self.volume_z,
                        self.volume_trend,
                        self.reversal,
                    )
                ]
            )


def build_context_panels(
    prices: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    *,
    horizons: tuple[int, ...] = (21, 63, 252),
    level_window: int = 63,
    volume_window: int = 63,
) -> ContextPanels:
    """Compute every context panel once for the whole history."""
    views = tuple(trend_state(prices, horizon) for horizon in horizons)
    volume_z, volume_trend = volume_context(volumes, volume_window)
    return ContextPanels(
        views=views,
        alignment=mta_alignment(views),
        support_distance=distance_to_level(prices, support_levels(lows, level_window)),
        resistance_distance=distance_to_level(prices, resistance_levels(highs, level_window)),
        volume_z=volume_z,
        volume_trend=volume_trend,
        reversal=reversal_score(prices, highs, lows),
    )


def market_feature_history(
    prices: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    *,
    horizons: tuple[int, ...] = (21, 63, 252),
    level_window: int = 63,
    volume_window: int = 63,
) -> np.ndarray:
    """Market-level condition history, one row per session.

    Cross-sectional means collapse the per-symbol context into a single market
    state per session, because a *regime* is a property of the market rather
    than of an instrument.

    Computing this once over the whole panel and slicing is safe -- and is what
    :mod:`amat.regime` relies on -- because every underlying function uses
    trailing windows only, so row ``t`` depends on no row after ``t``. Slicing
    ``[:index + 1]`` therefore yields exactly what recomputing on the truncated
    panel would. ``tests/unit/test_context.py`` asserts that equivalence rather
    than assuming it; the alternative, recomputing per row, is quadratic.

    Returns:
        ``(T, 6)`` array: alignment, support distance, resistance distance,
        volume z-score, volume trend, reversal.
    """
    views = tuple(trend_state(prices, horizon) for horizon in horizons)
    alignment = mta_alignment(views)
    support = distance_to_level(prices, support_levels(lows, level_window))
    resistance = distance_to_level(prices, resistance_levels(highs, level_window))
    volume_z, volume_trend = volume_context(volumes, volume_window)
    reversal = reversal_score(prices, highs, lows)

    with np.errstate(invalid="ignore"):
        return np.column_stack(
            [
                _nanmean(panel, axis=1)
                for panel in (
                    alignment,
                    support,
                    resistance,
                    volume_z,
                    volume_trend,
                    reversal,
                )
            ]
        )


def _nanmean(values: np.ndarray, *, axis: int) -> np.ndarray:
    """``np.nanmean`` that returns ``nan`` for an all-``nan`` slice, quietly.

    An all-``nan`` slice is the *normal* state during warm-up -- before the
    longest horizon fills, every symbol legitimately has no trend estimate.
    ``np.nanmean`` emits a ``RuntimeWarning`` there, and the project runs pytest
    with warnings as errors, so a routine warm-up row would fail the suite. The
    result is identical; only the warning is suppressed, and only for the case
    that is genuinely expected.
    """
    mask = np.all(np.isnan(values), axis=axis)
    with np.errstate(invalid="ignore"):
        summed = np.nansum(values, axis=axis)
        counted = np.sum(~np.isnan(values), axis=axis)
        result = np.divide(
            summed,
            counted,
            out=np.full(summed.shape, np.nan, dtype=float),
            where=counted > 0,
        )
    result[mask] = np.nan
    return np.asarray(result, dtype=float)


def _require_panel(values: np.ndarray) -> None:
    if values.ndim != _PANEL_NDIM:
        message = f"expected a 2-D (T, N) panel, received shape {values.shape}"
        raise DataIntegrityError(message)
