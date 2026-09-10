"""The research runner: an explicit, ordered session loop.

Feature computation is vectorized, but portfolio state advances one session at a
time in calendar order. That ordering is not an efficiency choice -- it is what
makes the causality claim checkable, because at no point does the loop hold a
target computed from data the ledger has not yet reached.

Timing, restated from the contract and enforced here:

* the decision for session ``t`` is formed from closes through ``t``;
* its instructions carry ``execution_session = t + lag`` (lag >= 1);
* they fill against the execution window of that later session;
* the ledger is then marked at that later session's raw closes.

No step *uses* a row beyond the one it is entitled to. Stated precisely,
because the feature cache computes over the full panel up front: every
feature recursion is strictly causal, so row ``t`` of the cache is
bit-identical to what a pass over the prefix ``[:t+1]`` would produce -- the
future-mutation tests assert exactly that -- and the session loop itself
never touches a row past its session.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

import numpy as np

from amat.backtest.accounting import SessionAccountant
from amat.backtest.exits import ExitPolicy, apply_exit_rules, track_entry_bases
from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.fills import Fill, MissedFill, MissedFillReason
from amat.domain.instruments import Universe
from amat.domain.ledgers import LedgerSnapshot, PeriodAttribution
from amat.domain.orders import SimulatedOrder, TradeReason
from amat.domain.types import Cash, SessionDate, Side, Symbol
from amat.errors import CausalityError, ConfigurationError, DataIntegrityError
from amat.execution import SimulatedExecutionModel, build_instructions
from amat.portfolio import (
    AllocationResult,
    PrecomputedDecisionInputs,
    decide,
    precompute_decision_inputs,
)
from amat.validation.conditioning import regime_scalars

__all__ = [
    "BacktestResult",
    "DecisionOverlay",
    "RunResult",
    "SessionRecord",
    "StanceBlend",
    "run_backtest",
]


@dataclass(frozen=True, slots=True)
class DecisionOverlay:
    """Causal per-asset v6 risk and entry-quality decisions.

    ``risk_multipliers`` may only move an allocator target toward cash.
    ``allow_increase`` is deliberately asymmetric: a ``False`` cell blocks a
    buy or increase but never blocks a risk-reducing sale.  This is the v6
    liquidity rule -- quiet tape is a reason not to add, not a reason to trap
    the account in a position it is already trying to close.

    Both arrays are precomputed so they can be inspected, leak-tested, and
    replaced by controls independently of the session loop.
    """

    risk_multipliers: np.ndarray
    allow_increase: np.ndarray
    increase_multipliers: np.ndarray | None = None


@dataclass(frozen=True, slots=True)
class StanceBlend:
    """The v4 hook: a second, defensive stance of the same core, blended in.

    Both stances are computed by the same :func:`amat.portfolio.decide` -- the
    one-strategy-core guardrail -- with different frozen configurations. Each
    session's targets become the convex combination

        ``target = lambda * trend_target + (1 - lambda) * defensive_target``

    with ``lambda`` read from a precomputed ``(T,)`` series. Precomputed for
    the same reason ``weight_scalars`` is: an array can be leak-tested,
    shuffled into a control, and replaced by its own mean, none of which is
    possible with in-loop mutable state.

    Why convex blending is safe by construction: every linear cap (symbol,
    cluster, gross) satisfied by *both* stance target vectors is satisfied by
    every convex combination of them, bounded by the looser of the two
    declared values. v4's effective constraint set is therefore the
    element-wise maximum of its stances' caps, both of which are validated
    configurations in their own right.

    Session records carry the *trend* stance's allocation diagnostics
    (forecast volatility, binding constraints); the blend changes the targets
    only. A blended forecast-risk figure would require a joint covariance
    treatment the contract does not define, and a mislabelled number is worse
    than a labelled omission.

    Attributes:
        strategy: The defensive stance's validated configuration.
        universe: Its universe. Must be a subset of the primary universe --
            an instruction for a symbol without panel marks could never fill.
        adjusted_closes: ``(T, M)`` adjusted closes for exactly the stance's
            symbols, row-aligned to the primary panel's sessions.
        weights: ``(T,)`` blend weight toward the PRIMARY (trend) stance,
            in ``[0, 1]``; ``1.0`` is pure v1.
    """

    strategy: StrategyConfig
    universe: Universe
    adjusted_closes: np.ndarray
    weights: np.ndarray


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """What happened in one simulated session."""

    session: SessionDate
    equity: Cash
    cash: Cash
    gross_exposure: float
    forecast_volatility_pre: float
    forecast_volatility_post: float
    fills: tuple[Fill, ...]
    missed: tuple[MissedFill, ...]
    attribution: PeriodAttribution
    binding_constraints: tuple[str, ...]


@runtime_checkable
class RunResult(Protocol):
    """What every runner's result must expose to metrics and reports.

    Both :class:`BacktestResult` and
    :class:`amat.backtest.managed.ManagedBacktestResult` satisfy this. Writing it
    down replaces the ``# type: ignore[arg-type]`` that previously appeared at
    every call site where a managed result met a function annotated for the
    research result -- an ignore comment silences the checker without recording
    what the two types actually have in common, so the next divergence between
    them would have gone unnoticed.
    """

    @property
    def sessions(self) -> tuple[SessionRecord, ...]: ...

    @property
    def initial_equity(self) -> Cash: ...

    @property
    def one_way_bps(self) -> float: ...

    @property
    def is_primary(self) -> bool: ...

    @property
    def universe_symbols(self) -> tuple[str, ...]: ...

    @property
    def warnings(self) -> tuple[str, ...]: ...

    @property
    def final_equity(self) -> Cash: ...

    @property
    def equity_curve(self) -> tuple[Cash, ...]: ...

    @property
    def session_dates(self) -> tuple[SessionDate, ...]: ...

    @property
    def total_fills(self) -> int: ...

    @property
    def total_missed(self) -> int: ...

    @property
    def total_costs(self) -> Cash: ...


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The full record of one run. Reports read this; they never recompute it."""

    sessions: tuple[SessionRecord, ...]
    initial_equity: Cash
    one_way_bps: float
    is_primary: bool
    universe_symbols: tuple[str, ...]
    warnings: tuple[str, ...] = field(default=())

    @property
    def final_equity(self) -> Cash:
        return self.sessions[-1].equity if self.sessions else self.initial_equity

    @property
    def equity_curve(self) -> tuple[Cash, ...]:
        return tuple(record.equity for record in self.sessions)

    @property
    def session_dates(self) -> tuple[SessionDate, ...]:
        return tuple(record.session for record in self.sessions)

    @property
    def total_fills(self) -> int:
        return sum(len(record.fills) for record in self.sessions)

    @property
    def total_missed(self) -> int:
        return sum(len(record.missed) for record in self.sessions)

    @property
    def total_costs(self) -> Cash:
        return sum((record.attribution.fees for record in self.sessions), start=Decimal(0))


