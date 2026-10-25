"""Strict, read-only completed-session ingestion for frozen shadow campaigns.

Re-fetching the entire daily prefix deliberately fails closed on vendor revisions
and new retrospective adjustments. Such a change requires a new calibration;
appending prices expressed on a different adjustment basis is not permitted.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from zoneinfo import ZoneInfo

import exchange_calendars as xcals  # type: ignore[import-untyped]
import numpy as np

from amat.backtest.panel import MarketPanel
from amat.data.alpaca import AlpacaMarketData, DailyBar
from amat.errors import DataIntegrityError

_DAILY_FIELDS = (
    "adjusted_closes",
    "raw_closes",
    "adjusted_highs",
    "adjusted_lows",
    "session_volumes",
    "adjusted_opens",
)


def _indexed(
    bars: Sequence[DailyBar],
    expected: set[tuple[dt.date, str]],
) -> dict[tuple[dt.date, str], DailyBar]:
    result: dict[tuple[dt.date, str], DailyBar] = {}
    for bar in bars:
        key = (bar.session_date, bar.symbol)
        if key not in expected or key in result:
            raise DataIntegrityError("shadow feed contains duplicate or unexpected daily bars")
        prices = np.asarray([bar.open, bar.high, bar.low, bar.close], dtype=float)
        if (
            not np.all(np.isfinite(prices))
            or np.any(prices <= 0)
            or bar.high < max(bar.open, bar.low, bar.close)
            or bar.low > min(bar.open, bar.high, bar.close)
            or not np.isfinite(bar.volume)
            or bar.volume < 0
        ):
            raise DataIntegrityError("shadow feed contains invalid daily OHLCV")
        result[key] = bar
    if set(result) != expected:
        raise DataIntegrityError("shadow feed has missing daily sessions or symbols")
    return result


def extend_shadow_panel(
    panel: MarketPanel,
    client: AlpacaMarketData,
    *,
    through: dt.date,
    now: dt.datetime,
    window_start: dt.time = dt.time(9, 30),
    window_end: dt.time = dt.time(9, 35),
    exchange_timezone: str = "America/New_York",
) -> MarketPanel:
    """Append only closed XNYS sessions, preserving a verified frozen prefix.

    ``now`` is supplied by the campaign's trusted collection clock. Both raw and
    fully adjusted daily histories must cover every requested session and asset.
    Context follows snapshot semantics: raw OHLC times adjusted/raw close, with
    raw session volume. Existing execution windows are immutable snapshot inputs;
    newly collected windows must be complete and match the supplied contract.
    Even an unchanged end date revalidates daily history before returning.
    """
    _validate_request(
        panel,
        through,
        now,
        window_start=window_start,
        window_end=window_end,
        exchange_timezone=exchange_timezone,
    )
    calendar = xcals.get_calendar(
        "XNYS",
        start=str(panel.sessions[0] - dt.timedelta(days=1)),
        end=str(through + dt.timedelta(days=1)),
    )
    schedule = calendar.schedule
    schedule = schedule.loc[str(panel.sessions[0]) : str(through)]
    sessions = tuple(stamp.date() for stamp in schedule.index)
    if not sessions or sessions[-1] != through:
        raise DataIntegrityError("shadow feed through date must be an XNYS session")
    if schedule.iloc[-1]["close"].to_pydatetime() > now:
        raise DataIntegrityError("shadow feed refuses an unclosed session")
    if sessions[: len(panel.sessions)] != panel.sessions:
        raise DataIntegrityError("shadow feed existing panel has a calendar gap")
    expected = {(session, str(symbol)) for session in sessions for symbol in panel.symbols}
    raw = _indexed(
        client.daily_bars(
            panel.symbols,
            panel.sessions[0],
            through,
            adjustment="raw",
            exchange_timezone=exchange_timezone,
        ),
        expected,
    )
    adjusted = _indexed(
        client.daily_bars(
            panel.symbols,
            panel.sessions[0],
            through,
            adjustment="all",
            exchange_timezone=exchange_timezone,
        ),
        expected,
    )
    shape = (len(sessions), len(panel.symbols))
    daily = {name: np.empty(shape, dtype=float) for name in _DAILY_FIELDS}
    for row, session in enumerate(sessions):
        for column, symbol in enumerate(panel.symbols):
            bar, adj = raw[(session, symbol)], adjusted[(session, symbol)]
            factor = adj.close / bar.close
            values = (
                adj.close,
                bar.close,
                bar.high * factor,
                bar.low * factor,
                bar.volume,
                bar.open * factor,
            )
            for name, value in zip(_DAILY_FIELDS, values, strict=True):
                if not np.isfinite(value):
                    raise DataIntegrityError("shadow feed adjustment overflow")
                daily[name][row, column] = value
    for name in _DAILY_FIELDS:
        if not np.array_equal(daily[name][: len(panel.sessions)], getattr(panel, name)):
            raise DataIntegrityError(
                f"shadow feed history revision in {name}; recalibration required"
            )
    if len(sessions) == len(panel.sessions):
        return panel
    vwaps, volumes = _append_windows(
        panel,
        client,
        sessions,
        window_start=window_start,
        window_end=window_end,
        exchange_timezone=exchange_timezone,
    )
    return MarketPanel(
        sessions=sessions,
        symbols=panel.symbols,
        source=panel.source,
        adjusted_closes=daily["adjusted_closes"],
        raw_closes=daily["raw_closes"],
        adjusted_highs=daily["adjusted_highs"],
        adjusted_lows=daily["adjusted_lows"],
        adjusted_opens=daily["adjusted_opens"],
        session_volumes=daily["session_volumes"],
        window_vwaps=vwaps,
        window_volumes=volumes,
    )


def _append_windows(
    panel: MarketPanel,
    client: AlpacaMarketData,
    sessions: tuple[dt.date, ...],
    *,
    window_start: dt.time,
    window_end: dt.time,
    exchange_timezone: str,
) -> tuple[np.ndarray, np.ndarray]:
    zone = ZoneInfo(exchange_timezone)
    schedule = xcals.get_calendar(
        "XNYS",
        start=str(sessions[0] - dt.timedelta(days=1)),
        end=str(sessions[-1] + dt.timedelta(days=1)),
    ).schedule
    vwaps = np.empty((len(sessions), len(panel.symbols)), dtype=float)
    volumes = np.empty((len(sessions), len(panel.symbols)), dtype=float)
    vwaps[: len(panel.sessions)] = panel.window_vwaps
    volumes[: len(panel.sessions)] = panel.window_volumes
    for row in range(len(panel.sessions), len(sessions)):
        session = sessions[row]
        start = dt.datetime.combine(session, window_start, tzinfo=zone)
        end = dt.datetime.combine(session, window_end, tzinfo=zone)
        scheduled = schedule.loc[str(session)]
        if start < scheduled["open"].to_pydatetime() or end > scheduled["close"].to_pydatetime():
            raise DataIntegrityError("shadow execution window falls outside the exchange session")
        windows = client.execution_window_bars(
            panel.symbols,
            session,
            window_start=window_start,
            window_end=window_end,
            exchange_timezone=exchange_timezone,
        )
        seen: set[str] = set()
        for bar in windows:
            if bar.symbol not in panel.symbols or bar.symbol in seen or bar.session_date != session:
                raise DataIntegrityError("shadow feed contains duplicate or unexpected window bars")
            if (
                bar.window_start != start
                or bar.window_end != end
                or not np.isfinite(bar.vwap)
                or bar.vwap <= 0
                or not np.isfinite(bar.volume)
                or bar.volume < 0
            ):
                raise DataIntegrityError("shadow feed contains invalid execution windows")
            seen.add(bar.symbol)
            column = panel.symbols.index(bar.symbol)
            vwaps[row, column], volumes[row, column] = bar.vwap, bar.volume
        if seen != set(panel.symbols):
            raise DataIntegrityError("shadow feed has missing execution windows")
    return vwaps, volumes


def _validate_request(
    panel: MarketPanel,
    through: dt.date,
    now: dt.datetime,
    *,
    window_start: dt.time,
    window_end: dt.time,
    exchange_timezone: str,
) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise DataIntegrityError("shadow feed clock must be timezone-aware")
    if not panel.sessions or not panel.symbols:
        raise DataIntegrityError("shadow feed requires a nonempty calibration panel")
    if any(getattr(panel, name) is None for name in _DAILY_FIELDS):
        raise DataIntegrityError("shadow feed requires complete OHLCV context")
    if through < panel.sessions[-1]:
        raise DataIntegrityError("shadow feed cannot rewind the panel")
    if (
        window_start.tzinfo is not None
        or window_end.tzinfo is not None
        or window_end <= window_start
    ):
        raise DataIntegrityError("shadow feed requires an ordered local execution window")
    ZoneInfo(exchange_timezone)  # Validate the timezone before any requests.
