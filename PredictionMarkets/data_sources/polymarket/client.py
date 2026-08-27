"""Read-only Polymarket client: Gamma for discovery, CLOB for order books.

Polymarket splits its API in two, and the split matters here:

  * **Gamma** (``gamma-api.polymarket.com``) is the read-only discovery layer.
    No authentication, no keys.
  * **CLOB** (``clob.polymarket.com``) serves order books. Reading a book needs
    no authentication either; only *trading* does.

This module touches only the unauthenticated surface of both. It cannot place
an order even if asked to, which is what makes it safe to run beside a live
Kalshi bot.

Interval discovery is deliberately not hardcoded to 5/10/15 minutes. The slug
family is parsed out of whatever Gamma returns, so the collector records the
intervals that genuinely exist rather than the ones we assumed. As of writing,
live probing found ``{btc,eth,sol,xrp}-updown-5m-{unix}`` and no 10m or 15m
family — but that is an observation about today, not a constant.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# e.g. "btc-updown-5m-1766161800" -> asset=btc, interval=5, open_ts=1766161800
UPDOWN_SLUG = re.compile(
    r"^(?P<asset>[a-z0-9]+)-updown-(?P<interval>\d+)(?P<unit>[mh])-(?P<ts>\d+)$"
)

# Polymarket names its crypto assets by lowercase ticker; map to the symbols the
# rest of this repo uses so a Polymarket sample and a Kalshi sample can share a
# feature table.
ASSET_BY_SLUG = {
    "btc": "BTC",
    "eth": "ETH",
    "sol": "SOL",
    "xrp": "XRP",
    "doge": "DOGE",
    "hype": "HYPE",
}


def _dec(value: Any) -> Decimal | None:
    """Money as Decimal. Unparseable becomes None, never a silent zero."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True)
