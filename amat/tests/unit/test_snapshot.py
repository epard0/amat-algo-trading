"""Immutable snapshots: quality gates, digests, and panel loading."""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pytest

from amat.data.alpaca import DailyBar, DividendEvent, SplitEvent, WindowBar
from amat.data.snapshot import load_manifest, load_panel, write_snapshot
from amat.errors import DataIntegrityError, ImmutableArtifactError

SESSIONS = [dt.date(2024, 1, day) for day in (2, 3, 4, 5, 8)]
SYMBOLS = ("SPY", "TLT")


def daily(
    symbol: str, session: dt.date, close: float, *, adjusted: float | None = None
) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        session_date=session,
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close if adjusted is None else adjusted,
        volume=1_000_000.0,
        vwap=close,
        trade_count=5000,
    )


def _bars(closes: dict[str, list[float]], *, scale: float = 1.0) -> list[DailyBar]:
    return [
        daily(symbol, session, close * scale)
        for symbol, series in closes.items()
        for session, close in zip(SESSIONS, series, strict=True)
    ]


CLOSES = {"SPY": [400.0, 402.0, 401.0, 405.0, 407.0], "TLT": [95.0, 95.5, 94.8, 96.0, 96.4]}


def windows() -> list[WindowBar]:
    return [
        WindowBar(
            symbol=symbol,
            session_date=session,
            window_start=dt.datetime.combine(session, dt.time(14, 30), tzinfo=dt.UTC),
            window_end=dt.datetime.combine(session, dt.time(14, 35), tzinfo=dt.UTC),
            vwap=close,
            volume=50_000.0,
        )
        for symbol, series in CLOSES.items()
        for session, close in zip(SESSIONS, series, strict=True)
    ]


def write(root: Path, snapshot_id: str = "test-snapshot", **overrides: object):
    payload: dict[str, object] = {
        "raw_bars": _bars(CLOSES),
        "adjusted_bars": _bars(CLOSES, scale=0.9),
        "window_bars": windows(),
        "dividends": [],
        "splits": [],
        "provider": "test",
        "feed": "test-feed",
        "as_of": dt.datetime(2024, 1, 9, tzinfo=dt.UTC),
        "execution_window_start": dt.time(9, 30),
        "execution_window_end": dt.time(9, 35),
        "exchange_timezone": "America/New_York",
    }
    payload.update(overrides)
    return write_snapshot(root, snapshot_id, **payload)  # type: ignore[arg-type]


class TestWriting:
    def test_writes_partitions_and_a_manifest(self, tmp_path: Path) -> None:
        directory, manifest = write(tmp_path)
        for name in ("daily.parquet", "window.parquet", "actions.parquet", "manifest.json"):
            assert (directory / name).is_file()
        assert manifest.session_count == len(SESSIONS)
        assert manifest.symbols == SYMBOLS
        assert manifest.window_coverage == pytest.approx(1.0)

    def test_a_snapshot_id_cannot_be_reused(self, tmp_path: Path) -> None:
        """Evidence is append-only: a second write must not overwrite the first."""
        write(tmp_path)
        with pytest.raises(ImmutableArtifactError, match="already exists"):
            write(tmp_path)

    def test_impossible_ohlc_fails_closed(self, tmp_path: Path) -> None:
        broken = _bars(CLOSES)
        broken[0] = DailyBar(
            symbol="SPY",
            session_date=SESSIONS[0],
            open=400.0,
            high=390.0,  # high below low
            low=395.0,
            close=396.0,
            volume=1.0,
            vwap=396.0,
            trade_count=1,
        )
        with pytest.raises(DataIntegrityError, match="OHLC ordering"):
            write(tmp_path, raw_bars=broken)

    def test_an_unexplained_adjusted_jump_fails_closed(self, tmp_path: Path) -> None:
        """A discontinuity with no recorded split is a data defect, not a move."""
        jumped = _bars({"SPY": [400.0, 402.0, 100.0, 405.0, 407.0], "TLT": CLOSES["TLT"]})
        with pytest.raises(DataIntegrityError, match="discontinuities"):
            write(tmp_path, adjusted_bars=jumped)

    def test_a_jump_explained_by_a_recorded_split_is_accepted(self, tmp_path: Path) -> None:
        jumped = _bars({"SPY": [400.0, 402.0, 100.0, 101.0, 102.0], "TLT": CLOSES["TLT"]})
        _, manifest = write(
            tmp_path,
            adjusted_bars=jumped,
            splits=[SplitEvent(symbol="SPY", ex_date=SESSIONS[2], ratio=0.25)],
        )
        assert manifest.action_rows == 1

    def test_a_zero_volume_window_is_dropped_not_priced(self, tmp_path: Path) -> None:
        """An untradable window must become an absence, never an imputed fill."""
        bars = windows()
        bars[0] = WindowBar(
            symbol=bars[0].symbol,
            session_date=bars[0].session_date,
            window_start=bars[0].window_start,
            window_end=bars[0].window_end,
            vwap=bars[0].vwap,
            volume=0.0,
        )
        _, manifest = write(tmp_path, window_bars=bars)
        assert manifest.window_rows == len(bars) - 1
        assert manifest.window_coverage < 1.0

    def test_corporate_actions_are_recorded(self, tmp_path: Path) -> None:
        _, manifest = write(
            tmp_path,
            dividends=[DividendEvent(symbol="SPY", ex_date=SESSIONS[1], rate=1.5)],
            splits=[SplitEvent(symbol="TLT", ex_date=SESSIONS[3], ratio=2.0)],
        )
        assert manifest.action_rows == 2


