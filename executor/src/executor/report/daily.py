"""The daily performance report: build it, write it, prune the old ones.

Two things this module refuses to do, inherited from the strategy repository's
guardrail 2 and worth restating because a daily email is exactly where they
erode:

* It states measured results and their sample size. It never annualises a
  Sharpe ratio from a handful of days without saying how few days it is, and it
  labels every statistic below the meaningful-sample threshold as *not
  meaningful* rather than printing an impressive number.
* It never describes paper results as expected performance. Paper fills do not
  pay spread the way real ones do, and a paper account cannot be adversely
  selected.

Retention
---------
Report *files* are pruned to the configured number of trading days. The
``equity_curve.ndjson`` they are computed from is never pruned -- it is roughly
200 bytes per trading day, so a decade of it costs about half a megabyte, and
discarding it would make every statistic above permanently unrecoverable. What
consumes disk is the per-day artifacts (plans, rendered reports), and those are
what the window applies to.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from executor.broker import Account, Order, Position
from executor.order_registry import order_state
from executor.plan_source import ExecutionPlan
from executor.report.metrics import (
    _MEANINGFUL_SAMPLE,
    ExecutionQuality,
    PerformanceStats,
    compute_execution_quality,
    compute_stats,
)
from executor.submit import SubmissionResult

__all__ = [
    "DailyReport",
    "ExecutionQuality",
    "PerformanceStats",
    "build_daily_report",
    "prune_old_reports",
]

_COMPLETE_FILL_RATE = 0.999


def _number(value: float | None, *, percent: bool = False) -> str:
    """Render an unavailable metric honestly instead of coercing it to zero."""
    if value is None:
        return "not available"
    return f"{value:.2%}" if percent else f"{value:.2f}"


@dataclass(frozen=True, slots=True)
class DailyReport:
    """One trading day's report, in both machine and human form."""

    session: dt.date
    generated_at: dt.datetime
    stats: PerformanceStats
    account: dict[str, Any]
    positions: tuple[dict[str, Any], ...]
    submission: dict[str, Any]
    plan_summary: dict[str, Any]
    orders: tuple[dict[str, Any], ...]
    execution: ExecutionQuality
    warnings: tuple[str, ...]
    trade_monitor: dict[str, Any] = field(default_factory=dict)
    trading_mode: str = "paper"

    @property
    def subject(self) -> str:
        """Email subject line: the day, the move, and any alarm."""
        alarm = " [ATTENTION]" if self.warnings else ""
        return (
            f"AMAT {self.trading_mode} {self.session.isoformat()}: "
            f"{self.stats.day_return:+.2%} observed period, "
            f"equity {self.stats.current_equity:,.2f}{alarm}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "trading_mode": self.trading_mode,
            "session": self.session.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "stats": self.stats.as_dict(),
            "account": self.account,
            "positions": list(self.positions),
            "submission": self.submission,
            "plan": self.plan_summary,
            "orders": list(self.orders),
            "execution": self.execution.as_dict(),
            "warnings": list(self.warnings),
            "trade_monitor": self.trade_monitor,
        }

    def to_markdown(self) -> str:  # noqa: PLR0915 - render optional report sections
        """Render the human-readable report."""
        stats = self.stats
        lines: list[str] = [
            f"# AMAT {self.trading_mode} trading — {self.session.isoformat()}",
            "",
            f"*Generated {self.generated_at.isoformat(timespec='seconds')}*",
            "",
        ]

        if self.warnings:
            lines.append("## ⚠ Attention")
            lines.append("")
            lines.extend(f"- {warning}" for warning in self.warnings)
            lines.append("")

        lines.extend(
            [
                "## Account",
                "",
                "| | |",
                "|---|---:|",
                f"| Equity | {stats.current_equity:,.2f} |",
                f"| Cash | {Decimal(str(self.account.get('cash', '0'))):,.2f} |",
                f"| Return since prior observation | {stats.day_return:+.2%} |",
                f"| Observation interval | {stats.latest_interval_calendar_days} calendar day(s) |",
                f"| Gross exposure | {float(self.account.get('gross_exposure', 0.0)):.2%} |",
                "",
                "## Performance since inception",
                "",
                f"Measured over **{stats.sessions} session(s)** "
                f"({stats.first_session} → {stats.last_session}), "
                f"from a starting equity of {stats.starting_equity:,.2f}.",
                "",
                "| Statistic | Value |",
                "|---|---:|",
                f"| Total return | {stats.total_return:+.2%} |",
                f"| Annualized return | {_number(stats.annualized_return, percent=True)} |",
                f"| Max drawdown | {stats.max_drawdown:.2%} |",
                f"| Current drawdown | {stats.current_drawdown:.2%} |",
                f"| Drawdown duration | "
                f"{stats.current_drawdown_duration_observations} observation(s) |",
                f"| Ulcer index | {stats.ulcer_index:.2%} |",
                f"| Annualized volatility | {_number(stats.annualized_volatility, percent=True)} |",
                f"| Downside deviation | "
                f"{_number(stats.annualized_downside_deviation, percent=True)} |",
                f"| Sharpe ratio (0% cash) | {_number(stats.sharpe_ratio)} |",
                f"| Sortino ratio (0% target) | {_number(stats.sortino_ratio)} |",
                f"| Calmar ratio | {_number(stats.calmar_ratio)} |",
                f"| Historical 95% VaR | {_number(stats.historical_var_95, percent=True)} |",
                f"| Historical 95% CVaR | {_number(stats.historical_cvar_95, percent=True)} |",
                f"| Best observed period | {stats.best_day:+.2%} |",
                f"| Worst observed period | {stats.worst_day:+.2%} |",
                f"| Positive periods | {stats.positive_days} / {stats.return_observations} |",
                f"| Gain-to-pain ratio | {_number(stats.gain_to_pain_ratio)} |",
                "",
                "> Returns are changes in raw account equity. Deposits and withdrawals are "
                "not adjusted because broker account activities are not yet ingested.",
                "",
            ]
        )

        if not stats.is_meaningful:
            lines.extend(
                [
                    f"> **Not a meaningful sample.** Annualized, dispersion, and tail statistics "
                    f"need at least {_MEANINGFUL_SAMPLE} sessions with no extended gap; "
                    f"this report has {stats.sessions} session(s). Any values shown describe "
                    "this sample and support no inference about the strategy.",
                    "",
                ]
            )

        if stats.has_extended_gap:
            lines.extend(
                [
                    f"> **Observation gap.** The longest interval is "
                    f"{stats.longest_interval_calendar_days} calendar days. Annualized return, "
                    "volatility, Sharpe, Sortino, Calmar, VaR, and CVaR are suppressed because "
                    "treating that interval as one trading day would be misleading.",
                    "",
                ]
            )

        if self.trade_monitor:
            monitor = self.trade_monitor
            lines.extend(
                [
                    "## Trade performance monitor",
                    "",
                    f"- Recorded instruction events: {monitor['events']}",
                    f"- This session: {monitor['latest_session_events']} instruction events, "
                    f"{monitor['latest_session_attention_events']} with execution flags",
                    f"- Historical events with execution flags: {monitor['attention_events']}",
                    f"- Matured five-session price outcomes: {monitor['matured_5s']}",
                    f"- Events without an explicit model forecast: "
                    f"{monitor['events_without_forecasts']}",
                    "",
                    "Full history: `state/trade-monitor.json` and `state/trade-monitor.md`. "
                    "These files survive daily-report retention. Instruction counts and "
                    "price markouts are not closed-trade win rates or realized P&L.",
                    "",
                ]
            )
            if monitor["cohorts"]:
                lines.extend(
                    [
                        "| Model | Side | Matured 5s | Dates | Mean (bps) | Sample |",
                        "|---|---|---:|---:|---:|---|",
                    ]
                )
                for cohort in monitor["cohorts"]:
                    lines.append(
                        f"| {cohort['model_id'][:16]} | {cohort['side']} | "
                        f"{cohort['matured_5s']} | {cohort['matured_dates']} | "
                        f"{_number(cohort['date_weighted_mean_5s_bps'])} | "
                        f"{cohort['sample_status']} |"
                    )
                lines.extend(
                    [
                        "",
                        "Means give equal weight to execution dates; positive means price "
                        "rose after buying or fell after selling. One bp is 0.01%.",
                        "",
                    ]
                )
        lines.extend(["## Today's decision", ""])
        plan = self.plan_summary
        if plan:
            lines.extend(
                [
                    f"- Strategy: `{plan.get('strategy_name')}` "
                    f"v{plan.get('strategy_version')} (`{plan.get('engine')}`)",
                    f"- Signal formed from session: {plan.get('as_of_session')}",
                    f"- Config digest: `{str(plan.get('config_sha256', ''))[:16]}`",
                    f"- Snapshot: `{plan.get('snapshot_id')}` "
                    f"(+{plan.get('appended_sessions', 0)} live sessions)",
                    f"- Gross target exposure: {float(plan.get('gross_target_exposure', 0.0)):.2%}",
                    "",
                ]
            )
        else:
            lines.extend(["No plan was formed today.", ""])

        submission = self.submission
        if not submission.get("executed", False):
            lines.extend(
                [
                    "### No orders were placed",
                    "",
                    f"> {submission.get('refusal_reason', 'no reason recorded')}",
                    "",
                ]
            )
        else:
            outcomes = list(submission.get("outcomes", []))
            if outcomes:
                lines.extend(
                    [
                        "### Orders",
                        "",
                        "| Symbol | Side | Intended | Submitted | Status | Note |",
                        "|---|---|---:|---:|---|---|",
                    ]
                )
                for outcome in outcomes:
                    lines.append(
                        f"| {outcome['symbol']} | {outcome['side']} | "
                        f"{Decimal(outcome['intended_quantity']):,.4f} | "
                        f"{Decimal(outcome['submitted_quantity']):,.4f} | "
                        f"{outcome['status']} | {outcome.get('detail', '') or '—'} |"
                    )
                lines.append("")

        if self.orders:
            lines.extend(
                [
                    "### Fills",
                    "",
                    "| Symbol | Side | Filled | Avg price | Notional | Status |",
                    "|---|---|---:|---:|---:|---|",
                ]
            )
            for order in self.orders:
                price = order.get("filled_avg_price")
                filled = Decimal(str(order.get("filled_quantity", "0")))
                notional = filled * Decimal(str(price)) if price else Decimal("0")
                lines.append(
                    f"| {order['symbol']} | {order['side']} | {filled:,.4f} | "
                    f"{'—' if not price else f'{Decimal(str(price)):,.4f}'} | "
                    f"{notional:,.2f} | {order.get('registry_status', order.get('status', ''))} "
                    f"(broker: {order.get('status', '')}) |"
                )
            lines.append("")

        execution = self.execution
        lines.extend(
            [
                "### Execution quality",
                "",
                "| Measure | Value |",
                "|---|---:|",
                f"| Planned / broker orders | {execution.planned_order_count} / "
                f"{execution.broker_order_count} |",
                f"| Filled / partial / open | {execution.filled_order_count} / "
                f"{execution.partially_filled_order_count} / {execution.open_order_count} |",
                f"| Unmatched intents / orders / overfills | "
                f"{execution.unmatched_intent_count} / {execution.unmatched_order_count} / "
                f"{execution.overfilled_order_count} |",
                f"| Planned reference notional | {execution.planned_reference_notional:,.2f} |",
                f"| Filled notional | {execution.filled_notional:,.2f} |",
                f"| Reference-notional fill rate | "
                f"{_number(execution.reference_notional_fill_rate, percent=True)} |",
                f"| Decision-close-to-fill adverse markout | "
                f"{_number(execution.decision_to_fill_slippage_bps)} bps |",
                f"| Plan-to-order reconciliation complete | "
                f"{'yes' if execution.reconciliation_complete else 'NO'} |",
                "",
                "> The markout benchmark is the prior decision-session close. It includes the "
                "overnight move; it is not opening-VWAP implementation shortfall.",
                "> This check does not reconcile portfolio positions, cash, or account activities.",
                "",
            ]
        )

        if self.positions:
            lines.extend(
                [
                    "## Holdings",
                    "",
                    "| Symbol | Shares | Market value | Weight | Unrealized P/L |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for position in sorted(
                self.positions, key=lambda item: -float(item.get("weight", 0.0))
            ):
                lines.append(
                    f"| {position['symbol']} | "
                    f"{Decimal(str(position['quantity'])):,.4f} | "
                    f"{Decimal(str(position['market_value'])):,.2f} | "
                    f"{float(position['weight']):.2%} | "
                    f"{Decimal(str(position['unrealized_pl'])):+,.2f} |"
                )
            lines.append("")
        else:
            lines.extend(["## Holdings", "", "The account is fully in cash.", ""])

        lines.extend(
            [
                "---",
                "",
                "*Paper trading against Alpaca's simulated venue. Paper fills do not pay "
                "spread or market impact the way real fills do and cannot be adversely "
                "selected, so these results are an optimistic bound on live behaviour, not "
                "a forecast of it.*",
                "",
            ]
        )
        return "\n".join(lines)


def build_daily_report(
    *,
    session: dt.date,
    now: dt.datetime,
    account: Account,
    positions: tuple[Position, ...],
    equity_curve: list[dict[str, Any]],
    submission: SubmissionResult | None,
    plan: ExecutionPlan | None,
    orders: tuple[Order, ...],
    orders_available: bool = True,
    market_is_open: bool | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> DailyReport:
    """Assemble one day's report from state the caller already fetched."""
    stats = compute_stats(equity_curve)
    execution = compute_execution_quality(plan, orders, orders_available=orders_available)
    equity = account.equity

    position_rows = tuple(
        {
            "symbol": position.symbol,
            "quantity": str(position.quantity),
            "market_value": str(position.market_value),
            "current_price": str(position.current_price),
            "avg_entry_price": str(position.avg_entry_price),
            "unrealized_pl": str(position.unrealized_pl),
            "weight": float(position.market_value / equity) if equity > 0 else 0.0,
        }
        for position in positions
    )

    gross_exposure = (
        float(sum(position.market_value for position in positions) / equity) if equity > 0 else 0.0
    )

    warnings: list[str] = list(extra_warnings)
    if plan is None:
        warnings.append(
            "No plan is available for this session; reconciliation cannot be established."
        )
    if not orders_available:
        warnings.append("Broker orders are unavailable; reconciliation cannot be established.")
    if submission is not None and not submission.executed:
        warnings.append(f"No orders placed: {submission.refusal_reason}")
    if submission is not None:
        rejected = [item for item in submission.outcomes if item.status == "rejected"]
        if rejected:
            warnings.append(
                f"{len(rejected)} order(s) rejected by the broker: "
                + "; ".join(f"{item.symbol} ({item.detail[:120]})" for item in rejected)
            )
    if plan is not None and plan.unrecognized_holdings:
        warnings.append(
            f"Account holds symbols outside the strategy universe: "
            f"{', '.join(plan.unrecognized_holdings)}. Resolve before the next session."
        )
    if not account.is_tradable:
        warnings.append(
            f"Broker account is not accepting orders (status={account.status}, "
            f"trading_blocked={account.trading_blocked})."
        )
    if not execution.reconciliation_complete:
        warnings.append(
            "Plan-to-broker reconciliation is incomplete: "
            f"{execution.unmatched_intent_count} unmatched intent(s), "
            f"{execution.unmatched_order_count} unmatched order(s), and "
            f"{execution.open_order_count} open order(s), and "
            f"{execution.overfilled_order_count} overfilled order(s)."
        )
    if (
        execution.reference_notional_fill_rate is not None
        and execution.reference_notional_fill_rate < _COMPLETE_FILL_RATE
    ):
        warnings.append(
            "Broker fills covered only "
            f"{execution.reference_notional_fill_rate:.2%} of planned reference notional."
        )

    plan_summary: dict[str, Any] = {}
    if plan is not None:
        plan_summary = {
            "strategy_name": plan.strategy_name,
            "strategy_version": plan.strategy_version,
            "engine": plan.engine,
            "as_of_session": plan.as_of_session.isoformat(),
            "earliest_execution_session": plan.earliest_execution_session.isoformat(),
            "config_sha256": plan.config_sha256,
            "snapshot_id": plan.snapshot_id,
            "appended_sessions": plan.appended_sessions,
            "code_revision": plan.code_revision,
            "gross_target_exposure": plan.gross_target_exposure,
            "target_weights": plan.target_weights,
            "caveats": list(plan.caveats),
            "withheld": list(plan.withheld),
        }

    return DailyReport(
        session=session,
        generated_at=now,
        stats=stats,
        account={
            "account_number": account.account_number,
            "status": account.status,
            "equity": str(account.equity),
            "cash": str(account.cash),
            "buying_power": str(account.buying_power),
            "gross_exposure": gross_exposure,
        },
        positions=position_rows,
        submission=submission.as_dict() if submission is not None else {},
        plan_summary=plan_summary,
        orders=tuple(
            {
                "symbol": order.symbol,
                "side": order.side,
                "quantity": str(order.quantity),
                "filled_quantity": str(order.filled_quantity),
                "filled_avg_price": (
                    None if order.filled_avg_price is None else str(order.filled_avg_price)
                ),
                "status": order.status,
                "registry_status": order_state(order, market_is_open),
                "client_order_id": order.client_order_id,
                "submitted_at": order.submitted_at,
                "filled_at": order.raw.get("filled_at"),
            }
            for order in orders
        ),
        execution=execution,
        warnings=tuple(warnings),
    )


def write_report(report: DailyReport, reports_dir: Path) -> tuple[Path, Path]:
    """Write the Markdown and JSON forms. Returns both paths."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = report.session.isoformat()
    markdown_path = reports_dir / f"{stem}.md"
    json_path = reports_dir / f"{stem}.json"
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")
    json_path.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return markdown_path, json_path


def prune_old_reports(
    directories: tuple[Path, ...],
    *,
    retain_days: int,
) -> tuple[Path, ...]:
    """Keep the newest ``retain_days`` dated files per directory; delete the rest.

    Files are grouped by the ISO date in their stem, so the ``.md`` and ``.json``
    of one day are retained or removed together and a partially pruned day is
    not possible. Anything whose stem is not a date is left alone -- this
    function will not delete a file it cannot explain.

    Returns:
        The paths removed.
    """
    if retain_days < 1:
        message = "retain_days must be at least 1"
        raise ValueError(message)

    removed: list[Path] = []
    for directory in directories:
        if not directory.is_dir():
            continue

        by_date: dict[dt.date, list[Path]] = {}
        for path in directory.iterdir():
            if not path.is_file():
                continue
            stem = path.stem
            try:
                stamp = dt.date.fromisoformat(stem[:10])
            except ValueError:
                continue  # not a dated artifact; not ours to delete
            by_date.setdefault(stamp, []).append(path)

        for stamp in sorted(by_date, reverse=True)[retain_days:]:
            for path in by_date[stamp]:
                path.unlink(missing_ok=True)
                removed.append(path)

    return tuple(removed)
