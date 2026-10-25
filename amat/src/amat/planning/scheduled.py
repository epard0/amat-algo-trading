"""Inert annual-context planning from observed account and exit state.

This returns the same instructions as the native backtest. It neither submits
orders nor creates or replaces executor authorization. Purchase rounding and
cash/volume caps remain the native execution boundary's responsibility.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import (
    ScheduledDecisionContext,
    _precompute_run_inputs,
    _prepare_schedule,
    _resolve_weight_scalars,
    _schedule_decision,
)
from amat.config.models import StrategyConfig
from amat.domain.fills import MissedFill
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.orders import SimulatedOrder
from amat.domain.types import Symbol
from amat.errors import DataIntegrityError


@dataclass(frozen=True, slots=True)
class ScheduledPlan:
    decision_session: dt.date
    execution_session: dt.date
    context_id: str
    instructions: tuple[SimulatedOrder, ...]
    missed: tuple[MissedFill, ...]
    embargo_until: tuple[tuple[Symbol, int], ...]
    schema_version: str = "annual-research-plan/1"
    execution_authority: str = "none"


def plan_scheduled_decision(
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    ledger: LedgerSnapshot,
    schedule: Sequence[ScheduledDecisionContext],
    execution_session: dt.date,
    one_way_bps: float,
    entry_basis: Mapping[Symbol, float],
    embargo_until: Mapping[Symbol, int],
    pending: Mapping[dt.date, Sequence[SimulatedOrder]],
) -> ScheduledPlan:
    """Use actual settled state and existing pending intent; never initialize a ledger.

    Only observed activations may be supplied. Pending orders, entry bases and
    embargoes are copied; callers retain their original authorization objects.
    No execution-window price or volume for the future session is required.
    """
    if not math.isfinite(one_way_bps) or one_way_bps < 0:
        raise DataIntegrityError("planning cost must be finite and nonnegative")
    index = len(panel) - 1
    if ledger.session_date != panel.sessions[index] or execution_session <= ledger.session_date:
        raise DataIntegrityError("current observed ledger and later execution session required")
    if strategy.execution.signal_to_execution_lag_sessions != 1:
        raise DataIntegrityError("research scheduled planning supports next-session intent only")
    if tuple(panel.symbols) != tuple(strategy.to_universe().symbols):
        raise DataIntegrityError("planning panel and strategy universe differ")
    marks = panel.marks_at(index)
    if any(
        symbol not in marks or position.mark != marks[symbol]
        for symbol, position in ledger.positions.items()
    ):
        raise DataIntegrityError("settled holdings must use the observed decision-close marks")
    if strategy.adaptive_rotation.switch_condition == "volatility":
        strategy = strategy.model_copy(
            update={
                "adaptive_rotation": strategy.adaptive_rotation.model_copy(
                    update={"switch_cost_bps": one_way_bps}
                )
            }
        )
    prepared = _prepare_schedule(schedule, strategy, panel, one_way_bps=one_way_bps)
    active = prepared[max(prepared)] if prepared else None
    current = active.declaration.strategy if active else strategy
    inputs = active.inputs if active else _precompute_run_inputs(current, None, panel)[0]
    scalars = active.weight_scalars if active else _resolve_weight_scalars(current, panel, None)
    orders = {date: list(values) for date, values in pending.items()}
    initial = len(orders.get(execution_session, []))
    missed: dict[dt.date, list[MissedFill]] = {}
    embargo = dict(embargo_until)
    _, warning = _schedule_decision(
        current,
        panel,
        current.to_universe(),
        index=index,
        execution_index=index + 1,
        execution_session_override=execution_session,
        ledger=ledger,
        pending=orders,
        pending_missed=missed,
        weight_scalars=scalars,
        decision_overlay=active.overlay if active else None,
        exit_rules=(active.declaration.exit_rules or {}) if active else {},
        entry_basis=dict(entry_basis),
        embargo_until=embargo,
        symbol_columns={symbol: column for column, symbol in enumerate(panel.symbols)},
        precomputed=inputs,
    )
    if warning:
        raise DataIntegrityError(warning)
    return ScheduledPlan(
        ledger.session_date,
        execution_session,
        active.declaration.context_id if active else "baseline",
        tuple(orders.get(execution_session, [])[initial:]),
        tuple(missed.get(execution_session, [])),
        tuple(sorted(embargo.items())),
    )
