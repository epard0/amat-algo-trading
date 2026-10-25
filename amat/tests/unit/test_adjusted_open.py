"""Candle inputs share HLC's adjustment basis and never invent missing opens."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from amat.backtest.panel import MarketPanel
from amat.data.alpaca import DailyBar, SplitEvent
from amat.data.snapshot import load_panel, partition_digest, write_snapshot
from amat.domain.types import Symbol
from amat.errors import DataIntegrityError
from amat.validation.optimize import split_panel
from amat.validation.stress import _close_fill_panel

DATES = (dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4))


def _panel():
    closes = np.array([[50.0, 90.0], [51.0, 91.0], [52.0, 92.0]])
    return MarketPanel(
        sessions=DATES,
        symbols=(Symbol("SPY"), Symbol("TLT")),
        adjusted_closes=closes,
        raw_closes=closes * 2,
        window_vwaps=closes * 2,
        window_volumes=np.ones_like(closes),
        source="test",
        adjusted_highs=closes + 2,
        adjusted_lows=closes - 2,
        session_volumes=np.ones_like(closes),
        adjusted_opens=closes - 1,
    )


def _write_snapshot(tmp_path):
    # A split and then a distribution alter the adjustment factor by row.
    raw_closes = (100.0, 50.0, 49.0)
    adjusted_closes = (49.0, 49.0, 49.0)

    def bars(closes):
        return [
            DailyBar(
                symbol="SPY",
                session_date=session,
                open=close * 0.99,
                high=close * 1.02,
                low=close * 0.98,
                close=close,
                volume=1000.0,
                vwap=close,
                trade_count=100,
            )
            for session, close in zip(DATES, closes, strict=True)
        ]

    return write_snapshot(
        tmp_path,
        "candle-test",
        raw_bars=bars(raw_closes),
        adjusted_bars=bars(adjusted_closes),
        window_bars=[],
        dividends=[],
        splits=[SplitEvent(symbol="SPY", ex_date=DATES[1], ratio=2.0)],
        provider="test",
        feed="test",
        as_of=dt.datetime(2024, 1, 5, tzinfo=dt.UTC),
        execution_window_start=dt.time(9, 30),
        execution_window_end=dt.time(9, 35),
        exchange_timezone="America/New_York",
    )


def test_candle_inputs_remain_optional_and_fail_closed():
    panel = replace(_panel(), adjusted_opens=None)
    assert panel.has_context_inputs
    assert not panel.has_candle_inputs
    assert panel.context_inputs()[0] is panel.adjusted_closes
    with pytest.raises(DataIntegrityError, match="adjusted OHLC"):
        panel.candle_inputs()
    assert split_panel(panel, DATES[0])[0].adjusted_opens is None
    assert panel.slice_symbols([Symbol("SPY")]).adjusted_opens is None


def test_open_shape_and_missing_cell_availability():
    with pytest.raises(DataIntegrityError, match="adjusted_opens has shape"):
        replace(_panel(), adjusted_opens=np.zeros((1, 2)))
    opens = np.array([[np.nan, 89.0], [50.0, 90.0], [51.0, 91.0]])
    panel = replace(_panel(), adjusted_opens=opens)
    assert panel.has_candle_inputs
    assert np.isnan(panel.candle_inputs()[0][0, 0])
    assert np.isfinite(panel.adjusted_closes[0, 0])


def test_panel_copies_preserve_open_alignment():
    panel = _panel()
    reordered = panel.slice_symbols([Symbol("TLT"), Symbol("SPY")])
    np.testing.assert_array_equal(reordered.adjusted_opens, panel.adjusted_opens[:, [1, 0]])
    training, validation = split_panel(panel, DATES[1])
    np.testing.assert_array_equal(training.adjusted_opens, panel.adjusted_opens[:2])
    assert validation is panel
    assert _close_fill_panel(panel).adjusted_opens is panel.adjusted_opens


def test_snapshot_open_uses_rowwise_split_and_distribution_adjustment(tmp_path):
    directory, _ = _write_snapshot(tmp_path)
    ordinary, _ = load_panel(directory, ["SPY"])
    assert ordinary.adjusted_opens is None
    panel, _ = load_panel(directory, ["SPY"], with_context=True)
    opens, highs, lows, closes = panel.candle_inputs()
    np.testing.assert_allclose(opens[:, 0], [48.51] * 3)
    np.testing.assert_allclose(highs[:, 0], [49.98] * 3)
    np.testing.assert_allclose(lows[:, 0], [48.02] * 3)
    np.testing.assert_allclose(closes[:, 0], [49.0] * 3)
    # Raw valuation retains the split, while research candle geometry does not.
    np.testing.assert_allclose(panel.raw_closes[:, 0], [100.0, 50.0, 49.0])
    truncated, _ = load_panel(directory, ["SPY"], end=DATES[1], with_context=True)
    np.testing.assert_array_equal(truncated.adjusted_opens, opens[:2])


@pytest.mark.parametrize("missing_column", [False, True])
@pytest.mark.parametrize("unavailable_open", [None, float("nan"), float("inf"), 0.0, -1.0])
def test_legacy_or_missing_opens_do_not_become_closes(tmp_path, missing_column, unavailable_open):
    directory, manifest = _write_snapshot(tmp_path)
    partition = directory / "daily.parquet"
    frame = pl.read_parquet(partition)
    frame = (
        frame.drop("raw_open")
        if missing_column
        else frame.with_columns(pl.lit(unavailable_open, dtype=pl.Float64).alias("raw_open"))
    )
    # Construct an independently manifested legacy-format fixture, never bypass
    # the production loader's immutable-partition digest verification.
    frame.write_parquet(partition)
    manifest = manifest.model_copy(
        update={
            "partition_sha256": {
                **manifest.partition_sha256,
                "daily.parquet": partition_digest(partition),
            }
        }
    )
    (directory / "manifest.json").write_text(manifest.to_json())
    panel, _ = load_panel(directory, ["SPY"], with_context=True)
    assert panel.has_context_inputs
    if missing_column:
        assert panel.adjusted_opens is None
        with pytest.raises(DataIntegrityError, match="raw_open"):
            panel.candle_inputs()
    else:
        assert np.isnan(panel.candle_inputs()[0]).all()
