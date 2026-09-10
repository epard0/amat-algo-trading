"""Honest portfolio and execution metrics for the paper-trading report.

The two metric families deliberately remain separate:

* :class:`PerformanceStats` measures the account equity observations.  It does
  not pretend that an observation spanning an extended executor outage is a
  one-day return, and it suppresses annualized statistics when the observation
  cadence is not daily enough to support them.
* :class:`ExecutionQuality` reconciles the strategy plan with broker orders.
  Its slippage measure is explicitly decision-close-to-fill markout.  That
  includes the overnight move and must not be confused with implementation
  shortfall against the opening quote or VWAP.

Neither family adjusts for deposits or withdrawals because the executor does
not currently ingest broker account activities.  Reports state that limitation
instead of silently calling raw equity growth strategy P&L.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from executor.broker import Order
from executor.plan_source import ExecutionPlan

__all__ = [
    "ExecutionQuality",
    "PerformanceStats",
    "compute_execution_quality",
    "compute_stats",
]

_MEANINGFUL_SAMPLE = 20
_TRADING_DAYS_PER_YEAR = 252
_MAX_DAILY_OBSERVATION_GAP = 4
_MIN_RETURNS_FOR_DISPERSION = 2
_VAR_CONFIDENCE = 0.95


def _percentile(values: list[float], probability: float) -> float:
    """Linearly interpolated percentile without a heavyweight dependency."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


@dataclass(frozen=True, slots=True)
class PerformanceStats:
    """Measured statistics over the executor's append-only equity curve.

    ``day_return`` is retained in JSON for compatibility, but it is the latest
    *observed-period* return.  ``latest_interval_calendar_days`` says how much
    wall-clock time that observation spans.
    """

    sessions: int
    return_observations: int
    first_session: str
    last_session: str
    starting_equity: Decimal
    current_equity: Decimal
    total_return: float
    day_return: float
    latest_interval_calendar_days: int
    longest_interval_calendar_days: int
    has_extended_gap: bool
    annualized_return: float | None
    max_drawdown: float
    current_drawdown: float
    max_drawdown_duration_observations: int
    current_drawdown_duration_observations: int
    ulcer_index: float
    annualized_volatility: float | None
    annualized_downside_deviation: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    historical_var_95: float | None
    historical_cvar_95: float | None
    best_day: float
    worst_day: float
    positive_days: int
    negative_days: int
    average_gain: float
    average_loss: float
    gain_to_pain_ratio: float | None

    @property
    def is_meaningful(self) -> bool:
        """Whether annualized dispersion statistics support interpretation."""
        return self.sessions >= _MEANINGFUL_SAMPLE and not self.has_extended_gap

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.sessions,
            "return_observations": self.return_observations,
            "first_session": self.first_session,
            "last_session": self.last_session,
            "starting_equity": str(self.starting_equity),
            "current_equity": str(self.current_equity),
            "total_return": self.total_return,
            # Compatibility key. New consumers should use latest_observed_return.
            "day_return": self.day_return,
            "latest_observed_return": self.day_return,
            "latest_interval_calendar_days": self.latest_interval_calendar_days,
            "longest_interval_calendar_days": self.longest_interval_calendar_days,
            "has_extended_gap": self.has_extended_gap,
            "annualized_return": self.annualized_return,
            "max_drawdown": self.max_drawdown,
            "current_drawdown": self.current_drawdown,
            "max_drawdown_duration_observations": self.max_drawdown_duration_observations,
            "current_drawdown_duration_observations": (self.current_drawdown_duration_observations),
            "ulcer_index": self.ulcer_index,
            "annualized_volatility": self.annualized_volatility,
            "annualized_downside_deviation": self.annualized_downside_deviation,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "historical_var_95": self.historical_var_95,
            "historical_cvar_95": self.historical_cvar_95,
            # Compatibility names; these are returns between observations.
            "best_day": self.best_day,
            "worst_day": self.worst_day,
            "positive_days": self.positive_days,
            "negative_days": self.negative_days,
            "average_gain": self.average_gain,
            "average_loss": self.average_loss,
            "gain_to_pain_ratio": self.gain_to_pain_ratio,
            "sample_is_meaningful": self.is_meaningful,
        }


def _empty_stats() -> PerformanceStats:
    zero = Decimal("0")
    return PerformanceStats(
        sessions=0,
        return_observations=0,
        first_session="",
        last_session="",
        starting_equity=zero,
        current_equity=zero,
        total_return=0.0,
        day_return=0.0,
        latest_interval_calendar_days=0,
        longest_interval_calendar_days=0,
        has_extended_gap=False,
        annualized_return=None,
        max_drawdown=0.0,
        current_drawdown=0.0,
        max_drawdown_duration_observations=0,
        current_drawdown_duration_observations=0,
        ulcer_index=0.0,
        annualized_volatility=None,
        annualized_downside_deviation=None,
        sharpe_ratio=None,
        sortino_ratio=None,
        calmar_ratio=None,
        historical_var_95=None,
        historical_cvar_95=None,
        best_day=0.0,
        worst_day=0.0,
        positive_days=0,
        negative_days=0,
        average_gain=0.0,
        average_loss=0.0,
        gain_to_pain_ratio=None,
    )