#: Attribution for the opening session, before any trading has occurred.
_ZERO_ATTRIBUTION = PeriodAttribution(
    market_pnl=Decimal(0),
    distributions=Decimal(0),
    cash_yield=Decimal(0),
    fees=Decimal(0),
    slippage=Decimal(0),
)


def _clamp_sales_to_holdings(
    instructions: list[SimulatedOrder], ledger: LedgerSnapshot
) -> tuple[list[SimulatedOrder], list[MissedFill]]:
    """Cap every scheduled sale at what is actually held, recording the excess.

    Only reachable at a stressed lag >= 2: in-flight netting can schedule a
    sale against shares a pending buy was expected to deliver, and when that
    buy misses, the sale is unbacked. A cash account rejects the excess; so
    does this, loudly (``UNBACKED_SALE``), never by shorting. At the baseline
    lag of 1 nothing is in flight at decision time, so this function is an
    exact pass-through there -- the golden-digest identity check pins that.
    """
    available: dict[Symbol, Decimal] = {
        symbol: position.quantity for symbol, position in ledger.positions.items()
    }
    kept: list[SimulatedOrder] = []
    unbacked: list[MissedFill] = []
    for order in instructions:
        if order.side is not Side.SELL:
            kept.append(order)
            continue
        remaining = available.get(order.symbol, Decimal(0))
        sellable = min(order.quantity, remaining)
        available[order.symbol] = remaining - sellable
        excess = order.quantity - sellable
        if excess > 0:
            unbacked.append(
                MissedFill(
                    symbol=order.symbol,
                    execution_session=order.execution_session,
                    side=order.side,
                    requested_quantity=excess,
                    reason=MissedFillReason.UNBACKED_SALE,
                )
            )
        if sellable > 0:
            kept.append(
                order
                if sellable == order.quantity
                else SimulatedOrder(
                    symbol=order.symbol,
                    signal_session=order.signal_session,
                    execution_session=order.execution_session,
                    side=order.side,
                    quantity=sellable,
                    reason=order.reason,
                )
            )
    return kept, unbacked


