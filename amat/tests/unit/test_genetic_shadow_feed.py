"""Completed-session ingestion refuses calendar gaps and revised calibration."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.data.alpaca import DailyBar, WindowBar
from amat.errors import DataIntegrityError
from autoresearch.genetic.shadow_feed import extend_shadow_panel

DAYS = (dt.date(2025, 11, 26), dt.date(2025, 11, 28))  # Thanksgiving gap / early close
ZONE = ZoneInfo("America/New_York")


class Feed:
    def __init__(self) -> None:
        self.raw = [
            DailyBar("SPY", day, 100.0, 103.0, 99.0, 102.0, 1000.0, 101.0, 10) for day in DAYS
        ]
        self.adjusted = [
            replace(bar, open=50.0, high=51.5, low=49.5, close=51.0) for bar in self.raw
        ]
        self.windows = [
            WindowBar(
                "SPY",
                DAYS[-1],
                dt.datetime.combine(DAYS[-1], dt.time(9, 30), ZONE),
                dt.datetime.combine(DAYS[-1], dt.time(9, 35), ZONE),
                101.0,
                100.0,
            )
        ]
        self.calls = 0

    def daily_bars(
        self, symbols: Any, start: Any, end: Any, *, adjustment: str, **kwargs: Any
    ) -> list[DailyBar]:
        self.calls += 1
        return [
            bar
            for bar in (self.raw if adjustment == "raw" else self.adjusted)
            if bar.session_date <= end
        ]

    def execution_window_bars(self, *args: Any, **kwargs: Any) -> list[WindowBar]:
        return self.windows


def panel() -> MarketPanel:
    return MarketPanel(
        sessions=(DAYS[0],),
        symbols=("SPY",),
        adjusted_closes=np.array([[51.0]]),
        raw_closes=np.array([[102.0]]),
        window_vwaps=np.array([[101.0]]),
        window_volumes=np.array([[100.0]]),
        source="frozen-snapshot",
        adjusted_opens=np.array([[50.0]]),
        adjusted_highs=np.array([[51.5]]),
        adjusted_lows=np.array([[49.5]]),
        session_volumes=np.array([[1000.0]]),
    )


def run(feed: Feed, **kwargs: Any) -> MarketPanel:
    return extend_shadow_panel(
        panel(),
        feed,
        through=kwargs.pop("through", DAYS[-1]),
        now=kwargs.pop("now", dt.datetime(2025, 11, 28, 18, 1, tzinfo=dt.UTC)),
        **kwargs,
    )


def test_completed_early_close_appends_full_context_preserves_prefix() -> None:
    original = panel()
    result = run(Feed())
    assert result.sessions == DAYS
    assert result.source == original.source
    for name in (
        "adjusted_closes",
        "raw_closes",
        "adjusted_highs",
        "adjusted_lows",
        "adjusted_opens",
        "session_volumes",
        "window_vwaps",
        "window_volumes",
    ):
        np.testing.assert_array_equal(getattr(result, name)[:1], getattr(original, name))
        np.testing.assert_array_equal(getattr(result, name)[1:], getattr(original, name))


@pytest.mark.parametrize(
    "now,through",
    [
        (dt.datetime(2025, 11, 28, 17, 59, tzinfo=dt.UTC), DAYS[-1]),
        (dt.datetime(2025, 11, 28, 18, 1, tzinfo=dt.UTC).replace(tzinfo=None), DAYS[-1]),
        (dt.datetime(2025, 11, 29, 20, tzinfo=dt.UTC), dt.date(2025, 11, 29)),
        (dt.datetime(2025, 11, 28, 18, 1, tzinfo=dt.UTC), dt.date(2025, 11, 27)),
    ],
)
def test_refuses_unclosed_naive_holiday_weekend_before_network(
    now: dt.datetime, through: dt.date
) -> None:
    feed = Feed()
    with pytest.raises(DataIntegrityError):
        run(feed, now=now, through=through)
    assert feed.calls == 0


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "duplicate",
        "unexpected",
        "nan",
        "negative_volume",
        "range",
        "revision",
        "adjustment",
    ],
)
def test_daily_invalid_or_revised_is_rejected(mode: str) -> None:
    feed = Feed()
    if mode == "missing":
        feed.raw.pop()
    elif mode == "duplicate":
        feed.raw.append(feed.raw[-1])
    elif mode == "unexpected":
        feed.raw.append(replace(feed.raw[-1], symbol="QQQ"))
    elif mode == "nan":
        feed.raw[-1] = replace(feed.raw[-1], open=float("nan"))
    elif mode == "negative_volume":
        feed.raw[-1] = replace(feed.raw[-1], volume=-1.0)
    elif mode == "range":
        feed.raw[-1] = replace(feed.raw[-1], high=99.0)
    elif mode == "revision":
        feed.raw[0] = replace(feed.raw[0], volume=1001.0)
    elif mode == "adjustment":
        feed.adjusted[0] = replace(feed.adjusted[0], close=50.5)
    with pytest.raises(DataIntegrityError):
        run(feed)


@pytest.mark.parametrize(
    "mode",
    ["missing", "duplicate", "unexpected", "nan", "negative_volume", "wrong_time", "wrong_session"],
)
def test_window_invalid_is_rejected(mode: str) -> None:
    feed = Feed()
    if mode == "missing":
        feed.windows.clear()
    elif mode == "duplicate":
        feed.windows.append(feed.windows[0])
    elif mode == "unexpected":
        feed.windows[0] = replace(feed.windows[0], symbol="QQQ")
    elif mode == "nan":
        feed.windows[0] = replace(feed.windows[0], vwap=float("nan"))
    elif mode == "negative_volume":
        feed.windows[0] = replace(feed.windows[0], volume=-1.0)
    elif mode == "wrong_time":
        feed.windows[0] = replace(
            feed.windows[0], window_end=feed.windows[0].window_end + dt.timedelta(minutes=1)
        )
    elif mode == "wrong_session":
        feed.windows[0] = replace(feed.windows[0], session_date=DAYS[0])
    with pytest.raises(DataIntegrityError):
        run(feed)


def test_no_new_date_still_checks_revision() -> None:
    original, feed = panel(), Feed()
    result = extend_shadow_panel(
        original, feed, through=DAYS[0], now=dt.datetime(2025, 11, 28, tzinfo=dt.UTC)
    )
    assert result is original
    assert feed.calls == 2
    feed.raw[0] = replace(feed.raw[0], volume=1001.0)
    with pytest.raises(DataIntegrityError, match="history revision"):
        extend_shadow_panel(
            original, feed, through=DAYS[0], now=dt.datetime(2025, 11, 28, tzinfo=dt.UTC)
        )


def test_existing_calendar_gap_rejected_before_network() -> None:
    feed = Feed()
    base = panel()
    arrays = {
        name: np.repeat(getattr(base, name), 2, axis=0)
        for name in (
            "adjusted_closes",
            "raw_closes",
            "adjusted_highs",
            "adjusted_lows",
            "adjusted_opens",
            "session_volumes",
            "window_vwaps",
            "window_volumes",
        )
    }
    original = replace(base, sessions=(dt.date(2025, 11, 25), DAYS[-1]), **arrays)
    with pytest.raises(DataIntegrityError, match="calendar gap"):
        extend_shadow_panel(
            original, feed, through=DAYS[-1], now=dt.datetime(2025, 11, 29, tzinfo=dt.UTC)
        )
    assert feed.calls == 0


def test_no_context_fails_closed() -> None:
    with pytest.raises(DataIntegrityError, match="complete OHLCV"):
        extend_shadow_panel(
            replace(panel(), adjusted_opens=None),
            Feed(),
            through=DAYS[-1],
            now=dt.datetime(2025, 11, 29, tzinfo=dt.UTC),
        )
