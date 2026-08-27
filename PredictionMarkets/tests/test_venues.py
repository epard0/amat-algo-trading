"""The venue scaffolding: it must fail closed, and it must not invent prices.

Two things are tested hardest here, and neither is "does it work".

The eligibility gate decides whether this operator may contact a venue at all,
in a regulatory area that is actively contested. Every ambiguous input must
refuse. A gate that fails open is worse than no gate, because it reads like
one.

And the normalisation layer must never manufacture a price. This session
already produced a backtest that reported a losing configuration as profitable
because it priced off a snapshot rather than the executable book; the same
mistake at a venue boundary would be harder to find, not easier.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from data_sources.venues.adapters import (
    KalshiVenue,
    PolymarketVenue,
    UnimplementedVenue,
)
from data_sources.venues.base import (
    PredictionVenue,
    VenueBook,
    VenueLevel,
    VenueMarket,
)
from data_sources.venues.eligibility import (
    Access,
    OperatorDeclaration,
    VenueStatus,
    check_eligibility,
)

D = Decimal
TODAY = date(2026, 8, 20)
NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


def status(access: Access, *, reviewed=TODAY, venue="v") -> VenueStatus:
    return VenueStatus(
        venue=venue,
        regulator="CFTC-designated contract market (DCM)",
        access={"US-MI": access},
        reviewed_on=reviewed,
        notes="A state regulator disputes this.",
    )


# ------------------------------------------------------- failing closed --


def test_an_undeclared_jurisdiction_refuses():
    """It is never inferred from the network - a VPN would decide it."""
    result = check_eligibility(
        status(Access.AVAILABLE), OperatorDeclaration(jurisdiction=""), today=TODAY
    )
    assert not result
    assert "not inferred" in result.reason


def test_an_unresearched_jurisdiction_refuses():
    result = check_eligibility(
        status(Access.AVAILABLE),
        OperatorDeclaration(jurisdiction="US-NV"),
        today=TODAY,
    )
    assert not result
    assert result.access is Access.UNKNOWN


def test_contested_refuses_without_an_explicit_per_venue_opt_in():
    declaration = OperatorDeclaration(jurisdiction="US-MI")
    assert not check_eligibility(status(Access.CONTESTED), declaration, today=TODAY)


def test_contested_allows_only_the_venue_that_was_named():
    """A blanket 'accept contested' is deliberately not offered."""
    declaration = OperatorDeclaration(
        jurisdiction="US-MI", accept_contested=frozenset({"v"})
    )
    assert check_eligibility(status(Access.CONTESTED), declaration, today=TODAY)
    other = status(Access.CONTESTED, venue="somewhere-else")
    assert not check_eligibility(other, declaration, today=TODAY)


def test_blocked_can_never_be_opted_into():
    declaration = OperatorDeclaration(
        jurisdiction="US-MI", accept_contested=frozenset({"v"})
    )
    assert not check_eligibility(status(Access.BLOCKED), declaration, today=TODAY)


def test_a_stale_record_refuses_even_when_it_says_available():
    """The failure mode: a refusal that stopped happening because nobody looked.

    Staleness is checked BEFORE access, so an aged-out record cannot pass on
    the strength of a review nobody repeated.
    """
    old = status(Access.AVAILABLE, reviewed=date(2025, 1, 1))
    result = check_eligibility(
        old, OperatorDeclaration(jurisdiction="US-MI"), today=TODAY
    )
    assert not result
    assert "re-verify" in result.reason


def test_staleness_outranks_an_explicit_opt_in():
    declaration = OperatorDeclaration(
        jurisdiction="US-MI", accept_contested=frozenset({"v"})
    )
    old = status(Access.CONTESTED, reviewed=date(2025, 1, 1))
    assert not check_eligibility(old, declaration, today=TODAY)


def test_every_refusal_says_why():
    """An opaque refusal is a bug report waiting to be filed against the wrong
    thing."""
    declaration = OperatorDeclaration(jurisdiction="US-MI")
    for access in (Access.CONTESTED, Access.BLOCKED, Access.UNKNOWN):
        result = check_eligibility(status(access), declaration, today=TODAY)
        assert not result
        assert len(result.reason) > 20


# ------------------------------------------------- never invent a price --


def test_a_one_sided_book_has_no_midpoint():
    """Returning the single side, or 0.5, manufactures a price nobody quoted."""
    book = VenueBook(
        venue="v", market_id="m", outcome="yes", bids=(VenueLevel(D("0.4"), D("10")),)
    )
    assert book.best_bid == D("0.4")
    assert book.best_ask is None
    assert book.mid is None


def test_a_market_without_both_sides_has_no_implied_probability():
    market = VenueMarket(
        venue="v",
        market_id="m",
        asset="BTC",
        interval_minutes=15,
        open_time=NOW,
        close_time=NOW + timedelta(minutes=15),
        yes_bid=D("0.4"),
        yes_ask=None,
    )
    assert market.implied_probability_yes is None


def test_depth_counts_only_levels_at_or_below_the_limit():
    book = VenueBook(
        venue="v",
        market_id="m",
        outcome="yes",
        asks=(
            VenueLevel(D("0.60"), D("10")),
            VenueLevel(D("0.62"), D("25")),
            VenueLevel(D("0.70"), D("100")),
        ),
    )
    assert book.depth_to(D("0.62")) == D("35")
    assert book.depth_to(D("0.59")) == D("0")


# ------------------------------------------------------------ adapters --


class FakeAnalysis:
    yes_bid = D("0.60")
    yes_ask = D("0.62")
    yes_bid_size = D("500")
    no_bid_size = D("300")


class FakeKalshiClient:
    def __init__(self, analysis=None) -> None:
        self.analysis = analysis
        self.closed = False

    async def get_active_markets(self):
        from execution.kalshi_client import KalshiMarket

        return [
            KalshiMarket(
                asset="BTC",
                ticker="KXBTC15M-T",
                event_ticker="KXBTC15M",
                title="",
                target_price=D("100000"),
                open_time=NOW,
                close_time=NOW + timedelta(minutes=15),
                yes_bid=D("0.60"),
                yes_ask=D("0.62"),
                yes_bid_size=D("500"),
                yes_ask_size=D("400"),
                fee_multiplier=D("1"),
            )
        ]

    async def get_book_analysis(self, ticker, depth=20):
        return self.analysis

    async def close(self):
        self.closed = True


async def test_the_kalshi_adapter_normalises_a_market():
    venue = KalshiVenue(FakeKalshiClient())
    markets = await venue.discover_markets()
    assert len(markets) == 1
    market = markets[0]
    assert market.venue == "kalshi"
    assert market.interval_minutes == 15
    assert market.strike == D("100000")
    assert market.settlement_source == "CF Benchmarks"


async def test_the_no_side_is_one_minus_the_yes_side_and_lives_in_the_adapter():
    """Kalshi publishes ONE book. The mapping belongs here, not downstream."""
    venue = KalshiVenue(FakeKalshiClient(FakeAnalysis()))
    market = (await venue.discover_markets())[0]

    yes = await venue.get_book(market, "yes")
    no = await venue.get_book(market, "no")
    assert yes.best_bid == D("0.60") and yes.best_ask == D("0.62")
    # Buying NO crosses the YES bids: the NO ask is 1 - yes_bid.
    assert no.best_ask == D("0.40")
    assert no.best_bid == D("0.38")


async def test_an_unreadable_book_is_none_not_an_empty_book():
    """Unreachable and empty are different facts and must not be conflated."""
    venue = KalshiVenue(FakeKalshiClient(None))
    market = (await venue.discover_markets())[0]
    assert await venue.get_book(market, "yes") is None


async def test_the_kalshi_adapter_never_closes_the_bots_client():
    """Closing a session the trading loop still holds is a bug already had."""
    client = FakeKalshiClient()
    await KalshiVenue(client).close()
    assert client.closed is False


async def test_an_unintegrated_venue_raises_rather_than_reporting_no_markets():
    """Returning [] would look exactly like a venue with a quiet hour."""
    venue = UnimplementedVenue("somewhere", "no public API is documented")
    with pytest.raises(NotImplementedError, match="no public API"):
        await venue.discover_markets()


def test_the_adapters_satisfy_the_protocol():
    assert isinstance(KalshiVenue(FakeKalshiClient()), PredictionVenue)
    assert isinstance(PolymarketVenue(object()), PredictionVenue)
    assert isinstance(UnimplementedVenue("x", "y"), PredictionVenue)


def test_no_adapter_exposes_an_order_path():
    """The whole package is read-only. This is what that means concretely."""
    banned = {"place_order", "submit", "buy", "sell", "cancel_order", "sign"}
    for cls in (KalshiVenue, PolymarketVenue, UnimplementedVenue):
        assert not banned & set(dir(cls)), f"{cls.__name__} grew an order path"


# ------------------------------------------------------------ registry --


def test_an_uncatalogued_venue_refuses_rather_than_raising():
    """An unknown name is a venue nobody researched, which is the refusing state."""
    from data_sources.venues.registry import status_for

    unknown = status_for("some-new-exchange")
    result = check_eligibility(
        unknown, OperatorDeclaration(jurisdiction="US-MI"), today=TODAY
    )
    assert not result
    assert result.access is Access.UNKNOWN


def test_nothing_is_usable_until_a_status_has_been_researched():
    """The catalogue ships refusing. Verification is what opens a venue."""
    from data_sources.venues.registry import VENUES, usable_venues

    declaration = OperatorDeclaration(jurisdiction="US-MI")
    usable = usable_venues(declaration, today=TODAY)
    for name in usable:
        assert VENUES[name].access_for("US-MI") is Access.AVAILABLE, (
            f"{name} is usable without an AVAILABLE status"
        )


def test_every_catalogue_entry_states_a_regulator_and_a_review_date():
    from data_sources.venues.registry import REVIEWED_ON, VENUES

    assert VENUES, "the catalogue must not be empty"
    for name, status in VENUES.items():
        assert status.venue == name
        assert status.regulator, f"{name} does not say who regulates it"
        assert status.reviewed_on <= REVIEWED_ON
        assert len(status.notes) > 20, f"{name} has no reviewable note"


def test_the_report_names_a_reason_for_every_refusal():
    from data_sources.venues.registry import VENUES, report

    text = report(OperatorDeclaration(jurisdiction="US-MI"), today=TODAY)
    for name in VENUES:
        assert name in text
    assert "Not legal advice" in text


# --------------------------------------------- the series list going stale --


def test_reconcile_splits_missing_from_not_applicable():
    """A coin race is not a missing asset - it is a product this bot cannot price."""
    from scripts.reconcile_series import reconcile

    listed = {
        "KXBTC15M": "Bitcoin",
        "KXADA15M": "Cardano",
        "KXCRYPTOLEAD15M": "Coin Race",
    }
    result = reconcile(listed, {"KXBTC15M"})
    assert result["missing"] == ["KXADA15M"]
    assert result["not_applicable"] == ["KXCRYPTOLEAD15M"]
    assert result["delisted"] == []


def test_a_configured_series_the_venue_dropped_is_reported():
    """The more dangerous direction: asking for a market that cannot exist."""
    from scripts.reconcile_series import reconcile

    result = reconcile({"KXBTC15M": "Bitcoin"}, {"KXBTC15M", "KXGONE15M"})
    assert result["delisted"] == ["KXGONE15M"]


def test_only_fifteen_minute_crypto_series_are_considered():
    from scripts.reconcile_series import fifteen_minute_crypto

    series = [
        {"ticker": "KXBTC15M", "frequency": "fifteen_min", "title": "BTC"},
        {"ticker": "KXBTCD", "frequency": "hourly", "title": "BTC daily"},
        {"frequency": "fifteen_min", "title": "no ticker"},
    ]
    assert fifteen_minute_crypto(series) == {"KXBTC15M": "BTC"}


# ------------------------------------------------ streamed candles --------


def test_the_websocket_symbol_is_derived_not_a_second_list():
    """A hand-kept second mapping is what goes stale silently here."""
    from data_sources.kraken.stream import ws_symbol_for

    import config

    for asset in config.SERIES_BY_ASSET:
        assert ws_symbol_for(asset) == f"{asset}/USD"


def test_a_stream_that_never_spoke_is_not_live():
    """The failure polling does not have: a well-formed candle that stopped."""
    from data_sources.kraken.stream import KrakenCandleStream

    stream = KrakenCandleStream(
        rest=object(), assets=("BTC",), pair_by_asset={"BTC": "XBTUSD"}
    )
    assert stream.seconds_since_message is None
    assert stream.is_live() is False


def test_a_silent_stream_goes_stale_and_stops_being_trusted():
    from datetime import datetime, timedelta, timezone

    from data_sources.kraken.stream import KrakenCandleStream

    clock = {"now": datetime(2026, 8, 21, 12, tzinfo=timezone.utc)}
    stream = KrakenCandleStream(
        rest=object(),
        assets=("BTC",),
        pair_by_asset={"BTC": "XBTUSD"},
        stale_after_seconds=90,
        now=lambda: clock["now"],
    )
    stream._ingest(
        '{"channel":"ohlc","data":[{"symbol":"BTC/USD","open":1,"high":1,'
        '"low":1,"close":1,"volume":1,'
        '"interval_begin":"2026-08-21T12:00:00.000000000Z"}]}'
    )
    assert stream.is_live() is True
    clock["now"] += timedelta(seconds=91)
    assert stream.is_live() is False, "a stale stream must stop answering"


def test_the_minute_in_progress_replaces_rather_than_duplicates():
    """Candles are keyed by interval_begin; one minute is one candle."""
    from data_sources.kraken.stream import KrakenCandleStream

    stream = KrakenCandleStream(
        rest=object(), assets=("BTC",), pair_by_asset={"BTC": "XBTUSD"}
    )
    for close in (100, 101, 102):
        stream._ingest(
            '{"channel":"ohlc","data":[{"symbol":"BTC/USD","open":1,"high":1,'
            f'"low":1,"close":{close},"volume":1,'
            '"interval_begin":"2026-08-21T12:00:00.000000000Z"}]}'
        )
    window = stream._candles["XBTUSD"]
    assert len(window) == 1
    assert window[-1].close == D("102"), "its close is live spot, and it moved"


def test_only_a_data_row_counts_as_liveness():
    """A heartbeat proves the socket is open, not that candles are arriving."""
    from data_sources.kraken.stream import KrakenCandleStream

    stream = KrakenCandleStream(
        rest=object(), assets=("BTC",), pair_by_asset={"BTC": "XBTUSD"}
    )
    stream._ingest('{"channel":"heartbeat"}')
    stream._ingest('{"channel":"status","data":[{"system":"online"}]}')
    assert stream.is_live() is False


# --------------------------------------------------- queue-aware fills -----


def _quote(**kw):
    from datetime import datetime, timezone

    from execution.order_lifecycle import RestingQuote

    base = dict(
        ticker="T",
        asset="BTC",
        outcome="yes",
        price=D("0.60"),
        contracts=D("10"),
        placed_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        yes_leg_price=D("0.60"),
        book_side="bid",
    )
    base.update(kw)
    return RestingQuote(**base)


def test_queue_awareness_off_reproduces_the_through_rule_exactly():
    from execution.order_lifecycle import simulate_maker_fill

    quote = _quote(queue_ahead=D("0"))
    # Book has NOT traded through: ask is above our resting bid.
    assert simulate_maker_fill(
        quote, yes_bid=D("0.59"), yes_ask=D("0.61"), queue_aware=False
    ) == D("0")


def test_a_cleared_queue_fills_only_when_asked_for():
    from execution.order_lifecycle import simulate_maker_fill

    quote = _quote(queue_ahead=D("0"))
    assert simulate_maker_fill(
        quote, yes_bid=D("0.59"), yes_ask=D("0.61"), queue_aware=True
    ) == D("10")


def test_a_queue_still_ahead_does_not_fill_even_when_asked_for():
    from execution.order_lifecycle import simulate_maker_fill

    quote = _quote(queue_ahead=D("250"))
    assert simulate_maker_fill(
        quote, yes_bid=D("0.59"), yes_ask=D("0.61"), queue_aware=True
    ) == D("0")


def test_an_unknown_queue_never_fills():
    """Absent is absent: no queue reading is not a cleared queue."""
    from execution.order_lifecycle import queue_cleared, simulate_maker_fill

    quote = _quote(queue_ahead=None)
    assert queue_cleared(quote) is False
    assert simulate_maker_fill(
        quote, yes_bid=D("0.59"), yes_ask=D("0.61"), queue_aware=True
    ) == D("0")


def test_the_through_rule_still_fills_with_queue_awareness_on():
    from execution.order_lifecycle import simulate_maker_fill

    quote = _quote(queue_ahead=D("500"))
    assert simulate_maker_fill(
        quote, yes_bid=D("0.59"), yes_ask=D("0.60"), queue_aware=True
    ) == D("10")


async def test_queue_position_is_actually_recorded_by_the_servicing_loop():
    """Reachability, not arithmetic.

    The recording is wired into `service_quotes`, and after a live restart the
    sim had written zero of these - because no quote had rested yet, not
    because the wiring was missing. That is indistinguishable from a dead
    feature by inspection, so it gets a test that drives the real loop.
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from execution.order_lifecycle import RestingQuote
    from execution.position_book import PositionBook
    from execution.scalp_engine import ScalpEngine

    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    recorded: list[tuple[str, dict]] = []

    settings = SimpleNamespace(
        environment="prod",
        scalp_quote_ttl_seconds=60,
        scalp_requote_ticks=3,
        scalp_min_ev_per_dollar=D("0"),
        scalp_queue_aware_fills=False,
        scalp_fee_per_contract=D("0.01"),
    )
    engine = object.__new__(ScalpEngine)
    engine.settings = settings
    engine.live = False
    engine.mode = "paper"
    engine.client = None
    engine.decisions = SimpleNamespace(
        record_event=lambda kind, **kw: recorded.append((kind, kw))
    )
    engine.positions = PositionBook(max_concurrent={"scalper": 1})
    engine._now = lambda: now

    quote = RestingQuote(
        ticker="KXBTC15M-T",
        asset="BTC",
        outcome="yes",
        price=D("0.60"),
        contracts=D("10"),
        placed_at=now,
        yes_leg_price=D("0.60"),
        book_side="bid",
    )
    market = SimpleNamespace(
        yes_bid=D("0.60"),
        yes_ask=D("0.61"),
        yes_bid_size=D("420"),
        yes_ask_size=D("500"),
        fee_multiplier=D("1"),
    )

    engine._refresh_queue(quote, market, {})

    assert quote.queue_ahead == D("420"), "the size resting at our price"
    assert quote.queue_ahead_at_placement == D("420")
    kinds = [kind for kind, _ in recorded]
    assert "scalp_queue_position" in kinds, "the loop must write the record"
    fields = dict(recorded[0][1])
    assert fields["source"] == "book_estimate"
    # Money and sizes cross into the log as exact text, as everywhere here.
    assert str(fields["queue_ahead"]) == "420"


async def test_a_live_venue_queue_beats_the_book_estimate():
    """When Kalshi answers, its number is the one used - it counts matches."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from execution.order_lifecycle import RestingQuote
    from execution.scalp_engine import ScalpEngine

    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    engine = object.__new__(ScalpEngine)
    engine.settings = SimpleNamespace(environment="prod")
    engine.live = True
    engine.mode = "live"
    engine.decisions = SimpleNamespace(record_event=lambda *a, **k: None)
    engine._now = lambda: now

    quote = RestingQuote(
        ticker="T",
        asset="BTC",
        outcome="yes",
        price=D("0.60"),
        contracts=D("10"),
        placed_at=now,
        order_id="abc",
        yes_leg_price=D("0.60"),
        book_side="bid",
    )
    market = SimpleNamespace(yes_bid_size=D("999"), yes_ask_size=D("999"))

    engine._refresh_queue(quote, market, {"abc": D("37")})
    assert quote.queue_ahead == D("37"), "the venue's answer, not the book's"
