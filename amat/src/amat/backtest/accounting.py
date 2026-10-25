"""Session accounting: advance the ledger and explain every unit of equity change.

Implements :class:`amat.boundaries.AccountingBoundary`.

The identity enforced each session is::

    equity           == cash + sum(quantity * mark)
    change in equity == market P&L + distributions + cash yield - fees - slippage

Corporate actions are a required input, not an optional one. A cash dividend
credits cash and is attributed to ``distributions``; a split rescales shares and
the mark together and contributes nothing.

What the identity does and does not catch
-----------------------------------------
Omitting the actions input **fails silently**. Measured 2026-08-10: a 1,000-share
position carried across a 2:1 split with no action supplied fell from 102,220.00
to 55,920.00, booked the entire 46,300.00 as market P&L, and reconciled with a
residual of exactly 0.00.

That is not a bug in :func:`reconcile_session`; it is what the identity means.
``market_pnl`` below is *defined* as ``closing value - opening value - traded
notional``, so it absorbs any mark movement by construction. The check therefore
proves that every unit of cash and every share is accounted for -- it cannot
prove that a price step had a cause. Earlier revisions of this docstring
asserted that a missing split raises here. It does not, and believing it did is
what let the actions input go unsupplied by every caller for two years.

**Nothing else catches it either, so the vendor's completeness is the control.**
``_check_daily_quality`` rejects unexplained discontinuities at snapshot-write
time, but it measures them on the *adjusted* series -- and a split does not move
the adjusted series, only the raw one that valuation uses. A snapshot whose
vendor omitted a split therefore passes every gate in this repository and
silently halves a position. The honest statement is that this system detects a
*mislabelled* corporate action and does not detect a *missing* one.

Double-count guard: distribution cash enters *only* through
:meth:`~amat.domain.actions.CorporateAction.cash_credit`. Research features use
the adjusted close, which already embeds dividends, but valuation here uses the
raw close, so the dividend is counted exactly once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.fills import Fill
from amat.domain.ledgers import (
    DEFAULT_TOLERANCE,
    LedgerSnapshot,
    PeriodAttribution,
    reconcile_session,
)
from amat.domain.positions import Position
from amat.domain.types import Cash, Price, SessionDate, Side, Symbol
from amat.errors import AccountingError

__all__ = ["SessionAccountant"]


class SessionAccountant:
    """Advances a ledger one session at a time, reconciling every step."""

    def __init__(self, *, tolerance: Cash = DEFAULT_TOLERANCE) -> None:
        self._tolerance = tolerance

    def advance(
        self,
        previous: LedgerSnapshot,
        session: SessionDate,
        fills: Sequence[Fill],
        marks: Mapping[Symbol, Price],
        *,
        actions: Sequence[CorporateAction] = (),
        cash_rate_annualized: float = 0.0,
        sessions_per_year: int = 252,
    ) -> tuple[LedgerSnapshot, PeriodAttribution]:
        """Advance by one session and return the reconciled ledger.

        The order of operations is deliberate and must not be permuted:

        1. accrue cash yield on the *opening* balance;
        2. apply corporate actions to opening positions (entitlement is a
           function of shares held before the session's trading);
        3. apply fills at their execution prices;
        4. re-mark surviving positions to this session's closes;
        5. reconcile.

        Raises:
            AccountingError: if a fill or action references an unmarked symbol,
                a sale exceeds the held quantity, or the residual exceeds
                tolerance.
        """
        quantities: dict[Symbol, Decimal] = {
            symbol: position.quantity for symbol, position in previous.positions.items()
        }
        opening_marks: dict[Symbol, Price] = {
            symbol: position.mark for symbol, position in previous.positions.items()
        }
        cash = previous.cash

        # 1. Cash yield on the opening balance.
        cash_yield = self._accrue(cash, cash_rate_annualized, sessions_per_year)
        cash += cash_yield

        # 2. Corporate actions, against opening share counts.
        distributions = Decimal(0)
        for action in actions:
            held = quantities.get(action.symbol, Decimal(0))
            if held == 0:
                continue
            if action.kind == ActionKind.CASH_DIVIDEND:
                distributions += action.cash_credit(held)
            elif action.kind == ActionKind.SPLIT:
                quantities[action.symbol] = action.adjust_quantity(held)
                if action.symbol in opening_marks:
                    opening_marks[action.symbol] = action.adjust_mark(opening_marks[action.symbol])
        cash += distributions

        # Opening value is captured *after* actions and *before* fills. A split
        # rescales quantity and mark inversely, so this total is split-invariant
        # -- which is exactly why a split contributes nothing to market P&L.
        opening_values: dict[Symbol, Decimal] = {
            symbol: quantity * opening_marks[symbol]
            for symbol, quantity in quantities.items()
            if symbol in opening_marks
        }

        # 3. Fills.
        fees = Decimal(0)
        traded_notional: dict[Symbol, Decimal] = {}
        for fill in fills:
            if fill.symbol not in marks:
                message = (
                    f"{session.isoformat()}: fill for {fill.symbol} has no closing mark; "
                    "a position change must never outlive its valuation"
                )
                raise AccountingError(message)
            held = quantities.get(fill.symbol, Decimal(0))
            new_quantity = held + fill.signed_quantity
            if new_quantity < 0:
                message = (
                    f"{session.isoformat()}: {fill.symbol} sale of {fill.quantity} exceeds "
                    f"held {held}; the v1 strategy is long-or-cash and never shorts"
                )
                raise AccountingError(message)
            quantities[fill.symbol] = new_quantity
            cash += fill.cash_delta
            fees += fill.cost_cash
            signed = fill.gross_notional if fill.side == Side.BUY else -fill.gross_notional
            traded_notional[fill.symbol] = traded_notional.get(fill.symbol, Decimal(0)) + signed

        # 4. Re-mark to this session's closes.
        positions: dict[Symbol, Position] = {}
        market_pnl = Decimal(0)
        for symbol, quantity in quantities.items():
            if quantity == 0:
                continue
            mark = marks.get(symbol)
            if mark is None:
                message = (
                    f"{session.isoformat()}: holding {symbol} has no closing mark; "
                    "valuation may not fall back to a stale price"
                )
                raise AccountingError(message)
            positions[symbol] = Position(
                symbol=symbol, quantity=quantity, mark=mark, as_of_session=session
            )

        # Market P&L is the change in marked value that trading does not explain:
        #   (closing value - opening value) - net notional acquired.
        # Buying N shares for cash raises marked value by exactly that notional,
        # so subtracting it isolates price movement from position change.
        for symbol in set(opening_values) | set(quantities) | set(traded_notional):
            closing_quantity = quantities.get(symbol, Decimal(0))
            closing_mark = marks.get(symbol)
            closing_value = (
                closing_quantity * closing_mark if closing_mark is not None else Decimal(0)
            )
            market_pnl += (
                closing_value
                - opening_values.get(symbol, Decimal(0))
                - traded_notional.get(symbol, Decimal(0))
            )

        current = LedgerSnapshot(session_date=session, cash=cash, positions=positions)
        # `slippage` is zero BY DESIGN, not because slippage is unmodeled: the
        # configured one-way bps is the combined commission/spread/slippage
        # assumption and books under `fees` (every fill's cost_cash), while
        # price slippage proper -- sizing at decision marks, filling at the
        # next session's window VWAP -- lands in market_pnl where it belongs.
        attribution = PeriodAttribution(
            market_pnl=market_pnl,
            distributions=distributions,
            cash_yield=cash_yield,
            fees=fees,
            slippage=Decimal(0),
        )
        self._reconcile(previous, current, attribution)
        return current, attribution

    def _reconcile(
        self, previous: LedgerSnapshot, current: LedgerSnapshot, attribution: PeriodAttribution
    ) -> None:
        reconcile_session(previous, current, attribution, tolerance=self._tolerance)

    @staticmethod
    def _accrue(balance: Cash, annualized_rate: float, sessions_per_year: int) -> Cash:
        """Simple per-session accrual on the opening cash balance."""
        if annualized_rate == 0.0 or balance <= 0:
            return Decimal(0)
        per_session = Decimal(str(annualized_rate)) / Decimal(sessions_per_year)
        return (balance * per_session).quantize(Decimal("0.000001"))