def _settle_session(
    ledger: LedgerSnapshot,
    session: SessionDate,
    index: int,
    panel: MarketPanel,
    *,
    execution_model: SimulatedExecutionModel,
    accountant: SessionAccountant,
    instructions: list[SimulatedOrder],
    carried_missed: list[MissedFill],
    actions: Sequence[CorporateAction],
    cash_rate: float,
    sessions_per_year: int,
) -> tuple[LedgerSnapshot, PeriodAttribution, list[Fill], list[MissedFill]]:
    """Execute this session's scheduled instructions and advance the ledger.

    Instructions that could not even be attempted (``carried_missed``, e.g. those
    below the minimum notional) are merged into the session's missed-fill record
    so that no unreached target is silently lost.
    """
    instructions, unbacked = _clamp_sales_to_holdings(instructions, ledger)
    fills, missed = execution_model.execute(
        instructions, panel.windows_at(index), available_cash=ledger.cash
    )
    missed = [*unbacked, *missed]
    advanced, attribution = accountant.advance(
        ledger,
        session,
        fills,
        panel.marks_at(index),
        actions=actions,
        cash_rate_annualized=cash_rate,
        sessions_per_year=sessions_per_year,
    )
    return advanced, attribution, fills, [*carried_missed, *missed]


def _rescale_pending_across_split(
    pending: dict[SessionDate, list[SimulatedOrder]],
    pending_missed: dict[SessionDate, list[MissedFill]],
    actions: Sequence[CorporateAction],
) -> list[str]:
    """Restate in-flight instructions in post-split shares.

    An instruction is an absolute share count fixed from the decision session's
    marks, and it fills one or more sessions later. If a split lands in between,
    the count is denominated in shares that no longer exist. Both directions are
    wrong and only one is loud:

    * a **reverse** split shrinks the holding, so a pending full exit now
      exceeds it and :class:`~amat.errors.AccountingError` fires -- a crash;
    * a **forward** split doubles the holding, so a pending full exit sells half
      of it and the loop carries on holding a position it meant to close --
      silent, and it survives into every downstream statistic.

    Verified against the live accountant on 2026-08-10: 1,000 shares, a pending
    full-exit sale of 1,000, ratio 0.125 raises "sale of 1000 exceeds held 125";
    ratio 2 leaves 1,000 shares still held after an intended exit.

    Notional is preserved exactly -- quantity scales by ``ratio`` while the
    window price scales by ``1/ratio`` -- so the rescaled instruction buys or
    sells the same money and needs no minimum-notional recheck.

    This is only reachable because corporate actions are now applied at all.
    Before that, holdings and instructions were both denominated in unadjusted
    shares and agreed with each other by being equally wrong.

    Returns:
        One warning string per rescaled symbol, for the run's warning list. A
        share count changing underneath an instruction is not a routine event
        and should be visible in the artifact.
    """
    ratios = {action.symbol: action.ratio for action in actions if action.kind == ActionKind.SPLIT}
    if not ratios:
        return []

    notes: list[str] = []
    for symbol, ratio in ratios.items():
        touched = 0
        for orders in pending.values():
            for index, order in enumerate(orders):
                if order.symbol != symbol:
                    continue
                orders[index] = SimulatedOrder(
                    symbol=order.symbol,
                    signal_session=order.signal_session,
                    execution_session=order.execution_session,
                    side=order.side,
                    quantity=order.quantity * ratio,
                    # The instruction is no longer purely the rebalance decision
                    # that produced it; a corporate action changed its size.
                    reason=TradeReason.CORPORATE_ACTION,
                )
                touched += 1
        for missed in pending_missed.values():
            for index, entry in enumerate(missed):
                if entry.symbol != symbol:
                    continue
                missed[index] = MissedFill(
                    symbol=entry.symbol,
                    execution_session=entry.execution_session,
                    side=entry.side,
                    requested_quantity=entry.requested_quantity * ratio,
                    reason=entry.reason,
                )
                touched += 1
        if touched:
            notes.append(
                f"{symbol}: {touched} in-flight instruction(s) rescaled by split ratio {ratio}"
            )
    return notes