class TestLoading:
    def test_manifest_digests_detect_tampering(self, tmp_path: Path) -> None:
        directory, _ = write(tmp_path)
        (directory / "daily.parquet").write_bytes(b"not a parquet file")
        with pytest.raises(DataIntegrityError, match="has been modified"):
            load_manifest(directory)

    def test_panel_carries_both_price_bases_distinctly(self, tmp_path: Path) -> None:
        directory, _ = write(tmp_path)
        panel, _ = load_panel(directory, ["SPY", "TLT"])
        assert panel.sessions == tuple(SESSIONS)
        assert panel.symbols == SYMBOLS
        # Adjusted closes were written at 0.9x the raw closes.
        assert panel.adjusted_closes[0, 0] == pytest.approx(panel.raw_closes[0, 0] * 0.9)

    def test_panel_respects_the_requested_symbol_order(self, tmp_path: Path) -> None:
        """Column order fixes every downstream matrix; it must be the caller's."""
        directory, _ = write(tmp_path)
        panel, _ = load_panel(directory, ["TLT", "SPY"])
        assert panel.symbols == ("TLT", "SPY")
        assert panel.raw_closes[0, 0] == pytest.approx(95.0)

    def test_an_absent_symbol_is_named_not_padded(self, tmp_path: Path) -> None:
        directory, _ = write(tmp_path)
        with pytest.raises(DataIntegrityError, match="lacks symbols: GLD"):
            load_panel(directory, ["SPY", "GLD"])

    def test_a_date_range_narrows_the_session_axis(self, tmp_path: Path) -> None:
        directory, _ = write(tmp_path)
        panel, _ = load_panel(directory, ["SPY"], start=SESSIONS[1], end=SESSIONS[3])
        assert panel.sessions == tuple(SESSIONS[1:4])

    def test_a_missing_window_becomes_zero_volume_not_a_price(self, tmp_path: Path) -> None:
        """The panel must present no tradable window where none was observed."""
        bars = [bar for bar in windows() if bar.session_date != SESSIONS[2]]
        directory, _ = write(tmp_path, window_bars=bars)
        panel, _ = load_panel(directory, ["SPY", "TLT"])
        assert math.isnan(panel.window_vwaps[2, 0])
        assert panel.window_volumes[2, 0] == 0.0
        assert SYMBOLS[0] not in panel.windows_at(2)

    def test_a_range_with_no_complete_session_fails_closed(self, tmp_path: Path) -> None:
        directory, _ = write(tmp_path)
        with pytest.raises(DataIntegrityError, match="complete coverage"):
            load_panel(directory, ["SPY"], start=dt.date(2030, 1, 1))

    def test_window_labels_follow_the_exchange_clock_across_dst(self, tmp_path: Path) -> None:
        """Regression: the 09:30 New York window is 14:30 UTC only in winter.

        A hardcoded UTC label put summer windows an hour late. The prices were
        always right (they come from stored VWAPs); only the label lied.
        """
        directory, _ = write(tmp_path)
        panel, _ = load_panel(directory, ["SPY"])
        # SESSIONS are early-January dates: EST, so 09:30 New York is 14:30 UTC.
        window = panel.windows_at(0)[panel.symbols[0]]
        assert window.window_start.hour == 14
        # A synthetic July session must label the same wall-clock window 13:30 UTC.
        july = dt.date(2024, 7, 5)
        summer_daily = [daily(symbol, july, 100.0) for symbol in ("SPY",)]
        summer_windows = [
            WindowBar(
                symbol="SPY",
                session_date=july,
                window_start=dt.datetime.combine(july, dt.time(13, 30), tzinfo=dt.UTC),
                window_end=dt.datetime.combine(july, dt.time(13, 35), tzinfo=dt.UTC),
                vwap=100.0,
                volume=50_000.0,
            )
        ]
        summer_dir, _ = write(
            tmp_path / "summer",
            snapshot_id="summer",
            raw_bars=[*summer_daily, daily("SPY", dt.date(2024, 7, 8), 101.0)],
            adjusted_bars=[*summer_daily, daily("SPY", dt.date(2024, 7, 8), 101.0)],
            window_bars=summer_windows,
        )
        summer_panel, _ = load_panel(summer_dir, ["SPY"])
        summer_window = summer_panel.windows_at(0)[summer_panel.symbols[0]]
        assert summer_window.window_start.hour == 13
