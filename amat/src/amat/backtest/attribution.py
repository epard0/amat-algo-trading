"""Round-trip trade reconstruction from a fill stream, for attribution.

Why this layer exists
---------------------
The engine produces fills and positions, not "trades". Per-asset profit and loss,
win rate, and the concentration gates are all *trade-level* facts and have
nothing to attach to without a round trip. This module reconstructs round trips
from the fill stream so those statistics derive from the same immutable artifacts
the run produced, never from a recomputed sample.

Position accounting uses **average cost**: a partial sell realizes P&L against
the running average entry price and leaves the average unchanged. Entry cost is
apportioned by the fraction of the position being closed, so a partial exit
carries its share rather than all of it.

Provenance
----------
This began life as ``amat.risk.trades`` inside the v2 managed overlay, and was
deleted with it on 2026-08-10 before that deletion was found to be wrong:
:func:`amat.reporting.build_report` replays fills through :class:`TradeLedger` to
produce per-asset attribution, which is how the **v1** concentration gates are
computed. The reconstruction was never overlay logic; it was reporting
infrastructure that happened to live in the overlay's package.

What did *not* come back is the part that genuinely was overlay: ``TradeGrade``
and ``grade_setup`` scored a setup 1-3 against context and regime criteria, and
``min_grade`` was the single largest source of vetoes in the rejected engine
(15,413, 62% of all). Grades, context, and regime labels are gone from the trade
records here, and a trade carries only what a fill can prove.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from amat.domain.fills import Fill
from amat.domain.types import Cash, Price, Quantity, SessionDate, Side, Symbol
from amat.errors import DataIntegrityError

__all__ = ["ClosedTrade", "OpenTrade", "TradeLedger"]


@dataclass(slots=True)
class OpenTrade:
    """A position being accumulated, with running excursion tracking."""

    symbol: Symbol
    entry_session: SessionDate
    quantity: Quantity
    average_price: Price
    entry_cost: Cash
    initial_risk: Cash | None = None
    max_favourable: Cash = Decimal(0)
    max_adverse: Cash = Decimal(0)
    sessions_held: int = 0
    peak_quantity: Quantity = Decimal(0)

    def mark(self, price: Price) -> None:
        """Update excursions from a session close.

        MAE and MFE are the two numbers that reveal whether a stop would have
        helped or hurt, and they are unrecoverable after the fact, so they are
        accumulated as the trade runs.
        """
        self.sessions_held += 1
        open_pnl = (price - self.average_price) * self.quantity
        self.max_favourable = max(self.max_favourable, open_pnl)
        self.max_adverse = min(self.max_adverse, open_pnl)


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """A completed round trip and every metric measurable from it."""

    symbol: Symbol
    entry_session: SessionDate
    exit_session: SessionDate
    quantity: Quantity
    entry_price: Price
    exit_price: Price
    gross_pnl: Cash
    costs: Cash
    net_pnl: Cash
    initial_risk: Cash | None
    max_favourable: Cash
    max_adverse: Cash
    sessions_held: int

    @property
    def is_win(self) -> bool:
        """Net-of-cost win. Gross wins that cost turns into losses are losses."""
        return self.net_pnl > 0

    @property
    def r_multiple(self) -> float | None:
        """Net P&L in units of the risk committed at entry.

        ``None`` when no risk was recorded. A fabricated denominator here would
        silently corrupt every downstream statistic computed from it.
        """
        if self.initial_risk is None or self.initial_risk <= 0:
            return None
        return float(self.net_pnl / self.initial_risk)

    @property
    def mae_r(self) -> float | None:
        """Maximum adverse excursion in R."""
        if self.initial_risk is None or self.initial_risk <= 0:
            return None
        return float(self.max_adverse / self.initial_risk)

    @property
    def mfe_r(self) -> float | None:
        """Maximum favourable excursion in R."""
        if self.initial_risk is None or self.initial_risk <= 0:
            return None
        return float(self.max_favourable / self.initial_risk)

    @property
    def return_pct(self) -> float:
        """Net return on the entry notional."""
        notional = self.entry_price * self.quantity
        if notional <= 0:
            return 0.0
        return float(self.net_pnl / notional)

    def as_dict(self) -> dict[str, object]:
        """Flat mapping for a trade journal."""
        return {
            "symbol": self.symbol,
            "entry_session": self.entry_session.isoformat(),
            "exit_session": self.exit_session.isoformat(),
            "quantity": float(self.quantity),
            "entry_price": float(self.entry_price),
            "exit_price": float(self.exit_price),
            "gross_pnl": float(self.gross_pnl),
            "costs": float(self.costs),
            "net_pnl": float(self.net_pnl),
            "is_win": self.is_win,
            "r_multiple": self.r_multiple,
            "mae_r": self.mae_r,
            "mfe_r": self.mfe_r,
            "return_pct": self.return_pct,
            "sessions_held": self.sessions_held,
        }


class TradeLedger:
    """Reconstructs round trips from a fill stream.

    Average-cost accounting, as documented at module level. Sells beyond the held
    quantity are rejected rather than netted into a short, matching the
    long-or-cash mandate.
    """

    def __init__(self) -> None:
        self._open: dict[Symbol, OpenTrade] = {}
        self._closed: list[ClosedTrade] = []

    @property
    def closed(self) -> tuple[ClosedTrade, ...]:
        """Completed round trips, in close order."""
        return tuple(self._closed)

    @property
    def open_symbols(self) -> tuple[Symbol, ...]:
        """Symbols with an open position."""
        return tuple(self._open)

    def open_trade(self, symbol: Symbol) -> OpenTrade | None:
        """The open trade for ``symbol``, if any."""
        return self._open.get(symbol)

    def apply_split(self, symbol: Symbol, ratio: Decimal) -> None:
        """Rescale an open trade without changing its economic value or cost."""
        existing = self._open.get(symbol)
        if existing is None:
            return
        if ratio <= 0:
            message = f"{symbol}: split ratio must be positive, received {ratio}"
            raise DataIntegrityError(message)
        existing.quantity *= ratio
        existing.peak_quantity *= ratio
        existing.average_price /= ratio

    def apply(self, fill: Fill, *, initial_risk: Cash | None = None) -> ClosedTrade | None:
        """Apply one fill. Returns a closed trade if this fill flattened one.

        Raises:
            DataIntegrityError: on a sell with no open position, or a sell
                exceeding the held quantity. Either would net into a short, which
                the long-or-cash contract does not permit.
        """
        existing = self._open.get(fill.symbol)

        if fill.side == Side.BUY:
            if existing is None:
                self._open[fill.symbol] = OpenTrade(
                    symbol=fill.symbol,
                    entry_session=fill.execution_session,
                    quantity=fill.quantity,
                    average_price=fill.price,
                    entry_cost=fill.cost_cash,
                    initial_risk=initial_risk,
                    peak_quantity=fill.quantity,
                )
            else:
                total = existing.quantity + fill.quantity
                existing.average_price = (
                    existing.average_price * existing.quantity + fill.price * fill.quantity
                ) / total
                existing.quantity = total
                existing.entry_cost += fill.cost_cash
                existing.peak_quantity = max(existing.peak_quantity, total)
            return None

        if existing is None:
            message = (
                f"{fill.symbol} {fill.execution_session.isoformat()}: sell with no open "
                "position; the v1 strategy is long-or-cash and never shorts"
            )
            raise DataIntegrityError(message)
        if fill.quantity > existing.quantity:
            message = (
                f"{fill.symbol} {fill.execution_session.isoformat()}: sell of "
                f"{fill.quantity} exceeds held {existing.quantity}"
            )
            raise DataIntegrityError(message)

        gross = (fill.price - existing.average_price) * fill.quantity
        # Entry cost is apportioned by the fraction of the position being closed,
        # so a partial exit carries its share rather than all of it.
        share = fill.quantity / existing.quantity
        entry_cost_share = existing.entry_cost * share
        costs = entry_cost_share + fill.cost_cash

        existing.entry_cost -= entry_cost_share
        existing.quantity -= fill.quantity

        closed = ClosedTrade(
            symbol=fill.symbol,
            entry_session=existing.entry_session,
            exit_session=fill.execution_session,
            quantity=fill.quantity,
            entry_price=existing.average_price,
            exit_price=fill.price,
            gross_pnl=gross,
            costs=costs,
            net_pnl=gross - costs,
            initial_risk=existing.initial_risk,
            max_favourable=existing.max_favourable,
            max_adverse=existing.max_adverse,
            sessions_held=existing.sessions_held,
        )
        self._closed.append(closed)

        if existing.quantity == 0:
            del self._open[fill.symbol]
        return closed

    def mark_all(self, marks: Mapping[Symbol, Price]) -> None:
        """Update excursions for every open trade from this session's closes."""
        for symbol, trade in self._open.items():
            price = marks.get(symbol)
            if price is not None:
                trade.mark(price)

    def apply_session(
        self,
        fills: Sequence[Fill],
        marks: Mapping[Symbol, Price],
        *,
        initial_risk: Cash | None = None,
    ) -> tuple[ClosedTrade, ...]:
        """Apply a session's fills then re-mark. Returns trades closed today.

        Sells are applied before buys so that a same-session flip closes the old
        trade before opening the new one, rather than averaging the two together.
        """
        closed: list[ClosedTrade] = []
        ordered = sorted(fills, key=lambda item: (item.side == Side.BUY, item.symbol))
        for fill in ordered:
            result = self.apply(fill, initial_risk=initial_risk)
            if result is not None:
                closed.append(result)
        self.mark_all(marks)
        return tuple(closed)
