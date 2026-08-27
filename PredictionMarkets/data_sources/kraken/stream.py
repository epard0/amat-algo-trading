"""Streamed one-minute Kraken candles, with REST as the fallback beneath.

`KrakenRestSource` re-fetches every asset every pass, deliberately: the last
OHLC row is the minute in progress and its close is live spot, so caching it
would serve a stale price. That is correct, and it is also why the polling rate
scales with the asset count - nine assets at a five-second cadence is 1.8
requests a second, all of it re-asking for candles that mostly have not changed.

A stream inverts that. Kraken pushes the in-progress candle as it updates, so
spot is fresher than polling can make it *and* the request rate stops depending
on how many assets are traded.

**The danger is the opposite of the polling one.** A poll that fails is loud -
the call raises and the cycle sees it. A stream that dies quietly keeps a last
known candle in memory that looks perfectly well-formed and simply stops
advancing, and the bot would price a 15-minute market off a spot from ten
minutes ago. Everything below is arranged around that:

- **The stream never answers on its own.** Every read checks how long it has
  been since the socket last delivered anything, and falls back to REST past
  ``stale_after_seconds``. Falling back is normal operation, not an error path.
- **The window is warmed from REST**, so a subscription that arrives with fewer
  candles than the predictor needs cannot produce a short window. Kraken does
  send a snapshot on subscribe, but how many rows it contains is its choice and
  not a documented guarantee.
- **A closed candle replaces, an open candle updates.** Candles are keyed by
  ``interval_begin``; the same minute arriving twice is one candle, not two.
- **`MAX_CANDLE_AGE_SECONDS` still applies downstream.** This is a second line,
  not a replacement for the freshness gate the bot already runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

import aiohttp
from loguru import logger

from core.strategy_brain.strategies.crypto_15min_predictor import PriceCandle
from data_sources.kraken.rest import KrakenRestSource

WS_URL = "wss://ws.kraken.com/v2"
WINDOW = 60  # a little more than the predictor's 45, so a late row has room


def ws_symbol_for(asset: str) -> str:
    """Kraken's v2 websocket symbol for an asset.

    Derived from the asset rather than written down a second time. The REST
    endpoint wants ``XBTUSD`` while the v2 socket wants ``BTC/USD``, and a
    hand-maintained second mapping is exactly the thing ``CLAUDE.md`` records
    going stale silently every time it has existed in this repo. Every asset
    this bot trades is already its own base symbol.
    """
    return f"{asset.upper()}/USD"


class KrakenCandleStream:
    """Live one-minute candles for several assets over one socket.

    Deliberately exposes the same ``get_one_minute_candles(pair, limit)`` that
    ``KrakenRestSource`` does, so the bot's call sites do not learn which one
    they are talking to - and so the fallback is invisible to them.
    """

    def __init__(
        self,
        *,
        rest: KrakenRestSource,
        assets: Iterable[str],
        pair_by_asset: dict[str, str],
        stale_after_seconds: float = 90.0,
        url: str = WS_URL,
        now: Any = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.rest = rest
        self.assets = tuple(assets)
        self.stale_after_seconds = stale_after_seconds
        self.url = url
        self._now = now

        # REST pair ("XBTUSD") -> websocket symbol ("BTC/USD"), and back.
        self._symbol_by_pair = {
            pair_by_asset[asset]: ws_symbol_for(asset)
            for asset in self.assets
            if asset in pair_by_asset
        }
        self._pair_by_symbol = {v: k for k, v in self._symbol_by_pair.items()}

        self._candles: dict[str, deque[PriceCandle]] = {}
        self._index: dict[str, dict[datetime, PriceCandle]] = {}
        self._last_message_at: datetime | None = None
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.reconnects = 0
        self.fallbacks = 0
        self.updates = 0

    # ------------------------------------------------------------ lifecycle --

    async def start(self) -> None:
        """Warm the window from REST, then run the socket in the background."""
        await self._warm_up()
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _warm_up(self) -> None:
        """Fill each window from REST before the socket says anything.

        Concurrently, and failures are tolerated: an asset that cannot be
        warmed simply has no cached window, so its first read falls back to
        REST like any other stale asset. Refusing to start over one asset would
        make a nine-asset bot hostage to its thinnest pair.
        """

        async def one(pair: str) -> None:
            try:
                candles = await self.rest.get_one_minute_candles(pair, limit=WINDOW)
            except Exception as exc:  # noqa: BLE001 - warm-up is best effort
                logger.debug("Kraken stream warm-up failed for {}: {}", pair, exc)
                return
            self._replace_window(pair, candles)

        await asyncio.gather(*(one(pair) for pair in self._symbol_by_pair))

    def _replace_window(self, pair: str, candles: list[PriceCandle]) -> None:
        window: deque[PriceCandle] = deque(candles[-WINDOW:], maxlen=WINDOW)
        self._candles[pair] = window
        self._index[pair] = {candle.timestamp: candle for candle in window}

    # --------------------------------------------------------------- socket --

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                await self._connect_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a stream must not die
                self.reconnects += 1
                logger.warning(
                    "Kraken stream dropped ({}); reconnecting in {:.0f}s. "
                    "Reads fall back to REST until it is live again.",
                    type(exc).__name__,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _connect_once(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        async with self._session.ws_connect(self.url, heartbeat=20) as socket:
            await socket.send_json(
                {
                    "method": "subscribe",
                    "params": {
                        "channel": "ohlc",
                        "symbol": sorted(self._pair_by_symbol),
                        "interval": 1,
                    },
                }
            )
            logger.info(
                "Kraken stream live for {} assets: {}",
                len(self._pair_by_symbol),
                ", ".join(sorted(self._pair_by_symbol)),
            )
            async for message in socket:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    continue
                self._ingest(message.data)
            raise ConnectionError("websocket closed")

    def _ingest(self, payload: str) -> None:
        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            return
        if message.get("channel") != "ohlc":
            return
        rows = message.get("data") or []
        for row in rows:
            self._apply(row)
        if rows:
            # Only a real data row counts as liveness. A heartbeat or a status
            # frame proves the socket is open, not that candles are arriving,
            # and it is the candles that go stale.
            self._last_message_at = self._now()
            self.updates += len(rows)

    def _apply(self, row: dict[str, Any]) -> None:
        pair = self._pair_by_symbol.get(row.get("symbol", ""))
        if pair is None:
            return
        began = row.get("interval_begin")
        if not began:
            return
        try:
            # `timestamp` is deprecated by Kraken in favour of interval_begin,
            # and the two mean different things: interval_begin is the minute
            # this candle covers, timestamp is when it ends.
            stamp = datetime.fromisoformat(began.replace("Z", "+00:00")).replace(
                microsecond=0
            )
            candle = PriceCandle(
                timestamp=stamp,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row.get("volume", 0))),
            )
        except (KeyError, ValueError, ArithmeticError):
            return

        window = self._candles.setdefault(pair, deque(maxlen=WINDOW))
        index = self._index.setdefault(pair, {})
        if stamp in index:
            # The minute in progress, updating. Replace in place so the window
            # keeps one row per minute rather than growing a duplicate.
            for position, existing in enumerate(window):
                if existing.timestamp == stamp:
                    window[position] = candle
                    break
        else:
            if window and stamp < window[-1].timestamp:
                return  # a late row for a minute already rolled past
            window.append(candle)
        index[stamp] = candle
        if len(index) > WINDOW * 2:
            live = {c.timestamp for c in window}
            self._index[pair] = {k: v for k, v in index.items() if k in live}

    # ----------------------------------------------------------------- reads --

    @property
    def seconds_since_message(self) -> float | None:
        if self._last_message_at is None:
            return None
        return (self._now() - self._last_message_at).total_seconds()

    def is_live(self) -> bool:
        """Whether the stream may be trusted to answer this read."""
        age = self.seconds_since_message
        return age is not None and age <= self.stale_after_seconds

    def cached(self, pair: str, limit: int) -> list[PriceCandle] | None:
        window = self._candles.get(pair)
        if not window or len(window) < limit:
            return None
        return list(window)[-limit:]

    async def get_one_minute_candles(
        self, pair: str, limit: int = 45
    ) -> list[PriceCandle]:
        """The streamed window, or REST when the stream cannot be trusted.

        Same signature and same guarantees as ``KrakenRestSource``, so callers
        never learn which answered - including the guarantee that too few
        candles raises rather than letting the predictor guess.
        """
        if self.is_live():
            candles = self.cached(pair, limit)
            if candles is not None:
                return candles
        self.fallbacks += 1
        return await self.rest.get_one_minute_candles(pair, limit=limit)

    async def get_ohlc(
        self, pair: str, *, interval_minutes: int = 1, limit: int = 45
    ) -> list[PriceCandle]:
        """Non-minute intervals are not streamed; pass straight through."""
        if interval_minutes != 1:
            return await self.rest.get_ohlc(
                pair, interval_minutes=interval_minutes, limit=limit
            )
        return await self.get_one_minute_candles(pair, limit=limit)

    def health(self) -> dict[str, Any]:
        """What an operator needs to tell a live stream from a dead one."""
        age = self.seconds_since_message
        return {
            "live": self.is_live(),
            "seconds_since_message": None if age is None else round(age, 1),
            "assets": len(self._symbol_by_pair),
            "windows": {pair: len(w) for pair, w in sorted(self._candles.items())},
            "updates": self.updates,
            "reconnects": self.reconnects,
            "rest_fallbacks": self.fallbacks,
        }


def stale_window_for(candles: list[PriceCandle], now: datetime) -> timedelta:
    """Age of the newest candle. Exposed for the freshness gate and for tests."""
    if not candles:
        return timedelta.max
    return now - candles[-1].timestamp
