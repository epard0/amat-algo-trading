"""Immutable domain records shared by every runner and adapter.

This package is framework-neutral by construction. It imports only the standard
library and :mod:`amat.errors`. It must never import a broker SDK, a backtest
framework, a plotting or reporting library, a web framework, or a network
client. ``tests/unit/test_dependency_direction.py`` enforces that rule.
"""

from __future__ import annotations

from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.bars import AdjustmentMode, DailyBar, ExecutionWindowBar
from amat.domain.fills import Fill, MissedFill, MissedFillReason
from amat.domain.instruments import Cluster, Instrument, Universe
from amat.domain.ledgers import (
    CASH_QUANTUM,
    DEFAULT_TOLERANCE,
    LedgerSnapshot,
    PeriodAttribution,
    reconcile_session,
)
from amat.domain.orders import SimulatedOrder, TradeReason
from amat.domain.positions import Position
from amat.domain.types import (
    Cash,
    Price,
    Quantity,
    SessionDate,
    Side,
    Symbol,
    Weight,
    parse_side,
    parse_symbol,
    require_finite_decimal,
)

__all__ = [
    "CASH_QUANTUM",
    "DEFAULT_TOLERANCE",
    "ActionKind",
    "AdjustmentMode",
    "Cash",
    "Cluster",
    "CorporateAction",
    "DailyBar",
    "ExecutionWindowBar",
    "Fill",
    "Instrument",
    "LedgerSnapshot",
    "MissedFill",
    "MissedFillReason",
    "PeriodAttribution",
    "Position",
    "Price",
    "Quantity",
    "SessionDate",
    "Side",
    "SimulatedOrder",
    "Symbol",
    "TradeReason",
    "Universe",
    "Weight",
    "parse_side",
    "parse_symbol",
    "reconcile_session",
    "require_finite_decimal",
]
