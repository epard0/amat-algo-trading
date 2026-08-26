"""Fill and missed-fill records produced by the simulated execution model.

Every position change must trace to exactly one of these records or to a
corporate action. A missing or zero-volume execution window produces a
:class:`MissedFill`, never an imputed :class:`Fill`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from amat.domain.orders import TradeReason
from amat.domain.types import (
    Cash,
    Price,
    Quantity,
    SessionDate,
    Side,
    Symbol,
    parse_side,
    parse_symbol,
    require_finite_decimal,
)

__all__ = ["Fill", "MissedFill", "MissedFillReason"]


class MissedFillReason(StrEnum):
    """Why an execution window could not produce a fill."""

    NO_EXECUTION_BAR = "no_execution_bar"
    """The configured window has no market data at all."""

    ZERO_VOLUME = "zero_volume"
    """The window exists but traded no shares."""

    NON_SESSION_DATE = "non_session_date"
    """The requested execution date is not an exchange session."""

    BELOW_MINIMUM_NOTIONAL = "below_minimum_notional"
    """The instruction fell under the configured minimum simulated trade size."""

    INSTRUMENT_UNAVAILABLE = "instrument_unavailable"
    """The instrument had no valid history or metadata on that session."""

    INSUFFICIENT_CASH = "insufficient_cash"
    """Settled cash could not fund the purchase.

    Instructions are sized at the decision session's closing marks but fill at a
    later session's window price. An adverse move between the two can leave a
    buy unfunded, and the v1 mandate permits no leverage or margin borrowing, so
    the shortfall is recorded here rather than covered by an overdraft.
    """

    PARTICIPATION_CAP = "participation_cap"
    """A buy was clipped to the configured share of observed window volume."""

    STRESS_DROPPED = "stress_dropped"
    """Deterministically withheld by the missed-fill stress injection.

    Produced only when a stress run configures ``drop_every_nth_fill``; a
    primary run can never emit this reason. Recording the drop rather than
    silently skipping it keeps the stressed run's coverage auditable.
    """

    UNBACKED_SALE = "unbacked_sale"
    """A scheduled sale exceeded settled holdings at execution.

    Reachable only at a stressed signal-to-execution lag of two or more:
    overlapping decisions net each other's scheduled quantities, so a sale
    can be scheduled against shares a still-pending buy was expected to
    deliver -- and if that buy misses, the sale is no longer backed. The
    excess is recorded here and never shorted, exactly as a cash brokerage
    would reject it. At the baseline lag of 1 nothing is ever in flight at
    decision time and this reason cannot occur.
    """


@dataclass(frozen=True, slots=True)
class Fill:
    """A completed simulated trade.

    Attributes:
        symbol: Instrument traded.
        execution_session: Session in which the window filled.
        side: Direction of the trade.
        quantity: Positive filled share count.
        price: Raw execution price before cost, e.g. the opening-window VWAP.
        cost_cash: Side-adjusted one-way transaction cost, always non-negative.
            It is stored separately from ``price`` so that gross and net
            results can be reported and attributed independently.
        reason: The :class:`~amat.domain.orders.TradeReason` of the instruction
            this fill satisfied, carried through so a report can state *why*
            each trade happened without re-deriving the decision. ``None``
            means the producing path predates reason propagation or built the
            fill directly; it is rendered as "unrecorded", never guessed.

    Raises:
        ConfigurationError: on non-Decimal, non-finite, or non-positive values,
            or on a negative cost.
    """

    symbol: Symbol
    execution_session: SessionDate
    side: Side
    quantity: Quantity
    price: Price
    cost_cash: Cash
    reason: TradeReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", parse_symbol(self.symbol))
        object.__setattr__(self, "side", parse_side(self.side))
        if self.reason is not None:
            object.__setattr__(self, "reason", TradeReason(self.reason))
        for name, value in (("quantity", self.quantity), ("price", self.price)):
            _require_positive_decimal(self.symbol, name, value)
        require_finite_decimal(self.symbol, "cost_cash", self.cost_cash, minimum=Decimal(0))

    @property
    def signed_quantity(self) -> Quantity:
        """Filled quantity with sign applied, for position arithmetic."""
        return self.quantity if self.side == Side.BUY else -self.quantity

    @property
    def gross_notional(self) -> Cash:
        """Absolute traded value before costs."""
        return self.quantity * self.price

    @property
    def cash_delta(self) -> Cash:
        """Exact change in cash caused by this fill, costs included.

        A buy consumes cash for the notional and for the cost. A sell releases
        the notional but still pays the cost.
        """
        notional = self.gross_notional
        if self.side == Side.BUY:
            return -(notional + self.cost_cash)
        return notional - self.cost_cash


@dataclass(frozen=True, slots=True)
class MissedFill:
    """A recorded failure to execute. Never silently discarded.

    Attributes:
        symbol: Instrument that could not be traded.
        execution_session: Session whose window failed.
        side: Direction that was intended.
        requested_quantity: Positive share count that was requested.
        reason: Why the window could not fill.
    """

    symbol: Symbol
    execution_session: SessionDate
    side: Side
    requested_quantity: Quantity
    reason: MissedFillReason

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", parse_symbol(self.symbol))
        object.__setattr__(self, "side", parse_side(self.side))
        object.__setattr__(self, "reason", MissedFillReason(self.reason))
        _require_positive_decimal(self.symbol, "requested_quantity", self.requested_quantity)


def _require_positive_decimal(symbol: Symbol, name: str, value: object) -> None:
    require_finite_decimal(symbol, name, value, minimum=Decimal(0), strict=True)
