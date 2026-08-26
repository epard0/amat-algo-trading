"""Deterministic synthetic market data for engine verification.

WHAT THIS IS NOT
----------------
This is **not market data** and a result computed from it is **not a backtest
result**. It is a fixture generator whose only purpose is to exercise the engine
end to end -- session loop, execution timing, accounting identity, constraint
order -- without a data provider.

Any figure derived from this generator describes the *simulated process defined
below*, which was written by hand. It carries no information about any real
instrument and must never appear in a report as measured performance. The
generator stamps ``source="synthetic"`` onto the panel so that a run manifest
records the distinction and a report cannot silently omit it.

The process
-----------
Correlated geometric Brownian motion with a slow deterministic trend component,
built from a seeded ``numpy`` generator so that a given seed always reproduces a
given panel. Cluster structure is imposed through a block correlation matrix so
that the cluster caps have something to bind against.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.domain.instruments import Universe
from amat.errors import DataIntegrityError

__all__ = ["SYNTHETIC_SOURCE", "generate_panel", "trading_sessions"]

#: Recorded as the panel source so no artifact can hide its synthetic origin.
SYNTHETIC_SOURCE = "synthetic"


def trading_sessions(start: dt.date, count: int) -> tuple[dt.date, ...]:
    """A weekday-only session calendar of ``count`` sessions from ``start``.

    Weekday-only is a deliberate simplification and is *not* an exchange
    calendar: it observes no holidays. It is adequate for engine verification,
    where only strict ordering and adjacency matter, and inadequate for anything
    that depends on real session counts.
    """
    if count <= 0:
        message = f"count must be strictly positive, received {count}"
        raise DataIntegrityError(message)
    sessions: list[dt.date] = []
    cursor = start
    while len(sessions) < count:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += dt.timedelta(days=1)
    return tuple(sessions)


def generate_panel(
    universe: Universe,
    *,
    sessions: int = 1500,
    start: dt.date = dt.date(2015, 1, 1),
    seed: int = 20260808,
    annual_drift: float = 0.05,
    annual_volatility: float = 0.16,
    intra_cluster_correlation: float = 0.75,
    inter_cluster_correlation: float = 0.15,
    trend_strength: float = 0.35,
    trend_period_sessions: int = 300,
    zero_volume_probability: float = 0.002,
) -> MarketPanel:
    """Generate a deterministic synthetic panel for ``universe``.

    Args:
        universe: Supplies symbol order and cluster membership.
        sessions: Number of sessions to generate.
        start: First session date.
        seed: Reproducibility seed. The same seed always yields the same panel.
        annual_drift: Deterministic drift of the diffusion.
        annual_volatility: Annualized volatility of the diffusion.
        intra_cluster_correlation: Correlation between symbols in one cluster.
        inter_cluster_correlation: Correlation across clusters.
        trend_strength: Amplitude of the slow trend component. Set to ``0`` for
            a pure random walk, which is the correct null: a trend strategy
            should show no reliable edge on it.
        trend_period_sessions: Period of the slow trend component.
        zero_volume_probability: Probability that a session's execution window
            has zero volume, exercising the missed-fill path.

    Returns:
        A :class:`~amat.backtest.panel.MarketPanel` with ``source="synthetic"``.
    """
    symbols = universe.symbols
    count = len(symbols)
    if count == 0:
        message = "universe must contain at least one symbol"
        raise DataIntegrityError(message)

    rng = np.random.default_rng(seed)
    dates = trading_sessions(start, sessions)

    correlation = _block_correlation(
        universe, intra_cluster_correlation, inter_cluster_correlation
    )
    factor = np.linalg.cholesky(correlation)

    per_session_vol = annual_volatility / np.sqrt(252.0)
    per_session_drift = annual_drift / 252.0

    shocks = rng.standard_normal((sessions, count)) @ factor.T
    diffusion = per_session_drift - 0.5 * per_session_vol**2 + per_session_vol * shocks

    # A slow, phase-shifted cyclical component gives the trend signal something
    # to detect. Each symbol gets its own phase so the universe is not one trade.
    phases = rng.uniform(0.0, 2.0 * np.pi, size=count)
    time_axis = np.arange(sessions).reshape(-1, 1)
    cycle = trend_strength * per_session_vol * np.sin(
        2.0 * np.pi * time_axis / trend_period_sessions + phases
    )

    log_prices = np.cumsum(diffusion + cycle, axis=0)
    base = rng.uniform(40.0, 220.0, size=count)
    adjusted = base * np.exp(log_prices)

    # Raw and adjusted coincide: this generator emits no dividends or splits, so
    # inventing a divergence between the two series would be fabricating a
    # corporate-action history the accounting layer never sees.
    raw = adjusted.copy()

    # The execution window opens near the prior close with its own noise, so the
    # fill price genuinely differs from the price the decision was sized at.
    gap = rng.normal(0.0, per_session_vol * 0.5, size=(sessions, count))
    window_vwap = np.empty_like(raw)
    window_vwap[0] = raw[0]
    window_vwap[1:] = raw[:-1] * np.exp(gap[1:])

    volume = rng.uniform(5.0e5, 5.0e6, size=(sessions, count))
    volume[rng.random((sessions, count)) < zero_volume_probability] = 0.0

    # Intra-session range, so trailing support/resistance has something to find.
    # The half-range is drawn from the same per-session volatility that drives
    # the diffusion, which keeps the generated high/low consistent with the
    # price path instead of being an independent decoration -- a range unrelated
    # to realized volatility would make every level test pass or fail for
    # reasons that have nothing to do with the level logic.
    half_range = np.abs(rng.normal(0.0, per_session_vol, size=(sessions, count)))
    highs = adjusted * np.exp(half_range)
    lows = adjusted * np.exp(-half_range)

    # Session volume is a superset of the execution window's volume, so it is
    # scaled up rather than reused. A zero-volume window is a window property,
    # not a session property: the session still traded.
    session_volume = rng.uniform(5.0e6, 5.0e7, size=(sessions, count))

    return MarketPanel(
        sessions=dates,
        symbols=symbols,
        adjusted_closes=adjusted,
        raw_closes=raw,
        window_vwaps=window_vwap,
        window_volumes=volume,
        source=SYNTHETIC_SOURCE,
        adjusted_highs=highs,
        adjusted_lows=lows,
        session_volumes=session_volume,
    )


def _block_correlation(
    universe: Universe, intra: float, inter: float
) -> np.ndarray:
    """Block correlation matrix induced by cluster membership."""
    if not 0.0 <= inter < intra < 1.0:
        message = (
            f"require 0 <= inter ({inter}) < intra ({intra}) < 1 for a valid correlation block"
        )
        raise DataIntegrityError(message)

    symbols = universe.symbols
    clusters = [universe.cluster_of(symbol) for symbol in symbols]
    count = len(symbols)

    matrix = np.full((count, count), inter, dtype=float)
    for row in range(count):
        for column in range(count):
            if clusters[row] == clusters[column]:
                matrix[row, column] = intra
    np.fill_diagonal(matrix, 1.0)
    return matrix
