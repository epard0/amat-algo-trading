"""Public async Kraken REST adapter for one-minute spot candles."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from core.strategy_brain.strategies.crypto_15min_predictor import PriceCandle


class KrakenRestSource:
    """Fetches OHLC candles. Safe to call concurrently for several pairs.

    Kraken meters public calls per IP, so the semaphore bounds how many of the
    five assets are in flight at once even though they are all requested
    together.
    """

    def __init__(
        self,
        base_url: str = "https://api.kraken.com",
        timeout_seconds: float = 10.0,
        concurrency: int = 4,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"User-Agent": "KalshiCrypto15mBot/3.0"},
        )
        self._gate = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_ohlc(
        self,
        pair: str,
        *,
        interval_minutes: int = 1,
        limit: int = 45,
    ) -> list[PriceCandle]:
        """Fetch candles at an arbitrary Kraken interval.

        Kraken accepts interval in {1, 5, 15, 30, 60, 240, 1440, ...} and
        returns at most 720 rows, so a 15-minute interval reaches back about
        7.5 days — enough to chart every market the bot has evaluated, where
        one candle is exactly one 15-minute market.
        """
        async with self._gate:
            response = await self._client.get(
                "/0/public/OHLC",
                params={"pair": pair, "interval": interval_minutes},
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(
                f"Kraken rejected OHLC request for {pair}: {payload['error']}"
            )

        result = payload.get("result", {})
        pair_keys = [key for key in result if key != "last"]
        if len(pair_keys) != 1:
            raise RuntimeError(f"Unexpected Kraken OHLC response for {pair}")
        rows = result[pair_keys[0]][-limit:]
        return [
            PriceCandle(
                timestamp=datetime.fromtimestamp(row[0], tz=timezone.utc),
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[6]),
            )
            for row in rows
        ]

    async def get_one_minute_candles(
        self,
        pair: str,
        limit: int = 45,
    ) -> list[PriceCandle]:
        candles = await self.get_ohlc(pair, interval_minutes=1, limit=limit)
        # Trading path only: too few candles means the predictor cannot measure
        # drift or volatility, and must not be allowed to guess.
        if len(candles) < 8:
            raise RuntimeError(
                f"Kraken returned only {len(candles)} candles for {pair}"
            )
        return candles
