"""Tests for the pure scalping maths and its gates."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.strategy_brain.strategies.scalper import (
    break_even_win_rate,
    build_quote,
    ev_per_dollar,
    in_band,
    position_size,
    should_requote,
)
from execution import scalp_gate


D = Decimal


def test_the_band_floor_is_what_guarantees_the_win_rate():
    """The whole reconciliation of "EV-first" with ">=55% wins" lives here.

    Break-even needs price + fee, so a floor of 0.60 forces any EV-positive
    strategy above a 60% win rate. If this ever fails, the stated objective has
    silently stopped holding.
    """
    for price in (D("0.60"), D("0.75"), D("0.92")):
        assert break_even_win_rate(price) >= D("0.55")
    # And the converse: a cheap contract does not carry that guarantee.
    assert break_even_win_rate(D("0.30")) < D("0.55")


def test_longshots_are_refused_however_good_the_model_looks():
    """A 5c contract with a wildly favourable model is still refused.

    This is the paper's central finding encoded as a gate: contracts at or below
    10c returned worse than -60%, and no model confidence should be able to
    reopen that door.
    """
    quote = build_quote(
        probability=D("0.95"),
        yes_bid=D("0.04"),
        yes_ask=D("0.06"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.015"),
    )
    assert quote is None


def test_a_favourite_inside_the_band_is_quoted_one_tick_above_the_bid():
    quote = build_quote(
        probability=D("0.80"),
        yes_bid=D("0.70"),
        yes_ask=D("0.74"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.015"),
    )
    assert quote is not None
    assert quote.price == D("0.71")
    assert quote.improves_bid


def test_a_one_tick_spread_is_joined_not_crossed():
    """At a 1c spread there is no room to improve, so we join the bid.

    Quoting 0.60 + 0.01 here would equal the ask, and a post_only order priced
    at the ask is rejected by the venue - so the strategy would place orders
    that never rest.
    """
    quote = build_quote(
        probability=D("0.80"),
        yes_bid=D("0.60"),
        yes_ask=D("0.61"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.015"),
    )
    assert quote is not None
    assert quote.price == D("0.60")
    assert quote.joins_bid


def test_a_quote_is_never_priced_at_or_above_the_ask():
    for bid, ask in ((D("0.70"), D("0.71")), (D("0.80"), D("0.82"))):
        quote = build_quote(
            probability=D("0.99"),
            yes_bid=bid,
            yes_ask=ask,
            band_low=D("0.60"),
            band_high=D("0.92"),
            min_ev_per_dollar=D("0.001"),
        )
        if quote is not None:
            assert quote.price < ask


def test_ev_below_the_floor_is_refused_even_inside_the_band():
    quote = build_quote(
        probability=D("0.72"),
        yes_bid=D("0.70"),
        yes_ask=D("0.74"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.05"),
    )
    assert quote is None


def test_ev_per_dollar_puts_both_strategies_on_one_scale():
    cheap = ev_per_dollar(D("0.70"), D("0.60"))
    rich = ev_per_dollar(D("0.95"), D("0.90"))
    assert cheap > rich  # 16.7% vs 5.6% per dollar committed
    assert ev_per_dollar(D("0.5"), D("0")) == D("0")  # no divide-by-zero


def test_fees_can_turn_a_positive_edge_negative():
    without = ev_per_dollar(D("0.62"), D("0.60"))
    with_fee = ev_per_dollar(D("0.62"), D("0.60"), fee=D("0.02"))
    assert without > 0
    assert with_fee < without


def test_requote_fires_only_once_the_book_has_really_moved():
    assert not should_requote(quoted_bid=D("0.70"), current_bid=D("0.71"), max_ticks=2)
    assert should_requote(quoted_bid=D("0.70"), current_bid=D("0.72"), max_ticks=2)
    assert should_requote(quoted_bid=D("0.70"), current_bid=D("0.68"), max_ticks=2)


def test_size_is_clipped_to_book_depth_not_just_budget():
    assert position_size(
        budget=D("1000"), price=D("0.70"), fee_per_contract=D("0.01"), depth=D("12")
    ) == D("12")
    assert position_size(
        budget=D("7.10"), price=D("0.70"), fee_per_contract=D("0.01"), depth=D("999")
    ) == D("10")
    assert position_size(
        budget=D("0.10"), price=D("0.70"), fee_per_contract=D("0.01"), depth=D("999")
    ) == D("0")


@pytest.mark.parametrize(
    "price,allowed",
    [
        (D("0.59"), False),
        (D("0.60"), True),
        (D("0.92"), True),
        (D("0.93"), False),
    ],
)
def test_the_band_gate_is_inclusive_at_both_ends(price, allowed):
    assert scalp_gate.check_price_band(price, D("0.60"), D("0.92")).allowed is allowed


def test_a_wide_book_is_refused_because_the_exit_costs_the_same_spread():
    assert scalp_gate.check_spread(D("0.70"), D("0.72"), D("0.02")).allowed
    assert not scalp_gate.check_spread(D("0.70"), D("0.75"), D("0.02")).allowed
    assert not scalp_gate.check_spread(D("0"), D("0.75"), D("0.02")).allowed


def test_a_fill_that_could_not_be_exited_is_never_quoted():
    assert scalp_gate.check_quote_window(D("300"), D("120")).allowed
    assert not scalp_gate.check_quote_window(D("30"), D("120")).allowed


def test_concurrency_and_capital_ceilings_refuse_rather_than_raise():
    assert scalp_gate.check_concurrency(2, 3).allowed
    assert not scalp_gate.check_concurrency(3, 3).allowed
    assert scalp_gate.check_capital_headroom(D("5"), D("2"), D("10")).allowed
    assert not scalp_gate.check_capital_headroom(D("9"), D("2"), D("10")).allowed


def test_in_band_is_inclusive():
    assert in_band(D("0.60"), D("0.60"), D("0.92"))
    assert in_band(D("0.92"), D("0.60"), D("0.92"))
    assert not in_band(D("0.925"), D("0.60"), D("0.92"))


def test_the_no_side_is_quoted_when_yes_is_the_longshot():
    """The change that made this strategy able to trade at all.

    On a 15-minute market the strike is the interval's opening price, so P(yes)
    is a longshot about as often as it is a favourite. Probed live on 18 Aug
    2026, all five assets showed YES at 0.05-0.36 - every one outside the band.
    A YES-only quoter would have sat idle through all of them while the NO side
    sat squarely in the favourite range.
    """
    quote = build_quote(
        probability=D("0.05"),      # P(yes) - a longshot, so P(no) = 0.95
        yes_bid=D("0.10"),
        yes_ask=D("0.11"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.015"),
    )
    assert quote is not None
    assert quote.outcome == "no"
    assert quote.price == D("0.89")        # buying NO at 1 - 0.11
    assert quote.yes_leg_price == D("0.11")  # ...by resting a YES offer there
    assert quote.book_side == "ask"
    assert quote.probability == D("0.95")


def test_a_thin_no_edge_is_still_refused_by_the_ev_floor():
    """Being on the right side of the band is not enough.

    P(no)=0.90 against a 0.89 price is a 1.1% edge per dollar, under the 1.5%
    floor. At a 1c spread the touch is nearly fair, so the model has to disagree
    with the market by more than the tick to be worth resting on.
    """
    assert build_quote(
        probability=D("0.10"),
        yes_bid=D("0.10"),
        yes_ask=D("0.11"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.015"),
    ) is None


def test_the_better_side_wins_when_both_are_in_band():
    """Both sides can qualify near the middle; EV per dollar decides."""
    quote = build_quote(
        probability=D("0.75"),
        yes_bid=D("0.60"),
        yes_ask=D("0.64"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.001"),
    )
    assert quote is not None
    # P(yes)=0.75 against a 0.61 cost beats P(no)=0.25 against a 0.37 cost.
    assert quote.outcome == "yes"


def test_a_no_quote_never_prices_through_the_bid():
    """The mirror of "never at or above the ask" for the offer side."""
    quote = build_quote(
        probability=D("0.02"),
        yes_bid=D("0.10"),
        yes_ask=D("0.11"),
        band_low=D("0.60"),
        band_high=D("0.95"),
        min_ev_per_dollar=D("0.001"),
    )
    assert quote is not None
    assert quote.yes_leg_price > D("0.10")


def test_both_sides_outside_the_band_still_refuses():
    """A coin-flip market qualifies on neither side."""
    assert build_quote(
        probability=D("0.50"),
        yes_bid=D("0.49"),
        yes_ask=D("0.51"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.015"),
    ) is None


def test_the_taker_threshold_keeps_the_strategy_maker_heavy():
    """Crossing is reserved for setups clearly worth paying the spread for.

    The fallback fires at `min_ev * multiple`. At a multiple of 1.0 every
    expired quote would cross and the strategy would degenerate into the taker
    model it exists to improve on, so the multiple is what preserves the
    maker-heavy bias rather than a comment saying it is preserved.
    """
    min_ev = D("0.015")
    multiple = D("2.0")
    threshold = min_ev * multiple

    # A quote that merely qualified to rest does NOT qualify to cross:
    # 1.5c of edge on a 70c contract is 2.1% per dollar - over the 1.5% floor,
    # under the 3.0% needed to pay the spread.
    marginal = ev_per_dollar(D("0.715"), D("0.70"))
    assert marginal >= min_ev
    assert marginal < threshold

    # 5c of edge on the same contract is 7.1%, and does qualify.
    strong = ev_per_dollar(D("0.75"), D("0.70"))
    assert strong >= threshold


def test_unidentifiable_structure_is_refused():
    """VOLATILE_CHOP is the regime that means "no pattern here"."""
    blocked = ("VOLATILE_CHOP",)
    assert not scalp_gate.check_pattern(
        "VOLATILE_CHOP", D("0.90"), D("2.0"),
        blocked_regimes=blocked,
        minimum_confidence=D("0.45"),
        minimum_abs_z=D("0.50"),
    ).allowed
    assert scalp_gate.check_pattern(
        "TREND", D("0.90"), D("2.0"),
        blocked_regimes=blocked,
        minimum_confidence=D("0.45"),
        minimum_abs_z=D("0.50"),
    ).allowed


def test_a_low_confidence_label_is_not_a_pattern():
    result = scalp_gate.check_pattern(
        "TREND", D("0.20"), D("2.0"),
        blocked_regimes=("VOLATILE_CHOP",),
        minimum_confidence=D("0.45"),
        minimum_abs_z=D("0.50"),
    )
    assert not result.allowed
    assert "confidence" in result.reason


def test_a_contract_sitting_near_the_strike_is_refused():
    """The gate that would have stopped the session's only large loss.

    That position was NO at 0.61 with spot close to the strike - a coin flip
    the settlement index decides, not the model. It settled YES and cost more
    than the four managed trades earned.
    """
    result = scalp_gate.check_pattern(
        "TREND", D("0.90"), D("0.25"),
        blocked_regimes=("VOLATILE_CHOP",),
        minimum_confidence=D("0.45"),
        minimum_abs_z=D("0.50"),
    )
    assert not result.allowed
    assert "too close to call" in result.reason
    # Sign does not matter: distance does.
    assert not scalp_gate.check_pattern(
        "TREND", D("0.90"), D("-0.25"),
        blocked_regimes=("VOLATILE_CHOP",),
        minimum_confidence=D("0.45"),
        minimum_abs_z=D("0.50"),
    ).allowed
    assert scalp_gate.check_pattern(
        "TREND", D("0.90"), D("-1.20"),
        blocked_regimes=("VOLATILE_CHOP",),
        minimum_confidence=D("0.45"),
        minimum_abs_z=D("0.50"),
    ).allowed


def test_a_calm_book_is_not_refused_as_unidentifiable():
    """The gate fix. NEUTRAL confidence is hardcoded to 0.3 in the classifier.

    The original 0.45 floor therefore refused every NEUTRAL assessment by
    construction, plus half of CONSOLIDATION — 47% of everything on measured
    data. A maker is not betting a pattern continues; a calm book is where it
    most wants to quote.
    """
    assert scalp_gate.check_pattern(
        "NEUTRAL", D("0.30"), D("1.5"),
        blocked_regimes=("VOLATILE_CHOP",),
        minimum_confidence=D("0.0"),
        minimum_abs_z=D("0.50"),
    ).allowed


def test_a_malformed_assessment_is_still_refused():
    """Negative confidence is a contradiction, not mere uncertainty."""
    assert not scalp_gate.check_pattern(
        "CONSOLIDATION", D("-0.40"), D("1.5"),
        blocked_regimes=("VOLATILE_CHOP",),
        minimum_confidence=D("0.0"),
        minimum_abs_z=D("0.50"),
    ).allowed


def test_chop_and_the_coin_flip_guard_still_bite_at_the_new_floor():
    """Loosening confidence must not loosen the two gates that earn their keep."""
    common = dict(
        blocked_regimes=("VOLATILE_CHOP",),
        minimum_confidence=D("0.0"),
        minimum_abs_z=D("0.50"),
    )
    assert not scalp_gate.check_pattern("VOLATILE_CHOP", D("0.9"), D("2.0"), **common).allowed
    assert not scalp_gate.check_pattern("TREND", D("0.9"), D("0.25"), **common).allowed


def test_the_taker_entry_prices_at_the_touch_not_inside_it():
    """Crossing pays the spread, so the edge must be computed on what is paid.

    The maker path prices at bid+tick and the taker path at the ask. Pricing a
    cross off the maker price would overstate every taker edge by the spread.
    """
    from core.strategy_brain.strategies.scalper import build_taker_entry

    quote = build_taker_entry(
        probability=D("0.85"),
        yes_bid=D("0.70"),
        yes_ask=D("0.74"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.03"),
    )
    assert quote is not None
    assert quote.outcome == "yes"
    assert quote.price == D("0.74")        # the ask, not 0.71
    assert quote.book_side == "bid"


def test_the_taker_entry_buys_no_by_hitting_the_yes_bid():
    """One YES book: buying NO at 0.30 is selling YES at 0.70."""
    from core.strategy_brain.strategies.scalper import build_taker_entry

    quote = build_taker_entry(
        probability=D("0.05"),             # P(no) = 0.95
        yes_bid=D("0.30"),
        yes_ask=D("0.34"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.03"),
    )
    assert quote is not None
    assert quote.outcome == "no"
    assert quote.price == D("0.70")        # 1 - yes_bid
    assert quote.yes_leg_price == D("0.30")
    assert quote.book_side == "ask"


def test_crossing_is_held_to_a_higher_bar_than_resting():
    """The spread is the reason. A trade worth resting on is not automatically
    worth paying up for, and the multiple is what keeps the two apart."""
    from core.strategy_brain.strategies.scalper import build_taker_entry

    # Clears the resting floor (0.015) but not the crossing floor (0.03).
    assert build_taker_entry(
        probability=D("0.76"),
        yes_bid=D("0.70"),
        yes_ask=D("0.74"),
        band_low=D("0.60"),
        band_high=D("0.92"),
        min_ev_per_dollar=D("0.03"),
    ) is None


def test_positions_are_held_to_settlement():
    """Exit-on-reversal is gone, and this records why.

    A live BTC position was bought NO at 0.68, sold at 0.61 for -$1.73 when the
    model reversed mid-interval, and the market then settled NO - the position
    would have won. On a 15-minute horizon the model reverses constantly, so
    reacting to a reversal mostly converts noise into a realised loss and pays a
    round-trip fee to do it.

    Nothing may re-introduce a mid-interval exit path without evidence that the
    reversal predicts settlement better than the entry did.
    """
    import execution.scalp_engine as engine
    import bot as bot_module

    assert not hasattr(engine.ScalpEngine, "service_positions")
    assert not hasattr(engine.ScalpEngine, "_exit")
    assert not hasattr(bot_module.KalshiCryptoBot, "_service_predictor_positions")
