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
import itertools
import json
import math
import statistics
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from executor.broker import Account, Order, Position
from executor.plan_source import ExecutionPlan
from executor.submit import SubmissionResult

__all__ = [
    "DailyReport",
    "PerformanceStats",
    "build_daily_report",
    "prune_old_reports",
]

#: Below this many observations, dispersion statistics (volatility, Sharpe,
#: drawdown) describe the sample and essentially nothing else. They are still
#: computed -- hiding them invites recomputing them badly elsewhere -- but they
#: are labelled.
_MEANINGFUL_SAMPLE = 20

_TRADING_DAYS_PER_YEAR = 252

#: Standard deviation is undefined below two observations.
_MIN_RETURNS_FOR_DISPERSION = 2


@dataclass(frozen=True, slots=True)
class PerformanceStats:
    """Measured statistics over the executor's own equity curve."""

    sessions: int
    first_session: str
    last_session: str
    starting_equity: Decimal
    current_equity: Decimal
    total_return: float
    day_return: float
    max_drawdown: float
    annualized_volatility: float
    sharpe_ratio: float
    best_day: float
    worst_day: float
    positive_days: int

    @property
    def is_meaningful(self) -> bool:
        """Whether the sample supports the dispersion statistics."""
        return self.sessions >= _MEANINGFUL_SAMPLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.sessions,
            "first_session": self.first_session,
            "last_session": self.last_session,
            "starting_equity": str(self.starting_equity),
            "current_equity": str(self.current_equity),
            "total_return": self.total_return,
            "day_return": self.day_return,
            "max_drawdown": self.max_drawdown,
            "annualized_volatility": self.annualized_volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "best_day": self.best_day,
            "worst_day": self.worst_day,
            "positive_days": self.positive_days,
            "sample_is_meaningful": self.is_meaningful,
        }


def compute_stats(equity_curve: list[dict[str, Any]]) -> PerformanceStats:
    """Compute statistics from the append-only equity curve.

    A cash-return assumption of zero is used for the Sharpe ratio, matching the
    strategy's primary cash assumption (``[cash] constant_annualized_rate =
    0.0``). Using a different risk-free rate here than the backtest used would
    make the two numbers quietly incomparable.
    """
    if not equity_curve:
        zero = Decimal("0")
        return PerformanceStats(
            sessions=0,
            first_session="",
            last_session="",
            starting_equity=zero,
            current_equity=zero,
            total_return=0.0,
            day_return=0.0,
            max_drawdown=0.0,
            annualized_volatility=0.0,
            sharpe_ratio=0.0,
            best_day=0.0,
            worst_day=0.0,
            positive_days=0,
        )

    equities = [Decimal(str(entry["equity"])) for entry in equity_curve]
    starting = equities[0]
    current = equities[-1]

    returns: list[float] = [
        float(latest / previous) - 1.0
        for previous, latest in itertools.pairwise(equities)
        if previous > 0
    ]

    peak = equities[0]
    max_drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            drawdown = float(equity / peak) - 1.0
            max_drawdown = min(max_drawdown, drawdown)

    volatility = 0.0
    sharpe = 0.0
    if len(returns) >= _MIN_RETURNS_FOR_DISPERSION:
        daily_sigma = statistics.stdev(returns)
        volatility = daily_sigma * math.sqrt(_TRADING_DAYS_PER_YEAR)
        mean_daily = statistics.fmean(returns)
        if daily_sigma > 0:
            sharpe = (mean_daily / daily_sigma) * math.sqrt(_TRADING_DAYS_PER_YEAR)

    return PerformanceStats(
        sessions=len(equity_curve),
        first_session=str(equity_curve[0].get("session", "")),
        last_session=str(equity_curve[-1].get("session", "")),
        starting_equity=starting,
        current_equity=current,
        total_return=(float(current / starting) - 1.0) if starting > 0 else 0.0,
        day_return=returns[-1] if returns else 0.0,
        max_drawdown=max_drawdown,
        annualized_volatility=volatility,
        sharpe_ratio=sharpe,
        best_day=max(returns) if returns else 0.0,
        worst_day=min(returns) if returns else 0.0,
        positive_days=sum(1 for value in returns if value > 0),
    )


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
    warnings: tuple[str, ...]

    @property
    def subject(self) -> str:
        """Email subject line: the day, the move, and any alarm."""
        alarm = " [ATTENTION]" if self.warnings else ""
        return (
            f"AMAT paper {self.session.isoformat()}: "
            f"{self.stats.day_return:+.2%} day, "
            f"equity {self.stats.current_equity:,.2f}{alarm}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "session": self.session.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "stats": self.stats.as_dict(),
            "account": self.account,
            "positions": list(self.positions),
            "submission": self.submission,
            "plan": self.plan_summary,
            "orders": list(self.orders),
            "warnings": list(self.warnings),
        }

    def to_markdown(self) -> str:
        """Render the human-readable report."""
        stats = self.stats
        lines: list[str] = [
            f"# AMAT paper trading — {self.session.isoformat()}",
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
                f"| Day return | {stats.day_return:+.2%} |",
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
                f"| Max drawdown | {stats.max_drawdown:.2%} |",
                f"| Annualized volatility | {stats.annualized_volatility:.2%} |",
                f"| Sharpe ratio (0% cash) | {stats.sharpe_ratio:.2f} |",
                f"| Best day | {stats.best_day:+.2%} |",
                f"| Worst day | {stats.worst_day:+.2%} |",
                f"| Positive days | {stats.positive_days} / {max(stats.sessions - 1, 0)} |",
                "",
            ]
        )

        if not stats.is_meaningful:
            lines.extend(
                [
                    f"> **Not a meaningful sample.** Volatility, Sharpe, and drawdown above are "
                    f"computed from {stats.sessions} session(s), fewer than the "
                    f"{_MEANINGFUL_SAMPLE} this report treats as a floor. They describe this "
                    f"sample and support no inference about the strategy.",
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
                    f"{notional:,.2f} | {order.get('status', '')} |"
                )
            lines.append("")

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
    extra_warnings: tuple[str, ...] = (),
) -> DailyReport:
    """Assemble one day's report from state the caller already fetched."""
    stats = compute_stats(equity_curve)
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
                "client_order_id": order.client_order_id,
            }
            for order in orders
        ),
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