def _schedule_decision(
    strategy: StrategyConfig,
    panel: MarketPanel,
    universe: Universe,
    *,
    index: int,
    execution_index: int,
    ledger: LedgerSnapshot,
    pending: dict[SessionDate, list[SimulatedOrder]],
    pending_missed: dict[SessionDate, list[MissedFill]],
    weight_scalars: np.ndarray | None,
    decision_overlay: DecisionOverlay | None = None,
    stance: StanceBlend | None = None,
    exit_rules: ExitPolicy | None = None,
    entry_basis: Mapping[Symbol, float] | None = None,
    embargo_until: dict[Symbol, int] | None = None,
    symbol_columns: Mapping[Symbol, int] | None = None,
    precomputed: PrecomputedDecisionInputs | None = None,
    stance_inputs: PrecomputedDecisionInputs | None = None,
) -> tuple[AllocationResult | None, str | None]:
    """Form session ``index``'s decision and schedule it into a later session.

    Returns the allocation for the session record, and a warning string when no
    decision could be formed. A session with no decision is recorded rather than
    skipped: an unformed decision is a fact about the run, not an absence.
    """
    session = panel.sessions[index]
    try:
        allocation = decide(
            strategy,
            panel.adjusted_closes,
            universe,
            decision_index=index,
            precomputed=precomputed,
        )
    except DataIntegrityError as exc:
        return None, f"{session.isoformat()}: no decision formed ({exc})"

    targets = allocation.as_mapping(universe)
    if stance is not None:
        blend = float(stance.weights[index])
        if blend < 1.0:
            try:
                defensive = decide(
                    stance.strategy,
                    stance.adjusted_closes,
                    stance.universe,
                    decision_index=index,
                    precomputed=stance_inputs,
                )
            except DataIntegrityError as exc:
                # Fail the whole decision rather than silently reverting to the
                # pure trend stance: a v4 session that quietly became v1 would
                # make the blend series unauditable.
                return None, f"{session.isoformat()}: no defensive decision formed ({exc})"
            defensive_targets = defensive.as_mapping(stance.universe)
            targets = {
                symbol: blend * targets.get(symbol, 0.0)
                + (1.0 - blend) * defensive_targets.get(symbol, 0.0)
                for symbol in set(targets) | set(defensive_targets)
            }
    if weight_scalars is not None:
        # v3's regime conditioning, and the only place it acts. Scaling the
        # target weights leaves the allocator's relative mix untouched and moves
        # the whole book toward cash, which is what a risk scalar means.
        scalar = float(weight_scalars[index])
        targets = {symbol: weight * scalar for symbol, weight in targets.items()}
    if decision_overlay is not None:
        targets = _apply_decision_overlay(
            targets,
            current_weights=ledger.weights(),
            symbols=universe.symbols,
            overlay=decision_overlay,
            index=index,
        )
    if exit_rules is not None:
        # The exit overlay acts last: whatever the allocator, stance, and
        # scalar chose, a breached or embargoed symbol goes to cash. It reads
        # only this session's close, settled fills' bases, and embargoes set
        # by earlier decisions, so the causality tests cover it unchanged.
        assert entry_basis is not None  # noqa: S101 - wired together in run_backtest
        assert embargo_until is not None  # noqa: S101
        assert symbol_columns is not None  # noqa: S101
        targets = apply_exit_rules(
            exit_rules,
            targets,
            positions=ledger.positions,
            entry_basis=entry_basis,
            embargo_until=embargo_until,
            decision_index=index,
            adjusted_row=panel.adjusted_closes[index],
            symbol_columns=symbol_columns,
        )

    execution_session = panel.sessions[execution_index]
    # Net signed quantities already scheduled but not yet settled. Empty at
    # the baseline lag of 1 (everything settles before the next decision);
    # at a stressed lag >= 2 it is what stops consecutive overlapping
    # decisions from selling the same shares twice.
    in_flight: dict[Symbol, Decimal] = {}
    for orders in pending.values():
        for order in orders:
            signed = order.quantity if order.side is Side.BUY else -order.quantity
            in_flight[order.symbol] = in_flight.get(order.symbol, Decimal(0)) + signed
    instructions, below_minimum = build_instructions(
        targets,
        ledger.positions,
        panel.marks_at(index),
        ledger.equity,
        signal_session=session,
        execution_session=execution_session,
        rebalance=strategy.rebalance,
        execution=strategy.execution,
        in_flight=in_flight,
    )
    if instructions:
        pending.setdefault(execution_session, []).extend(instructions)
    if below_minimum:
        pending_missed.setdefault(execution_session, []).extend(below_minimum)
    return allocation, None


def _apply_decision_overlay(
    targets: Mapping[Symbol, float],
    *,
    current_weights: Mapping[Symbol, float],
    symbols: Sequence[Symbol],
    overlay: DecisionOverlay,
    index: int,
) -> dict[Symbol, float]:
    """Apply v6's risk cap and asymmetric entry priority to one target row."""
    adjusted: dict[Symbol, float] = {}
    for column, symbol in enumerate(symbols):
        proposed = targets.get(symbol, 0.0) * float(overlay.risk_multipliers[index, column])
        current = current_weights.get(symbol, 0.0)
        if proposed > current:
            if not bool(overlay.allow_increase[index, column]):
                proposed = current
            elif overlay.increase_multipliers is not None:
                increase = proposed - current
                proposed = current + increase * float(overlay.increase_multipliers[index, column])
        adjusted[symbol] = proposed
    return adjusted


