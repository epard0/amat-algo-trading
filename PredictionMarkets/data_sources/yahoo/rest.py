"""Async Yahoo chart adapter for recent commodity futures candles.

This runtime adapter intentionally uses the public chart JSON endpoint rather
than importing pandas/yfinance into the trading process. Historical downloads
and fitting use yfinance under research/; runtime model artifacts stay data.
Yahoo futures quotes may be delayed, so the existing candle-freshness gate is
authoritative and normally limits this source to paper evaluation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

import httpx

from core.strategy_brain.strategies.candles import PriceCandle


class YahooFinanceSource:
    def __init__(self, *, timeout_seconds: float = 3.0, concurrency: int = 3) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://query1.finance.yahoo.com",
            timeout=timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0 KX15Research/1.0"},
        )
        self._gate = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_one_minute_candles(
        self, symbol: str, limit: int = 45
    ) -> list[PriceCandle]:
        async with self._gate:
            response = await self._client.get(
                f"/v8/finance/chart/{quote(symbol, safe='')}",
                params={"range": "5d", "interval": "1m", "includePrePost": "true"},
            )
        response.raise_for_status()
        payload = response.json().get("chart", {})
        if payload.get("error"):
            raise RuntimeError(
                f"Yahoo rejected candles for {symbol}: {payload['error']}"
            )
        results = payload.get("result") or []
        if not results:
            raise RuntimeError(f"Yahoo returned no candles for {symbol}")
        result = results[0]
        stamps = result.get("timestamp") or []
        quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        candles: list[PriceCandle] = []
        for index, stamp in enumerate(stamps):
            values = {
                name: (quote_rows.get(name) or [None] * len(stamps))[index]
                for name in ("open", "high", "low", "close", "volume")
            }
            if any(values[name] is None for name in ("open", "high", "low", "close")):
                continue
            candles.append(
                PriceCandle(
                    timestamp=datetime.fromtimestamp(stamp, timezone.utc),
                    open=Decimal(str(values["open"])),
                    high=Decimal(str(values["high"])),
                    low=Decimal(str(values["low"])),
                    close=Decimal(str(values["close"])),
                    volume=Decimal(str(values["volume"] or 0)),
                )
            )
        if len(candles) < 8:
            raise RuntimeError(
                f"Yahoo returned only {len(candles)} usable candles for {symbol}"
            )
        return candles[-limit:]


class RoutedPriceSource:
    """Route continuous futures symbols to their settlement-aware source."""

    def __init__(self, kraken: object, commodities: object) -> None:
        self.kraken = kraken
        self.commodities = commodities

    async def get_one_minute_candles(self, symbol: str, limit: int = 45):
        source = self.commodities if symbol.endswith("=F") else self.kraken
        return await source.get_one_minute_candles(symbol, limit=limit)
