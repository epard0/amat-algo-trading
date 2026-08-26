"""Build an in-memory market panel for ephemeral news asset candidates."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from amat.data.alpaca import AlpacaMarketData, DailyBar
from amat.data.live import extend_market_panel
from amat.data.snapshot import SnapshotManifest, load_manifest, load_panel
from amat.domain.types import Symbol

__all__ = ["NewsPanelResult", "load_news_plan_panel"]


@dataclass(frozen=True, slots=True)
class NewsPanelResult:
    panel: MarketPanel
    manifest: SnapshotManifest
    appended_sessions: int
    accepted: tuple[str, ...]
    rejected: dict[str, str]


def _bar_map(rows: list[DailyBar]) -> dict[tuple[dt.date, str], float]:
    return {(bar.session_date, bar.symbol.upper()): bar.close for bar in rows}


def load_news_plan_panel(
    snapshot: Path,
    base_symbols: list[str],
    candidate_symbols: tuple[str, ...],
    *,
    strategy: StrategyConfig,
    client: AlpacaMarketData | None,
    through: dt.date | None = None,
) -> NewsPanelResult:
    """Load base history, add candidates, and optionally append current bars.

    A candidate must have raw and adjusted closes on every base-panel session.
    Missing cells are never forward-filled: the covariance matrix cannot
    distinguish an invented zero return from a real one. Candidates failing
    this gate are omitted individually and reported in the plan caveats so one
    recent IPO does not suppress otherwise usable discoveries.
    """
    base_panel, manifest = load_panel(snapshot, base_symbols)
    if not candidate_symbols:
        if client is None:
            return NewsPanelResult(base_panel, manifest, 0, (), {})
        extended, appended = extend_market_panel(
            base_panel,
            client,
            base_symbols,
            strategy=strategy,
            through=through,
        )
        return NewsPanelResult(extended, manifest, appended, (), {})

    snapshot_symbols = set(load_manifest(snapshot).symbols)
    sessions = tuple(base_panel.sessions)
    adjusted_by_symbol: dict[str, dict[dt.date, float]] = {}
    raw_by_symbol: dict[str, dict[dt.date, float]] = {}
    window_by_symbol: dict[str, dict[dt.date, tuple[float, float]]] = {}
    rejected: dict[str, str] = {}

    missing_from_snapshot = [symbol for symbol in candidate_symbols if symbol not in snapshot_symbols]
    if missing_from_snapshot and client is None:
        rejected.update(
            dict.fromkeys(missing_from_snapshot, "not in the snapshot and --offline forbids backfill")
        )
    elif missing_from_snapshot:
        active_client = cast(AlpacaMarketData, client)
        fetched_raw = _bar_map(
            active_client.daily_bars(
                missing_from_snapshot,
                sessions[0],
                sessions[-1],
                adjustment="raw",
            )
        )
        fetched_adjusted = _bar_map(
            active_client.daily_bars(
                missing_from_snapshot,
                sessions[0],
                sessions[-1],
                adjustment="all",
            )
        )
        for symbol in missing_from_snapshot:
            raw_by_symbol[symbol] = {
                session: fetched_raw[(session, symbol)]
                for session in sessions
                if (session, symbol) in fetched_raw
            }
            adjusted_by_symbol[symbol] = {
                session: fetched_adjusted[(session, symbol)]
                for session in sessions
                if (session, symbol) in fetched_adjusted
            }
            window_by_symbol[symbol] = {}

    for symbol in candidate_symbols:
        if symbol not in snapshot_symbols:
            continue
        candidate, _candidate_manifest = load_panel(snapshot, [symbol])
        adjusted_by_symbol[symbol] = dict(
            zip(candidate.sessions, candidate.adjusted_closes[:, 0], strict=True)
        )
        raw_by_symbol[symbol] = dict(zip(candidate.sessions, candidate.raw_closes[:, 0], strict=True))
        window_by_symbol[symbol] = {
            session: (float(candidate.window_vwaps[row, 0]), float(candidate.window_volumes[row, 0]))
            for row, session in enumerate(candidate.sessions)
        }

    accepted: list[str] = []
    for symbol in candidate_symbols:
        if symbol in rejected:
            continue
        symbol_adjusted = adjusted_by_symbol.get(symbol, {})
        symbol_raw = raw_by_symbol.get(symbol, {})
        missing = [
            session
            for session in sessions
            if session not in symbol_adjusted or session not in symbol_raw
        ]
        if missing:
            rejected[symbol] = (
                f"missing raw/adjusted history on {len(missing)} of {len(sessions)} "
                "base sessions"
            )
            continue
        accepted.append(symbol)

    if accepted:
        columns = len(base_symbols) + len(accepted)
        rows = len(sessions)
        adjusted_array = np.full((rows, columns), np.nan)
        raw_array = np.full((rows, columns), np.nan)
        window_array = np.full((rows, columns), np.nan)
        volume_array = np.zeros((rows, columns))
        base_width = len(base_symbols)
        adjusted_array[:, :base_width] = base_panel.adjusted_closes
        raw_array[:, :base_width] = base_panel.raw_closes
        window_array[:, :base_width] = base_panel.window_vwaps
        volume_array[:, :base_width] = base_panel.window_volumes
        for offset, symbol in enumerate(accepted, start=base_width):
            adjusted_array[:, offset] = [adjusted_by_symbol[symbol][session] for session in sessions]
            raw_array[:, offset] = [raw_by_symbol[symbol][session] for session in sessions]
            windows = window_by_symbol[symbol]
            for row, session in enumerate(sessions):
                if session in windows:
                    window_array[row, offset], volume_array[row, offset] = windows[session]
        combined = MarketPanel(
            sessions=sessions,
            symbols=tuple(Symbol(symbol) for symbol in (*base_symbols, *accepted)),
            adjusted_closes=adjusted_array,
            raw_closes=raw_array,
            window_vwaps=window_array,
            window_volumes=volume_array,
            source=f"{base_panel.source}+news-assets",
        )
    else:
        combined = base_panel

    appended = 0
    if client is not None:
        symbols = [str(symbol) for symbol in combined.symbols]
        combined, appended = extend_market_panel(
            combined,
            client,
            symbols,
            strategy=strategy,
            through=through,
        )
    return NewsPanelResult(
        panel=combined,
        manifest=manifest,
        appended_sessions=appended,
        accepted=tuple(accepted),
        rejected=rejected,
    )
