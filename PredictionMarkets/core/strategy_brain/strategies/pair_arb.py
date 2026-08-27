"""Pure Kalshi complementary-pair arbitrage checks.

This is intentionally venue-specific. Kalshi publishes one net YES book: the
YES ask is ``1 - best NO bid`` and the NO ask is ``1 - best YES bid``. A pair
therefore has a guaranteed terminal payout of exactly $1, but a buyable pair
below $1 can only appear when the single book is crossed. That should normally
be matched away by the venue, so observations are treated as stale/race-prone
until both legs are proven fillable from one contemporaneous snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Sequence


ZERO = Decimal("0")
ONE = Decimal("1")
CENTICENT = Decimal("0.0001")
Level = tuple[Decimal, Decimal]


def _best(levels: Sequence[Level]) -> Level | None:
    return max(levels, key=lambda level: level[0]) if levels else None


def taker_fee_per_contract(price: Decimal, multiplier: Decimal) -> Decimal:
    return (
        Decimal("0.07") * multiplier * price * (ONE - price)
    ).quantize(CENTICENT, rounding=ROUND_UP)


@dataclass(frozen=True)
class PairArbAssessment:
    approved: bool
    gate: str
    reason: str
    yes_ask: Decimal = ZERO
    no_ask: Decimal = ZERO
    pair_cost: Decimal = ZERO
    guaranteed_payout: Decimal = ONE
    gross_edge: Decimal = ZERO
    yes_fee: Decimal = ZERO
    no_fee: Decimal = ZERO
    net_edge: Decimal = ZERO
    yes_depth: Decimal = ZERO
    no_depth: Decimal = ZERO
    contracts: Decimal = ZERO
    modeled_profit: Decimal = ZERO


def assess_pair_arb(
    *,
    yes_bid_levels: Sequence[Level],
    no_bid_levels: Sequence[Level],
    fixed_budget: Decimal,
    fee_multiplier: Decimal,
    minimum_net_edge: Decimal,
    minimum_leg_price: Decimal,
    minimum_contracts: Decimal,
) -> PairArbAssessment:
    """Hunt, risk-check, and fee-check one equal-quantity YES/NO pair."""
    yes_bid = _best(yes_bid_levels)
    no_bid = _best(no_bid_levels)
    if yes_bid is None or no_bid is None:
        return PairArbAssessment(False, "book", "both bid books are required")

    # Buying each outcome crosses the opposite outcome's resting bid.
    yes_ask, yes_depth = ONE - no_bid[0], no_bid[1]
    no_ask, no_depth = ONE - yes_bid[0], yes_bid[1]
    pair_cost = yes_ask + no_ask
    gross_edge = ONE - pair_cost
    yes_fee = taker_fee_per_contract(yes_ask, fee_multiplier)
    no_fee = taker_fee_per_contract(no_ask, fee_multiplier)
    net_edge = gross_edge - yes_fee - no_fee
    common = {
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "pair_cost": pair_cost,
        "gross_edge": gross_edge,
        "yes_fee": yes_fee,
        "no_fee": no_fee,
        "net_edge": net_edge,
        "yes_depth": yes_depth,
        "no_depth": no_depth,
    }
    if min(yes_ask, no_ask) < minimum_leg_price:
        return PairArbAssessment(
            False, "stale_leg", "one leg is suspiciously close to zero", **common
        )
    if net_edge < minimum_net_edge:
        return PairArbAssessment(
            False,
            "net_edge",
            f"net pair edge {net_edge:.4f} is below {minimum_net_edge:.4f}",
            **common,
        )
    all_in_cost = pair_cost + yes_fee + no_fee
    affordable = (
        (fixed_budget / all_in_cost).quantize(ONE, rounding=ROUND_DOWN)
        if all_in_cost > ZERO
        else ZERO
    )
    contracts = min(
        affordable,
        yes_depth.quantize(ONE, rounding=ROUND_DOWN),
        no_depth.quantize(ONE, rounding=ROUND_DOWN),
    )
    if contracts < minimum_contracts:
        return PairArbAssessment(
            False,
            "depth",
            "equal fillable depth does not cover the minimum pair size",
            contracts=contracts,
            **common,
        )
    return PairArbAssessment(
        True,
        "approved",
        "equal YES/NO quantities lock a $1 payout after both fees",
        contracts=contracts,
        modeled_profit=contracts * net_edge,
        **common,
    )
