from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.domain.types import Symbol
from amat.patterns import scan_session_patterns


def _panel(*, sessions: int = 160, symbols: int = 2) -> MarketPanel:
    dates = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=index) for index in range(sessions))
    names = tuple(Symbol(f"S{index}") for index in range(symbols))
    adjusted = np.full((sessions, symbols), 100.0)
    raw = adjusted.copy()
    # Every day opens 20 bps above the previous close and closes back at 100.
    # With a 4 bp round trip, overnight is +16 bp and intraday is negative.
    window = np.full((sessions, symbols), 100.2)
    volume = np.full((sessions, symbols), 10_000.0)
    return MarketPanel(
        sessions=dates,
        symbols=names,
        adjusted_closes=adjusted,
        raw_closes=raw,
        window_vwaps=window,
        window_volumes=volume,
        source="synthetic:test",
    )


def test_scanner_finds_a_stable_cost_adjusted_overnight_effect() -> None:
    panel = _panel()
    patterns = scan_session_patterns(
        panel,
        train_end=panel.sessions[79],
        round_trip_cost_bps=4.0,
        minimum_observations=50,
    )
    overnight = next(row for row in patterns if row.symbol == "S0" and row.leg == "overnight")
    intraday = next(row for row in patterns if row.symbol == "S0" and row.leg == "intraday")
    assert overnight.train.mean_net_bps == pytest.approx(16.0)
    assert overnight.validation.mean_net_bps == pytest.approx(16.0)
    assert overnight.train.win_rate == 1.0
    assert overnight.robust_candidate
    assert intraday.train.mean_net_bps < 0.0
    assert not intraday.robust_candidate


def test_missing_open_window_is_excluded_not_imputed() -> None:
    panel = _panel()
    panel.window_vwaps[10, 0] = np.nan
    panel.window_volumes[11, 0] = 0.0
    patterns = scan_session_patterns(
        panel,
        train_end=panel.sessions[79],
        minimum_observations=20,
    )
    overnight = next(row for row in patterns if row.symbol == "S0" and row.leg == "overnight")
    assert overnight.train.observations == 77


def test_scanner_rejects_negative_costs() -> None:
    with pytest.raises(Exception, match="non-negative"):
        scan_session_patterns(_panel(), train_end=dt.date(2020, 3, 1), round_trip_cost_bps=-1.0)
