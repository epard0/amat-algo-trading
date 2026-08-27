"""The resting-order state machine for maker-side scalping.

The existing order path is one shot: send an IOC, get a fill or don't. A maker
order has a life instead - it rests, ages, may be overtaken by the book, may
fill partially, and must be withdrawn when the reason for it evaporates. That
lifetime is what this module owns.

**Paper fills are modelled pessimistically, on purpose.** A paper engine that
fills a resting order whenever the market trades *at* our price would flatter
this strategy badly, because in reality we join the back of a queue that may
never be exhausted. Here a quote fills only when a later observation shows the
offer at or below our bid - that is, somebody was willing to sell *through* us.
That understates fills. Understating them is the right direction to be wrong in:
the alternative is a paper record that recommends going live on an edge that was
never there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


ZERO = Decimal("0")
CENT = Decimal("0.01")


class QuoteState(str, Enum):
    RESTING = "resting"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class RestingQuote:
    """A maker order that is live on the book (or simulated to be)."""

    ticker: str
    asset: str
    outcome: str
    price: Decimal
    contracts: Decimal
    placed_at: datetime
    strategy: str = "scalper"
    order_id: str = ""
    client_order_id: str = ""
    state: QuoteState = QuoteState.RESTING
    filled_contracts: Decimal = ZERO
    # The book as it stood when we quoted, so drift is measured from the right
    # baseline rather than from whatever the book happens to be now.
    bid_at_placement: Decimal = ZERO
    reservation_key: str = ""
    # What actually sits on the YES book, and on which side. For a NO position
    # these differ from ``price``/"buy": Kalshi runs one YES book, so buying NO
    # at 0.90 is resting a sell at 0.10.
    yes_leg_price: Decimal = ZERO
    book_side: str = "bid"
    exchange_index: int = 0
    # Contracts that must be matched ahead of this order before it fills, on
    # price-time priority. In live mode this is Kalshi's own answer from
    # `GET /portfolio/orders/queue_positions`; on paper it is estimated from
    # the size already resting at our price when we joined, which is the same
    # quantity a real queue position measures.
    queue_ahead: Decimal | None = None
    queue_ahead_at_placement: Decimal | None = None
    context: dict = field(default_factory=dict)

    @property
    def is_resting(self) -> bool:
        return self.state is QuoteState.RESTING

    def age_seconds(self, now: datetime) -> float:
        return (now - self.placed_at).total_seconds()


@dataclass(frozen=True)
class QuoteOutcome:
    """What one cycle's evaluation decided about a resting quote."""

    quote: RestingQuote
    # "hold" | "fill" | "expire" | "cancel" (we no longer want it) | "requote"
    # (we still want it, the book moved away). The last two are both withdrawals
    # but they mean opposite things about intent, and only "cancel" says the
    # trade itself went bad.
    action: str
    reason: str = ""
    fill_price: Decimal | None = None
    fill_contracts: Decimal = ZERO


def queue_cleared(quote: RestingQuote) -> bool:
    """Whether everything ahead of this order has been matched away.

    A real fill has two causes and the pessimistic rule only models one. The
    book trading *through* our price is the obvious one. The other is that we
    sat at the touch and the queue in front of us was consumed - which is the
    normal way a maker order fills, and the case the through-rule scores as
    nothing happening.

    In live mode the input is Kalshi's own queue position, which counts only
    matches. On paper it is inferred from resting size, which also falls when
    someone *cancels* ahead of us - so the paper form is optimistic in a way
    the live form is not. That asymmetry is why acting on this is opt-in and
    why both are recorded either way.
    """
    if quote.queue_ahead is None:
        return False
    return quote.queue_ahead <= ZERO


