"""Recent Pyth spot candles layered over Yahoo's longer futures history."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from core.strategy_brain.strategies.candles import PriceCandle


FEED_BY_SYMBOL = {
    "GC=F": "765d2ba906dbc32ca17cc11f5310a89e9ee1f6420508c63861f2f8ba4ee34bb2",  # Metal.XAU/USD
    "SI=F": "f2fb02c32b055c805e7238d628e5e9dadef274376114eb1f012337cabe93871e",  # Metal.XAG/USD
    "CL=F": "925ca92ff005ae943c158e3563f59698ce7e75c5a8c8dd43303a0a154887b3e6",  # USOILSPOT
}


class PythCommoditySource:
    """Use settlement-aligned Pyth spot for the recent edge of the window.

    Yahoo continuous futures supply history for volatility/model features;
    Pyth replaces the recent minutes and current spot. The two sources are
    deliberately recorded by name in model/run metadata and should not be
    mistaken for one homogeneous OHLCV series.
    """

    def __init__(
        self,
        history_source: Any,
        *,
        timeout_seconds: float = 3.0,
        refresh_seconds: float = 5.0,
        failure_retry_seconds: float = 15.0,
    ) -> None:
        self.history_source = history_source
        self._client = httpx.AsyncClient(
            base_url="https://hermes.pyth.network", timeout=timeout_seconds
        )
        self.refresh_seconds = refresh_seconds
        self.failure_retry_seconds = failure_retry_seconds
        self._cache: dict[str, list[PriceCandle]] = {}
        self._last_refresh: dict[str, float] = {}
        self._failed_until: dict[str, float] = {}
        self._locks = {symbol: asyncio.Lock() for symbol in FEED_BY_SYMBOL}

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _candle(parsed: dict) -> PriceCandle:
        price = parsed["price"]
        value = Decimal(price["price"]) * (Decimal(10) ** int(price["expo"]))
        stamp = datetime.fromtimestamp(int(price["publish_time"]), timezone.utc)
        return PriceCandle(stamp, value, value, value, value, Decimal("0"))

    async def _point(self, feed: str, timestamp: int | None = None) -> PriceCandle:
        endpoint = (
            "/v2/updates/price/latest"
            if timestamp is None
            else f"/v2/updates/price/{timestamp}"
        )
        response = await self._client.get(
            endpoint, params=[("ids[]", feed), ("parsed", "true")]
        )
        response.raise_for_status()
        parsed = (response.json().get("parsed") or [])[0]
        return self._candle(parsed)

    async def get_one_minute_candles(
        self, symbol: str, limit: int = 45
    ) -> list[PriceCandle]:
        feed = FEED_BY_SYMBOL[symbol]
        now_mono = time.monotonic()
        cached = self._cache.get(symbol)
        if cached and (
            now_mono < self._failed_until.get(symbol, 0.0)
            or now_mono - self._last_refresh.get(symbol, 0.0) < self.refresh_seconds
        ):
            return cached[-limit:]

        async with self._locks[symbol]:
            # Event-driven and polling evaluations can arrive together. The
            # first caller refreshes; queued callers consume that result rather
            # than issuing another identical Hermes request.
            now_mono = time.monotonic()
            cached = self._cache.get(symbol)
            if cached and (
                now_mono < self._failed_until.get(symbol, 0.0)
                or now_mono - self._last_refresh.get(symbol, 0.0) < self.refresh_seconds
            ):
                return cached[-limit:]

            if cached is None:
                history = await self.history_source.get_one_minute_candles(
                    symbol, limit=max(limit, 600)
                )
            else:
                history = cached

            try:
                latest = await self._point(feed)
            except httpx.HTTPError:
                if cached:
                    # The bot's candle-freshness gate decides how long this is
                    # safe to use. A brief Hermes outage therefore costs data
                    # freshness, not an entire scan pass or position service.
                    self._failed_until[symbol] = (
                        time.monotonic() + self.failure_retry_seconds
                    )
                    return cached[-limit:]
                raise

            if cached is None:
                # Continuous futures and Pyth spot have a level/basis gap.
                # Rescale history to the first live spot so the join cannot
                # manufacture a return that occurred in neither source.
                reference = history[-1].close
                factor = latest.close / reference if reference > 0 else Decimal("1")
                history = [
                    PriceCandle(
                        candle.timestamp,
                        candle.open * factor,
                        candle.high * factor,
                        candle.low * factor,
                        candle.close * factor,
                        candle.volume,
                    )
                    for candle in history
                ]

            base = history if cached is None else cached
            by_minute = {int(c.timestamp.timestamp()) // 60: c for c in base}
            by_minute[int(latest.timestamp.timestamp()) // 60] = latest
            cached = [by_minute[key] for key in sorted(by_minute)][-max(limit, 600) :]
            self._cache[symbol] = cached
            self._last_refresh[symbol] = time.monotonic()
            self._failed_until.pop(symbol, None)
            return cached[-limit:]
