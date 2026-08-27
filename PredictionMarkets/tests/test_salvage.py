"""The salvage exit: when selling a dying position beats carrying it.

The whole point of this rule is that it is *not* the exit-on-reversal that was
removed. That one asked the model; this one asks the price. Several tests below
exist specifically to pin that distinction, because the failure mode is not a
crash - it is a rule that looks reasonable and quietly sells winners.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.strategy_brain.strategies.salvage import (
    decide_salvage,
    fair_value,
    outcome_book,
)


D = Decimal


def book(outcome: str, yes_bid: str, yes_ask: str, yes_size="1000", no_size="1000"):
    return outcome_book(
        outcome=outcome,
        yes_bid=D(yes_bid),
        yes_ask=D(yes_ask),
        yes_bid_size=D(yes_size),
        no_bid_size=D(no_size),
    )


def decide(book_, *, contracts="10", entry="0.68", fee="0.0089", **overrides):
    kwargs = dict(
        max_bid=D("0.15"),
        max_spread=D("0.02"),
        haircut=D("0.40"),
        min_gain_per_contract=D("0.01"),
    )
    kwargs.update({k: D(str(v)) for k, v in overrides.items()})
    return decide_salvage(
        book=book_,
        contracts=D(contracts),
        entry_price=D(entry),
        exit_fee_per_contract=D(fee),
        **kwargs,
    )


# ------------------------------------------------------- the book mapping --


def test_selling_yes_hits_the_yes_bids():
    b = book("yes", "0.12", "0.14")
    assert b.bid == D("0.12")
    assert b.ask == D("0.14")
    assert b.book_side == "ask"          # selling YES
    assert b.yes_leg_price == D("0.12")


def test_selling_no_is_a_buy_on_the_only_book_there_is():
    """Kalshi publishes one YES book, so a NO bid at q IS a YES offer at 1-q.

    Getting this backwards would send the order to the wrong side and, without
    reduce_only, would double the position instead of closing it.
    """
    b = book("no", "0.86", "0.88")
    assert b.bid == D("0.12")            # 1 - yes_ask
    assert b.ask == D("0.14")            # 1 - yes_bid
    assert b.book_side == "bid"          # buying YES == selling NO
    assert b.yes_leg_price == D("0.88")  # the yes ask


def test_the_exit_is_always_the_opposite_side_from_the_entry():
    """Structural, not incidental: this is why the path cannot flip a side.

    A position was opened by buying the outcome; the only order this module
    constructs sells it. Combined with reduce_only on the wire, there is no
    sequence of inputs that turns a salvage into a new position.
    """
    assert book("yes", "0.10", "0.12").book_side == "ask"
    assert book("no", "0.88", "0.90").book_side == "bid"


def test_depth_is_read_at_the_touch_for_the_side_being_sold():
    yes = book("yes", "0.12", "0.14", yes_size="40", no_size="900")
    assert yes.depth == D("40")
    no = book("no", "0.86", "0.88", yes_size="900", no_size="40")
    assert no.depth == D("40")


def test_an_unusable_book_yields_nothing_rather_than_a_price():
    assert book("yes", "0.14", "0.12") is None   # crossed
    assert book("yes", "0", "0.14") is None      # empty side
    assert outcome_book(
        outcome="maybe",
        yes_bid=D("0.10"),
        yes_ask=D("0.12"),
        yes_bid_size=D("100"),
        no_bid_size=D("100"),
    ) is None


# --------------------------------------------------------- the arithmetic --


def test_fair_value_applies_the_measured_overpricing():
    """Burgi/Deng/Whelan measured >=60% at or below 10c; 0.40 is weaker."""
    assert fair_value(D("0.10"), D("0.40")) == D("0.060")
    assert fair_value(D("0.10"), D("0.60")) == D("0.040")
    # A zero haircut says the price is fair, which is the safe degenerate: the
    # gain then reduces to -fee and the rule can never fire.
    assert fair_value(D("0.10"), D("0")) == D("0.10")


def test_a_deep_longshot_with_a_tight_book_is_sold():
    decision = decide(book("yes", "0.12", "0.14"))
    assert decision.should_exit
    # bid 0.12 - fee 0.0089 - mid 0.13 * 0.60 = +0.0331
    assert decision.gain_per_contract == D("0.0331")
    assert decision.fair_value == D("0.0780")


def test_the_no_side_reaches_the_same_verdict_from_the_mirrored_book():
    yes = decide(book("yes", "0.12", "0.14"))
    no = decide(book("no", "0.86", "0.88"))
    assert no.should_exit == yes.should_exit
    assert no.gain_per_contract == yes.gain_per_contract


# ------------------------------------------------------------- the gates --


def test_the_bug_this_replaces_now_resolves_to_hold():
    """The live BTC position: NO at 0.68, book at 0.61, settled NO.

    Exit-on-reversal sold it for -$1.73 on a market it went on to win. Nothing
    about this rule's inputs can produce a sale there - 0.61 is nowhere near the
    longshot zone, so the band gate refuses before the arithmetic is reached.
    """
    decision = decide(book("no", "0.38", "0.40"), entry="0.68")
    assert not decision.should_exit
    assert decision.gate == "band"


def test_a_fairly_priced_position_is_never_sold_even_just_above_the_band():
    """One cent above the ceiling is still a refusal, and deliberately so.

    The haircut alone would manufacture a positive number at any price. The
    ceiling, not the arithmetic, is what confines this to the zone where
    overpricing was actually measured.
    """
    decision = decide(book("yes", "0.16", "0.18"))
    assert not decision.should_exit
    assert decision.gate == "band"


def test_a_wide_book_refuses_even_deep_in_the_zone():
    """The exit is paid out of the spread; measured, it dies by 3-4c."""
    decision = decide(book("yes", "0.10", "0.14"))
    assert not decision.should_exit
    assert decision.gate == "spread"


def test_a_touch_that_cannot_absorb_the_position_refuses_outright():
    """No partial exits. record_exit values the unsold remainder at the exit
    price, so a partial fill writes a number into the ledger no trade produced.
    """
    decision = decide(book("yes", "0.12", "0.14", yes_size="4"), contracts="10")
    assert not decision.should_exit
    assert decision.gate == "depth"
    # ... and the same book with the position it CAN absorb is sold.
    assert decide(
        book("yes", "0.12", "0.14", yes_size="4"), contracts="4"
    ).should_exit


def test_a_position_the_book_revalued_upward_is_not_touched():
    """This reduces losses. Selling a winner at 0.12 that was bought at 0.05 is
    a different trade with a different justification, and not the one asked for.
    """
    decision = decide(book("yes", "0.12", "0.14"), entry="0.05")
    assert not decision.should_exit
    assert decision.gate == "losing"


def test_a_marginal_gain_is_left_alone():
    decision = decide(book("yes", "0.12", "0.14"), min_gain_per_contract="0.05")
    assert not decision.should_exit
    assert decision.gate == "margin"
    assert decision.gain_per_contract == D("0.0331")


def test_a_zero_haircut_makes_the_rule_inert():
    """The degenerate setting fails safe: gain reduces to roughly -fee."""
    decision = decide(book("yes", "0.12", "0.14"), haircut="0")
    assert not decision.should_exit
    assert decision.gain_per_contract < 0


def test_no_book_is_a_refusal_not_a_crash():
    decision = decide(None)
    assert not decision.should_exit
    assert decision.gate == "book"


@pytest.mark.parametrize("gate_kwargs,expected", [
    ({"entry": "0.05"}, "losing"),
    ({"max_bid": "0.10"}, "band"),
    ({"max_spread": "0.01"}, "spread"),
    ({"contracts": "5000"}, "depth"),
    ({"min_gain_per_contract": "0.90"}, "margin"),
])
def test_every_refusal_names_the_gate_that_caused_it(gate_kwargs, expected):
    """A rule that fires rarely by design is indistinguishable from one that is
    unreachable, unless each refusal is attributable. Three dead features in
    this project were found by exactly this kind of counter.
    """
    decision = decide(book("yes", "0.12", "0.14"), **gate_kwargs)
    assert not decision.should_exit
    assert decision.gate == expected
    assert decision.blocked_by == expected
    assert decision.reason
