"""Maturity-aware entry outcomes on observed windows; no quoted-liquidity claims."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.labels import adjusted_window_prices, forward_returns


@dataclass(frozen=True)
class EntryOutcomes:
    net_return: np.ndarray
    adverse_excursion: np.ndarray
    deferral_advantage: np.ndarray
    markout_one: np.ndarray
    markout_five: np.ndarray


def entry_outcomes(panel: MarketPanel, horizon: int) -> EntryOutcomes:
    """All arrays are indexed by the decision close, before a next-session entry.

    Deferral advantage is entry-window price / following-window price - 1:
    the relative terminal value gained by deferring one day, for equal capital,
    equal proportional costs and a common eventual exit. It matures at t+2.
    Excursion uses the entry-day close and the following four days' lows; an
    entry-day low could precede execution and is deliberately ineligible.
    Markouts are price-return proxies, not NBBO or liquidity-provider markouts.
    """
    closes, _, lows, _ = panel.context_inputs()
    window = adjusted_window_prices(panel)
    adverse, deferral, one, five = (np.full_like(closes, np.nan) for _ in range(4))
    for decision in range(len(panel) - 1):
        entry = decision + 1
        price = window[entry]
        one[decision] = closes[entry] / price - 1
        if entry + 1 < len(panel):
            deferral[decision] = price / window[entry + 1] - 1
        if entry + 4 < len(panel):
            future = np.vstack((closes[entry], lows[entry + 1 : entry + 5]))
            available = np.isfinite(future).all(axis=0) & (future > 0).all(axis=0)
            worst = np.where(available, future.min(axis=0), np.nan)
            adverse[decision] = np.maximum(0.0, 1 - worst / price)
            five[decision] = closes[entry + 4] / price - 1
    return EntryOutcomes(forward_returns(panel, horizon), adverse, deferral, one, five)
