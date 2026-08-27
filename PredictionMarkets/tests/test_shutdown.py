"""Graceful signal shutdown must protect orders and release every resource."""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from types import SimpleNamespace

import bot as bot_module
from bot import KalshiCryptoBot
from execution.scalp_engine import ScalpEngine


class AsyncCloser:
    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name

    async def close(self) -> None:
        self.events.append(self.name)


async def test_stop_is_idempotent_wakes_wait_and_disables_orders() -> None:
    events = []
    instance = object.__new__(KalshiCryptoBot)
    instance._stopping = False
    instance._wake_event = asyncio.Event()
    instance.client = SimpleNamespace(
        stop_order_submission=lambda: events.append("orders-disabled")
    )

    instance.stop()
    instance.stop()

    assert instance._stopping is True
    assert instance._wake_event.is_set()
    assert events == ["orders-disabled"]


async def test_close_is_exactly_once_and_continues_after_cleanup_failure() -> None:
    events = []
    instance = object.__new__(KalshiCryptoBot)
    instance._stopping = False
    instance._wake_event = asyncio.Event()
    instance._close_lock = asyncio.Lock()
    instance._closed_complete = False
    instance._rest_book_refresh_task = None
    instance._event_entry_tasks = set()
    instance.price_stream = AsyncCloser(events, "price-stream")
    instance._rest_prices = AsyncCloser(events, "rest-prices")
    instance._commodity_prices = AsyncCloser(events, "commodity-prices")
    instance._yahoo_prices = AsyncCloser(events, "yahoo-prices")
    instance._book_reconcile_client = AsyncCloser(events, "reconcile-client")

    class Client(AsyncCloser):
        def stop_order_submission(self) -> None:
            events.append("orders-disabled")

    instance.client = Client(events, "kalshi-client")

    class BrokenScalp:
        async def drain(self) -> None:
            events.append("drain-attempted")
            raise RuntimeError("venue unavailable")

    instance.scalp = BrokenScalp()
    instance.salvage = None
    instance.take_profit = None
    instance.pair_arb = None
    instance.strategy_lab = None
    instance.kalshi_book_stream = AsyncCloser(events, "book-stream")
    instance.data_run_id = "run-1"
    instance._data_run_completed = False

    class Ledger:
        def finish_data_run(self, run_id: str, *, completed: bool) -> None:
            events.append(f"finish:{run_id}:{completed}")

        def close(self) -> None:
            events.append("ledger")

    class Decisions:
        def close(self) -> None:
            events.append("decisions")

    instance.ledger = Ledger()
    instance.decisions = Decisions()

    await instance.close()
    await instance.close()

    assert events == [
        "orders-disabled",
        "drain-attempted",
        "book-stream",
        "price-stream",
        "rest-prices",
        "commodity-prices",
        "yahoo-prices",
        "reconcile-client",
        "kalshi-client",
        "finish:run-1:False",
        "ledger",
        "decisions",
    ]
    assert instance._closed_complete is True


async def test_signal_handlers_route_interrupt_and_are_uninstalled(monkeypatch) -> None:
    callbacks = {}
    removed = []
    events = []

    class Loop:
        def add_signal_handler(self, signal_name, callback) -> None:
            callbacks[signal_name] = callback

        def remove_signal_handler(self, signal_name) -> None:
            removed.append(signal_name)

    target = SimpleNamespace(stop=lambda: events.append("stop"))
    monkeypatch.setattr(bot_module.asyncio, "get_running_loop", lambda: Loop())

    uninstall = bot_module._install_signal_handlers(target)
    callbacks[signal.SIGINT]()
    uninstall()

    assert events == ["stop"]
    assert removed == [signal.SIGINT, signal.SIGTERM]


async def test_interrupted_run_is_not_marked_complete(monkeypatch) -> None:
    @dataclass(frozen=True)
    class Settings:
        scalp_enabled: bool = False
        predictor_enabled: bool = False
        pair_arb_enabled: bool = False
        salvage_enabled: bool = False
        oversight_enabled: bool = False

    instances = []
    uninstall_calls = []

    class FakeBot:
        def __init__(self, settings, *, live: bool, silent: bool) -> None:
            del settings, live, silent
            self._stopping = False
            self._data_run_completed = False
            self.closed = False
            instances.append(self)

        async def run(self, *, once: bool) -> None:
            del once
            self._stopping = True

        def log_stopped(self) -> None:
            pass

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(bot_module, "KalshiCryptoBot", FakeBot)
    monkeypatch.setattr(
        bot_module,
        "_install_signal_handlers",
        lambda bot: lambda: uninstall_calls.append(bot),
    )
    args = SimpleNamespace(
        strategy="all",
        live=False,
        once=False,
        continuous=True,
        silent=True,
        session_seconds=60,
        session_passes=6,
    )

    await bot_module.run_bot(args, Settings())

    assert instances[0]._data_run_completed is False
    assert instances[0].closed is True
    assert uninstall_calls == instances


async def test_scalp_drain_attempts_every_resting_cancel() -> None:
    calls = []

    class Client:
        async def cancel_order(self, order_id: str, **kwargs) -> None:
            del kwargs
            calls.append(order_id)
            if order_id == "first":
                raise RuntimeError("temporary venue failure")

    instance = object.__new__(ScalpEngine)
    instance.live = True
    instance.client = Client()
    instance.quotes = SimpleNamespace(
        drain=lambda: [
            SimpleNamespace(order_id="first"),
            SimpleNamespace(order_id="second"),
        ]
    )
    instance.record_funnel = lambda occasion: None

    await instance.drain()

    assert calls == ["first", "second"]
