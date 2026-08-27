"""The salvage exit: sell a deeply-losing position, on price evidence alone.

This is **not** the exit-on-reversal that was removed, and the difference is the
whole justification. That rule asked our own model whether the position had gone
bad, which meant it only paid when the model beat the market — and it does not.
It sold a live BTC position at 0.61 for -$1.73 on a market that then settled in
that position's favour.

This rule never consults the model. It asks one question about the *price*:

    is the book still paying more for this position than the position is worth?

Under fair pricing the answer is always no, by exactly the fee plus half the
spread — which is why cutting a position near a fair price is a guaranteed loss
and why the old exit destroyed money. But prediction-market prices are not fair
at the extremes. Bürgi, Deng & Whelan (2026), *Makers or Takers* (GWU CER WP
2026-001), measure contracts under 10c returning **worse than -60%** over
313,972 contracts: a 5c contract is worth nearer 2c. Deep in that zone the book
is overpaying, and selling into it is not a bet against the market but the
market's own documented bias, collected.

So, per contract::

    p_true  ~=  mid * (1 - haircut)
    gain    =   (bid - exit_fee) - p_true

The haircut is the estimate the whole rule rests on, so it is a configured
number rather than a buried constant, and its default (0.40) is deliberately
**weaker** than the >=0.60 that was measured. Two caveats travel with that
figure and must not be dropped: the paper's sample is 24-hour-to-10-day markets
rather than 15-minute crypto, and the bias is only measured at the extremes.
That second caveat is why a price ceiling gates this independently of the
arithmetic — above roughly 0.20 there is no measured overpricing to collect, the
formula would still produce a positive number from the haircut alone, and acting
on it would be the reversal exit wearing different clothes.

Everything here is side-effect free: no clock, no network, no ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")
ONE = Decimal("1")
TWO = Decimal("2")


@dataclass(frozen=True)
class OutcomeBook:
    """The book as seen from the outcome we are holding.

    Kalshi publishes one YES book as resting bids on both sides, so what the
    holder of NO can sell at is ``1 - yes_ask``, and the order that does it is a
    *buy* on the YES book. Deriving that once, here, keeps the mapping out of
    the runtime and makes it testable without a socket.
    """

    outcome: str
    bid: Decimal
    ask: Decimal
    depth: Decimal
    yes_leg_price: Decimal
    book_side: str

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / TWO


@dataclass(frozen=True)
class SalvageDecision:
    """Whether to sell, and the full arithmetic behind either answer."""

    should_exit: bool
    gate: str
    reason: str
    bid: Decimal = ZERO
    spread: Decimal = ZERO
    fair_value: Decimal = ZERO
    exit_fee: Decimal = ZERO
    gain_per_contract: Decimal = ZERO

    @property
    def blocked_by(self) -> str:
        return "" if self.should_exit else self.gate


def outcome_book(
    *,
    outcome: str,
    yes_bid: Decimal,
    yes_ask: Decimal,
    yes_bid_size: Decimal,
    no_bid_size: Decimal,
) -> OutcomeBook | None:
    """Re-express one YES book from the perspective of the outcome held.

    Selling YES hits the YES bids: ``side="ask"`` at ``yes_bid``.
    Selling NO hits the NO bids, and a NO bid at ``q`` IS a YES offer at
    ``1 - q``, so the order is ``side="bid"`` at ``yes_ask``.

    In both cases the order is on the opposite side of the book from the one
    that opened the position, which — together with ``reduce_only`` on the wire
    — is what makes it structurally impossible for this path to flip a side or
    open a short.
    """
    if outcome not in {"yes", "no"}:
        return None
    if yes_bid <= ZERO or yes_ask <= ZERO or yes_ask <= yes_bid:
        return None

    if outcome == "yes":
        return OutcomeBook(
            outcome="yes",
            bid=yes_bid,
            ask=yes_ask,
            depth=yes_bid_size,
            yes_leg_price=yes_bid,
            book_side="ask",
        )
    return OutcomeBook(
        outcome="no",
        bid=ONE - yes_ask,
        ask=ONE - yes_bid,
        depth=no_bid_size,
        yes_leg_price=yes_ask,
        book_side="bid",
    )


def fair_value(mid: Decimal, haircut: Decimal) -> Decimal:
    """What a longshot contract is really worth, given the measured bias.

    Anchored on the mid rather than the bid: the mid is the better estimate of
    where the market thinks value is, and using the bid would flatter the
    comparison by half a spread on both sides of the subtraction.
    """
    if mid <= ZERO:
        return ZERO
    return mid * (ONE - haircut)


def decide_salvage(
    *,
    book: OutcomeBook | None,
    contracts: Decimal,
    entry_price: Decimal,
    exit_fee_per_contract: Decimal,
    max_bid: Decimal,
    max_spread: Decimal,
    haircut: Decimal,
    min_gain_per_contract: Decimal,
) -> SalvageDecision:
    """Whether selling this position into the book beats carrying it.

    Every refusal is attributed to a named gate, because a rule that fires
    rarely by design is only auditable if it says why it declined. Five features
    in this project have shipped unreachable; the ones that were caught were
    caught by exactly this kind of counter.
    """
    if book is None:
        return SalvageDecision(False, "book", "no usable two-sided book")

    bid, spread, mid = book.bid, book.spread, book.mid

    # A loss-reduction rule, and only that. Selling a position the book has
    # revalued *upward* is a different trade with a different justification,
    # and it is not the one anybody asked for.
    if bid >= entry_price:
        return SalvageDecision(
            False,
            "losing",
            f"bid {bid:.2f} is not below the entry price {entry_price:.2f}; "
            "this exit reduces losses, it does not harvest winners",
            bid=bid,
            spread=spread,
        )

    # The ceiling is the rule. Above it there is no measured overpricing to
    # collect, and the haircut alone would still manufacture a positive number.
    if bid > max_bid:
        return SalvageDecision(
            False,
            "band",
            f"bid {bid:.2f} is above the longshot ceiling {max_bid:.2f}; the "
            "favourite-longshot bias is not measured there, so selling would "
            "cost fee plus spread for nothing",
            bid=bid,
            spread=spread,
        )

    if spread > max_spread:
        return SalvageDecision(
            False,
            "spread",
            f"spread {spread:.2f} exceeds {max_spread:.2f}; the exit is paid "
            "out of the spread and does not survive a wide one",
            bid=bid,
            spread=spread,
        )

    # Full exit or none. record_exit values any unsold remainder at the exit
    # price, so a partial fill would write a number into the ledger that no
    # trade produced.
    if book.depth < contracts:
        return SalvageDecision(
            False,
            "depth",
            f"only {book.depth} resting at {bid:.2f} against {contracts} held; "
            "a partial exit would mis-value the remainder",
            bid=bid,
            spread=spread,
        )

    value = fair_value(mid, haircut)
    gain = (bid - exit_fee_per_contract) - value
    if gain < min_gain_per_contract:
        return SalvageDecision(
            False,
            "margin",
            f"selling at {bid:.2f} nets {gain:.4f}/contract against a fair "
            f"value of {value:.4f}, below the {min_gain_per_contract:.4f} "
            "required to act",
            bid=bid,
            spread=spread,
            fair_value=value,
            exit_fee=exit_fee_per_contract,
            gain_per_contract=gain,
        )

    return SalvageDecision(
        True,
        "",
        f"book pays {bid:.2f} for a contract worth about {value:.4f}; selling "
        f"{contracts} nets {gain:.4f}/contract after fees",
        bid=bid,
        spread=spread,
        fair_value=value,
        exit_fee=exit_fee_per_contract,
        gain_per_contract=gain,
    )
