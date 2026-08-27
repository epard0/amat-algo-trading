"""Record Polymarket up/down markets to build a labelled dataset.

This is the point of the whole package. The Kalshi ledger holds 17 settled
trades over three days, which is far too little to fit anything — the genetic
programming plan makes the same point at length. Polymarket runs a 5-minute
up/down market on four assets continuously and lets anyone read it without
authentication, so observing it produces on the order of a thousand settled
outcomes per asset per day at zero risk and zero capital.

Two record types are written to an append-only JSONL:

  ``snapshot``   the book and the model's own features at a point in time
  ``settlement`` the outcome, once the market has resolved

Joined on ``slug``, those give a supervised training row whose label came from
a real exchange rather than from reconstructing settlement off a spot feed —
which is exactly the weakness the GP plan flags in Kraken-derived labels.

The collector never writes to the bot's ledger or decision log, and never
imports from ``bot.py``. It is a separate process by construction.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from data_sources.polymarket.client import (
    PolymarketMarket,
    PolymarketReadOnlyClient,
)

SCHEMA_VERSION = 1


def _encode(value: Any) -> Any:
    """Decimals as strings and datetimes as ISO-8601, matching decision_log."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


@dataclass
class CollectorStats:
    snapshots: int = 0
    settlements: int = 0
    errors: int = 0
    cycles: int = 0


