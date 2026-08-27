import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from data_sources.pyth.rest import FEED_BY_SYMBOL, PythCommoditySource
from data_sources.yahoo.rest import RoutedPriceSource, YahooFinanceSource
from core.strategy_brain.strategies.candles import PriceCandle


async def test_yahoo_chart_adapter_normalizes_ohlcv() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("GC%3DF") or request.url.path.endswith("GC=F")
        return httpx.Response(
            200,
            json={
                "chart": {
                    "error": None,
                    "result": [
                        {
                            "timestamp": list(range(1_700_000_000, 1_700_000_540, 60)),
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [10] * 9,
                                        "high": [11] * 9,
                                        "low": [9] * 9,
                                        "close": [10.5] * 9,
                                        "volume": [100] * 9,
                                    }
                                ]
                            },
                        }
                    ],
                }
            },
        )

    source = YahooFinanceSource()
    await source._client.aclose()
    source._client = httpx.AsyncClient(
        base_url="https://query1.finance.yahoo.com",
        transport=httpx.MockTransport(handler),
    )
    rows = await source.get_one_minute_candles("GC=F", limit=8)
    await source.close()
    assert len(rows) == 8
    assert rows[-1].close == Decimal("10.5")
    assert rows[-1].timestamp.tzinfo is timezone.utc


def test_pyth_feed_registry_and_decimal_exponent_are_exact() -> None:
    assert set(FEED_BY_SYMBOL) == {"GC=F", "SI=F", "CL=F"}
    candle = PythCommoditySource._candle(
        {"price": {"price": "123456", "expo": -3, "publish_time": 1_700_000_000}}
    )
    assert candle.close == Decimal("123.456")
    assert candle.volume == 0


async def test_router_never_sends_futures_to_kraken() -> None:
    calls = []

    class Source:
        def __init__(self, name):
            self.name = name

        async def get_one_minute_candles(self, symbol, limit=45):
            calls.append((self.name, symbol, limit))
            return []

    router = RoutedPriceSource(Source("kraken"), Source("commodity"))
    await router.get_one_minute_candles("GC=F", limit=600)
    await router.get_one_minute_candles("XBTUSD", limit=45)
    assert calls == [
        ("commodity", "GC=F", 600),
        ("kraken", "XBTUSD", 45),
    ]


def _history() -> list[PriceCandle]:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return [
        PriceCandle(
            timestamp=now - timedelta(minutes=10 - index),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("10"),
        )
        for index in range(10)
    ]


async def test_pyth_concurrent_callers_share_one_refresh() -> None:
    history_calls = []
    point_calls = []

    class History:
        async def get_one_minute_candles(self, symbol, limit):
            history_calls.append((symbol, limit))
            return _history()

    source = PythCommoditySource(History(), refresh_seconds=5)

    async def point(feed, timestamp=None):
        point_calls.append((feed, timestamp))
        await asyncio.sleep(0)
        now = datetime.now(timezone.utc)
        return PriceCandle(now, *(Decimal("105"),) * 4, Decimal("0"))

    source._point = point
    rows = await asyncio.gather(
        *(source.get_one_minute_candles("GC=F", limit=8) for _ in range(3))
    )
    await source.close()

    assert len(history_calls) == 1
    assert len(point_calls) == 1
    assert all(result[-1].close == Decimal("105") for result in rows)


async def test_pyth_timeout_uses_fresh_cache_and_opens_short_breaker() -> None:
    class History:
        async def get_one_minute_candles(self, symbol, limit):
            del symbol, limit
            return _history()

    source = PythCommoditySource(History(), refresh_seconds=0, failure_retry_seconds=30)
    calls = 0

    async def point(feed, timestamp=None):
        nonlocal calls
        del feed, timestamp
        calls += 1
        if calls > 1:
            raise httpx.ConnectTimeout("Hermes unavailable")
        now = datetime.now(timezone.utc)
        return PriceCandle(now, *(Decimal("105"),) * 4, Decimal("0"))

    source._point = point
    first = await source.get_one_minute_candles("GC=F", limit=8)
    fallback = await source.get_one_minute_candles("GC=F", limit=8)
    breaker = await source.get_one_minute_candles("GC=F", limit=8)
    await source.close()

    assert calls == 2
    assert fallback == first
    assert breaker == first
