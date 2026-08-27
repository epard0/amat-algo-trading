"""Lock-guarded registry of open positions and committed capital.

Dropping ``idx_trades_one_open`` moved the "how many positions may be open"
invariant out of SQLite and into this object. That is a deliberate weakening of
a durable guarantee into a process-lifetime one, and this module is the whole of
the compensation, so it is worth being explicit about how it holds.

**Reservation happens before the order, not after the fill.** Checking capacity,
awaiting a venue round trip, and then recording the result would let two
coroutines both pass the check and jointly exceed the limit - the classic
check-then-act race. ``reserve`` mutates state while still holding the lock, and
the caller releases it if the order does not fill.

**It is rebuilt from the ledger at startup.** The ledger remains the source of
truth; this is a fast in-memory mirror of it, not a second record. A restart
re-reads rather than forgetting.

There are no OS threads here. One asyncio lock is sufficient because every
mutation happens on one event loop, and the SQLite connection underneath is
bound to its creating thread by design.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")


@dataclass(frozen=True)
class Reservation:
    """Capital committed to an order that has not yet resolved."""

    key: str
    strategy: str
    ticker: str
    amount: Decimal


@dataclass
class _Slot:
    strategy: str
    ticker: str
    amount: Decimal
    filled: bool = False
    trade_id: str | None = None


class PositionBook:
    """Concurrency and capital limits for every strategy in one place."""

    def __init__(
        self,
        *,
        max_concurrent: dict[str, int] | None = None,
        capital_ceiling: dict[str, Decimal] | None = None,
        default_max_concurrent: int = 1,
    ) -> None:
        self._lock = asyncio.Lock()
        self._slots: dict[str, _Slot] = {}
        self._max_concurrent = dict(max_concurrent or {})
        self._capital_ceiling = dict(capital_ceiling or {})
        self._default_max_concurrent = default_max_concurrent
        self._counter = 0

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def limit_for(self, strategy: str) -> int:
        return self._max_concurrent.get(strategy, self._default_max_concurrent)

    def ceiling_for(self, strategy: str) -> Decimal | None:
        return self._capital_ceiling.get(strategy)

    def set_ceiling(self, strategy: str, ceiling: Decimal) -> None:
        """Update a strategy's capital ceiling.

        Recomputed each cycle rather than fixed at startup, because it is a
        fraction of a balance that compounds. A ceiling frozen at the opening
        balance would stop tracking the account it is supposed to protect.
        """
        self._capital_ceiling[strategy] = ceiling

    # -- reads (cheap, lock-free; callers that need consistency take the lock) --

    def open_count(self, strategy: str | None = None) -> int:
        return sum(
            1
            for slot in self._slots.values()
            if strategy is None or slot.strategy == strategy
        )

    def committed(self, strategy: str | None = None) -> Decimal:
        return sum(
            (
                slot.amount
                for slot in self._slots.values()
                if strategy is None or slot.strategy == strategy
            ),
            ZERO,
        )

    def tickers(self, strategy: str | None = None) -> set[str]:
        return {
            slot.ticker
            for slot in self._slots.values()
            if strategy is None or slot.strategy == strategy
        }

    def holds(self, strategy: str, ticker: str) -> bool:
        return any(
            slot.strategy == strategy and slot.ticker == ticker
            for slot in self._slots.values()
        )

    # -- mutations (all under the lock) --

    async def reserve(
        self,
        *,
        strategy: str,
        ticker: str,
        amount: Decimal,
    ) -> Reservation | None:
        """Claim a slot and capital, or return None if either limit binds.

        Returning ``None`` rather than raising keeps this usable inside a
        ranking loop: a candidate that cannot be funded is skipped, not fatal.
        """
        async with self._lock:
            if any(
                slot.strategy == strategy and slot.ticker == ticker
                for slot in self._slots.values()
            ):
                return None
            if self.open_count(strategy) >= self.limit_for(strategy):
                return None
            ceiling = self.ceiling_for(strategy)
            if ceiling is not None and self.committed(strategy) + amount > ceiling:
                return None

            self._counter += 1
            key = f"{strategy}:{ticker}:{self._counter}"
            self._slots[key] = _Slot(strategy=strategy, ticker=ticker, amount=amount)
            return Reservation(
                key=key, strategy=strategy, ticker=ticker, amount=amount
            )

    async def release(self, reservation: Reservation) -> None:
        """Give back a reservation whose order never became a position."""
        async with self._lock:
            self._slots.pop(reservation.key, None)

    async def confirm(
        self,
        reservation: Reservation,
        *,
        trade_id: str,
        actual_amount: Decimal | None = None,
    ) -> None:
        """Promote a reservation to a held position.

        ``actual_amount`` covers a partial fill: an IOC or a partially filled
        resting order commits less than was reserved, and holding the larger
        figure would understate available capital for the rest of the session.
        """
        async with self._lock:
            slot = self._slots.get(reservation.key)
            if slot is None:
                return
            slot.filled = True
            slot.trade_id = trade_id
            if actual_amount is not None:
                slot.amount = actual_amount

    async def close(self, trade_id: str) -> None:
        """Drop a position that has settled or been exited."""
        async with self._lock:
            for key, slot in list(self._slots.items()):
                if slot.trade_id == trade_id:
                    del self._slots[key]

    async def reduce(self, trade_id: str, remaining_fraction: Decimal) -> None:
        """Release capital proportionally after an IOC partial exit."""
        if not Decimal("0") <= remaining_fraction <= Decimal("1"):
            raise ValueError("remaining_fraction must be between zero and one")
        async with self._lock:
            for key, slot in list(self._slots.items()):
                if slot.trade_id != trade_id:
                    continue
                if remaining_fraction == 0:
                    del self._slots[key]
                else:
                    slot.amount *= remaining_fraction

    async def resync(self, positions: list[tuple[str, str, str, Decimal]]) -> None:
        """Rebuild from the ledger: ``(trade_id, strategy, ticker, amount)``.

        Called at startup and after a mode switch. Reservations in flight are
        discarded, which is correct in both cases: at startup there are none,
        and a mode switch only lands when the book is flat.
        """
        async with self._lock:
            self._slots.clear()
            for trade_id, strategy, ticker, amount in positions:
                self._counter += 1
                key = f"{strategy}:{ticker}:{self._counter}"
                self._slots[key] = _Slot(
                    strategy=strategy,
                    ticker=ticker,
                    amount=amount,
                    filled=True,
                    trade_id=trade_id,
                )
