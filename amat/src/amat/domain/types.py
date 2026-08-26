"""Primitive domain types and numeric conventions.

Numeric policy
--------------
``Decimal`` is used wherever an exact quantity of money or shares is required:
prices used for execution and valuation, cash, fees, slippage, and position
quantities. Statistical quantities (returns, volatilities, correlations, signal
scores, weights) are IEEE-754 ``float`` because their estimators are themselves
approximate and because NumPy/Polars operate on floats.

The float boundary is therefore explicit and one-directional: exact records are
converted to float when entering :mod:`amat.features` and
:mod:`amat.portfolio`, and target weights are converted back to exact share
quantities exactly once, inside the execution layer.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from enum import StrEnum
from typing import NewType

from amat.errors import ConfigurationError, DataIntegrityError

__all__ = [
    "Cash",
    "Price",
    "Quantity",
    "SessionDate",
    "Side",
    "Symbol",
    "Weight",
    "parse_side",
    "parse_symbol",
    "require_finite_decimal",
]

#: An exchange ticker, normalized to upper case.
Symbol = NewType("Symbol", str)

#: A completed exchange session, identified by its local trading date.
#: Session dates are calendar dates, never timezone-naive datetimes; instants
#: (for example an execution window boundary) are stored separately as
#: timezone-aware UTC datetimes.
SessionDate = dt.date

#: Exact monetary amount in account currency.
Cash = Decimal
#: Exact traded or valuation price in account currency.
Price = Decimal
#: Exact share quantity. Fractional shares are permitted in simulation.
Quantity = Decimal
#: Portfolio weight as a fraction of equity. Approximate by construction.
Weight = float

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}(?:\.[A-Z]{1,3})?$")


class Side(StrEnum):
    """Direction of a simulated trade.

    The v1 strategy is long-or-cash, so ``SELL`` only ever reduces or closes an
    existing long position. It never opens a short.
    """

    BUY = "buy"
    SELL = "sell"


def parse_symbol(raw: str) -> Symbol:
    """Normalize and validate an exchange ticker.

    Raises:
        DataIntegrityError: if ``raw`` is not a plausible ticker.
    """
    if not isinstance(raw, str):
        message = f"symbol must be a string, received {type(raw).__name__}"
        raise DataIntegrityError(message)
    candidate = raw.strip().upper()
    if not _SYMBOL_RE.fullmatch(candidate):
        message = f"invalid symbol: {raw!r}"
        raise DataIntegrityError(message)
    return Symbol(candidate)


def parse_side(raw: object) -> Side:
    """Coerce a trade direction to the :class:`Side` enum.

    Records compare sides with ``==`` rather than ``is`` because ``Side`` is a
    ``StrEnum`` and a plain string compares equal while failing an identity
    test. Coercing at construction removes the ambiguity entirely, so a record
    can never hold a non-enum side.

    Raises:
        ConfigurationError: if ``raw`` is not a recognized direction.
    """
    if isinstance(raw, Side):
        return raw
    if not isinstance(raw, str):
        message = f"side must be a string or Side, received {type(raw).__name__}"
        raise ConfigurationError(message)
    try:
        return Side(raw)
    except ValueError as exc:
        message = f"invalid side: {raw!r}; expected one of {[member.value for member in Side]}"
        raise ConfigurationError(message) from exc


def require_finite_decimal(
    label: str,
    name: str,
    value: object,
    *,
    minimum: Decimal | None = None,
    strict: bool = False,
) -> Decimal:
    """Validate that ``value`` is a finite ``Decimal`` within an optional bound.

    Centralized so that every record rejects ``NaN`` and ``Infinity`` the same
    way. Finiteness is checked *before* any comparison, because ``NaN`` raises
    ``InvalidOperation`` on ordered comparison and would otherwise escape the
    :class:`~amat.errors.AmatError` hierarchy.

    Args:
        label: Identifying context, e.g. ``"SPY 2020-01-02"``.
        name: Field name for the error message.
        value: Candidate value.
        minimum: Lower bound, if any.
        strict: Whether ``minimum`` is exclusive.

    Raises:
        ConfigurationError: if the value is not a finite Decimal or is out of
            bounds.
    """
    if not isinstance(value, Decimal):
        message = f"{label}: {name} must be Decimal, received {type(value).__name__}"
        raise ConfigurationError(message)
    if not value.is_finite():
        message = f"{label}: {name} must be finite, received {value}"
        raise ConfigurationError(message)
    if minimum is not None:
        if strict and value <= minimum:
            message = f"{label}: {name} must be strictly greater than {minimum}, received {value}"
            raise ConfigurationError(message)
        if not strict and value < minimum:
            message = f"{label}: {name} must be at least {minimum}, received {value}"
            raise ConfigurationError(message)
    return value
