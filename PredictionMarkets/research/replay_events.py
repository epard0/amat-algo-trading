"""Conservative event replay for the normalized v4 Kalshi data contract.

Orders see only the reconstructed book at their configured arrival time. The
engine never reaches forward for a later quote and never assumes both sides of
an arbitrage pair fill merely because they existed in one snapshot.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


ONE = Decimal("1")


@dataclass(frozen=True)
class ReplayEvent:
    event_id: str
    ticker: str
    monotonic_ns: int
    sequence: int | None
    event_type: str
    side: str | None
    price: Decimal | None
    delta: Decimal | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class ReplayOrder:
    client_order_id: str
    ticker: str
    outcome: str
    limit_price: Decimal
    contracts: Decimal
    submitted_monotonic_ns: int


@dataclass(frozen=True)
class ReplayFill:
    client_order_id: str
    requested: Decimal
    filled: Decimal
    average_price: Decimal | None
    complete: bool


class EventBook:
    def __init__(self) -> None:
        self.levels = {"yes": {}, "no": {}}
        self.sequence: int | None = None

    def apply(self, event: ReplayEvent) -> None:
        if event.event_type == "snapshot":
            self.levels = {
                side: {
                    Decimal(str(price)): Decimal(str(quantity))
                    for price, quantity in event.payload.get(side, ())
                    if Decimal(str(quantity)) > 0
                }
                for side in ("yes", "no")
            }
        elif event.event_type == "delta":
            if event.side not in self.levels or event.price is None or event.delta is None:
                raise ValueError(f"malformed delta {event.event_id}")
            side = self.levels[event.side]
            quantity = side.get(event.price, Decimal("0")) + event.delta
            if quantity <= 0:
                side.pop(event.price, None)
            else:
                side[event.price] = quantity
        else:
            raise ValueError(f"unknown book event type {event.event_type}")
        self.sequence = event.sequence

    def taker_fill(self, order: ReplayOrder) -> ReplayFill:
        """Walk executable opposing bids and return an IOC-style partial fill."""
        opposing = "no" if order.outcome == "yes" else "yes"
        # Highest opposing bid is the cheapest complementary ask.
        offers = sorted(self.levels[opposing].items(), reverse=True)
        remaining = order.contracts
        notional = Decimal("0")
        filled = Decimal("0")
        for opposing_bid, available in offers:
            price = ONE - opposing_bid
            if price > order.limit_price:
                continue
            quantity = min(remaining, available)
            filled += quantity
            notional += quantity * price
            remaining -= quantity
            leftover = available - quantity
            if leftover > 0:
                self.levels[opposing][opposing_bid] = leftover
            else:
                self.levels[opposing].pop(opposing_bid, None)
            if remaining <= 0:
                break
        return ReplayFill(
            client_order_id=order.client_order_id,
            requested=order.contracts,
            filled=filled,
            average_price=notional / filled if filled > 0 else None,
            complete=filled == order.contracts,
        )


def replay(
    events: Iterable[ReplayEvent],
    orders: Iterable[ReplayOrder],
    *,
    latency_ms: Decimal,
) -> list[ReplayFill]:
    """Replay orders without look-ahead at p50/p90/p99 latency assumptions."""
    timeline = sorted(events, key=lambda row: (row.monotonic_ns, row.event_id))
    pending = sorted(orders, key=lambda row: (row.submitted_monotonic_ns, row.client_order_id))
    books: dict[str, EventBook] = {}
    event_index = 0
    results: list[ReplayFill] = []
    latency_ns = int(latency_ms * Decimal("1000000"))
    for order in pending:
        arrival = order.submitted_monotonic_ns + latency_ns
        # Apply only observations received no later than venue arrival. A later
        # event may be the first convenient quote, but using it is look-ahead.
        while event_index < len(timeline) and timeline[event_index].monotonic_ns <= arrival:
            event = timeline[event_index]
            books.setdefault(event.ticker, EventBook()).apply(event)
            event_index += 1
        book = books.get(order.ticker)
        results.append(
            book.taker_fill(order)
            if book is not None
            else ReplayFill(order.client_order_id, order.contracts, Decimal("0"), None, False)
        )
    return results


def load_run(connection: sqlite3.Connection, run_id: str) -> tuple[list[ReplayEvent], list[ReplayOrder]]:
    connection.row_factory = sqlite3.Row
    events = [
        ReplayEvent(
            event_id=row["event_id"],
            ticker=row["ticker"],
            monotonic_ns=row["received_monotonic_ns"],
            sequence=row["sequence"],
            event_type=row["event_type"],
            side=row["outcome_side"],
            price=Decimal(row["price"]) if row["price"] is not None else None,
            delta=Decimal(row["quantity_delta"]) if row["quantity_delta"] is not None else None,
            payload=json.loads(row["payload_json"]),
        )
        for row in connection.execute(
            """SELECT * FROM book_events WHERE run_id = ?
               ORDER BY received_monotonic_ns, event_id""",
            (run_id,),
        )
    ]
    orders = [
        ReplayOrder(
            client_order_id=row["client_order_id"],
            ticker=row["ticker"],
            outcome=row["outcome"],
            limit_price=Decimal(row["limit_price"]),
            contracts=Decimal(row["requested_contracts"]),
            submitted_monotonic_ns=row["submitted_monotonic_ns"],
        )
        for row in connection.execute(
            """SELECT * FROM orders WHERE run_id = ? AND intent = 'entry'
               ORDER BY submitted_monotonic_ns, order_record_id""",
            (run_id,),
        )
    ]
    return events, orders


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("--latency-ms", type=Decimal, default=Decimal("250"))
    args = parser.parse_args()
    with sqlite3.connect(args.ledger) as connection:
        events, orders = load_run(connection, args.run_id)
    fills = replay(events, orders, latency_ms=args.latency_ms)
    completed = sum(fill.complete for fill in fills)
    filled = sum((fill.filled for fill in fills), Decimal("0"))
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "latency_ms": str(args.latency_ms),
                "orders": len(fills),
                "completed": completed,
                "contracts_filled": str(filled),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
