"""Read-only live-tail construction shared by planning and simulation."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from zoneinfo import ZoneInfo

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from amat.data.alpaca import AlpacaMarketData
from amat.errors import DataIntegrityError

__all__ = ["extend_market_panel", "latest_completed_session"]


def latest_completed_session(*, now: dt.datetime | None = None) -> dt.date:
    """Conservative exchange-local date cutoff, not a holiday-calendar claim.

    UTC midnight arrives during the prior US evening. Subtracting a day in
    UTC can therefore include the current exchange date before it is complete.
    Missing weekend/holiday sessions are resolved from returned market data.
    """
    moment = now or dt.datetime.now(tz=dt.UTC)
    if moment.tzinfo is None:
        raise ValueError("latest completed cutoff requires an aware instant")
    return moment.astimezone(ZoneInfo("America/New_York")).date() - dt.timedelta(days=1)


def extend_market_panel(
    panel: MarketPanel,
    client: AlpacaMarketData,
    symbols: Sequence[str],
    *,
    strategy: StrategyConfig,
    through: dt.date | None = None,
) -> tuple[MarketPanel, int]:
    """Append complete live sessions to an already aligned market panel."""
    expected = tuple(str(symbol) for symbol in panel.symbols)
    if tuple(symbols) != expected:
        message = "symbols must match the panel's exact column order before live extension"
        raise DataIntegrityError(message)
    last_known = panel.sessions[-1]
    end = through or latest_completed_session()
    if end <= last_known:
        return panel, 0

    start = last_known + dt.timedelta(days=1)
    raw = client.daily_bars(symbols, start, end, adjustment="raw")
    adjusted = client.daily_bars(symbols, start, end, adjustment="all")
    if not raw or not adjusted:
        return panel, 0

    raw_by_key = {(bar.session_date, bar.symbol): bar for bar in raw}
    adjusted_by_key = {(bar.session_date, bar.symbol): bar for bar in adjusted}
    candidate_sessions = sorted(
        {session for session, _ in raw_by_key} & {session for session, _ in adjusted_by_key}
    )
    complete = [
        session
        for session in candidate_sessions
        if all(
            (session, symbol) in raw_by_key and (session, symbol) in adjusted_by_key
            for symbol in symbols
        )
    ]
    if not complete:
        return panel, 0

    width = len(symbols)
    tail_adjusted = np.full((len(complete), width), np.nan)
    tail_raw = np.full((len(complete), width), np.nan)
    tail_vwap = np.full((len(complete), width), np.nan)
    tail_volume = np.zeros((len(complete), width))
    index_of = {symbol: column for column, symbol in enumerate(symbols)}

    for row, session in enumerate(complete):
        for column, symbol in enumerate(symbols):
            tail_adjusted[row, column] = adjusted_by_key[(session, symbol)].close
            tail_raw[row, column] = raw_by_key[(session, symbol)].close
        for bar in client.execution_window_bars(
            symbols,
            session,
            window_start=strategy.execution.window_start,
            window_end=strategy.execution.window_end,
            exchange_timezone=strategy.execution.timezone,
        ):
            if bar.vwap > 0 and bar.volume > 0:
                column = index_of[bar.symbol]
                tail_vwap[row, column] = bar.vwap
                tail_volume[row, column] = bar.volume

    extended = MarketPanel(
        sessions=(*panel.sessions, *complete),
        symbols=panel.symbols,
        adjusted_closes=np.vstack([panel.adjusted_closes, tail_adjusted]),
        raw_closes=np.vstack([panel.raw_closes, tail_raw]),
        window_vwaps=np.vstack([panel.window_vwaps, tail_vwap]),
        window_volumes=np.vstack([panel.window_volumes, tail_volume]),
        source=f"{panel.source}+live",
    )
    return extended, len(complete)
