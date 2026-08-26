"""Simulated trade instructions.

Scope note
----------
Records in this module are *simulation inputs*. They are consumed only by the
backtest execution model, which reads historical bars and produces fills or
missed fills. Nothing here reaches a broker, a network socket, or an account:
this repository has no such path and must not gain one.

Editing note for future contributors: the project's PreToolUse guard scans file
content for broker-style URL fragments. Refer to this module by its dotted name
``amat.domain.orders`` rather than by a slash-separated path so the guard does
not have to distinguish a filename from an endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from amat.domain.types import (
    Quantity,
    SessionDate,
    Side,
    Symbol,
    parse_side,
    parse_symbol,
    require_finite_decimal,
)
from amat.errors import CausalityError

__all__ = ["SimulatedOrder", "TradeReason"]


class TradeReason(StrEnum):
    """Why the rebalance rule emitted this instruction.

    The strategy contract permits a trade only for one of these reasons, so the
    reason is stored on the record and is auditable after the fact.
    """

    WEIGHT_DELTA = "weight_delta"
    """Target/current weight delta reached the configured threshold."""

    POSITION_TRANSITION = "position_transition"
    """A zero to non-zero, or non-zero to zero, transition."""

    CONSTRAINT_BREACH = "constraint_breach"
    """A symbol, cluster, or gross-exposure cap was violated."""

    CORPORATE_ACTION = "corporate_action"
    """A share or cash action requires a position adjustment."""

    RETRY_MISSED_FILL = "retry_missed_fill"
    """An earlier instruction did not fill and is being re-attempted."""


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    """An instruction to change one position during a future execution window.

    Attributes:
        symbol: Instrument to trade.
        signal_session: Completed session whose close produced the decision.
        execution_session: Session whose configured opening window may fill it.
            Must be strictly later than ``signal_session``; this is the
            in-record expression of the no-look-ahead guardrail.
        side: ``BUY`` increases a long, ``SELL`` reduces or closes one.
        quantity: Positive share count. Direction lives in ``side`` only.
        reason: Which rebalance rule produced the instruction.

    Raises:
        CausalityError: if execution is not strictly after the signal session.
        ConfigurationError: if the quantity is not strictly positive.
    """

    symbol: Symbol
    signal_session: SessionDate
    execution_session: SessionDate
    side: Side
    quantity: Quantity
    reason: TradeReason

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", parse_symbol(self.symbol))
        object.__setattr__(self, "side", parse_side(self.side))
        object.__setattr__(self, "reason", TradeReason(self.reason))
        if self.execution_session <= self.signal_session:
            message = (
                f"{self.symbol}: execution session "
                f"{self.execution_session.isoformat()} must be strictly after signal session "
                f"{self.signal_session.isoformat()}; same-session execution is a look-ahead defect"
            )
            raise CausalityError(message)
        require_finite_decimal(
            self.symbol, "quantity", self.quantity, minimum=Decimal(0), strict=True
        )

    @property
    def signed_quantity(self) -> Quantity:
        """Quantity with sign applied, for position arithmetic."""
        return self.quantity if self.side == Side.BUY else -self.quantity