class PolymarketCollector:
    """Polls open markets, snapshots their books, and records settlements."""

    def __init__(
        self,
        output_path: Path,
        *,
        client: PolymarketReadOnlyClient | None = None,
        assets: Sequence[str] | None = None,
        intervals: Sequence[int] | None = None,
    ) -> None:
        self.output_path = Path(output_path)
        self.client = client or PolymarketReadOnlyClient()
        self._owns_client = client is None
        self.assets = assets
        self.intervals = intervals
        self.stats = CollectorStats()
        # Markets seen open, awaiting a settlement record. slug -> market.
        self._pending: dict[str, PolymarketMarket] = {}
        self._settled: set[str] = set()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()

    def _write(self, record: dict[str, Any]) -> None:
        """Append one record. Write errors are swallowed, like decision_log.

        A data collector must never take down the process it runs beside, and
        losing a sample is not worth an exception.
        """
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_encode(record), separators=(",", ":")) + "\n")
        except OSError:
            self.stats.errors += 1

    async def snapshot_once(self, now: datetime | None = None) -> int:
        """Snapshot every open market's book. Returns the number written."""
        now = now or datetime.now(timezone.utc)
        self.stats.cycles += 1
        try:
            markets = await self.client.discover_updown_markets(
                assets=self.assets, intervals=self.intervals, now=now
            )
        except Exception:  # noqa: BLE001 - collection must not raise
            self.stats.errors += 1
            return 0

        written = 0
        for market in markets:
            self._pending.setdefault(market.slug, market)
            token_id = market.up_token_id
            book = await self.client.fetch_book(token_id) if token_id else None
            self._write(
                {
                    "schema_version": SCHEMA_VERSION,
                    "record_type": "snapshot",
                    "venue": "polymarket",
                    "timestamp": now,
                    "slug": market.slug,
                    "asset": market.asset,
                    "interval_minutes": market.interval_minutes,
                    "open_time": market.open_time,
                    "close_time": market.close_time,
                    "seconds_to_close": market.seconds_to_close(now),
                    "condition_id": market.condition_id,
                    "up_token_id": token_id,
                    "gamma_best_bid": market.best_bid,
                    "gamma_best_ask": market.best_ask,
                    "implied_up_probability": market.implied_up_probability,
                    "volume": market.volume,
                    "book": (
                        None
                        if book is None
                        else {
                            "best_bid": book.best_bid,
                            "best_ask": book.best_ask,
                            "mid": book.mid,
                            "bid_levels": len(book.bids),
                            "ask_levels": len(book.asks),
                            "bids": [
                                {"price": lvl.price, "size": lvl.size}
                                for lvl in book.bids[:10]
                            ],
                            "asks": [
                                {"price": lvl.price, "size": lvl.size}
                                for lvl in book.asks[:10]
                            ],
                        }
                    ),
                }
            )
            written += 1
            self.stats.snapshots += 1
        return written

    async def collect_settlements(self, now: datetime | None = None) -> int:
        """Record outcomes for markets that have closed since we saw them."""
        now = now or datetime.now(timezone.utc)
        due = [
            market
            for slug, market in self._pending.items()
            if slug not in self._settled and market.close_time <= now
        ]
        if not due:
            return 0

        try:
            records = await self.client.fetch_markets(closed=None, limit=100)
        except Exception:  # noqa: BLE001
            self.stats.errors += 1
            return 0
        by_slug = {r.get("slug"): r for r in records if isinstance(r, dict)}

        written = 0
        for market in due:
            payload = by_slug.get(market.slug)
            if payload is None:
                continue
            prices = payload.get("outcomePrices")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except ValueError:
                    prices = None
            # A resolved binary market prints 1/0 against its outcomes.
            outcome: str | None = None
            if isinstance(prices, list) and len(prices) == len(market.outcomes):
                for label, price in zip(market.outcomes, prices):
                    try:
                        if Decimal(str(price)) >= Decimal("0.99"):
                            outcome = label
                            break
                    except Exception:  # noqa: BLE001
                        continue
            if outcome is None and not payload.get("closed"):
                continue  # not resolved yet; try again next cycle

            self._settled.add(market.slug)
            self._write(
                {
                    "schema_version": SCHEMA_VERSION,
                    "record_type": "settlement",
                    "venue": "polymarket",
                    "timestamp": now,
                    "slug": market.slug,
                    "asset": market.asset,
                    "interval_minutes": market.interval_minutes,
                    "close_time": market.close_time,
                    "outcome": outcome,
                    "label_up": None if outcome is None else int(
                        outcome.strip().lower() in {"up", "yes"}
                    ),
                    "closed": bool(payload.get("closed")),
                    "outcome_prices": prices,
                }
            )
            written += 1
            self.stats.settlements += 1

        # Keep the pending map from growing without bound across a long run.
        for slug in list(self._pending):
            if slug in self._settled:
                self._pending.pop(slug, None)
        return written

    async def run(
        self,
        *,
        interval_seconds: float = 30.0,
        max_cycles: int | None = None,
    ) -> CollectorStats:
        """Poll until cancelled, or for ``max_cycles`` iterations."""
        cycle = 0
        while max_cycles is None or cycle < max_cycles:
            await self.snapshot_once()
            await self.collect_settlements()
            cycle += 1
            if max_cycles is not None and cycle >= max_cycles:
                break
            await asyncio.sleep(interval_seconds)
        return self.stats


async def _main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Record Polymarket up/down markets (read-only, no wallet)"
    )
    parser.add_argument("--output", type=Path, default=Path("polymarket_samples.jsonl"))
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--assets", help="comma-separated, e.g. BTC,ETH,SOL")
    parser.add_argument(
        "--market-intervals",
        help="comma-separated minutes to keep, e.g. 5,10,15; omit for all",
    )
    parser.add_argument("--cycles", type=int, help="stop after N cycles")
    args = parser.parse_args(argv)

    assets = (
        [a.strip().upper() for a in args.assets.split(",") if a.strip()]
        if args.assets
        else None
    )
    intervals = (
        [int(i) for i in args.market_intervals.split(",") if i.strip()]
        if args.market_intervals
        else None
    )

    collector = PolymarketCollector(
        args.output, assets=assets, intervals=intervals
    )
    print(f"recording to {args.output} every {args.interval_seconds}s (read-only)")
    try:
        stats = await collector.run(
            interval_seconds=args.interval_seconds, max_cycles=args.cycles
        )
    except KeyboardInterrupt:
        stats = collector.stats
    finally:
        await collector.close()
    print(
        f"cycles={stats.cycles} snapshots={stats.snapshots} "
        f"settlements={stats.settlements} errors={stats.errors}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