def _validate_weight_scalars(weight_scalars: np.ndarray | None, total: int) -> None:
    """Reject a conditioning series that would silently misalign or re-lever.

    Raises:
        DataIntegrityError: on a wrong length, a non-finite entry, or a value
            outside ``[0, 1]``. A misaligned series is the dangerous case: it
            conditions every session on some other session's regime and still
            produces a plausible-looking equity curve.
    """
    if weight_scalars is None:
        return
    if weight_scalars.shape != (total,):
        message = (
            f"weight_scalars has shape {weight_scalars.shape}, expected ({total},); "
            "a misaligned scalar series would condition every session on the wrong one"
        )
        raise DataIntegrityError(message)
    if not np.all(np.isfinite(weight_scalars)):
        message = "weight_scalars contains a non-finite value"
        raise DataIntegrityError(message)
    # Above 1.0 would re-lever past the allocator's own gross cap, which the
    # contract forbids outright; below 0 is meaningless for a long-only book.
    if np.any(weight_scalars < 0.0) or np.any(weight_scalars > 1.0):
        message = "weight_scalars must lie in [0, 1]; conditioning may reduce risk, never add"
        raise DataIntegrityError(message)


def _validate_decision_overlay(overlay: DecisionOverlay | None, total: int, symbols: int) -> None:
    """Reject a v6 overlay that is misaligned, non-finite, or risk-amplifying."""
    if overlay is None:
        return
    expected = (total, symbols)
    if overlay.risk_multipliers.shape != expected:
        message = (
            f"decision-overlay risk multipliers have shape "
            f"{overlay.risk_multipliers.shape}, expected {expected}"
        )
        raise DataIntegrityError(message)
    if overlay.allow_increase.shape != expected:
        message = (
            f"decision-overlay entry permissions have shape "
            f"{overlay.allow_increase.shape}, expected {expected}"
        )
        raise DataIntegrityError(message)
    if not np.all(np.isfinite(overlay.risk_multipliers)):
        raise DataIntegrityError("decision-overlay risk multipliers contain a non-finite value")
    if np.any(overlay.risk_multipliers < 0.0) or np.any(overlay.risk_multipliers > 1.0):
        message = "decision-overlay risk multipliers must lie in [0, 1]"
        raise DataIntegrityError(message)
    if overlay.allow_increase.dtype != np.bool_:
        message = "decision-overlay entry permissions must be a boolean array"
        raise DataIntegrityError(message)
    if overlay.increase_multipliers is not None:
        if overlay.increase_multipliers.shape != expected:
            message = (
                f"decision-overlay increase multipliers have shape "
                f"{overlay.increase_multipliers.shape}, expected {expected}"
            )
            raise DataIntegrityError(message)
        if not np.all(np.isfinite(overlay.increase_multipliers)):
            raise DataIntegrityError(
                "decision-overlay increase multipliers contain a non-finite value"
            )
        if np.any(overlay.increase_multipliers < 0.0) or np.any(overlay.increase_multipliers > 1.0):
            message = "decision-overlay increase multipliers must lie in [0, 1]"
            raise DataIntegrityError(message)


def _validate_stance(
    stance: StanceBlend | None, panel: MarketPanel, primary: StrategyConfig, total: int
) -> None:
    """Reject a stance blend that could misalign, re-lever, or trade off-panel.

    Raises:
        DataIntegrityError: on a blend series with the wrong shape or range, a
            closes matrix that does not match the stance universe row-for-row,
            a stance symbol absent from the primary universe, or a stance
            warm-up longer than the primary's (its decisions would fail exactly
            on the sessions the primary starts trading).
    """
    if stance is None:
        return
    if stance.weights.shape != (total,):
        message = (
            f"stance blend weights have shape {stance.weights.shape}, expected ({total},); "
            "a misaligned series would blend every session on the wrong regime"
        )
        raise DataIntegrityError(message)
    if not np.all(np.isfinite(stance.weights)):
        message = "stance blend weights contain a non-finite value"
        raise DataIntegrityError(message)
    if np.any(stance.weights < 0.0) or np.any(stance.weights > 1.0):
        message = "stance blend weights must lie in [0, 1]; the blend is convex by contract"
        raise DataIntegrityError(message)
    expected = (total, len(stance.universe.symbols))
    if stance.adjusted_closes.shape != expected:
        message = (
            f"stance adjusted_closes has shape {stance.adjusted_closes.shape}, expected {expected}"
        )
        raise DataIntegrityError(message)
    unknown = [
        str(symbol) for symbol in stance.universe.symbols if symbol not in set(panel.symbols)
    ]
    if unknown:
        message = (
            f"stance universe symbols {unknown} are not in the panel; their "
            "instructions could never price or fill"
        )
        raise DataIntegrityError(message)
    if stance.strategy.warmup.min_completed_sessions > primary.warmup.min_completed_sessions:
        message = (
            f"stance warm-up ({stance.strategy.warmup.min_completed_sessions}) exceeds the "
            f"primary warm-up ({primary.warmup.min_completed_sessions}); early blended "
            "decisions would all fail"
        )
        raise DataIntegrityError(message)


