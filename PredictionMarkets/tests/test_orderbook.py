"""Order-book analytics, and the research each measure is grounded in."""

from __future__ import annotations

from decimal import Decimal

from core.strategy_brain.strategies.orderbook import (
    analyse,
    book_imbalance,
    contrarian_bonus,
    crowd_side,
    flow_toxicity,
    microprice,
    total_depth,
)
from execution import scalp_gate


D = Decimal


def book(yes_levels, no_levels):
    return analyse(
        [(D(str(p)), D(str(s))) for p, s in yes_levels],
        [(D(str(p)), D(str(s))) for p, s in no_levels],
    )


def test_the_yes_ask_is_derived_from_the_no_bid():
    """Kalshi publishes resting bids on both sides and no asks at all.

    A YES bid at p IS a NO ask at 1-p for the same size, so reconstructing an
    ask book would be inventing data. Getting this backwards prices every trade
    off a spread that does not exist.
    """
    a = book([(0.70, 100)], [(0.28, 100)])
    assert a is not None
    assert a.yes_bid == D("0.70")
    assert a.yes_ask == D("0.72")  # 1 - 0.28
    assert a.spread == D("0.02")


def test_a_crossed_book_is_refused_rather_than_priced():
    """A locked or crossed book means a stale snapshot, not free money."""
    assert book([(0.75, 100)], [(0.30, 100)]) is None  # ask 0.70 < bid 0.75


def test_an_empty_side_yields_no_analysis():
    assert book([], [(0.28, 100)]) is None
    assert book([(0.70, 100)], []) is None


def test_imbalance_is_signed_and_bounded():
    assert book_imbalance(D("100"), D("0")) == D("1")
    assert book_imbalance(D("0"), D("100")) == D("-1")
    assert book_imbalance(D("50"), D("50")) == D("0")
    assert book_imbalance(D("0"), D("0")) == D("0")  # no divide-by-zero


def test_toxicity_is_one_sidedness_regardless_of_direction():
    """The VPIN proxy. Bartlett & O'Hara: one-sided flow predicts maker losses.

    Direction does not matter for the toxicity reading itself - a book that is
    entirely on one side is equally dangerous to sit passively in whichever
    side that is.
    """
    assert flow_toxicity(D("100"), D("0")) == D("1")
    assert flow_toxicity(D("0"), D("100")) == D("1")
    assert flow_toxicity(D("50"), D("50")) == D("0")


def test_the_toxicity_gate_stands_aside_on_a_one_sided_book():
    assert scalp_gate.check_toxicity(D("0.50"), D("0.85")).allowed
    assert not scalp_gate.check_toxicity(D("0.95"), D("0.85")).allowed


def test_the_microprice_leans_toward_the_thin_side():
    """Weighting is crossed on purpose: bid price by ask size, and vice versa.

    Depth on one side is pressure pushing price toward the other. A heavy bid
    with a thin offer means true value sits nearer the ask, and using the plain
    midpoint reads it backwards by up to half the spread - which on a 1c market
    is the entire edge.
    """
    heavy_bid = microprice(D("0.70"), D("0.72"), D("1000"), D("10"))
    mid = D("0.71")
    assert heavy_bid > mid

    heavy_ask = microprice(D("0.70"), D("0.72"), D("10"), D("1000"))
    assert heavy_ask < mid

    balanced = microprice(D("0.70"), D("0.72"), D("100"), D("100"))
    assert balanced == mid


def test_microprice_falls_back_to_the_mid_on_an_empty_book():
    assert microprice(D("0.70"), D("0.72"), D("0"), D("0")) == D("0.71")


def test_crowd_side_reads_where_the_resting_pressure_is():
    heavy_yes = book([(0.70, 5000)], [(0.28, 100)])
    assert crowd_side(heavy_yes) == "yes"
    heavy_no = book([(0.70, 100)], [(0.28, 5000)])
    assert crowd_side(heavy_no) == "no"
    balanced = book([(0.70, 1000)], [(0.28, 1000)])
    assert crowd_side(balanced) == "flat"


def test_the_depth_adjustment_is_inert_by_default():
    """It ships disabled because its SIGN is unresolved, not its size.

    Resting bids are maker orders, and Becker measures makers at +1.12% against
    takers at -1.12% - which argues for leaning TOWARD resting depth. But
    Bartlett & O'Hara's order-flow fact (YES bought 61% of the time, settles
    32%) argues that visible pressure marks where the crowd overpays, i.e. lean
    AGAINST it. Opposite signs, and the abstracts do not settle which applies to
    a 15-minute crypto strike.

    A directional nudge with the wrong sign pushes every marginal trade the
    wrong way while looking like a refinement, so the default weight is zero.
    """
    heavy_yes = book([(0.70, 5000)], [(0.28, 100)])
    assert contrarian_bonus(heavy_yes, "no") == D("0")
    assert contrarian_bonus(heavy_yes, "yes") == D("0")


def test_the_documented_sign_holds_when_it_is_switched_on():
    """Convention: positive for opposing resting depth, negative for joining."""
    heavy_yes = book([(0.70, 5000)], [(0.28, 100)])
    assert contrarian_bonus(heavy_yes, "no", D("0.25")) > 0
    assert contrarian_bonus(heavy_yes, "yes", D("0.25")) < 0


def test_a_balanced_book_adjusts_nothing_even_when_enabled():
    balanced = book([(0.70, 1000)], [(0.28, 1000)])
    assert contrarian_bonus(balanced, "yes", D("0.25")) == D("0")
    assert contrarian_bonus(balanced, "no", D("0.25")) == D("0")


def test_the_bonus_scales_with_how_lopsided_the_book_is():
    mild = book([(0.70, 1200)], [(0.28, 800)])
    extreme = book([(0.70, 5000)], [(0.28, 50)])
    assert contrarian_bonus(extreme, "no", D("0.25")) > contrarian_bonus(
        mild, "no", D("0.25")
    )


def test_depth_sums_every_level_not_only_the_touch():
    """A tight spread with shallow depth is the trap the guides warn about."""
    assert total_depth([(D("0.70"), D("10")), (D("0.69"), D("500"))]) == D("510")
    a = book([(0.70, 10), (0.69, 500)], [(0.28, 100)])
    assert a.yes_depth == D("510")
    assert a.levels_yes == 2
