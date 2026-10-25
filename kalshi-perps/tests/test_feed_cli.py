import json

import httpx
import pytest

from kalshi_perps.cli import main
from kalshi_perps.domain import HOUR
from kalshi_perps.runtime import Feed
from kalshi_perps.venue import Transport


@pytest.mark.parametrize("offset", [0, -1])
def test_actual_and_inclusive_candle_boundaries(btc, offset):
    boundary = 1000 * HOUR

    def handler(request):
        start = int(request.url.params["start_ts"]) * 1000
        end = int(request.url.params["end_ts"]) * 1000
        rows = []
        for t in range(start + HOUR, end + 1, HOUR):
            rows.append(
                dict(
                    end_period_ts=t // 1000 + offset,
                    volume="1",
                    price=dict(open="8", high="8.01", low="7.99", close="8"),
                )
            )
        return httpx.Response(200, json={"candlesticks": rows})

    tr = Transport("demo", client=httpx.Client(transport=httpx.MockTransport(handler)))
    feed = Feed(tr, {btc.ticker: btc})
    first = feed.candles(boundary)
    assert len(first[btc.ticker]) == 36 * 24
    assert first[btc.ticker][-1]["timestamp_ms"] == boundary - HOUR
    extended = feed.candles(boundary + HOUR, first)
    assert len(extended[btc.ticker]) == 36 * 24 + 1
    assert extended[btc.ticker][0] == first[btc.ticker][0]


def test_three_command_surface(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "{run,status,stop}" in output
    assert "instruments" not in output


def test_disabled_demo_status_works(capsys):
    assert main(["status", "--config", "configs/demo.toml"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "demo"


def test_funding_parses_exact_mark_and_timestamp(btc):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "funding_rates": [
                    dict(
                        market_ticker=btc.ticker,
                        funding_time="2026-01-01T08:00:00Z",
                        funding_rate=0.0001,
                        mark_price="8.1234",
                    )
                ]
            },
        )

    tr = Transport("demo", client=httpx.Client(transport=httpx.MockTransport(handler)))
    feed = Feed(tr, {btc.ticker: btc})
    events = feed.funding(1767225600000, 1767254400000)
    assert events[0]["timestamp_ms"] == 1767254400000
    assert events[0]["mark"] == "8.1234"


def test_snapshot_normalizes_books_and_checks_age_at_completion(btc):
    from copy import deepcopy
    from pathlib import Path

    row = next(
        m
        for m in json.loads(
            (Path(__file__).resolve().parents[1] / "configs/instruments.json").read_text()
        )["markets"]
        if m["ticker"] == btc.ticker
    )

    class Fake:
        now = 100000

        def clock(self):
            return self.now

        def request(self, method, path, **kwargs):
            if path.endswith("/orderbook"):
                self.now += 2000
                return dict(
                    orderbook=dict(
                        bids=[["7.98", "2"], ["8.00", "0"], ["7.99", "1"]],
                        asks=[["8.02", "2"], ["8.01", "1"]],
                    )
                )
            return dict(
                market=dict(deepcopy(row), liquidation_mark_price=dict(price="8", ts_ms=86000))
            )

    tr = Fake()
    with pytest.raises(ValueError, match="snapshot completion"):
        Feed(tr, {btc.ticker: btc}).snapshot(15000)
    tr = Fake()
    tr.now = 98000
    # Give the response a fresh mark for the successful case.
    original = tr.request

    def fresh(*args, **kwargs):
        result = original(*args, **kwargs)
        if "market" in result:
            result["market"]["liquidation_mark_price"]["ts_ms"] = 98000
        return result

    tr.request = fresh
    event = Feed(tr, {btc.ticker: btc}).snapshot(15000)
    assert event["quotes"][btc.ticker]["bids"] == [["7.99", "1"], ["7.98", "2"]]
    assert event["quotes"][btc.ticker]["asks"][0] == ["8.01", "1"]
    assert event["collection_duration_ms"] == 2000


def test_snapshot_fetches_independent_instruments_concurrently(btc):
    from dataclasses import replace
    from pathlib import Path
    from threading import Barrier

    row = next(
        m
        for m in json.loads(
            (Path(__file__).resolve().parents[1] / "configs/instruments.json").read_text()
        )["markets"]
        if m["ticker"] == btc.ticker
    )
    other = replace(btc, ticker="OTHER")
    barrier = Barrier(2)

    class Fake:
        def clock(self):
            return 100000

        def request(self, method, path, **kwargs):
            if path.endswith("/orderbook"):
                return dict(orderbook=dict(bids=[["7.99", "1"]], asks=[["8.01", "1"]]))
            barrier.wait(timeout=2)
            ticker = path.rsplit("/", 1)[-1]
            return dict(
                market=dict(
                    row, ticker=ticker, liquidation_mark_price=dict(price="8", ts_ms=100000)
                )
            )

    event = Feed(Fake(), {btc.ticker: btc, other.ticker: other}).snapshot(15000)
    assert len(event["quotes"]) == 2


def test_slow_snapshot_still_fails_without_relaxing_freshness(btc):
    from pathlib import Path

    row = next(
        m
        for m in json.loads(
            (Path(__file__).resolve().parents[1] / "configs/instruments.json").read_text()
        )["markets"]
        if m["ticker"] == btc.ticker
    )

    class Slow:
        now = 100000

        def clock(self):
            return self.now

        def request(self, method, path, **kwargs):
            if path.endswith("/orderbook"):
                self.now += 16000
                return dict(orderbook=dict(bids=[["7.99", "1"]], asks=[["8.01", "1"]]))
            return dict(market=dict(row, liquidation_mark_price=dict(price="8", ts_ms=100000)))

    with pytest.raises(ValueError, match="snapshot exceeded freshness limit"):
        Feed(Slow(), {btc.ticker: btc}).snapshot(15000)