def _apply_signal_override(
    precomputed: PrecomputedDecisionInputs, override: np.ndarray, total: int
) -> PrecomputedDecisionInputs:
    """Replace the long scores with a control's, preserving availability.

    The override is masked by the real signal's own NaN pattern, so a control
    cannot form a decision on any session the strategy itself could not --
    warm-up and data gaps bind identically. Values are confined to ``[0, 1]``
    because the mandate is long-or-cash whatever the control believes.
    """
    expected = (total, precomputed.long_scores.shape[1])
    if override.shape != expected:
        message = f"signal_override has shape {override.shape}, expected {expected}"
        raise DataIntegrityError(message)
    if not np.all(np.isfinite(override)):
        raise DataIntegrityError("signal_override contains a non-finite value")
    if np.any(override < 0.0) or np.any(override > 1.0):
        message = "signal_override must lie in [0, 1]; the mandate is long-or-cash"
        raise DataIntegrityError(message)
    masked = np.where(np.isfinite(precomputed.long_scores), override, np.nan)
    return PrecomputedDecisionInputs(
        returns=precomputed.returns,
        sigma_panel=precomputed.sigma_panel,
        long_scores=masked,
    )


def _precompute_run_inputs(
    strategy: StrategyConfig,
    stance: StanceBlend | None,
    panel: MarketPanel,
    *,
    signal_override: np.ndarray | None = None,
) -> tuple[PrecomputedDecisionInputs, PrecomputedDecisionInputs | None]:
    """Feature panels computed once for the whole run.

    Every feature recursion is strictly causal (row t reads rows <= t only),
    so reading row t of these panels is bit-identical to recomputing on the
    visible prefix at t -- the equivalence the future-mutation leakage tests
    police. Recomputing per decision instead is O(T^2) and was measured at
    ~91% of runtime.

    ``signal_override`` (research controls only) replaces the primary long
    scores after the causal pass, masked by their own availability.
    """
    primary = precompute_decision_inputs(strategy, panel.adjusted_closes)
    if signal_override is not None:
        primary = _apply_signal_override(primary, signal_override, len(panel))
    # Built only when some session actually blends: with every weight at 1.0
    # the defensive decide() is never called, and the pre-cache code never
    # touched the stance panel either -- eager computation would change which
    # runs a defective stance panel can fail.
    defensive = (
        precompute_decision_inputs(stance.strategy, stance.adjusted_closes)
        if stance is not None and bool(np.any(stance.weights < 1.0))
        else None
    )
    return primary, defensive


def _require_constant_cash_rate(strategy: StrategyConfig) -> float:
    """The configured constant cash rate, refusing sources this runner lacks.

    Guardrail 9: a cash-yield source this runner cannot price must fail
    closed. Silently crediting 0% would misstate every long-cash stretch
    while the configuration claims a Treasury-yield sensitivity ran.
    """
    if strategy.cash.source != "constant":
        message = (
            f"cash yield source {strategy.cash.source!r} is not implemented by this "
            'runner; use source = "constant" (a treasury_3m series would need '
            "rate data this repository does not ingest)"
        )
        raise ConfigurationError(message)
    return strategy.cash.constant_annualized_rate


def _resolve_weight_scalars(
    strategy: StrategyConfig,
    panel: MarketPanel,
    provided: np.ndarray | None,
) -> np.ndarray | None:
    """Build the configured causal regime series, rejecting double conditioning."""
    if not strategy.learned_regime.enabled:
        return provided
    if provided is not None:
        raise DataIntegrityError(
            "learned_regime is enabled and an external weight_scalars series was also "
            "supplied; two regime layers cannot be attributed to one run"
        )
    learned = strategy.learned_regime
    return regime_scalars(
        panel,
        n_regimes=learned.n_regimes,
        fit_window=learned.fit_window_sessions,
        refit_every=learned.refit_every_sessions,
        risk_floor=learned.risk_floor,
        unknown_scalar=learned.unknown_scalar,
        seed=learned.seed,
        horizons=strategy.trend.horizons_sessions,
    )


