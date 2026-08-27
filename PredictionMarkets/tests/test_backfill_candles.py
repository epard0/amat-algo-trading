"""Rebuilding candles from a trade tape: the aggregation and its guards.

Offline. Kraken is never called; `aggregate` and the file round-trip are pure.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from backfill_candles import (  # noqa: E402
    _result_rows,
    aggregate,
    read_candles,
    write_candles,
)


def _tape(*rows):
    """(price, volume, epoch_seconds) triples, as fetch_trades yields them."""
    return iter((Decimal(str(p)), Decimal(str(v)), float(t)) for p, v, t in rows)


def test_one_minute_of_trades_becomes_one_bar() -> None:
    candles = aggregate(_tape(
        (100, 1, 60), (105, 2, 70), (95, 1, 80), (102, 1, 119),
    ))
    assert len(candles) == 1
    bar = candles[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (
        Decimal("100"), Decimal("105"), Decimal("95"), Decimal("102"))
    assert bar.volume == Decimal("5")
    assert bar.trades == 4


def test_bars_are_stamped_with_the_start_of_their_minute() -> None:
    """Kraken buckets by the minute a trade falls in, and labels the bar with
    that minute's start. `--verify` checks this against the exchange itself."""
    candles = aggregate(_tape((100, 1, 137)))
    assert candles[0].minute == 120


def test_trades_split_across_minutes_split_into_bars() -> None:
    candles = aggregate(_tape((100, 1, 59), (200, 1, 61), (300, 1, 121)))
    assert [c.minute for c in candles] == [0, 60, 120]
    assert [c.close for c in candles] == [Decimal("100"), Decimal("200"), Decimal("300")]


def test_a_minute_with_no_trades_produces_no_bar() -> None:
    """The store stays faithful to the tape. Gap-filling is the reader's job,
    because a forward-filled bar is an inference and the file is evidence."""
    candles = aggregate(_tape((100, 1, 0), (110, 1, 180)))
    assert [c.minute for c in candles] == [0, 180]


def test_an_empty_tape_is_not_an_error() -> None:
    assert aggregate(_tape()) == []


def test_ohlc_is_ordered_not_sorted() -> None:
    """Open is the first trade and close the last, even when the high came
    between them — using min/max for those would be a real bug."""
    candles = aggregate(_tape((50, 1, 0), (999, 1, 10), (75, 1, 20)))
    bar = candles[0]
    assert bar.open == Decimal("50")
    assert bar.close == Decimal("75")
    assert bar.high == Decimal("999")


def test_candles_round_trip_through_disk_as_decimal(tmp_path) -> None:
    original = aggregate(_tape((0.0000123, 1, 0), (0.0000456, 2, 30)))
    path = tmp_path / "DOGE_1m.jsonl"
    write_candles(path, original)
    assert read_candles(path) == original
    assert isinstance(read_candles(path)[0].close, Decimal)


def test_a_rerun_merges_rather_than_duplicating(tmp_path) -> None:
    path = tmp_path / "BTC_1m.jsonl"
    write_candles(path, aggregate(_tape((100, 1, 0), (110, 1, 60))))
    total = write_candles(path, aggregate(_tape((120, 1, 60), (130, 1, 120))))
    stored = read_candles(path)
    assert total == 3
    assert [c.minute for c in stored] == [0, 60, 120]
    # The later fetch wins for a minute both cover.
    assert stored[1].close == Decimal("120")


def test_corrupt_lines_are_skipped_not_fatal(tmp_path) -> None:
    path = tmp_path / "ETH_1m.jsonl"
    path.write_text('{"t":0,"o":"1","h":"1","l":"1","c":"1","v":"1","n":1}\nnot json\n\n')
    assert len(read_candles(path)) == 1


def test_the_payload_key_is_not_the_pair_that_was_asked_for() -> None:
    """XBTUSD comes back as XXBTZUSD. Taking `the key that is not last` is why
    this works for every pair without a lookup table."""
    rows, last = _result_rows({"XXBTZUSD": [[1, 2, 3]], "last": "12345"})
    assert rows == [[1, 2, 3]]
    assert last == "12345"


def test_a_result_with_only_a_cursor_terminates_cleanly() -> None:
    rows, last = _result_rows({"last": "999"})
    assert rows == []
    assert last == "999"


def test_the_module_has_no_order_path() -> None:
    """It reads a public tape. It must not be able to do anything else."""
    source = (Path(__file__).resolve().parents[1]
              / "research" / "backfill_candles.py").read_text()
    for forbidden in ("create_order", "OrdersApi", "KalshiAuth", "private_key",
                      "api_key", "AsyncKalshiTradingClient"):
        assert forbidden not in source, forbidden