class PolymarketMarket:
    """One up/down market, normalised toward the shape KalshiMarket uses."""

    slug: str
    asset: str
    interval_minutes: int
    open_time: datetime
    close_time: datetime
    question: str
    condition_id: str
    token_ids: tuple[str, ...]
    outcomes: tuple[str, ...]
    best_bid: Decimal | None
    best_ask: Decimal | None
    volume: Decimal | None
    closed: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def up_token_id(self) -> str | None:
        """Token whose payout is 1 if the interval closed up.

        Gamma lists ``outcomes`` and ``clobTokenIds`` positionally, so the Up
        token is whichever index carries the "Up" label rather than index 0 by
        assumption.
        """
        for index, outcome in enumerate(self.outcomes):
            if outcome.strip().lower() in {"up", "yes"}:
                if index < len(self.token_ids):
                    return self.token_ids[index]
        return None

    @property
    def implied_up_probability(self) -> Decimal | None:
        """Mid of the top of book, or whichever side exists."""
        quotes = [q for q in (self.best_bid, self.best_ask) if q is not None and q > 0]
        if not quotes:
            return None
        return sum(quotes, Decimal("0")) / Decimal(len(quotes))

    def seconds_to_close(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (self.close_time - now).total_seconds()


@dataclass(frozen=True)
class PolymarketBookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class PolymarketBook:
    """A CLOB order book for one outcome token.

    Unlike Kalshi, Polymarket returns genuine two-sided books: ``bids`` and
    ``asks`` are both present and quoted in that token's own price terms, so no
    ``1 - p`` mapping is needed.
    """

    token_id: str
    bids: tuple[PolymarketBookLevel, ...]
    asks: tuple[PolymarketBookLevel, ...]
    fetched_at: datetime

    @property
    def best_bid(self) -> Decimal | None:
        return max((level.price for level in self.bids), default=None)

    @property
    def best_ask(self) -> Decimal | None:
        return min((level.price for level in self.asks), default=None)

    @property
    def mid(self) -> Decimal | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / Decimal(2)

    def depth_to(self, limit_price: Decimal) -> Decimal:
        """Size available buying up to and including ``limit_price``."""
        return sum(
            (level.size for level in self.asks if level.price <= limit_price),
            Decimal("0"),
        )


def parse_updown_slug(slug: str) -> tuple[str, int, datetime] | None:
    """Extract (asset, interval_minutes, open_time) from an up/down slug.

    Returns None for anything that is not an up/down market, which is how the
    discovery path filters Gamma's mixed feed without a hardcoded asset list.
    """
    match = UPDOWN_SLUG.match(slug or "")
    if not match:
        return None
    asset_key = match.group("asset")
    asset = ASSET_BY_SLUG.get(asset_key, asset_key.upper())
    interval = int(match.group("interval"))
    if match.group("unit") == "h":
        interval *= 60
    if interval <= 0:
        return None
    try:
        open_time = datetime.fromtimestamp(int(match.group("ts")), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None
    return (asset, interval, open_time)


def _parse_json_list(value: Any) -> list[Any]:
    """Gamma returns some list fields as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        import json

        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def market_from_gamma(payload: dict[str, Any]) -> PolymarketMarket | None:
    """Normalise one Gamma market record; None if it is not an up/down market."""
    parsed = parse_updown_slug(payload.get("slug", ""))
    if parsed is None:
        return None
    asset, interval, open_time = parsed

    end_raw = payload.get("endDate") or payload.get("end_date_iso")
    close_time: datetime | None = None
    if end_raw:
        try:
            close_time = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
        except ValueError:
            close_time = None
    if close_time is None:
        # The slug's timestamp is the open; the close is one interval later.
        close_time = open_time.fromtimestamp(
            open_time.timestamp() + interval * 60, tz=timezone.utc
        )
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=timezone.utc)

    return PolymarketMarket(
        slug=payload["slug"],
        asset=asset,
        interval_minutes=interval,
        open_time=open_time,
        close_time=close_time,
        question=str(payload.get("question") or ""),
        condition_id=str(payload.get("conditionId") or ""),
        token_ids=tuple(str(t) for t in _parse_json_list(payload.get("clobTokenIds"))),
        outcomes=tuple(str(o) for o in _parse_json_list(payload.get("outcomes"))),
        best_bid=_dec(payload.get("bestBid")),
        best_ask=_dec(payload.get("bestAsk")),
        volume=_dec(payload.get("volumeNum") or payload.get("volume")),
        closed=bool(payload.get("closed")),
        raw=payload,
    )


class PolymarketReadOnlyClient:
    """Unauthenticated Gamma + CLOB reads. No order path exists on this class."""

    def __init__(
        self,
        *,
        gamma_base: str = GAMMA_BASE,
        clob_base: str = CLOB_BASE,
        timeout_seconds: float = 15.0,
        concurrency: int = 4,
    ) -> None:
        headers = {"User-Agent": "KalshiCrypto15mBot/3.0 (read-only research)"}
        self._gamma = httpx.AsyncClient(
            base_url=gamma_base, timeout=timeout_seconds, headers=headers
        )
        self._clob = httpx.AsyncClient(
            base_url=clob_base, timeout=timeout_seconds, headers=headers
        )
        self._gate = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._gamma.aclose()
        await self._clob.aclose()

    async def __aenter__(self) -> "PolymarketReadOnlyClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def fetch_markets(
        self,
        *,
        limit: int = 100,
        closed: bool | None = False,
        order: str = "startDate",
        ascending: bool = False,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Raw Gamma market records, newest-created first.

        The default ordering is load-bearing. ``closed=false`` is not reliable —
        the feed carries long-dead markets that were never flagged closed — so
        ordering by ``endDate`` ascending returns pages of stale 2025 markets
        and finds nothing live. Ordering by ``startDate`` descending returns the
        most recently *created* markets, which for a continuously regenerated
        5-minute series is exactly the set that is currently open.

        Query parameters are otherwise kept minimal: Gamma's WAF answers 403 to
        some filter combinations (``end_date_min``, ``tag_slug``), so filtering
        happens client-side rather than through predicates that may be rejected.
        """
        params: dict[str, Any] = {
            "limit": max(1, min(limit, 100)),
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if closed is not None:
            params["closed"] = str(closed).lower()
        async with self._gate:
            response = await self._gamma.get("/markets", params=params)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def discover_updown_markets(
        self,
        *,
        assets: Sequence[str] | None = None,
        intervals: Sequence[int] | None = None,
        pages: int = 3,
        now: datetime | None = None,
        only_open: bool = True,
    ) -> list[PolymarketMarket]:
        """Find short-interval up/down markets, filtered client-side.

        ``intervals`` is an allow-list in minutes; leave it None to accept every
        interval Polymarket happens to run. Passing [5, 10, 15] will silently
        return nothing for an interval that does not exist, which is the honest
        outcome rather than an error.
        """
        now = now or datetime.now(timezone.utc)
        wanted_assets = {a.upper() for a in assets} if assets else None
        wanted_intervals = set(intervals) if intervals else None

        markets: list[PolymarketMarket] = []
        seen: set[str] = set()
        for page in range(max(1, pages)):
            try:
                records = await self.fetch_markets(offset=page * 100)
            except httpx.HTTPError:
                break
            if not records:
                break
            for record in records:
                market = market_from_gamma(record)
                if market is None or market.slug in seen:
                    continue
                seen.add(market.slug)
                if wanted_assets and market.asset not in wanted_assets:
                    continue
                if wanted_intervals and market.interval_minutes not in wanted_intervals:
                    continue
                if only_open and (market.closed or market.close_time <= now):
                    continue
                markets.append(market)
        markets.sort(key=lambda m: m.close_time)
        return markets

    async def fetch_book(self, token_id: str) -> PolymarketBook | None:
        """Read one CLOB order book. Unauthenticated."""
        async with self._gate:
            try:
                response = await self._clob.get("/book", params={"token_id": token_id})
                response.raise_for_status()
            except httpx.HTTPError:
                return None
        payload = response.json()
        if not isinstance(payload, dict):
            return None

        def levels(raw: Any) -> tuple[PolymarketBookLevel, ...]:
            out: list[PolymarketBookLevel] = []
            for entry in raw or []:
                if isinstance(entry, dict):
                    price, size = _dec(entry.get("price")), _dec(entry.get("size"))
                elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    price, size = _dec(entry[0]), _dec(entry[1])
                else:
                    continue
                if price is not None and size is not None and size > 0:
                    out.append(PolymarketBookLevel(price=price, size=size))
            return tuple(out)

        return PolymarketBook(
            token_id=token_id,
            bids=levels(payload.get("bids")),
            asks=levels(payload.get("asks")),
            fetched_at=datetime.now(timezone.utc),
        )

    async def fetch_books(
        self, token_ids: Iterable[str]
    ) -> dict[str, PolymarketBook | None]:
        """Read several books concurrently, bounded by the semaphore."""
        ids = [t for t in token_ids if t]
        results = await asyncio.gather(
            *(self.fetch_book(token_id) for token_id in ids),
            return_exceptions=True,
        )
        return {
            token_id: (None if isinstance(result, BaseException) else result)
            for token_id, result in zip(ids, results)
        }
