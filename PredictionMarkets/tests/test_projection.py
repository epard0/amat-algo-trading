"""The forward projection: fees, whole contracts, depth, and honest tails.

A projection on a sales page is a claim about somebody else's money. These
tests exist to stop it drifting back toward the flattering version, which it
will, because the flattering version has bigger numbers.
"""

from __future__ import annotations

import pytest

from storefront.verified import (
    CALIBRATED_ENTRY_PRICE,
    CALIBRATED_STAKE_FRACTION,
    MAX_CONTRACTS_PER_TRADE,
    VerifiedRecord,
    break_even_win_rate,
    build_payload,
    project,
    stress_projection,
    taker_fee,
)


@pytest.fixture(scope="module")
def record() -> VerifiedRecord:
    loaded = VerifiedRecord.load()
    assert loaded is not None
    return loaded


# ------------------------------------------------------------------ fees ---

def test_the_fee_matches_kalshis_schedule() -> None:
    """0.07 x contracts x P x (1-P), ceiled to a hundredth of a cent."""
    assert taker_fee(1, 0.50) == pytest.approx(0.0175, abs=1e-4)
    assert taker_fee(100, 0.50) == pytest.approx(1.75, abs=1e-3)


def test_the_fee_is_quadratic_and_peaks_at_the_middle() -> None:
    """Which is why the late-entry rule near the extremes is viable at all."""
    assert taker_fee(1, 0.50) > taker_fee(1, 0.90)
    assert taker_fee(1, 0.50) > taker_fee(1, 0.10)
    # Exactly equal, not approximately: P*(1-P) is symmetric, and a fee that
    # is a tenth of a cent higher on one side of the book is a rounding bug.
    assert taker_fee(1, 0.90) == taker_fee(1, 0.10)


def test_break_even_is_above_the_entry_price() -> None:
    """Buying at 59c does not break even at a 59% win rate. Fees are not free."""
    assert break_even_win_rate(0.59) > 0.59
    assert break_even_win_rate(0.59) == pytest.approx(0.607, abs=0.001)


def test_the_page_never_claims_an_edge_it_has_not_demonstrated(
    record: VerifiedRecord,
) -> None:
    """The original form of this test asserted the win rate cleared break-even
    by five points, on the grounds that otherwise none of the page would be
    worth publishing. That was right, and it started failing when the sample
    grew from 74 trades to 103 and the win rate fell from 68.9% to 65.0%
    against a 60.7% break-even.

    The fix is not a looser threshold. Break-even now sits inside the Wilson
    interval (55.5%-73.6%), so the record is consistent with a strategy that
    earns nothing, and `edge_is_established` says so. What must hold is that
    the two agree: either the margin is comfortable, or the payload declares
    the edge unestablished and the page stops projecting from it.
    """
    comfortable = record.win_rate > break_even_win_rate() + 0.05
    assert comfortable == record.edge_is_established or not record.edge_is_established
    if not comfortable:
        assert not record.edge_is_established, (
            "the win rate does not clear break-even by a comfortable margin, "
            "so the payload must not report the edge as established"
        )


def test_break_even_is_inside_the_interval_at_the_current_sample(
    record: VerifiedRecord,
) -> None:
    """Pinned so that a future record which *does* establish an edge has to
    change this test deliberately rather than by accident."""
    low, high = record.win_rate_interval()
    assert low < break_even_win_rate() < high
    assert record.edge_is_established is False


# ------------------------------------------------------------- parameters ---

def test_the_parameters_are_the_shipped_ones_not_fitted_ones() -> None:
    """The stake is what a buyer actually gets, not what makes the maths work."""
    assert CALIBRATED_STAKE_FRACTION == 0.20      # POSITION_FRACTION in .env.example
    assert 0.55 <= CALIBRATED_ENTRY_PRICE <= 0.62  # the ledger's mean fill


# ------------------------------------------------------------------ tails ---

def test_the_downside_is_published(record: VerifiedRecord) -> None:
    proj = project(record, starting_balance=10.0)

    assert proj["percentiles"]["p5"] < 10.0, "the 5th percentile must show a real loss"
    assert proj["probability_of_loss"] > 0.15
    assert proj["probability_of_half_loss"] > 0.05


def test_uncertainty_about_the_model_widens_both_tails(record: VerifiedRecord) -> None:
    """74 trades do not pin down a level, and pretending they do hides risk.

    The uncertainty now sits in the calibration shift - how far the model's
    average call has to move to match the record - which is redrawn per path.
    Pinning it removes the only source of parameter risk in the simulation.
    """
    drawn = project(record, starting_balance=10.0)
    pinned = project(record, starting_balance=10.0,
                     calibration_shift=drawn["calibration_gap"])

    assert drawn["percentiles"]["p95"] > pinned["percentiles"]["p95"]
    assert drawn["percentiles"]["p5"] <= pinned["percentiles"]["p5"]


def test_a_tiny_balance_cannot_trade(record: VerifiedRecord) -> None:
    """Below one contract's worth of budget there is no trade, not a fraction."""
    proj = project(record, starting_balance=1.0)

    assert proj["percentiles"]["p50"] == pytest.approx(1.0, abs=0.01)


def test_depth_caps_the_trade(record: VerifiedRecord) -> None:
    tight = project(record, starting_balance=500.0, max_contracts=5)
    loose = project(record, starting_balance=500.0, max_contracts=MAX_CONTRACTS_PER_TRADE)

    assert tight["percentiles"]["p95"] < loose["percentiles"]["p95"]


# ----------------------------------------------------------------- stress ---

def test_the_stress_case_halves_the_edge_over_break_even(record: VerifiedRecord) -> None:
    """Halving the *edge*, not the win rate: 34% is not half of 69% here.

    The shift composes with the anchor rather than replacing it. Getting that
    wrong once left the stressed run above the base one.
    """
    base = project(record, starting_balance=10.0)
    stress = stress_projection(record, starting_balance=10.0)
    floor = break_even_win_rate(stress["mean_price"])

    assert stress["win_rate"] == pytest.approx(
        floor + (record.win_rate - floor) / 2, abs=0.001
    )
    assert floor < stress["win_rate"] < record.win_rate
    assert stress["shift"] < base["calibration_gap"]


def test_half_the_edge_is_roughly_break_even(record: VerifiedRecord) -> None:
    """The single most useful number on the page, and it is not a good one."""
    stress = stress_projection(record, starting_balance=10.0)

    assert stress["percentiles"]["p50"] < 2.0 * 10.0
    assert 0.4 < stress["probability_of_profit"] < 0.65


# ---------------------------------------------------------------- payload ---

def test_the_payload_carries_the_stress_case(record: VerifiedRecord) -> None:
    """It has to reach the page, not just exist in the module."""
    payload = build_payload(record)

    assert set(payload["stress"]) == set(payload["projections"])
    assert payload["stress"]["start_10"]["scenario"] == "half the measured edge"


def test_the_calibration_block_states_what_was_assumed(record: VerifiedRecord) -> None:
    calibration = build_payload(record)["calibration"]

    for key in ("entry_price", "stake_fraction", "max_contracts",
                "fee_per_contract", "break_even_win_rate", "observed_percentile"):
        assert key in calibration
    assert "nothing here is fitted" in calibration["note"].lower()


def test_projections_are_deterministic(record: VerifiedRecord) -> None:
    """Same seed, same page: the numbers must not move between two requests."""
    first = project(record, starting_balance=10.0)
    second = project(record, starting_balance=10.0)

    assert first["percentiles"] == second["percentiles"]