def compute_stats(  # noqa: PLR0912, PLR0915 - one auditable metric pass
    equity_curve: list[dict[str, Any]],
) -> PerformanceStats:
    """Compute return, drawdown, downside, and tail statistics.

    Equity observations must be strictly ordered and positive.  Annualized
    fields become ``None`` when fewer than two returns exist or any observation
    interval exceeds four calendar days.  A Friday-to-Monday interval remains
    valid; a week-long executor outage does not become one fictitious day.
    """
    if not equity_curve:
        return _empty_stats()

    sessions: list[dt.date] = []
    equities: list[Decimal] = []
    for entry in equity_curve:
        try:
            session = dt.date.fromisoformat(str(entry["session"]))
            equity = Decimal(str(entry["equity"]))
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise ValueError(f"malformed equity observation: {entry!r}") from exc
        if not equity.is_finite() or equity <= 0:
            raise ValueError(f"equity must be finite and positive, received {equity}")
        if sessions and session <= sessions[-1]:
            raise ValueError("equity sessions must be strictly increasing")
        sessions.append(session)
        equities.append(equity)

    returns = [float(latest / previous) - 1.0 for previous, latest in itertools.pairwise(equities)]
    gaps = [(latest - previous).days for previous, latest in itertools.pairwise(sessions)]
    latest_gap = gaps[-1] if gaps else 0
    longest_gap = max(gaps, default=0)
    has_extended_gap = longest_gap > _MAX_DAILY_OBSERVATION_GAP

    drawdowns: list[float] = []
    peak = equities[0]
    duration = 0
    maximum_duration = 0
    for equity in equities:
        if equity >= peak:
            peak = equity
            duration = 0
        else:
            duration += 1
            maximum_duration = max(maximum_duration, duration)
        drawdowns.append(float(equity / peak) - 1.0)

    annualized_return: float | None = None
    volatility: float | None = None
    downside_deviation: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    value_at_risk: float | None = None
    conditional_value_at_risk: float | None = None
    if returns and not has_extended_gap:
        years = len(returns) / _TRADING_DAYS_PER_YEAR
        annualized_return = float(
            (equities[-1] / equities[0]) ** Decimal(str(1.0 / years)) - Decimal("1")
        )
        value_at_risk = _percentile(returns, 1.0 - _VAR_CONFIDENCE)
        tail = [value for value in returns if value <= value_at_risk]
        conditional_value_at_risk = statistics.fmean(tail)
        maximum_drawdown = min(drawdowns)
        if maximum_drawdown < 0:
            calmar = annualized_return / abs(maximum_drawdown)

    if len(returns) >= _MIN_RETURNS_FOR_DISPERSION and not has_extended_gap:
        daily_sigma = statistics.stdev(returns)
        mean_daily = statistics.fmean(returns)
        volatility = daily_sigma * math.sqrt(_TRADING_DAYS_PER_YEAR)
        if daily_sigma > 0:
            sharpe = (mean_daily / daily_sigma) * math.sqrt(_TRADING_DAYS_PER_YEAR)
        else:
            sharpe = 0.0

        downside = [min(value, 0.0) for value in returns]
        downside_daily = math.sqrt(statistics.fmean(value * value for value in downside))
        downside_deviation = downside_daily * math.sqrt(_TRADING_DAYS_PER_YEAR)
        if downside_daily > 0:
            sortino = (mean_daily / downside_daily) * math.sqrt(_TRADING_DAYS_PER_YEAR)

    gains = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    loss_pain = abs(sum(losses))
    gain_to_pain = sum(gains) / loss_pain if loss_pain > 0 else None

    return PerformanceStats(
        sessions=len(equities),
        return_observations=len(returns),
        first_session=sessions[0].isoformat(),
        last_session=sessions[-1].isoformat(),
        starting_equity=equities[0],
        current_equity=equities[-1],
        total_return=float(equities[-1] / equities[0]) - 1.0,
        day_return=returns[-1] if returns else 0.0,
        latest_interval_calendar_days=latest_gap,
        longest_interval_calendar_days=longest_gap,
        has_extended_gap=has_extended_gap,
        annualized_return=annualized_return,
        max_drawdown=min(drawdowns),
        current_drawdown=drawdowns[-1],
        max_drawdown_duration_observations=maximum_duration,
        current_drawdown_duration_observations=duration,
        ulcer_index=math.sqrt(statistics.fmean(value * value for value in drawdowns)),
        annualized_volatility=volatility,
        annualized_downside_deviation=downside_deviation,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        historical_var_95=value_at_risk,
        historical_cvar_95=conditional_value_at_risk,
        best_day=max(returns, default=0.0),
        worst_day=min(returns, default=0.0),
        positive_days=len(gains),
        negative_days=len(losses),
        average_gain=statistics.fmean(gains) if gains else 0.0,
        average_loss=statistics.fmean(losses) if losses else 0.0,
        gain_to_pain_ratio=gain_to_pain,
    )


