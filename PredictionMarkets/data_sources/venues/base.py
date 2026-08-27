"""One shape for every prediction venue, and one place the differences live.

The bot prices, gates and sizes against `KalshiMarket`. Adding a second venue
by teaching every one of those paths a second market shape is how a trading
system acquires two subtly different definitions of "the price" - so instead
each venue adapter normalises *into* this module, and nothing downstream learns
a venue name.

Three rules hold for everything in this package:

- **Read-only.** Nothing here can place an order, hold funds, or sign a
  transaction. `data_sources/polymarket/` already establishes that precedent
  and says why: an order path makes a venue a live-money venue with its own
  risk gates, circuit breakers and settlement handling, and that is a separate
  decision from being able to read it.
- **Absent is absent.** A missing bid is `None`, never zero and never the last
  known value. This session's backtest measured a discovery snapshot 0.10-0.13
  away from the price actually payable and reported a strategy as profitable
  that was not; the same class of mistake made at the venue boundary would be
  invisible for much longer.
- **Money is `Decimal`.** As everywhere else in this repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol, Sequence, runtime_checkable

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class VenueLevel:
    """One resting price level."""

    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class VenueBook:
    """A two-sided book for ONE outcome, in that outcome's own price terms.

    Normalised deliberately. Kalshi publishes a single YES book and expresses
    NO as `1 - yes_bid`; Polymarket publishes a genuine book per outcome token.
    Adapters resolve that here so no caller has to know which convention it is
    looking at - the mapping stays inside the adapter that owns it.
    """

    venue: str
    market_id: str
    outcome: str
    bids: tuple[VenueLevel, ...] = ()
    asks: tuple[VenueLevel, ...] = ()
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def best_bid(self) -> Decimal | None:
        return max((level.price for level in self.bids), default=None)

    @property
    def best_ask(self) -> Decimal | None:
        return min((level.price for level in self.asks), default=None)

    @property
    def mid(self) -> Decimal | None:
        """Midpoint, or None. A one-sided book has no midpoint.

        Returning the single side, or 0.5, would manufacture a price nobody
        quoted - and a manufactured price is indistinguishable from a real one
        by the time it reaches a sizing rule.
        """
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / Decimal(2)

    def depth_to(self, limit_price: Decimal) -> Decimal:
        """Size buyable at or below ``limit_price``."""
        return sum(
            (level.size for level in self.asks if level.price <= limit_price),
            ZERO,
        )


@dataclass(frozen=True)
class VenueMarket:
    """One short-interval up/down market, whatever venue it came from.

    ``strike`` and ``settlement_source`` are carried because they are what make
    two venues' contracts comparable *or not*. Kalshi settles its 15-minute
    crypto series on a CF Benchmarks 60-second average; a venue settling on a
    different index is a different instrument even when the question reads the
    same, and the divergence gate this repo already runs exists precisely
    because a price feed and a settlement index can disagree.
    """

    venue: str
    market_id: str
    asset: str
    interval_minutes: int
    open_time: datetime
    close_time: datetime
    strike: Decimal | None = None
    settlement_source: str | None = None
    yes_bid: Decimal | None = None
    yes_ask: Decimal | None = None
    outcomes: tuple[str, ...] = ("yes", "no")
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def seconds_to_close(self, now: datetime | None = None) -> float:
        return (self.close_time - (now or datetime.now(timezone.utc))).total_seconds()

    @property
    def implied_probability_yes(self) -> Decimal | None:
        """Mid of the top of book, or None when the book is not two-sided."""
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / Decimal(2)


@runtime_checkable
class PredictionVenue(Protocol):
    """What every venue adapter must provide. Reads only.

    There is deliberately no ``place_order`` in this protocol. Adding one is
    not a matter of extending an interface: it is the decision to make a second
    venue a live-money venue, and it would need that venue's own fee model,
    settlement handling, duplicate protection and circuit breakers before a
    single contract could safely be bought.
    """

    name: str

    async def discover_markets(
        self, assets: Sequence[str] | None = None
    ) -> list[VenueMarket]:
        """Open short-interval up/down markets, normalised."""
        ...

    async def get_book(self, market: VenueMarket, outcome: str) -> VenueBook | None:
        """The live book for one outcome, or None when unreadable.

        None means unreadable, and callers must treat it as such. An empty book
        and an unreachable venue are not the same fact.
        """
        ...

    async def close(self) -> None:
        """Release transport."""
        ...
