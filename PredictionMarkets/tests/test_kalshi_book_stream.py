from types import SimpleNamespace
from decimal import Decimal

import pytest

from execution.kalshi_book_stream import KalshiBookStream, SequenceGap
from execution.trade_ledger import TradeLedger


class Ledger:
    def __init__(self) -> None:
        self.books = []
        self.trades = []
        self.quality = []

    def record_book_event(self, **values):
        self.books.append(values)

    def record_public_trade(self, **values):
        self.trades.append(values)

    def record_data_quality_event(self, **values):
        self.quality.append(values)


def stream(ledger, seen=None):
    async def callback(ticker, analysis):
        if seen is not None:
            seen.append((ticker, analysis))

    return KalshiBookStream(
        settings=SimpleNamespace(
            kalshi_websocket_enabled=False,
            raw_book_event_recording_enabled=True,
            public_trade_recording_enabled=True,
            api_key_id=None,
            private_key_path=SimpleNamespace(is_file=lambda: False),
        ),
        ledger=ledger,
        run_id_provider=lambda: "run-1",
        on_analysis=callback,
    )


async def test_snapshot_and_delta_are_recorded_before_derived_analysis():
    ledger = Ledger()
    seen = []
    subject = stream(ledger, seen)

    await subject.handle_message(
        {
            "type": "orderbook_snapshot",
            "seq": 10,
            "msg": {
                "market_ticker": "KXBTC15M-TEST",
                "yes_dollars_fp": [["0.55", "10"], ["0.54", "20"]],
                "no_dollars_fp": [["0.43", "12"]],
                "ts_ms": 1000,
            },
        }
    )
    await subject.handle_message(
        {
            "type": "orderbook_delta",
            "seq": 11,
            "msg": {
                "market_ticker": "KXBTC15M-TEST",
                "side": "yes",
                "price_dollars": "0.55",
                "delta_fp": "5",
                "ts_ms": 1001,
            },
        }
    )

    assert [event["event_type"] for event in ledger.books] == ["snapshot", "delta"]
    assert seen[-1][0] == "KXBTC15M-TEST"
    assert seen[-1][1].yes_bid == Decimal("0.55")
    assert seen[-1][1].yes_bid_size == Decimal("15")
    assert seen[-1][1].yes_ask == Decimal("0.57")
    snapshot = subject.snapshot("KXBTC15M-TEST", max_age_seconds=5)
    assert snapshot is not None
    assert snapshot.source == "websocket"
    assert snapshot.sequence == 11
    assert snapshot.analysis.yes_bid == Decimal("0.55")


async def test_raw_capture_can_be_disabled_without_disabling_live_books():
    ledger = Ledger()
    subject = stream(ledger)
    subject.settings.raw_book_event_recording_enabled = False

    await subject.handle_message(
        {
            "type": "orderbook_snapshot",
            "seq": 1,
            "msg": {
                "market_ticker": "M",
                "yes_dollars_fp": [["0.40", "2"]],
                "no_dollars_fp": [["0.50", "2"]],
            },
        }
    )
    await subject.handle_message(
        {
            "type": "orderbook_delta",
            "seq": 2,
            "msg": {
                "market_ticker": "M",
                "side": "yes",
                "price_dollars": "0.40",
                "delta_fp": "1",
            },
        }
    )

    assert ledger.books == []
    assert subject.snapshot("M").analysis.yes_bid_size == Decimal("3")


async def test_sequence_gap_is_recorded_and_forces_snapshot_recovery():
    ledger = Ledger()
    subject = stream(ledger)
    await subject.handle_message(
        {
            "type": "orderbook_snapshot",
            "seq": 20,
            "msg": {
                "market_ticker": "M",
                "yes_dollars_fp": [["0.40", "2"]],
                "no_dollars_fp": [["0.50", "2"]],
            },
        }
    )

    with pytest.raises(SequenceGap):
        await subject.handle_message(
            {
                "type": "orderbook_delta",
                "seq": 22,
                "msg": {
                    "market_ticker": "M",
                    "side": "no",
                    "price_dollars": "0.50",
                    "delta_fp": "1",
                },
            }
        )

    assert ledger.quality[-1]["event_type"] == "sequence_gap"
    assert ledger.quality[-1]["expected_sequence"] == 21
    assert ledger.quality[-1]["actual_sequence"] == 22


