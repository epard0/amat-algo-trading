"""Pure no-arbitrage detection over a Kalshi strike ladder.

A set of ``P(price >= K)`` contracts on one underlying and one expiry is not a
set of independent markets: it is a discretised cumulative distribution, and it
must satisfy two identities. Where it does not, the violation is model-free -
it needs no forecast, only arithmetic.

**This does not apply to the 15-minute series.** ``KXBTC15M`` and its siblings
expose exactly one strike per event, one event at a time, so there is no ladder
there to be inconsistent. The ladder lives on the daily series (``KXBTCD``),
which is why scalping quotes the 15-minute markets and this scans a different
horizon entirely.

Two identities are checked:

*Monotonicity* - for ``K1 < K2``, ``P(>=K1) >= P(>=K2)``. A higher bar cannot be
more likely to clear. Violated when someone will *buy* the higher strike above
what the lower strike can be *bought* for.

*Convexity* - for equally spaced ``K1 < K2 < K3``,
``P(K1) - 2*P(K2) + P(K3) >= 0``. This is the discrete second derivative of a
cumulative distribution, which cannot be negative. Violated by a butterfly.

Every candidate is quoted against *executable* prices - buy at the ask, sell at
the bid - never mid. An arbitrage that only exists at the midpoint is not an
arbitrage; it is the spread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class LadderLevel:
    """One strike's executable top of book."""

    ticker: str
    strike: Decimal
    yes_bid: Decimal
    yes_ask: Decimal
    bid_size: Decimal
    ask_size: Decimal

    @property
    def tradeable(self) -> bool:
        return (
            self.yes_bid > ZERO
            and self.yes_ask > ZERO
            and self.yes_ask > self.yes_bid
            and self.bid_size > ZERO
            and self.ask_size > ZERO
        )


@dataclass(frozen=True)
class ArbLeg:
    ticker: str
    side: str  # "buy" (cross the ask) or "sell" (hit the bid)
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class ArbOpportunity:
    kind: str  # "monotonicity" | "convexity"
    legs: tuple[ArbLeg, ...]
    gross_edge: Decimal
    fees: Decimal
    net_edge: Decimal
    size: Decimal
    detail: str = ""
    tickers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ev_per_dollar(self) -> Decimal:
        """Net edge per dollar committed, so it ranks against scalp candidates."""
        committed = sum(
            (leg.price * leg.quantity for leg in self.legs if leg.side == "buy"),
            ZERO,
        )
        if committed <= ZERO:
            return ZERO
        return (self.net_edge * self.size) / committed


def quadratic_fee(
    contracts: Decimal,
    price: Decimal,
    fee_multiplier: Decimal = ONE,
    rate: Decimal = Decimal("0.07"),
) -> Decimal:
    """Kalshi's taker fee, unrounded.

    Rounding is deliberately omitted here: this is used to decide whether an
    edge survives, and the caller adds a buffer that dwarfs the rounding. The
    ledger does the exact arithmetic when a fill is actually recorded.
    """
    return rate * fee_multiplier * contracts * price * (ONE - price)


def find_monotonicity_violations(
    levels: Sequence[LadderLevel],
    *,
    buffer: Decimal,
    fee_multiplier: Decimal = ONE,
    max_contracts: Decimal = Decimal("25"),
) -> list[ArbOpportunity]:
    """Buy the lower strike, sell the higher one, when the book inverts.

    Compares every pair rather than only neighbours: an inversion can span
    several strikes when one contract is stale, and the widest crossing pair is
    the most profitable expression of the same violation.
    """
    ladder = sorted(
        (level for level in levels if level.tradeable), key=lambda item: item.strike
    )
    found: list[ArbOpportunity] = []
    for i, low in enumerate(ladder):
        for high in ladder[i + 1 :]:
            # Buying the lower strike costs its ask; selling the higher strike
            # earns its bid. Profitable only when the latter exceeds the former.
            gross = high.yes_bid - low.yes_ask
            if gross <= ZERO:
                continue
            size = min(low.ask_size, high.bid_size, max_contracts)
            if size <= ZERO:
                continue
            fees = quadratic_fee(ONE, low.yes_ask, fee_multiplier) + quadratic_fee(
                ONE, ONE - high.yes_bid, fee_multiplier
            )
            net = gross - fees - buffer
            if net <= ZERO:
                continue
            found.append(
                ArbOpportunity(
                    kind="monotonicity",
                    legs=(
                        ArbLeg(low.ticker, "buy", low.yes_ask, size),
                        ArbLeg(high.ticker, "sell", high.yes_bid, size),
                    ),
                    gross_edge=gross,
                    fees=fees,
                    net_edge=net,
                    size=size,
                    tickers=(low.ticker, high.ticker),
                    detail=(
                        f"P(>={low.strike}) buyable at {low.yes_ask:.2f} while "
                        f"P(>={high.strike}) sells at {high.yes_bid:.2f}"
                    ),
                )
            )
    return sorted(found, key=lambda item: item.net_edge, reverse=True)


