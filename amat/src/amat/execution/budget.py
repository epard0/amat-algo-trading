"""Pure proportional buy authorization using known prices and confirmed cash."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from amat.execution import one_way_cost


@dataclass(frozen=True)
class CashBudgetPolicy:
    reserve_fraction: Decimal = Decimal("0.01")
    price_buffer_fraction: Decimal = Decimal("0.002")
    quantity_quantum: Decimal = Decimal("0.000001")

    def __post_init__(self) -> None:
        for value in (self.reserve_fraction, self.price_buffer_fraction):
            if not value.is_finite() or not 0 <= value < 1:
                raise ValueError("cash buffers must be finite fractions")
        if not self.quantity_quantum.is_finite() or self.quantity_quantum <= 0:
            raise ValueError("quantity quantum must be positive")


def budget_buys(
    requested: Mapping[str, Decimal],
    prices: Mapping[str, Decimal],
    *,
    confirmed_cash: Decimal,
    equity: Decimal,
    committed_cash: Decimal = Decimal(0),
    one_way_bps: float = 2.0,
    policy: CashBudgetPolicy | None = None,
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Never authorize pending sale proceeds or more than the supplied buy budget."""
    policy = policy or CashBudgetPolicy()
    if any(not x.is_finite() or x < 0 for x in (confirmed_cash, equity, committed_cash)):
        raise ValueError("invalid cash inputs")
    if not Decimal(str(one_way_bps)).is_finite() or one_way_bps < 0:
        raise ValueError("invalid fee")
    buffered = {}
    for symbol, quantity in requested.items():
        price = prices.get(symbol)
        if not quantity.is_finite() or quantity < 0 or price is None:
            raise ValueError("invalid buy quantity or reference price")
        if not price.is_finite() or price <= 0:
            raise ValueError("invalid buy reference price")
        buffered[symbol] = price * (1 + policy.price_buffer_fraction)
    available = max(Decimal(0), confirmed_cash - committed_cash - equity * policy.reserve_fraction)
    required = sum(
        (
            q * buffered[s] + one_way_cost(q * buffered[s], one_way_bps)
            for s, q in requested.items()
        ),
        Decimal(0),
    )
    scale = min(Decimal(1), available / required) if required else Decimal(0)
    quantum = policy.quantity_quantum
    result = {
        s: (q * scale / quantum).to_integral_value(rounding=ROUND_DOWN) * quantum
        for s, q in sorted(requested.items())
    }

    # Fee rounding can add a few millionths after scaling. Remove quanta
    # deterministically until the cash invariant holds, preserving original limits.
    def total() -> Decimal:
        return sum(
            (
                q * buffered[s] + one_way_cost(q * buffered[s], one_way_bps)
                for s, q in result.items()
            ),
            Decimal(0),
        )

    while total() > available:
        symbol = max(result, key=lambda s: (result[s] * buffered[s], s))
        result[symbol] = max(Decimal(0), result[symbol] - quantum)
    return result, {s: requested[s] - result[s] for s in result}