async def test_book_sequence_is_subscription_wide_not_per_ticker():
    ledger = Ledger()
    subject = stream(ledger)
    for sequence, ticker in ((1, "A"), (2, "B")):
        await subject.handle_message(
            {
                "type": "orderbook_snapshot",
                "sid": 7,
                "seq": sequence,
                "msg": {
                    "market_ticker": ticker,
                    "yes_dollars_fp": [["0.40", "2"]],
                    "no_dollars_fp": [["0.50", "2"]],
                },
            }
        )

    await subject.handle_message(
        {
            "type": "orderbook_delta",
            "sid": 7,
            "seq": 3,
            "msg": {
                "market_ticker": "A",
                "side": "yes",
                "price_dollars": "0.40",
                "delta_fp": "1",
            },
        }
    )

    assert ledger.quality == []
    assert subject.snapshot("A").sequence == 3


async def test_trade_channel_sequence_does_not_interrupt_book_sequence():
    ledger = Ledger()
    subject = stream(ledger)
    await subject.handle_message(
        {
            "type": "orderbook_snapshot",
            "sid": 7,
            "seq": 1,
            "msg": {
                "market_ticker": "A",
                "yes_dollars_fp": [["0.40", "2"]],
                "no_dollars_fp": [["0.50", "2"]],
            },
        }
    )
    await subject.handle_message(
        {
            "type": "trade",
            "sid": 8,
            "seq": 1,
            "msg": {
                "market_ticker": "A",
                "yes_price_dollars": "0.41",
                "count_fp": "1",
            },
        }
    )
    await subject.handle_message(
        {
            "type": "orderbook_delta",
            "sid": 7,
            "seq": 2,
            "msg": {
                "market_ticker": "A",
                "side": "yes",
                "price_dollars": "0.40",
                "delta_fp": "1",
            },
        }
    )

    assert ledger.quality == []


async def test_public_trade_is_normalized():
    ledger = Ledger()
    subject = stream(ledger)
    await subject.handle_message(
        {
            "type": "trade",
            "msg": {
                "market_ticker": "M",
                "trade_id": "venue-1",
                "yes_price_dollars": "0.61",
                "count_fp": "3.5",
                "taker_side": "yes",
                "ts": 123,
            },
        }
    )

    assert ledger.trades[0]["trade_id"] == "venue-1"
    assert str(ledger.trades[0]["yes_price"]) == "0.61"
    assert str(ledger.trades[0]["contracts"]) == "3.5"


def test_stream_health_vocabulary_is_accepted_by_the_strict_ledger(tmp_path):
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    run_id = ledger.start_data_run(
        mode="paper",
        environment="production",
        strategy="predictor",
        assets=("BTC",),
    )
    subject = KalshiBookStream(
        settings=SimpleNamespace(
            kalshi_websocket_enabled=False,
            api_key_id=None,
            private_key_path=SimpleNamespace(is_file=lambda: False),
        ),
        ledger=ledger,
        run_id_provider=lambda: run_id,
    )

    subject._quality("websocket_connected", "info", details={"attempt": 1})
    row = ledger.connection.execute(
        "SELECT event_type, severity, details_json FROM data_quality_events"
    ).fetchone()
    assert (row["event_type"], row["severity"]) == ("reconnect", "info")
    assert '"stream_event":"websocket_connected"' in row["details_json"]
    ledger.close()


def test_quality_persistence_failure_cannot_terminate_stream_recovery():
    class BrokenLedger(Ledger):
        def record_data_quality_event(self, **values):
            raise RuntimeError("disk unavailable")

    subject = stream(BrokenLedger())
    subject._quality("websocket_error", "warning", details={"attempt": 1})


async def test_completed_stream_task_is_restarted_on_the_next_cycle(tmp_path):
    private_key = tmp_path / "private.pem"
    private_key.write_text("test")
    subject = KalshiBookStream(
        settings=SimpleNamespace(
            kalshi_websocket_enabled=True,
            kalshi_websocket_stale_seconds=10.0,
            api_key_id="key-id",
            private_key_path=private_key,
        ),
        ledger=Ledger(),
        run_id_provider=lambda: "run-1",
    )
    calls = 0

    async def completed_worker():
        nonlocal calls
        calls += 1

    subject._run = completed_worker
    await subject.start()
    first = subject._task
    await first
    await subject.update_markets(())
    second = subject._task
    await second

    assert calls == 2
    assert second is not first
    await subject.close()