def find_convexity_violations(
    levels: Sequence[LadderLevel],
    *,
    buffer: Decimal,
    fee_multiplier: Decimal = ONE,
    max_contracts: Decimal = Decimal("25"),
    spacing_tolerance: Decimal = Decimal("0.01"),
) -> list[ArbOpportunity]:
    """Detect a negative second difference across three equally spaced strikes.

    The butterfly buys the wings and sells two of the body. Only equally spaced
    triples are considered, because the convexity identity is only defined for
    them; unequal spacing needs a weighting the ladder does not give us.
    """
    ladder = sorted(
        (level for level in levels if level.tradeable), key=lambda item: item.strike
    )
    found: list[ArbOpportunity] = []
    for low, mid, high in zip(ladder, ladder[1:], ladder[2:]):
        lower_gap = mid.strike - low.strike
        upper_gap = high.strike - mid.strike
        if lower_gap <= ZERO or upper_gap <= ZERO:
            continue
        if abs(lower_gap - upper_gap) > spacing_tolerance * max(lower_gap, upper_gap):
            continue

        # Executable butterfly: buy both wings at their asks, sell two bodies at
        # the body's bid. Positive when the wings cost less than the bodies pay.
        gross = (Decimal("2") * mid.yes_bid) - low.yes_ask - high.yes_ask
        if gross <= ZERO:
            continue
        size = min(
            low.ask_size,
            high.ask_size,
            mid.bid_size / Decimal("2"),
            max_contracts,
        )
        if size <= ZERO:
            continue
        fees = (
            quadratic_fee(ONE, low.yes_ask, fee_multiplier)
            + quadratic_fee(ONE, high.yes_ask, fee_multiplier)
            + Decimal("2") * quadratic_fee(ONE, ONE - mid.yes_bid, fee_multiplier)
        )
        net = gross - fees - buffer
        if net <= ZERO:
            continue
        found.append(
            ArbOpportunity(
                kind="convexity",
                legs=(
                    ArbLeg(low.ticker, "buy", low.yes_ask, size),
                    ArbLeg(mid.ticker, "sell", mid.yes_bid, size * Decimal("2")),
                    ArbLeg(high.ticker, "buy", high.yes_ask, size),
                ),
                gross_edge=gross,
                fees=fees,
                net_edge=net,
                size=size,
                tickers=(low.ticker, mid.ticker, high.ticker),
                detail=(
                    f"butterfly {low.strike}/{mid.strike}/{high.strike}: "
                    f"{low.yes_ask:.2f} - 2x{mid.yes_bid:.2f} + {high.yes_ask:.2f}"
                ),
            )
        )
    return sorted(found, key=lambda item: item.net_edge, reverse=True)


def scan_ladder(
    levels: Sequence[LadderLevel],
    *,
    buffer: Decimal,
    fee_multiplier: Decimal = ONE,
    max_contracts: Decimal = Decimal("25"),
) -> list[ArbOpportunity]:
    """Every violation the ladder currently admits, best net edge first."""
    return sorted(
        find_monotonicity_violations(
            levels,
            buffer=buffer,
            fee_multiplier=fee_multiplier,
            max_contracts=max_contracts,
        )
        + find_convexity_violations(
            levels,
            buffer=buffer,
            fee_multiplier=fee_multiplier,
            max_contracts=max_contracts,
        ),
        key=lambda item: item.net_edge,
        reverse=True,
    )
