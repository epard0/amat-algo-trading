"""Venue adapters. Each one normalises into ``base``; none of them can trade.

An adapter's whole job is to own its venue's conventions so nothing downstream
has to. The two that exist wrap clients this repo already has - the Kalshi
trading client, in a read-only capacity, and the Polymarket read-only client -
rather than opening a second connection to either.

The stubs below are deliberately stubs. `NotImplementedError` with a reason is
an honest description of a venue nobody has integrated; a class that returns
`[]` looks like a venue with no markets open, and this project has shipped five
features that were correct and silently did nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Sequence

from data_sources.venues.base import VenueBook, VenueLevel, VenueMarket

ONE = Decimal("1")


class KalshiVenue:
    """Read-only view of the Kalshi client the bot already holds.

    It takes an existing ``AsyncKalshiTradingClient`` rather than building one:
    a second authenticated client would double this account's request rate
    against the same API key, and the bot's own transport already carries the
    layered retry and idempotent-reconciliation logic.

    ``close()`` is a no-op **on purpose**. The bot owns the client's lifetime,
    and an adapter closing a session the trading loop is still using is exactly
    the ``RuntimeError: Session is closed`` this repo has already debugged once.
    """

    name = "kalshi"

    def __init__(self, client: Any, *, settlement_source: str = "CF Benchmarks") -> None:
        self.client = client
        self.settlement_source = settlement_source

    async def discover_markets(
        self, assets: Sequence[str] | None = None
    ) -> list[VenueMarket]:
        markets = await self.client.get_active_markets()
        wanted = {a.upper() for a in assets} if assets else None
        out: list[VenueMarket] = []
        for market in markets:
            if wanted and market.asset.upper() not in wanted:
                continue
            interval = max(
                1,
                round((market.close_time - market.open_time).total_seconds() / 60),
            )
            out.append(
                VenueMarket(
                    venue=self.name,
                    market_id=market.ticker,
                    asset=market.asset,
                    interval_minutes=interval,
                    open_time=market.open_time,
                    close_time=market.close_time,
                    strike=market.target_price,
                    settlement_source=self.settlement_source,
                    yes_bid=market.yes_bid or None,
                    yes_ask=market.yes_ask or None,
                    raw={"fee_multiplier": str(market.fee_multiplier)},
                )
            )
        return out

    async def get_book(self, market: VenueMarket, outcome: str) -> VenueBook | None:
        """Normalise Kalshi's single YES book into one book per outcome.

        Kalshi publishes resting **bids only**, on both sides: buying YES
        crosses the NO bids and buying NO crosses the YES bids. So for the NO
        outcome the prices are ``1 - p`` of the YES side. That mapping lives
        here and nowhere else - introducing a second NO book downstream is the
        thing ``CLAUDE.md`` warns against.
        """
        analysis = await self.client.get_book_analysis(market.market_id)
        if analysis is None:
            return None
        if outcome == "yes":
            bid, ask = analysis.yes_bid, analysis.yes_ask
            bid_size = getattr(analysis, "yes_bid_size", None)
        else:
            bid, ask = ONE - analysis.yes_ask, ONE - analysis.yes_bid
            bid_size = getattr(analysis, "no_bid_size", None)
        if bid is None or ask is None:
            return None
        return VenueBook(
            venue=self.name,
            market_id=market.market_id,
            outcome=outcome,
            bids=(VenueLevel(bid, bid_size or Decimal("0")),),
            asks=(VenueLevel(ask, Decimal("0")),),
            fetched_at=datetime.now(timezone.utc),
        )

    async def close(self) -> None:
        """No-op: the bot owns this client's lifetime, not the adapter."""
        return None


class PolymarketVenue:
    """Read-only Polymarket adapter over ``data_sources.polymarket``.

    Polymarket returns genuine two-sided books per outcome token, so no
    ``1 - p`` mapping is needed - which is precisely why the mapping belongs in
    each adapter rather than in shared code.
    """

    name = "polymarket"

    def __init__(self, client: Any) -> None:
        self.client = client

    async def discover_markets(
        self, assets: Sequence[str] | None = None
    ) -> list[VenueMarket]:
        found = await self.client.discover_updown_markets(assets=assets)
        out: list[VenueMarket] = []
        for market in found:
            out.append(
                VenueMarket(
                    venue=self.name,
                    market_id=market.slug,
                    asset=market.asset,
                    interval_minutes=market.interval_minutes,
                    open_time=market.open_time,
                    close_time=market.close_time,
                    # Polymarket's up/down slugs do not publish a strike the way
                    # a Kalshi 15-minute series does, and the settlement source
                    # is not the same index. Left as None rather than guessed:
                    # a strike inferred here would be compared against Kalshi's
                    # CF Benchmarks strike as though the two were the same
                    # number.
                    strike=None,
                    settlement_source=None,
                    yes_bid=market.best_bid,
                    yes_ask=market.best_ask,
                    outcomes=market.outcomes or ("up", "down"),
                    raw={"condition_id": market.condition_id},
                )
            )
        return out

    async def get_book(self, market: VenueMarket, outcome: str) -> VenueBook | None:
        token = market.raw.get(f"{outcome}_token_id") or market.raw.get("up_token_id")
        if not token:
            return None
        book = await self.client.fetch_book(token)
        if book is None:
            return None
        return VenueBook(
            venue=self.name,
            market_id=market.market_id,
            outcome=outcome,
            bids=tuple(VenueLevel(lv.price, lv.size) for lv in book.bids),
            asks=tuple(VenueLevel(lv.price, lv.size) for lv in book.asks),
            fetched_at=book.fetched_at,
        )

    async def close(self) -> None:
        await self.client.close()


class UnimplementedVenue:
    """A venue that is catalogued but not integrated.

    It raises rather than returning nothing. A stub that answers "no markets"
    is indistinguishable from a venue having a quiet hour, and this repo has
    shipped five features that were correct and unreachable; the sixth was
    found earlier in this same session.
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    async def discover_markets(
        self, assets: Sequence[str] | None = None
    ) -> list[VenueMarket]:
        raise NotImplementedError(f"{self.name}: {self.reason}")

    async def get_book(self, market: VenueMarket, outcome: str) -> VenueBook | None:
        raise NotImplementedError(f"{self.name}: {self.reason}")

    async def close(self) -> None:
        return None
