"""Ledger snapshots and the accounting identities that must hold every session.

Two identities are enforced, exactly as stated in the validation protocol::

    equity            == cash + sum(quantity * mark)
    change in equity  == market P&L + distributions + cash yield - fees - slippage

Both are checked with exact ``Decimal`` arithmetic. The tolerance is a declared
numeric quantum, never a value chosen after seeing how large an error turned out
to be. An unexplained residual fails the run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from amat.domain.positions import Position
from amat.domain.types import Cash, SessionDate, Symbol
from amat.errors import AccountingError, ConfigurationError

__all__ = [
    "CASH_QUANTUM",
    "DEFAULT_TOLERANCE",
    "LedgerSnapshot",
    "PeriodAttribution",
    "reconcile_session",
]

#: Smallest representable cash amount in the account currency.
CASH_QUANTUM: Cash = Decimal("0.01")

#: Default reconciliation tolerance: one cash quantum. Derived from declared
#: precision, not from observed error. Raise it only with a written rationale.
DEFAULT_TOLERANCE: Cash = CASH_QUANTUM


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """Cash and marked holdings at the close of one session.

    Attributes:
        session_date: Session whose close this snapshot describes.
        cash: Exact cash balance. Must be non-negative: the v1 strategy uses no
            leverage and no margin borrowing.
        positions: Marked holdings keyed by symbol.

    Raises:
        ConfigurationError: on a non-Decimal or negative cash balance, or on a
            position whose mark session disagrees with ``session_date``.
    """

    session_date: SessionDate
    cash: Cash
    positions: Mapping[Symbol, Position]

    def __post_init__(self) -> None:
        if not isinstance(self.cash, Decimal):
            message = f"cash must be Decimal, received {type(self.cash).__name__}"
            raise ConfigurationError(message)
        if not self.cash.is_finite() or self.cash < 0:
            message = (
                f"{self.session_date.isoformat()}: cash must be finite and non-negative "
                f"(no leverage, no margin), received {self.cash}"
            )
            raise ConfigurationError(message)

        # ``frozen=True`` protects the field binding, not the container. Without
        # this copy the caller retains a live handle and can mutate holdings
        # after validation, silently changing every derived property -- equity,
        # gross exposure, and weights are all recomputed on access.
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))

        for symbol, position in self.positions.items():
            if position.symbol != symbol:
                message = (
                    f"{self.session_date.isoformat()}: position keyed {symbol} "
                    f"carries symbol {position.symbol}"
                )
                raise ConfigurationError(message)
            if position.as_of_session != self.session_date:
                message = (
                    f"{symbol}: position marked on "
                    f"{position.as_of_session.isoformat()} cannot appear in the "
                    f"{self.session_date.isoformat()} snapshot"
                )
                raise ConfigurationError(message)

    @property
    def holdings_value(self) -> Cash:
        """Exact sum of marked position values."""
        return sum(
            (position.market_value for position in self.positions.values()),
            start=Decimal(0),
        )

    @property
    def equity(self) -> Cash:
        """Total account equity: ``cash + sum(quantity * mark)``."""
        return self.cash + self.holdings_value

    @property
    def gross_exposure(self) -> float:
        """Marked holdings as a fraction of equity.

        Returns ``0.0`` when equity is zero rather than dividing by zero; the
        caller is expected to treat a zero-equity account as a failed run.
        """
        equity = self.equity
        if equity == 0:
            return 0.0
        return float(self.holdings_value / equity)

    def weights(self) -> Mapping[Symbol, float]:
        """Realized portfolio weights implied by this snapshot."""
        equity = self.equity
        if equity == 0:
            return {}
        return {
            symbol: float(position.market_value / equity)
            for symbol, position in self.positions.items()
        }


@dataclass(frozen=True, slots=True)
class PeriodAttribution:
    """The explained components of one session's equity change.

    Every field is an exact cash amount. ``fees`` and ``slippage`` are stated as
    non-negative magnitudes and subtracted, so a report can never accidentally
    present a cost as a gain.
    """

    market_pnl: Cash
    distributions: Cash
    cash_yield: Cash
    fees: Cash
    slippage: Cash

    def __post_init__(self) -> None:
        for name in ("market_pnl", "distributions", "cash_yield", "fees", "slippage"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                message = f"{name} must be Decimal, received {type(value).__name__}"
                raise ConfigurationError(message)
            if not value.is_finite():
                message = f"{name} must be finite, received {value}"
                raise ConfigurationError(message)
        for name in ("fees", "slippage"):
            value = getattr(self, name)
            if value < 0:
                message = f"{name} is a cost magnitude and must be non-negative, received {value}"
                raise ConfigurationError(message)

    @property
    def explained_change(self) -> Cash:
        """Total equity change these components account for."""
        return self.market_pnl + self.distributions + self.cash_yield - self.fees - self.slippage


def reconcile_session(
    previous: LedgerSnapshot,
    current: LedgerSnapshot,
    attribution: PeriodAttribution,
    *,
    tolerance: Cash = DEFAULT_TOLERANCE,
) -> Cash:
    """Verify that the equity change between two snapshots is fully explained.

    Args:
        previous: Snapshot at the prior session close.
        current: Snapshot at this session close.
        attribution: The explained components of the change.
        tolerance: Maximum permitted absolute residual, in cash units.

    Returns:
        The signed residual, which is within ``tolerance`` on success.

    Note this checks *ordering*, not calendar adjacency: the signature carries no
    session calendar, so it cannot tell a one-session gap from a one-year gap.
    The session loop in :mod:`amat.backtest` is what guarantees adjacency, by
    calling this on every consecutive pair.

    Raises:
        ConfigurationError: if the current session does not strictly follow the
            previous one, or the tolerance is not a finite non-negative Decimal.
        AccountingError: if any part of the equity change is unexplained.
    """
    # Finiteness is tested before the sign comparison: ``Decimal("NaN") < 0``
    # raises ``InvalidOperation`` and ``Decimal("Infinity")`` would accept any
    # residual whatsoever, reporting an arbitrarily large unexplained change as
    # reconciled.
    if not isinstance(tolerance, Decimal):
        message = f"tolerance must be a Decimal, received {type(tolerance).__name__}"
        raise ConfigurationError(message)
    if not tolerance.is_finite() or tolerance < 0:
        message = f"tolerance must be a finite non-negative Decimal, received {tolerance!r}"
        raise ConfigurationError(message)
    if current.session_date <= previous.session_date:
        message = (
            f"current session {current.session_date.isoformat()} must be after "
            f"previous session {previous.session_date.isoformat()}"
        )
        raise ConfigurationError(message)

    observed = current.equity - previous.equity
    residual = observed - attribution.explained_change
    if abs(residual) > tolerance:
        message = (
            f"{current.session_date.isoformat()}: unexplained equity change of {residual} "
            f"exceeds tolerance {tolerance}. Observed {observed}, "
            f"explained {attribution.explained_change}. "
            "Every position change requires a fill or a corporate action."
        )
        raise AccountingError(message)
    return residual
