"""Corporate-action records.

Guardrail 6 requires that every position change trace to a fill *or a corporate
action*. Without a record for the second half of that disjunction, an ex-dividend
session or a split is unrepresentable: :class:`~amat.domain.fills.Fill` demands a
strictly positive quantity *and* price, so a cash dividend (cash, no shares) and a
share split (shares, no cash) both fail construction.

Accounting rules encoded here
-----------------------------
* A **cash dividend** credits cash and leaves the share count untouched. It is
  attributed to ``distributions``, never to market P&L.
* A **split** multiplies the share count and divides the mark by the same ratio,
  so equity is preserved apart from rounding. It contributes nothing to any
  attribution component.

The double-count trap: research features use the *adjusted* close, which already
embeds dividends. Valuation uses the *raw* close plus this ledger. A dividend must
therefore be counted exactly once -- through the cash credit here, never also
through an adjusted-price return. :func:`CorporateAction.cash_credit` is the only
sanctioned source of distribution cash.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from amat.domain.types import (
    Cash,
    Quantity,
    SessionDate,
    Symbol,
    parse_symbol,
    require_finite_decimal,
)
from amat.errors import ConfigurationError

__all__ = ["ActionKind", "CorporateAction"]


class ActionKind(StrEnum):
    """The corporate actions the v1 accounting model represents."""

    CASH_DIVIDEND = "cash_dividend"
    """Cash per share credited on the ex-date. Share count unchanged."""

    SPLIT = "split"
    """Share count multiplied by ``ratio``; mark divided by the same ratio."""


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One corporate action applying to one instrument on one session.

    Attributes:
        symbol: Instrument affected.
        session_date: Session on which the action takes effect. For a cash
            dividend this is the ex-date, because that is when the holder's
            economic entitlement changes.
        kind: Which action this is.
        cash_per_share: Cash credited per held share. Required and strictly
            positive for ``CASH_DIVIDEND``; must be zero otherwise.
        ratio: Share multiplier. Required and strictly positive for ``SPLIT``
            (2 for a 2-for-1, ``Decimal("0.5")`` for a 1-for-2 reverse); must be
            one otherwise.

    Raises:
        ConfigurationError: if the payload does not match the declared kind.
    """

    symbol: Symbol
    session_date: SessionDate
    kind: ActionKind
    cash_per_share: Cash = Decimal(0)
    ratio: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", parse_symbol(self.symbol))
        object.__setattr__(self, "kind", ActionKind(self.kind))
        label = f"{self.symbol} {self.session_date.isoformat()}"

        require_finite_decimal(label, "cash_per_share", self.cash_per_share, minimum=Decimal(0))
        require_finite_decimal(label, "ratio", self.ratio, minimum=Decimal(0), strict=True)

        # Each kind must carry exactly its own payload. A split that also
        # claims a dividend, or a dividend that also rescales shares, would let
        # one record change equity through two channels at once -- the precise
        # shape of a corporate-action double count.
        if self.kind == ActionKind.CASH_DIVIDEND:
            if self.cash_per_share <= 0:
                message = f"{label}: a cash dividend requires a positive cash_per_share"
                raise ConfigurationError(message)
            if self.ratio != 1:
                message = (
                    f"{label}: a cash dividend must not rescale shares, got ratio {self.ratio}"
                )
                raise ConfigurationError(message)
        elif self.kind == ActionKind.SPLIT:
            if self.cash_per_share != 0:
                message = (
                    f"{label}: a split must not credit cash, "
                    f"got cash_per_share {self.cash_per_share}"
                )
                raise ConfigurationError(message)
            if self.ratio == 1:
                message = f"{label}: a split with ratio 1 is a no-op and is rejected as ambiguous"
                raise ConfigurationError(message)

    def cash_credit(self, quantity: Quantity) -> Cash:
        """Cash credited to the account for holding ``quantity`` shares."""
        if self.kind != ActionKind.CASH_DIVIDEND:
            return Decimal(0)
        return quantity * self.cash_per_share

    def adjust_quantity(self, quantity: Quantity) -> Quantity:
        """Share count after this action."""
        if self.kind != ActionKind.SPLIT:
            return quantity
        return quantity * self.ratio

    def adjust_mark(self, mark: Decimal) -> Decimal:
        """Mark after this action, preserving marked value across a split."""
        if self.kind != ActionKind.SPLIT:
            return mark
        return mark / self.ratio