def run_backtest(  # noqa: PLR0915 - ordered accounting loop is intentionally explicit
    strategy: StrategyConfig,
    panel: MarketPanel,
    universe: Universe,
    *,
    initial_cash: Cash = Decimal("100000"),
    one_way_bps: float | None = None,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
    weight_scalars: np.ndarray | None = None,
    decision_overlay: DecisionOverlay | None = None,
    max_entry_window_participation: float | None = None,
    stance: StanceBlend | None = None,
    exit_rules: ExitPolicy | None = None,
    drop_every_nth_fill: int | None = None,
    signal_override: np.ndarray | None = None,
) -> BacktestResult:
    """Run the research backtest over ``panel``.

    Args:
        strategy: Validated configuration; supplies every frozen parameter.
        panel: Aligned market series whose symbol order matches ``universe``.
        universe: Cluster membership and deterministic column order.
        initial_cash: Opening cash balance.
        one_way_bps: Cost override for the stress ladder. ``None`` uses the
            configured baseline.
        actions_by_session: Corporate actions keyed by the session they take
            effect on.
        weight_scalars: Optional ``(T,)`` multiplier per session, applied to the
            target weights before instructions are built. This is how strategy
            v3 expresses regime conditioning, and deliberately the *only* hook
            of its kind: a precomputed array can be leak-tested directly and
            swapped for a shuffled control, neither of which was possible with
            the rejected v2 overlay's in-loop mutable state. ``None`` means v1 --
            identical to an array of ones.
        decision_overlay: Optional causal ``(T, N)`` per-asset v6 overlay. It
            may reduce targets and may block increases, but cannot add risk.
        max_entry_window_participation: Optional v6 execution guard that clips
            buys to this fraction of observed execution-window share volume.
            Sells are not clipped, so liquidity protection cannot trap an
            account that is trying to reduce risk.
        stance: Optional :class:`StanceBlend`, strategy v4's cooperative
            second stance. Applied *before* ``weight_scalars``: the blend
            chooses the book's composition, the scalar then de-risks the whole
            book. ``None`` means the pure primary stance.
        exit_rules: Optional :class:`~amat.backtest.exits.ExitRules`, the
            third overlay (registered experiment 0003): per-position
            stop-loss/take-profit on the adjusted basis, applied *after* the
            stance and scalar so a breached or embargoed symbol goes to cash
            whatever they chose. ``None`` -- the frozen v1 behavior -- means
            positions exit only through the deadband and volatility target.
        drop_every_nth_fill: Missed-fill stress injection: every Nth would-be
            fill is recorded as a ``STRESS_DROPPED`` missed fill instead of
            executing. Robustness-protocol use only; ``None`` for every
            primary run.
        signal_override: Optional ``(T, N)`` replacement for the long scores,
            in ``[0, 1]``, used by research *controls* (the signal-free
            allocator benchmark passes all-ones). Availability is preserved:
            wherever the real signal was unavailable (NaN), the override is
            ignored, so a control can never trade before the strategy could.
            Never set on a primary run.

    Raises:
        DataIntegrityError: if the panel is too short to satisfy warm-up, or its
            symbol order disagrees with the universe.
        CausalityError: if the configured lag would execute on the signal bar.
    """
    if tuple(panel.symbols) != tuple(universe.symbols):
        message = (
            "panel symbol order must match the universe declaration order; "
            f"panel={panel.symbols} universe={universe.symbols}"
        )
        raise DataIntegrityError(message)

    lag = strategy.execution.signal_to_execution_lag_sessions
    if lag < 1:  # pragma: no cover - pinned ge=1 by the config type
        message = "signal_to_execution_lag_sessions must be at least 1"
        raise CausalityError(message)

    warmup = strategy.warmup.min_completed_sessions
    total = len(panel)
    if total < warmup + lag + 1:
        message = (
            f"panel has {total} sessions; need at least {warmup + lag + 1} to place a "
            f"single trade under a {warmup}-session warm-up and a {lag}-session lag"
        )
        raise DataIntegrityError(message)

    weight_scalars = _resolve_weight_scalars(strategy, panel, weight_scalars)
    _validate_weight_scalars(weight_scalars, total)
    _validate_decision_overlay(decision_overlay, total, len(universe.symbols))
    _validate_stance(stance, panel, strategy, total)

    execution_model = SimulatedExecutionModel(
        strategy.execution,
        strategy.costs,
        one_way_bps=one_way_bps,
        max_entry_window_participation=max_entry_window_participation,
        drop_every_nth_fill=drop_every_nth_fill,
    )
    accountant = SessionAccountant()
    actions_by_session = actions_by_session or {}

    cash_rate = _require_constant_cash_rate(strategy)

    precomputed, stance_inputs = _precompute_run_inputs(
        strategy, stance, panel, signal_override=signal_override
    )

    # The ledger opens at the last warm-up session, flat and fully in cash.
    first_decision = warmup - 1
    ledger = LedgerSnapshot(
        session_date=panel.sessions[first_decision], cash=initial_cash, positions={}
    )

    records: list[SessionRecord] = []
    warnings: list[str] = []
    pending: dict[SessionDate, list[SimulatedOrder]] = {}
    pending_missed: dict[SessionDate, list[MissedFill]] = {}

    # Exit-overlay state: the adjusted entry basis per open position and the
    # first decision index at which a breached symbol may re-enter. Both are
    # populated exclusively from settled fills and past decisions.
    entry_basis: dict[Symbol, float] = {}
    embargo_until: dict[Symbol, int] = {}
    symbol_columns: dict[Symbol, int] = {
        symbol: column for column, symbol in enumerate(universe.symbols)
    }

    for index in range(first_decision, total):
        session = panel.sessions[index]

        # --- restate in-flight instructions across a split ----------------
        # Before settling, because the accountant applies actions to opening
        # positions and would otherwise meet an instruction denominated in
        # pre-split shares. Every order still pending at this point was formed
        # on an earlier session; today's decision is not formed until below.
        session_actions = actions_by_session.get(session, ())
        warnings.extend(
            f"{session.isoformat()}: {note}"
            for note in _rescale_pending_across_split(pending, pending_missed, session_actions)
        )

        # --- settle anything scheduled to execute today -------------------
        if index > first_decision:
            ledger, attribution, fills, missed = _settle_session(
                ledger,
                session,
                index,
                panel,
                execution_model=execution_model,
                accountant=accountant,
                instructions=pending.pop(session, []),
                carried_missed=pending_missed.pop(session, []),
                actions=session_actions,
                cash_rate=cash_rate,
                sessions_per_year=strategy.volatility.annualization_sessions,
            )
        else:
            fills, missed = [], []
            attribution = _ZERO_ATTRIBUTION

        if exit_rules is not None:
            track_entry_bases(
                entry_basis,
                fills=fills,
                positions=ledger.positions,
                adjusted_row=panel.adjusted_closes[index],
                raw_row=panel.raw_closes[index],
                symbol_columns=symbol_columns,
            )

        # --- form today's decision, and schedule it for a later session ---
        allocation = None
        execution_index = index + lag
        if execution_index < total:
            allocation, note = _schedule_decision(
                strategy,
                panel,
                universe,
                index=index,
                execution_index=execution_index,
                ledger=ledger,
                pending=pending,
                pending_missed=pending_missed,
                weight_scalars=weight_scalars,
                decision_overlay=decision_overlay,
                stance=stance,
                exit_rules=exit_rules,
                entry_basis=entry_basis,
                embargo_until=embargo_until,
                symbol_columns=symbol_columns,
                precomputed=precomputed,
                stance_inputs=stance_inputs,
            )
            if note is not None:
                warnings.append(note)

        records.append(
            SessionRecord(
                session=session,
                equity=ledger.equity,
                cash=ledger.cash,
                gross_exposure=ledger.gross_exposure,
                forecast_volatility_pre=(
                    allocation.forecast_volatility_pre if allocation else float("nan")
                ),
                forecast_volatility_post=(
                    allocation.forecast_volatility_post if allocation else float("nan")
                ),
                fills=tuple(fills),
                missed=tuple(missed),
                attribution=attribution,
                binding_constraints=allocation.binding if allocation else (),
            )
        )

    if pending:
        leftover = ", ".join(sorted(date.isoformat() for date in pending))
        warnings.append(f"instructions scheduled beyond the panel were never executed: {leftover}")

    return BacktestResult(
        sessions=tuple(records),
        initial_equity=initial_cash,
        one_way_bps=execution_model.one_way_bps,
        # A zero-cost run is diagnostic whatever the configuration says, because
        # the *effective* cost is what the result depends on. Overriding the
        # baseline to 0 for the stress ladder must not inherit the configured
        # primacy of the 2 bps assumption.
        is_primary=(
            strategy.costs.is_primary
            and strategy.cash.is_primary
            and execution_model.one_way_bps > 0.0
        ),
        universe_symbols=tuple(universe.symbols),
        warnings=tuple(warnings),
    )
