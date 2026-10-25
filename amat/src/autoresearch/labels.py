"""Executable-window forward labels; never consumed before their exit has matured."""

from __future__ import annotations

import numpy as np

from amat.backtest.panel import MarketPanel

HORIZONS = (5, 20, 60)


def adjusted_window_prices(panel: MarketPanel) -> np.ndarray:
    """Executable observations on the feature basis; absent volume is not a fill."""
    valid = (
        np.isfinite(panel.window_vwaps)
        & (panel.window_vwaps > 0)
        & np.isfinite(panel.window_volumes)
        & (panel.window_volumes > 0)
        & np.isfinite(panel.raw_closes)
        & (panel.raw_closes > 0)
        & np.isfinite(panel.adjusted_closes)
        & (panel.adjusted_closes > 0)
    )
    prices = np.divide(
        panel.window_vwaps,
        panel.raw_closes,
        out=np.full_like(panel.adjusted_closes, np.nan),
        where=valid,
    )
    return np.asarray(prices * panel.adjusted_closes)


def forward_returns(
    panel: MarketPanel, horizon: int, *, delay: int = 1, one_way_bps: float = 2.0
) -> np.ndarray:
    if horizon <= 0 or delay < 1:
        raise ValueError("labels require a positive horizon and next-session-or-later entry")
    # The daily adjustment factor puts both executable windows on a common
    # total-return basis. This is a research label, not a substitute for the
    # runner's cash/dividend/split accounting.
    prices = adjusted_window_prices(panel)
    valid = np.isfinite(prices) & (prices > 0)
    labels = np.full_like(prices, np.nan)
    count = len(panel) - delay - horizon
    if count <= 0:
        return np.asarray(labels)
    entry = prices[delay : delay + count]
    exit_price = prices[delay + horizon : delay + horizon + count]
    usable = valid[delay : delay + count] & valid[delay + horizon : delay + horizon + count]
    cost = one_way_bps / 10000
    labels[:count] = np.where(usable, exit_price / entry * (1 - cost) / (1 + cost) - 1, np.nan)
    return np.asarray(labels)


def purged_stop(prediction_start: int, horizon: int, embargo: int = 1, delay: int = 1) -> int:
    """Exclusive training-decision stop; every exit precedes prediction start."""
    return max(0, prediction_start - horizon - delay - embargo)
