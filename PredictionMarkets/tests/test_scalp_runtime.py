"""Tests for the scalping runtime: concurrency, the quote lifecycle, ladder arb."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.strategy_brain.strategies.ladder_arb import (
    LadderLevel,
    find_convexity_violations,
    find_monotonicity_violations,
    scan_ladder,
)
from execution.order_lifecycle import (
    QuoteBook,
    RestingQuote,
    evaluate_quote,
    simulate_maker_fill,
)
from execution.position_book import PositionBook
from execution.scalp_engine import (
    entry_touch_depth,
    immediate_exit_book,
    risk_bounded_contracts,
)
from core.strategy_brain.strategies.scalper import ScalpQuote


D = Decimal
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def test_taker_depth_uses_the_liquidity_it_actually_consumes() -> None:
    from types import SimpleNamespace

    market = SimpleNamespace(yes_bid_size=D("900"), yes_ask_size=D("2"))
    yes = ScalpQuote(
        price=D("0.70"),
        probability=D("0.80"),
        ev_per_dollar=D("0.10"),
        spread=D("0.01"),
        joins_bid=False,
        outcome="yes",
        yes_leg_price=D("0.70"),
        book_side="bid",
    )
    no = ScalpQuote(
        price=D("0.70"),
        probability=D("0.80"),
        ev_per_dollar=D("0.10"),
        spread=D("0.01"),
        joins_bid=False,
        outcome="no",
        yes_leg_price=D("0.30"),
        book_side="ask",
    )

    assert entry_touch_depth(market, yes, "taker") == D("2")
    assert entry_touch_depth(market, no, "taker") == D("900")
    assert entry_touch_depth(market, yes, "maker") == D("900")
    assert entry_touch_depth(market, no, "maker") == D("2")


def test_exitability_uses_the_opposite_touch_for_each_outcome() -> None:
    from types import SimpleNamespace

    market = SimpleNamespace(
        yes_bid=D("0.68"),
        yes_ask=D("0.71"),
        yes_bid_size=D("37"),
        yes_ask_size=D("11"),
    )
    assert immediate_exit_book(market, "yes") == (D("0.68"), D("37"))
    assert immediate_exit_book(market, "no") == (D("0.29"), D("11"))


def test_exit_risk_clips_size_instead_of_rejecting_the_opportunity() -> None:
    contracts = risk_bounded_contracts(
        proposed=D("70"),
        entry_price=D("0.70"),
        entry_fee=D("0.01"),
        exit_price=D("0.69"),
        exit_fee=D("0.01"),
        exit_depth=D("100"),
        maximum_exit_loss=D("1.18"),
    )
    assert contracts == D("39")


def test_exit_risk_also_caps_to_real_exit_depth() -> None:
    contracts = risk_bounded_contracts(
        proposed=D("70"),
        entry_price=D("0.70"),
        entry_fee=D("0.01"),
        exit_price=D("0.69"),
        exit_fee=D("0.01"),
        exit_depth=D("12"),
        maximum_exit_loss=D("1.18"),
    )
    assert contracts == D("12")


async def test_place_quote_uses_the_clipped_size_instead_of_refusing() -> None:
    from types import SimpleNamespace

    from execution.scalp_engine import ScalpEngine

    settings = SimpleNamespace(
        environment="production",
        trade_budget_usd=D("50"),
        live_capital_limit_usd=D("10"),
        scalp_order_style="maker",
        scalp_max_modeled_exit_loss_usd=D("1.18"),
        scalp_band_low=D("0.60"),
        scalp_band_high=D("0.92"),
        max_entries_per_market=2,
        size_probability_uplift=D("0"),
    )
    decisions = SimpleNamespace(events=[], record_event=lambda *a, **k: None)
    ledger = SimpleNamespace(can_enter_ticker=lambda **kwargs: (True, "approved"))
    positions = PositionBook(max_concurrent={"scalper": 9})
    engine = ScalpEngine(
        settings=settings,
        client=None,
        ledger=ledger,
        decisions=decisions,
        positions=positions,
        mode="paper",
        live=False,
    )
    market = SimpleNamespace(
        ticker="KXBTC15M-RISK-CLIP",
        asset="BTC",
        event_ticker="KXBTC15M-RISK",
        yes_bid=D("0.68"),
        yes_ask=D("0.70"),
        yes_bid_size=D("100"),
        yes_ask_size=D("100"),
        fee_multiplier=D("1"),
        close_time=NOW,
        target_price=D("100000"),
    )
    scalp_quote = ScalpQuote(
        price=D("0.70"),
        probability=D("0.80"),
        ev_per_dollar=D("0.10"),
        spread=D("0.02"),
        joins_bid=True,
        outcome="yes",
        yes_leg_price=D("0.70"),
        book_side="bid",
    )

    placed = await engine.place_quote(
        market,
        scalp_quote,
        available_cash=D("100"),
        portfolio_value=D("100"),
    )

    assert placed is not None
    assert placed.contracts == D("23")
    assert positions.committed("scalper") < D("50")
    assert (engine.stats.gate_blocks or {}).get("exitability", 0) == 0


def quote(**kwargs) -> RestingQuote:
    defaults = dict(
        ticker="KXBTC15M-T",
        asset="BTC",
        outcome="yes",
        price=D("0.70"),
        contracts=D("10"),
        placed_at=NOW,
        bid_at_placement=D("0.69"),
        yes_leg_price=D("0.70"),
        book_side="bid",
    )
    defaults.update(kwargs)
    return RestingQuote(**defaults)


def no_quote(**kwargs) -> RestingQuote:
    """A NO position: bought at 0.90, resting as a YES offer at 0.10."""
    defaults = dict(
        outcome="no",
        price=D("0.90"),
        yes_leg_price=D("0.10"),
        book_side="ask",
    )
    defaults.update(kwargs)
    return quote(**defaults)


# ------------------------------------------------------------- concurrency --


async def test_the_position_book_never_exceeds_its_limit_under_a_race():
    """50 coroutines race for 3 slots. Exactly 3 win.

    This is the invariant that replaced the dropped SQLite index, so it is the
    single most important test in this file.
    """
    book = PositionBook(max_concurrent={"scalper": 3})
    results = await asyncio.gather(
        *(
            book.reserve(strategy="scalper", ticker=f"T{i}", amount=D("1"))
            for i in range(50)
        )
    )
    assert sum(1 for r in results if r is not None) == 3
    assert book.open_count("scalper") == 3


async def test_capital_ceiling_holds_under_a_race():
    book = PositionBook(
        max_concurrent={"scalper": 100}, capital_ceiling={"scalper": D("10")}
    )
    results = await asyncio.gather(
        *(
            book.reserve(strategy="scalper", ticker=f"T{i}", amount=D("3"))
            for i in range(50)
        )
    )
    granted = [r for r in results if r is not None]
    assert len(granted) == 3  # 3 x $3 = $9; a fourth would exceed $10
    assert book.committed("scalper") <= D("10")


async def test_the_same_ticker_is_never_reserved_twice_by_one_strategy():
    book = PositionBook(max_concurrent={"scalper": 5})
    first = await book.reserve(strategy="scalper", ticker="SAME", amount=D("1"))
    second = await book.reserve(strategy="scalper", ticker="SAME", amount=D("1"))
    assert first is not None
    assert second is None


async def test_two_strategies_have_independent_limits():
    book = PositionBook(max_concurrent={"predictor": 1, "scalper": 3})
    await book.reserve(strategy="predictor", ticker="A", amount=D("1"))
    assert await book.reserve(strategy="predictor", ticker="B", amount=D("1")) is None
    assert await book.reserve(strategy="scalper", ticker="A", amount=D("1")) is not None


async def test_a_released_reservation_frees_its_slot():
    book = PositionBook(max_concurrent={"scalper": 1})
    first = await book.reserve(strategy="scalper", ticker="A", amount=D("1"))
    assert await book.reserve(strategy="scalper", ticker="B", amount=D("1")) is None
    await book.release(first)
    assert await book.reserve(strategy="scalper", ticker="B", amount=D("1")) is not None


async def test_resync_rebuilds_from_the_ledger():
    book = PositionBook(max_concurrent={"scalper": 3})
    await book.resync([("t1", "scalper", "A", D("5")), ("t2", "scalper", "B", D("6"))])
    assert book.open_count("scalper") == 2
    assert book.committed("scalper") == D("11")
    await book.close("t1")
    assert book.open_count("scalper") == 1


# ---------------------------------------------------------- quote lifecycle --


def test_a_quote_only_fills_when_the_book_trades_through_it():
    """The pessimistic paper model. Sitting at the touch fills nothing."""
    q = quote(price=D("0.70"))
    # Market still offered above us: no fill.
    assert simulate_maker_fill(q, yes_bid=D("0.70"), yes_ask=D("0.72")) == D("0")
    # Offer comes down THROUGH our price: filled.
    assert simulate_maker_fill(q, yes_bid=D("0.68"), yes_ask=D("0.70")) == D("10")


def test_the_no_side_fill_test_is_mirrored_not_copied():
    """A resting offer needs a BUYER to lift it, not a seller to hit it.

    Reusing the YES comparison here would have filled every NO quote instantly,
    since the YES ask is always above a NO position's low leg price.
    """
    q = no_quote()  # offering YES at 0.10, i.e. holding NO at 0.90
    # Nobody is bidding up to our offer yet.
    assert simulate_maker_fill(q, yes_bid=D("0.09"), yes_ask=D("0.11")) == D("0")
    # A buyer bids through our offer: filled.
    assert simulate_maker_fill(q, yes_bid=D("0.10"), yes_ask=D("0.12")) == D("10")


def test_a_fill_beats_a_cancellation_decided_in_the_same_cycle():
    """An order that filled before we pulled it did fill.

    Letting a cancel retroactively erase a fill would hide precisely the adverse
    selection this strategy needs to measure.
    """
    outcome = evaluate_quote(
        quote(),
        now=NOW,
        yes_bid=D("0.68"),
        yes_ask=D("0.70"),
        edge_survives=False,  # we would otherwise cancel
        ttl_seconds=20,
        requote_ticks=2,
    )
    assert outcome.action == "fill"


def test_a_quote_is_cancelled_when_its_edge_evaporates():
    outcome = evaluate_quote(
        quote(),
        now=NOW,
        yes_bid=D("0.69"),
        yes_ask=D("0.72"),
        edge_survives=False,
        ttl_seconds=20,
        requote_ticks=2,
    )
    assert outcome.action == "cancel"
    assert "edge" in outcome.reason


def test_a_quote_is_withdrawn_when_the_book_walks_away_from_it():
    """Withdrawn, but as a REQUOTE - the trade is still wanted.

    This used to assert "cancel", which is what made the taker fallback
    unreachable. See test_drift_is_a_requote_not_a_cancel below.
    """
    outcome = evaluate_quote(
        quote(bid_at_placement=D("0.69")),
        now=NOW,
        yes_bid=D("0.72"),
        yes_ask=D("0.74"),
        edge_survives=True,
        ttl_seconds=20,
        requote_ticks=2,
    )
    assert outcome.action == "requote"
    assert "ticks" in outcome.reason


def test_a_quote_expires_at_its_ttl():
    outcome = evaluate_quote(
        quote(),
        now=NOW + timedelta(seconds=21),
        yes_bid=D("0.69"),
        yes_ask=D("0.72"),
        edge_survives=True,
        ttl_seconds=20,
        requote_ticks=2,
    )
    assert outcome.action == "expire"


def test_a_healthy_quote_is_simply_held():
    outcome = evaluate_quote(
        quote(),
        now=NOW + timedelta(seconds=5),
        yes_bid=D("0.69"),
        yes_ask=D("0.72"),
        edge_survives=True,
        ttl_seconds=20,
        requote_ticks=2,
    )
    assert outcome.action == "hold"


def test_the_quote_book_holds_one_quote_per_ticker_per_strategy():
    book = QuoteBook()
    book.add(quote(ticker="A"))
    book.add(quote(ticker="A", price=D("0.71")))
    assert len(book) == 1
    assert book.get("scalper", "A").price == D("0.71")
    assert book.drain() and len(book) == 0


# ------------------------------------------------------------- ladder arb --


def level(strike, bid, ask, bsz=D("1000"), asz=D("1000")) -> LadderLevel:
    return LadderLevel(
        ticker=f"T{strike}",
        strike=D(str(strike)),
        yes_bid=D(str(bid)),
        yes_ask=D(str(ask)),
        bid_size=bsz,
        ask_size=asz,
    )


def test_a_well_formed_ladder_produces_no_signal():
    """Guards against false positives, which on an arb scanner are the danger.

    These are the real prices probed from KXBTCD on 18 Aug 2026. Monotonicity
    held, so the scanner must stay silent.
    """
    ladder = [
        level(63499.99, 0.94, 0.95),
        level(63999.99, 0.83, 0.84),
        level(64249.99, 0.72, 0.73),
        level(64499.99, 0.59, 0.60),
        level(64999.99, 0.30, 0.31),
        level(65249.99, 0.18, 0.19),
    ]
    assert find_monotonicity_violations(ladder, buffer=D("0.02")) == []


def test_an_inverted_ladder_is_detected():
    """A higher strike bid above a lower strike ask is free money."""
    ladder = [
        level(64000, 0.50, 0.52),
        level(64250, 0.60, 0.62),  # cannot be MORE likely than a lower bar
    ]
    found = find_monotonicity_violations(ladder, buffer=D("0.01"))
    assert found
    assert found[0].kind == "monotonicity"
    assert found[0].net_edge > 0
    assert [leg.side for leg in found[0].legs] == ["buy", "sell"]


def test_a_violation_smaller_than_fees_is_rejected():
    """The reason ladder arb ships disabled: most violations do not survive."""
    ladder = [level(64000, 0.50, 0.52), level(64250, 0.525, 0.53)]
    assert find_monotonicity_violations(ladder, buffer=D("0.02")) == []


def test_a_convexity_violation_is_detected_when_one_really_exists():
    ladder = [
        level(63999.99, 0.86, 0.87),
        level(64249.99, 0.78, 0.79),
        level(64499.99, 0.56, 0.57),
    ]
    found = find_convexity_violations(ladder, buffer=D("0"), fee_multiplier=D("0"))
    assert found
    assert found[0].kind == "convexity"
    assert [leg.side for leg in found[0].legs] == ["buy", "sell", "buy"]
    # Wings bought, body sold twice.
    assert found[0].legs[1].quantity == found[0].legs[0].quantity * 2


def test_the_butterfly_measured_live_is_not_actually_tradeable():
    """The KXBTCD butterfly from 18 Aug 2026: 0.83 - 2(0.72) + 0.59 = -0.02.

    At mid prices that is a 2c inefficiency. Priced where it could actually be
    executed - wings at the ask, body at the bid - it nets exactly zero before a
    single fee is paid. This is why ladder arbitrage ships disabled, and the
    test exists so that nobody re-derives the 2c figure from mids and concludes
    there is money in it.
    """
    ladder = [
        level(63999.99, 0.83, 0.84),
        level(64249.99, 0.72, 0.73),
        level(64499.99, 0.59, 0.60),
    ]
    assert find_convexity_violations(ladder, buffer=D("0"), fee_multiplier=D("0")) == []
    assert find_convexity_violations(ladder, buffer=D("0.02")) == []


def test_unequally_spaced_strikes_are_skipped():
    """Convexity is only defined for equal spacing."""
    ladder = [
        level(64000, 0.86, 0.87),
        level(64250, 0.72, 0.73),
        level(65000, 0.56, 0.57),  # 750 gap against a 250 gap
    ]
    assert find_convexity_violations(ladder, buffer=D("0"), fee_multiplier=D("0")) == []


def test_an_untradeable_level_is_ignored():
    ladder = [
        level(64000, 0.50, 0.52),
        LadderLevel("T2", D("64250"), D("0.60"), D("0.62"), D("0"), D("0")),
    ]
    assert scan_ladder(ladder, buffer=D("0.01")) == []


def test_drift_is_a_requote_not_a_cancel():
    """The audit fix: the two withdrawal reasons mean opposite things.

    Gating the taker fallback on "expire" alone made it dead code — across two
    live sessions 15 quotes were withdrawn for drift and exactly 1 aged out,
    because on a 1c-spread market a two-tick move arrives long before a
    20-second timer. So the strategy was maker-ONLY in practice, not the
    maker-heavy design it was specified as.
    """
    outcome = evaluate_quote(
        quote(bid_at_placement=D("0.69")),
        now=NOW,
        yes_bid=D("0.72"),
        yes_ask=D("0.74"),
        edge_survives=True,
        ttl_seconds=20,
        requote_ticks=2,
    )
    assert outcome.action == "requote"


def test_a_dead_edge_is_still_a_plain_cancel():
    """Only drift means "we still want this". A dead edge must not cross."""
    outcome = evaluate_quote(
        quote(bid_at_placement=D("0.69")),
        now=NOW,
        yes_bid=D("0.72"),
        yes_ask=D("0.74"),
        edge_survives=False,
        ttl_seconds=20,
        requote_ticks=2,
    )
    assert outcome.action == "cancel"


# --------------------------------------- the pattern gate reads distance_z --


async def test_the_pattern_gate_is_fed_distance_not_distance_plus_drift():
    """The wiring, not the rule. check_pattern's |z| floor is documented as the
    distance from spot to the strike; the model's z_score has the drift forecast
    folded in. On 20 Aug 2026 that difference admitted a 0.16-sigma coin flip as
    a 0.58-sigma trade and cost $8.66.

    Five features in this repo have shipped correct and unreachable, so the fact
    that assess() reads the right field gets its own test.
    """
    from types import SimpleNamespace

    from execution.scalp_engine import ScalpEngine
    from execution.position_book import PositionBook

    settings = SimpleNamespace(
        environment="prod",
        scalp_min_seconds_to_close=60,
        scalp_blocked_regimes=("VOLATILE_CHOP",),
        scalp_min_regime_confidence=D("0"),
        scalp_min_abs_z=D("0.50"),
        scalp_max_toxicity=D("0.85"),
        scalp_orderbook_enabled=False,
        scalp_band_low=D("0.60"),
        scalp_band_high=D("0.92"),
        scalp_min_ev_per_dollar=D("0.015"),
        scalp_max_spread=D("0.02"),
        scalp_min_book_depth=D("1"),
        scalp_order_style="taker",
        scalp_taker_edge_multiple=D("2.0"),
        scalp_crowd_weight=D("0"),
        scalp_max_concurrent=9,
    )
    engine = ScalpEngine(
        settings=settings,
        client=None,
        ledger=None,
        decisions=SimpleNamespace(record_event=lambda *a, **k: None),
        positions=PositionBook(max_concurrent={"scalper": 9}),
        mode="paper",
        live=False,
    )

    market = SimpleNamespace(
        ticker="KXBTC15M-T",
        asset="BTC",
        event_ticker="E",
        yes_bid=D("0.60"),
        yes_ask=D("0.61"),
        fee_multiplier=D("1"),
        yes_bid_size=D("5000"),
        yes_ask_size=D("5000"),
        no_ask=D("0.40"),
        target_price=D("69305.38"),
        close_time=NOW,
        open_time=NOW,
    )
    # The measured decomposition of the losing trade: aggregate 0.58, distance
    # 0.16. Only the aggregate clears the 0.50 floor.
    prediction = SimpleNamespace(
        probability_yes=D("0.7184"),
        regime="CONSOLIDATION",
        regime_confidence=D("0.55"),
        z_score=D("0.58"),  # would pass
        distance_z=D("0.16"),  # must refuse
    )

    quote = engine.assess(
        market,
        prediction,
        seconds_to_close=D("190"),
        available_cash=D("100"),
        book=None,
    )
    assert quote is None, "a 0.16-sigma coin flip must not be quoted"
    assert (engine.stats.gate_blocks or {}).get("pattern") == 1

    # And the same setup with the strike genuinely far away still quotes, so
    # the gate is refusing on distance rather than refusing everything.
    engine.stats.gate_blocks = {}
    prediction.distance_z = D("1.40")
    assert (
        engine.assess(
            market,
            prediction,
            seconds_to_close=D("190"),
            available_cash=D("100"),
            book=None,
        )
        is not None
    )
