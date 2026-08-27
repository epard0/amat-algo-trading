"""Polymarket read-only adapter: slug parsing, normalisation, and collection.

No test here reaches the network; the client is driven with recorded payload
shapes taken from live Gamma and CLOB responses.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from data_sources.polymarket.client import (
    PolymarketBook,
    PolymarketBookLevel,
    market_from_gamma,
    parse_updown_slug,
)
from data_sources.polymarket.collector import PolymarketCollector

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def gamma_record(**overrides) -> dict:
    """A Gamma record shaped like the live API returns."""
    payload = {
        "slug": "btc-updown-5m-1785823800",
        "question": "Bitcoin Up or Down - August 3, 8:30AM-8:35AM ET",
        "endDate": "2026-08-03T12:35:00Z",
        "conditionId": "0xabc",
        # Gamma returns these as JSON-encoded strings, not lists.
        "clobTokenIds": '["111", "222"]',
        "outcomes": '["Up", "Down"]',
        "bestBid": "0.50",
        "bestAsk": "0.51",
        "volumeNum": "1234.5",
        "closed": False,
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ parsing --

@pytest.mark.parametrize(
    "slug, asset, interval",
    [
        ("btc-updown-5m-1785823800", "BTC", 5),
        ("eth-updown-15m-1785823800", "ETH", 15),
        ("sol-updown-10m-1785823800", "SOL", 10),
        ("xrp-updown-1h-1785823800", "XRP", 60),
    ],
)
def test_parses_every_interval_family(slug: str, asset: str, interval: int) -> None:
    """5m and 15m exist today; 10m does not, but must parse if it appears.

    Discovery is interval-agnostic on purpose, so a new series is picked up
    without a code change.
    """
    parsed = parse_updown_slug(slug)

    assert parsed is not None
    assert parsed[0] == asset
    assert parsed[1] == interval


def test_rejects_non_updown_slugs() -> None:
    for slug in ("will-trump-win-2028", "btc-updown-5m", "", "btc-updown-xm-1"):
        assert parse_updown_slug(slug) is None


def test_open_time_comes_from_the_slug_timestamp() -> None:
    _, _, open_time = parse_updown_slug("btc-updown-5m-1785823800")

    assert open_time == datetime.fromtimestamp(1785823800, tz=timezone.utc)


# ------------------------------------------------------------ normalisation --

def test_market_from_gamma_decodes_json_encoded_lists() -> None:
    market = market_from_gamma(gamma_record())

    assert market is not None
    assert market.asset == "BTC"
    assert market.interval_minutes == 5
    assert market.outcomes == ("Up", "Down")
    assert market.token_ids == ("111", "222")
    assert market.best_bid == Decimal("0.50")


def test_up_token_is_selected_by_label_not_position() -> None:
    """Gamma lists outcomes positionally; Down can come first."""
    market = market_from_gamma(
        gamma_record(outcomes='["Down", "Up"]', clobTokenIds='["999", "111"]')
    )

    assert market is not None
    assert market.up_token_id == "111"


def test_up_token_is_none_when_no_up_outcome_exists() -> None:
    market = market_from_gamma(gamma_record(outcomes='["Yes2", "No2"]'))

    assert market is not None
    assert market.up_token_id is None


def test_non_updown_record_normalises_to_none() -> None:
    assert market_from_gamma(gamma_record(slug="will-trump-win")) is None


def test_implied_probability_uses_the_mid() -> None:
    market = market_from_gamma(gamma_record())

    assert market.implied_up_probability == Decimal("0.505")


def test_implied_probability_handles_a_one_sided_book() -> None:
    market = market_from_gamma(gamma_record(bestBid="0", bestAsk="0.51"))

    assert market.implied_up_probability == Decimal("0.51")


def test_unparseable_money_becomes_none_not_zero() -> None:
    """A silent zero would read as a real price of $0.00 downstream."""
    market = market_from_gamma(gamma_record(bestBid="not-a-number"))

    assert market.best_bid is None


def test_seconds_to_close_is_measured_from_close_time() -> None:
    market = market_from_gamma(gamma_record())

    assert market.seconds_to_close(NOW) == pytest.approx(2100)  # 35 minutes


# --------------------------------------------------------------------- book --

def book(bids, asks) -> PolymarketBook:
    return PolymarketBook(
        token_id="111",
        bids=tuple(PolymarketBookLevel(Decimal(p), Decimal(s)) for p, s in bids),
        asks=tuple(PolymarketBookLevel(Decimal(p), Decimal(s)) for p, s in asks),
        fetched_at=NOW,
    )


def test_book_top_of_book_and_mid() -> None:
    b = book([("0.48", "100"), ("0.50", "50")], [("0.51", "40"), ("0.53", "10")])

    assert b.best_bid == Decimal("0.50")   # highest bid
    assert b.best_ask == Decimal("0.51")   # lowest ask
    assert b.mid == Decimal("0.505")


def test_book_depth_accumulates_up_to_the_limit() -> None:
    b = book([], [("0.51", "40"), ("0.52", "25"), ("0.60", "999")])

    assert b.depth_to(Decimal("0.52")) == Decimal("65")
    assert b.depth_to(Decimal("0.50")) == Decimal("0")


def test_empty_book_has_no_mid() -> None:
    assert book([], []).mid is None


# ---------------------------------------------------------------- collector --

class FakeClient:
    """Stands in for the network; records what was asked for."""

    def __init__(self, markets, books=None, settle=None):
        self._markets = markets
        self._books = books or {}
        self._settle = settle or []
        self.book_calls: list[str] = []

    async def discover_updown_markets(self, **_):
        return self._markets

    async def fetch_book(self, token_id):
        self.book_calls.append(token_id)
        return self._books.get(token_id)

    async def fetch_markets(self, **_):
        return self._settle

    async def close(self):
        pass


async def test_collector_writes_a_snapshot_per_open_market(tmp_path) -> None:
    market = market_from_gamma(gamma_record())
    client = FakeClient(
        [market], books={"111": book([("0.50", "10")], [("0.51", "10")])}
    )
    collector = PolymarketCollector(tmp_path / "out.jsonl", client=client)

    written = await collector.snapshot_once(now=NOW)

    assert written == 1
    rows = [json.loads(line) for line in (tmp_path / "out.jsonl").read_text().splitlines()]
    assert rows[0]["record_type"] == "snapshot"
    assert rows[0]["asset"] == "BTC"
    assert rows[0]["interval_minutes"] == 5
    # Money survives as a string, never a float.
    assert rows[0]["book"]["best_bid"] == "0.50"
    assert isinstance(rows[0]["implied_up_probability"], str)


async def test_collector_records_a_settlement_and_its_label(tmp_path) -> None:
    market = market_from_gamma(gamma_record())
    resolved = gamma_record(closed=True, outcomePrices='["1", "0"]')
    client = FakeClient([market], settle=[resolved])
    collector = PolymarketCollector(tmp_path / "out.jsonl", client=client)

    await collector.snapshot_once(now=NOW)
    written = await collector.collect_settlements(now=NOW + timedelta(hours=1))

    assert written == 1
    rows = [json.loads(line) for line in (tmp_path / "out.jsonl").read_text().splitlines()]
    settlement = rows[-1]
    assert settlement["record_type"] == "settlement"
    assert settlement["outcome"] == "Up"
    assert settlement["label_up"] == 1


async def test_collector_does_not_settle_an_open_market(tmp_path) -> None:
    market = market_from_gamma(gamma_record())
    client = FakeClient([market], settle=[gamma_record()])
    collector = PolymarketCollector(tmp_path / "out.jsonl", client=client)

    await collector.snapshot_once(now=NOW)
    # Still before close.
    assert await collector.collect_settlements(now=NOW) == 0


async def test_collector_settles_each_market_only_once(tmp_path) -> None:
    market = market_from_gamma(gamma_record())
    resolved = gamma_record(closed=True, outcomePrices='["0", "1"]')
    client = FakeClient([market], settle=[resolved])
    collector = PolymarketCollector(tmp_path / "out.jsonl", client=client)

    await collector.snapshot_once(now=NOW)
    later = NOW + timedelta(hours=1)
    assert await collector.collect_settlements(now=later) == 1
    assert await collector.collect_settlements(now=later) == 0
    assert collector.stats.settlements == 1


async def test_collector_survives_a_discovery_failure(tmp_path) -> None:
    """Collection must never raise into the process it runs beside."""

    class Broken(FakeClient):
        async def discover_updown_markets(self, **_):
            raise RuntimeError("gamma is down")

    collector = PolymarketCollector(tmp_path / "out.jsonl", client=Broken([]))

    assert await collector.snapshot_once(now=NOW) == 0
    assert collector.stats.errors == 1


async def test_collector_handles_a_market_with_no_book(tmp_path) -> None:
    market = market_from_gamma(gamma_record())
    collector = PolymarketCollector(
        tmp_path / "out.jsonl", client=FakeClient([market], books={})
    )

    assert await collector.snapshot_once(now=NOW) == 1
    row = json.loads((tmp_path / "out.jsonl").read_text().splitlines()[0])
    assert row["book"] is None
