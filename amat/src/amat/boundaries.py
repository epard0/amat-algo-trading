"""Protocols for the five architectural boundaries.

These protocols exist so that the research runner and the future event-driven
adapter are structurally forced to call the same strategy core. An adapter that
re-implements signal or allocation mathematics inside a framework callback
cannot satisfy :class:`DecisionBoundary`, and the parity tests will say so.

Nothing here is implemented yet. The stage packages (``amat.data``,
``amat.features``, ``amat.portfolio``, ``amat.execution``, ``amat.backtest``)
are populated in later phases; see ``.claude/references/workflow-map.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import polars as pl

    from amat.domain.actions import CorporateAction
    from amat.domain.bars import ExecutionWindowBar
    from amat.domain.fills import Fill, MissedFill
    from amat.domain.ledgers import LedgerSnapshot, PeriodAttribution
    from amat.domain.orders import SimulatedOrder
    from amat.domain.types import Cash, Price, SessionDate, Symbol, Weight
    from amat.provenance import RunManifest

__all__ = [
    "AccountingBoundary",
    "DataBoundary",
    "DecisionBoundary",
    "ExecutionBoundary",
    "ReportingBoundary",
]


class DataBoundary(Protocol):
    """Immutable source snapshots enter; normalized typed tables leave.

    Implementations must fail closed on schema violations, partial provider
    pagination, non-session dates, and unexplained coverage gaps.
    """

    def normalize(self, snapshot_id: str) -> pl.DataFrame:
        """Produce the normalized table for a named immutable snapshot.

        Raises:
            DataIntegrityError: if the snapshot fails any declared invariant.
        """
        ...


class DecisionBoundary(Protocol):
    """Only information available by the decision timestamp enters; weights leave.

    The ``decision_session`` argument is the contract: an implementation may
    read history up to and including that completed session and nothing after
    it. Leakage tests mutate post-decision rows and assert the output is
    unchanged.
    """

    def target_weights(
        self,
        decision_session: SessionDate,
        history: pl.DataFrame,
    ) -> Mapping[Symbol, Weight]:
        """Return non-negative target weights for the decision session.

        Raises:
            CausalityError: if ``history`` contains rows at or after the
                decision session's successor.
        """
        ...


class ExecutionBoundary(Protocol):
    """Target deltas and future observations enter; fills or missed fills leave.

    An implementation never returns a synthetic price. If the configured window
    has no bar or no volume, it returns a :class:`~amat.domain.fills.MissedFill`.

    The window argument is an :class:`~amat.domain.bars.ExecutionWindowBar`, not
    a :class:`~amat.domain.bars.DailyBar`, because the contract prices fills at
    the raw VWAP of the configured opening window. A daily OHLC bar cannot
    express that quantity, so accepting one here would force every conforming
    implementation to substitute the session open -- a silent fallback the
    validation protocol classifies as a sensitivity rather than the canonical
    fill.
    """

    def execute(
        self,
        instructions: Sequence[SimulatedOrder],
        windows: Mapping[Symbol, ExecutionWindowBar],
        available_cash: Cash | None = None,
    ) -> tuple[Sequence[Fill], Sequence[MissedFill]]:
        """Attempt each instruction against the execution window.

        ``available_cash`` lets the implementation refuse a purchase it cannot
        fund. The v1 mandate permits no leverage, and sizing at one session's
        marks while filling at another's price can leave a buy short.
        """
        ...


class AccountingBoundary(Protocol):
    """Prior ledger, fills, actions, and marks enter; a reconciled ledger leaves.

    ``actions`` is required, not optional: guardrail 6 demands that every
    position change trace to a fill *or a corporate action*, and a
    :class:`~amat.domain.fills.Fill` cannot represent either a cash dividend
    (cash, no shares) or a split (shares, no cash).

    Omitting it does **not** raise. This docstring claimed for months that a
    missing split would "raise a spurious :class:`~amat.errors.AccountingError`",
    and that is false -- measured 2026-08-10 on a 1,000-share position across a
    2:1 split: equity fell 102,220.00 to 55,920.00, the whole 46,300.00 was
    booked as market P&L, and the reconciliation residual was exactly 0.00.

    The reason is structural and worth stating rather than hiding. ``market_pnl``
    is *computed* as ``closing value - opening value - traded notional``, so it
    absorbs whatever the marks did by construction. The identity
    ``equity change == market P&L + distributions + cash yield - fees -
    slippage`` therefore verifies **bookkeeping consistency, not mark
    plausibility**: it catches cash that appeared from nowhere, and it cannot
    catch a price series that stepped for a reason nobody recorded.

    Nor does anything upstream. ``_check_daily_quality`` measures discontinuity
    on the *adjusted* series, which a split leaves smooth; the jump is in the
    *raw* series that valuation uses. A vendor that omits a split passes every
    gate here. Supplying complete actions is the control, not a backstop for one.
    """

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
        """Advance the ledger by one session and explain the equity change.

        Raises:
            AccountingError: if the equity change is not fully explained.
        """
        ...


class ReportingBoundary(Protocol):
    """Immutable run artifacts enter; presentation artifacts leave.

    A reporting implementation must not recompute strategy results. It reads a
    run manifest and the artifacts that manifest references, and renders them.
    """

    def render(self, manifest: RunManifest) -> Sequence[str]:
        """Render artifacts for a completed run and return their paths."""
        ...