def simulate_maker_fill(
    quote: RestingQuote,
    *,
    yes_bid: Decimal,
    yes_ask: Decimal,
    queue_aware: bool = False,
) -> Decimal:
    """Contracts a resting order would have filled, on the pessimistic rule.

    A fill is credited only when the book has traded *through* our resting
    price, never merely *at* it. Sitting at the touch while the market trades
    elsewhere fills nothing, which is the honest model of joining a queue we
    cannot see.

    The test is mirrored for the two sides, and the mirroring is the whole of
    it: a resting bid needs the offer to fall to it, and a resting ask needs the
    bid to rise to it. ``yes_leg_price`` is used rather than ``price`` because
    both comparisons happen on the YES book, which is the only book there is.
    """
    if not quote.is_resting or quote.contracts <= ZERO:
        return ZERO
    remaining = quote.contracts - quote.filled_contracts
    if remaining <= ZERO:
        return ZERO

    if quote.book_side == "ask":
        # We are offering YES (economically buying NO). A buyer must lift us.
        if yes_bid > ZERO and yes_bid >= quote.yes_leg_price:
            return remaining
    # We are bidding for YES. A seller must hit us.
    elif yes_ask > ZERO and yes_ask <= quote.yes_leg_price:
        return remaining

    # Only then, and only when asked for: the queue in front of us is gone.
    # Checked second so that turning it off restores the through-rule exactly.
    if queue_aware and queue_cleared(quote):
        return remaining
    return ZERO


def evaluate_quote(
    quote: RestingQuote,
    *,
    now: datetime,
    yes_bid: Decimal,
    yes_ask: Decimal,
    edge_survives: bool,
    ttl_seconds: float,
    requote_ticks: int,
    tick: Decimal = CENT,
    paper: bool = True,
    queue_aware: bool = False,
) -> QuoteOutcome:
    """Decide the fate of one resting quote this cycle.

    Order matters. A fill is checked before cancellation, because an order that
    filled before we decided to pull it *did* fill - the venue does not honour
    intentions formed after the fact, and a paper engine that let a cancel
    retroactively erase a fill would understate exactly the adverse selection
    this strategy most needs to measure.
    """
    if paper:
        filled = simulate_maker_fill(
            quote, yes_bid=yes_bid, yes_ask=yes_ask, queue_aware=queue_aware
        )
        if filled > ZERO:
            through = simulate_maker_fill(
                quote, yes_bid=yes_bid, yes_ask=yes_ask, queue_aware=False
            )
            why = (
                f"offer fell to {yes_ask:.2f}, through our resting {quote.price:.2f}"
                if through > ZERO
                else f"queue ahead cleared at {quote.price:.2f}"
            )
            return QuoteOutcome(
                quote,
                "fill",
                why,
                fill_price=quote.price,
                fill_contracts=filled,
            )

    if not edge_survives:
        return QuoteOutcome(quote, "cancel", "edge no longer survives at this price")

    if quote.bid_at_placement > ZERO and tick > ZERO:
        drift = abs(yes_bid - quote.bid_at_placement) / tick
        if drift >= Decimal(requote_ticks):
            return QuoteOutcome(
                quote,
                "requote",
                f"book moved {drift:.0f} ticks from the {quote.bid_at_placement:.2f} "
                "we quoted against",
            )

    if quote.age_seconds(now) >= ttl_seconds:
        return QuoteOutcome(
            quote, "expire", f"quote reached its {ttl_seconds:.0f}s lifetime"
        )

    return QuoteOutcome(quote, "hold")


class QuoteBook:
    """Every resting quote this process owns, keyed by ticker.

    One quote per ticker per strategy. Stacking several prices on one market is
    a different strategy - it is inventory-based market making, which needs a
    two-sided model and an inventory limit this design does not have.
    """

    def __init__(self) -> None:
        self._quotes: dict[tuple[str, str], RestingQuote] = {}

    def __len__(self) -> int:
        return len(self._quotes)

    def all(self) -> list[RestingQuote]:
        return list(self._quotes.values())

    def get(self, strategy: str, ticker: str) -> RestingQuote | None:
        return self._quotes.get((strategy, ticker))

    def has(self, strategy: str, ticker: str) -> bool:
        return (strategy, ticker) in self._quotes

    def add(self, quote: RestingQuote) -> None:
        self._quotes[(quote.strategy, quote.ticker)] = quote

    def remove(self, quote: RestingQuote) -> None:
        self._quotes.pop((quote.strategy, quote.ticker), None)

    def tickers(self) -> set[str]:
        return {ticker for _, ticker in self._quotes}

    def drain(self) -> list[RestingQuote]:
        """Take every quote out of the book, for shutdown or a mode switch."""
        quotes = list(self._quotes.values())
        self._quotes.clear()
        return quotes