@dataclass(frozen=True, slots=True)
class ExecutionQuality:
    """Reconciliation between one strategy plan and its broker orders."""

    planned_order_count: int
    broker_order_count: int
    filled_order_count: int
    partially_filled_order_count: int
    rejected_order_count: int
    canceled_order_count: int
    open_order_count: int
    unmatched_intent_count: int
    unmatched_order_count: int
    overfilled_order_count: int
    planned_reference_notional: Decimal
    filled_notional: Decimal
    reference_notional_fill_rate: float | None
    decision_to_fill_slippage_bps: float | None
    reconciliation_complete: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "planned_order_count": self.planned_order_count,
            "broker_order_count": self.broker_order_count,
            "filled_order_count": self.filled_order_count,
            "partially_filled_order_count": self.partially_filled_order_count,
            "rejected_order_count": self.rejected_order_count,
            "canceled_order_count": self.canceled_order_count,
            "open_order_count": self.open_order_count,
            "unmatched_intent_count": self.unmatched_intent_count,
            "unmatched_order_count": self.unmatched_order_count,
            "overfilled_order_count": self.overfilled_order_count,
            "planned_reference_notional": str(self.planned_reference_notional),
            "filled_notional": str(self.filled_notional),
            "reference_notional_fill_rate": self.reference_notional_fill_rate,
            "decision_to_fill_slippage_bps": self.decision_to_fill_slippage_bps,
            "reconciliation_complete": self.reconciliation_complete,
            "slippage_benchmark": "prior decision-session close",
        }


def compute_execution_quality(
    plan: ExecutionPlan | None,
    orders: tuple[Order, ...],
) -> ExecutionQuality:
    """Match plan intents to orders and quantify fills and adverse markout."""
    intents = () if plan is None else plan.intents
    remaining_orders = list(orders)
    unmatched_intents = 0
    planned_notional = sum((intent.notional for intent in intents), Decimal("0"))
    filled_notional = sum((order.notional_filled for order in orders), Decimal("0"))
    filled_reference_notional = Decimal("0")
    adverse_markout_notional = Decimal("0")
    overfilled_orders = 0

    for intent in intents:
        matched_index = next(
            (
                index
                for index, order in enumerate(remaining_orders)
                if order.symbol == intent.symbol and order.side == intent.side
            ),
            None,
        )
        if matched_index is None:
            unmatched_intents += 1
            continue
        order = remaining_orders.pop(matched_index)
        if order.filled_quantity > intent.quantity:
            overfilled_orders += 1
        reference_filled = min(order.filled_quantity, intent.quantity) * intent.reference_price
        filled_reference_notional += reference_filled
        if order.filled_avg_price is not None and intent.reference_price > 0:
            if intent.side == "buy":
                adverse_fraction = order.filled_avg_price / intent.reference_price - Decimal("1")
            else:
                adverse_fraction = Decimal("1") - order.filled_avg_price / intent.reference_price
            adverse_markout_notional += adverse_fraction * reference_filled

    fill_rate = (
        float(filled_reference_notional / planned_notional) if planned_notional > 0 else None
    )
    slippage = (
        float(adverse_markout_notional / filled_reference_notional) * 10_000.0
        if filled_reference_notional > 0
        else None
    )
    open_orders = sum(not order.is_terminal for order in orders)
    terminal_failure_statuses = {"rejected", "canceled", "expired", "done_for_day"}

    return ExecutionQuality(
        planned_order_count=len(intents),
        broker_order_count=len(orders),
        filled_order_count=sum(order.status == "filled" for order in orders),
        partially_filled_order_count=sum(
            order.filled_quantity > 0 and order.status != "filled" for order in orders
        ),
        rejected_order_count=sum(order.status == "rejected" for order in orders),
        canceled_order_count=sum(
            order.status in terminal_failure_statuses - {"rejected"} for order in orders
        ),
        open_order_count=open_orders,
        unmatched_intent_count=unmatched_intents,
        unmatched_order_count=len(remaining_orders),
        overfilled_order_count=overfilled_orders,
        planned_reference_notional=planned_notional,
        filled_notional=filled_notional,
        reference_notional_fill_rate=fill_rate,
        decision_to_fill_slippage_bps=slippage,
        reconciliation_complete=(
            open_orders == 0
            and unmatched_intents == 0
            and not remaining_orders
            and overfilled_orders == 0
        ),
    )
