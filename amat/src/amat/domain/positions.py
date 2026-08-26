"""Position records and their provenance.

A position exists only as the accumulation of fills and corporate actions. It
carries the identifiers of the events that produced it so that an auditor can
answer "why does this line exist?" without re-running the backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from amat.domain.types import (
    Cash,
    Price,
    Quantity,
    SessionDate,
    Symbol,
    parse_symbol,
    require_finite_decimal,
)

__all__ = ["Position"]


@dataclass(frozen=True, slots=True)
class Position:
    """A long-or-flat holding marked as of a session close.

    Attributes:
        symbol: Instrument held.
        quantity: Share count. Non-negative: the v1 strategy never shorts.
        mark: Raw close used to value the position on ``as_of_session``.
        as_of_session: Session whose close produced ``mark``.

    Raises:
        ConfigurationError: on a negative quantity, a non-positive mark, or a
            non-Decimal value.
    """

    symbol: Symbol
    quantity: Quantity
    mark: Price
    as_of_session: SessionDate

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", parse_symbol(self.symbol))
        require_finite_decimal(self.symbol, "quantity", self.quantity, minimum=Decimal(0))
        require_finite_decimal(self.symbol, "mark", self.mark, minimum=Decimal(0), strict=True)

    @property
    def market_value(self) -> Cash:
        """Exact marked value of the holding."""
        return self.quantity * self.mark

    @property
    def is_flat(self) -> bool:
        """Whether the position holds no shares."""
        return self.quantity == 0
