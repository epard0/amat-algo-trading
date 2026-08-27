"""Prometheus text-format exporter for Grafana ingestion.

The exposition format is emitted by hand rather than via ``prometheus_client``.
That is a deliberate choice: the bot's runtime dependency set is what
``uv sync`` installs on the VPS, and an undeclared import is exactly the class
of failure that already exists elsewhere in this repo. The format is a few
lines of text; a dependency that can break a trading host is not worth it.

All values derive from the durable record (ledger + decision log), so the
exporter is a pure read: it can be started, stopped, or crash without any
effect on trading.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence

from monitoring.performance_tracker import (
    Health,
    brier_score,
    calibration,
    edge_realisation,
    equity_curve,
    gate_funnel,
    group_summaries,
    health,
    longest_losing_streak,
    max_drawdown,
    regime_distribution,
    summarize,
)
from monitoring.sources import LedgerTrade

PREFIX = "kalshi_bot"


def _escape(value: str) -> str:
    return value.replace("\\", r"\\").replace('"', r"\"").replace("\n", r"\n")


def _labels(pairs: dict[str, str]) -> str:
    if not pairs:
        return ""
    inner = ",".join(f'{k}="{_escape(str(v))}"' for k, v in sorted(pairs.items()))
    return "{" + inner + "}"


class MetricWriter:
    """Accumulates Prometheus exposition lines with correct HELP/TYPE headers."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._declared: set[str] = set()

    def add(
        self,
        name: str,
        value: Any,
        *,
        help_text: str = "",
        metric_type: str = "gauge",
        labels: dict[str, str] | None = None,
    ) -> None:
        if value is None:
            return
        full = f"{PREFIX}_{name}"
        if full not in self._declared:
            # HELP/TYPE must appear once, before the first sample of a metric.
            if help_text:
                self._lines.append(f"# HELP {full} {help_text}")
            self._lines.append(f"# TYPE {full} {metric_type}")
            self._declared.add(full)
        numeric = float(value) if isinstance(value, Decimal) else float(value)
        self._lines.append(f"{full}{_labels(labels or {})} {numeric:g}")

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def render_metrics(
    trades: Sequence[LedgerTrade],
    decisions: Sequence[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> str:
    """Render the full metric set in Prometheus exposition format."""
    now = now or datetime.now(timezone.utc)
    writer = MetricWriter()

    # ---- per-mode P&L ------------------------------------------------------
    for mode in ("paper", "live"):
        subset = [t for t in trades if t.mode == mode]
        summary = summarize(subset)
        labels = {"mode": mode}
        writer.add("trades_total", summary.trades, labels=labels,
                   help_text="Trades recorded in the ledger")
        writer.add("trades_open", summary.open_trades, labels=labels,
                   help_text="Positions currently open")
        writer.add("trades_settled", summary.settled, labels=labels,
                   help_text="Trades that reached settlement")
        writer.add("trades_won", summary.wins, labels=labels,
                   help_text="Settled trades that won")
        writer.add("trades_lost", summary.losses, labels=labels,
                   help_text="Settled trades that lost")
        writer.add("trades_void", summary.voids, labels=labels,
                   help_text="Settled trades voided by the exchange")
        writer.add("net_pnl_usd", summary.net_pnl, labels=labels,
                   help_text="Cumulative realised profit and loss in USD")
        writer.add("staked_usd", summary.total_staked, labels=labels,
                   help_text="Total capital committed across all trades")
        writer.add("fees_paid_usd", summary.fees_paid, labels=labels,
                   help_text="Cumulative Kalshi taker fees paid")
        writer.add("win_rate", summary.win_rate, labels=labels,
                   help_text="Wins divided by decided (non-void) trades")
        writer.add("roi", summary.roi, labels=labels,
                   help_text="Net P&L per dollar staked")
        writer.add("fee_drag", summary.fee_drag, labels=labels,
                   help_text="Fees as a fraction of capital staked")

        curve = equity_curve(subset)
        writer.add("max_drawdown_usd", max_drawdown(curve), labels=labels,
                   help_text="Deepest peak-to-trough decline in realised P&L")
        writer.add("longest_losing_streak", longest_losing_streak(subset),
                   labels=labels, help_text="Longest run of consecutive losses")

    # ---- model quality -----------------------------------------------------
    writer.add("brier_score", brier_score(trades),
               help_text="Mean squared probability error; 0.25 equals always guessing 50%")

    realisation = edge_realisation(trades)
    writer.add("claimed_edge_mean", realisation["claimed_mean_edge"],
               help_text="Mean model edge asserted at order time")
    writer.add("realised_roi", realisation["realised_roi"],
               help_text="Actual return per dollar staked on settled trades")
    writer.add("edge_shortfall", realisation["shortfall"],
               help_text="Realised ROI minus claimed edge; negative means the model overprices its edge")

    for bucket in calibration(trades):
        labels = {"bucket": bucket.label}
        writer.add("calibration_count", bucket.count, labels=labels,
                   help_text="Settled trades falling in this predicted-probability bucket")
        writer.add("calibration_predicted", bucket.predicted_mean, labels=labels,
                   help_text="Mean predicted probability in the bucket")
        writer.add("calibration_actual", bucket.actual_rate, labels=labels,
                   help_text="Observed win rate in the bucket")
        writer.add("calibration_gap", bucket.gap, labels=labels,
                   help_text="Actual minus predicted; negative means overconfident")
        writer.add("calibration_pnl_usd", bucket.net_pnl, labels=labels,
                   help_text="Net P&L attributable to the bucket")

    # ---- breakdowns --------------------------------------------------------
    for dimension in ("asset", "regime", "outcome"):
        for name, summary in group_summaries(trades, dimension).items():
            labels = {dimension: name}
            writer.add(f"{dimension}_net_pnl_usd", summary.net_pnl, labels=labels,
                       help_text=f"Net P&L grouped by {dimension}")
            writer.add(f"{dimension}_trades", summary.settled, labels=labels,
                       help_text=f"Settled trades grouped by {dimension}")
            writer.add(f"{dimension}_win_rate", summary.win_rate, labels=labels,
                       help_text=f"Win rate grouped by {dimension}")

    # ---- decision funnel ---------------------------------------------------
    funnel = gate_funnel(decisions)
    writer.add("evaluations_total", funnel.evaluations,
               help_text="Market evaluations recorded in the decision log")
    writer.add("evaluations_traded", funnel.traded,
               help_text="Evaluations that resulted in an order")
    writer.add("evaluations_errored", funnel.errors,
               help_text="Evaluations that raised an exception")
    writer.add("trade_rate", funnel.trade_rate,
               help_text="Fraction of evaluations that became a trade")
    for gate, count in funnel.blocked:
        writer.add("gate_blocked_total", count, labels={"gate": gate},
                   help_text="Evaluations rejected, by the gate that rejected them")

    for regime, count in regime_distribution(decisions).items():
        writer.add("regime_evaluations", count, labels={"regime": regime},
                   help_text="Evaluations classified into each market regime")

    # ---- health ------------------------------------------------------------
    status: Health = health(trades, decisions, now=now)
    writer.add("health_ok", 1 if status.ok else 0,
               help_text="1 when no health issue is detected, 0 otherwise")
    writer.add("seconds_since_last_decision", status.seconds_since_decision,
               help_text="Age of the newest decision record; the real liveness signal")
    writer.add("consecutive_empty_discovery", status.consecutive_empty_discovery,
               help_text="Consecutive cycles that discovered no market at all")
    writer.add("recent_errors", status.recent_errors,
               help_text="Evaluation errors within the last 500 decisions")
    writer.add("open_trades", status.open_trades,
               help_text="Positions open right now across all modes")
    if status.last_decision_at:
        writer.add("last_decision_timestamp_seconds",
                   status.last_decision_at.timestamp(),
                   help_text="Unix time of the newest decision record")
    if status.last_trade_at:
        writer.add("last_trade_timestamp_seconds",
                   status.last_trade_at.timestamp(),
                   help_text="Unix time of the most recent order")

    writer.add("exporter_scrape_timestamp_seconds", now.timestamp(),
               help_text="Unix time this exposition was generated")
    return writer.render()


def render_from_paths(
    ledger_path: Any,
    decisions_path: Any,
    *,
    now: datetime | None = None,
    decision_limit: int | None = 50_000,
) -> str:
    """Convenience wrapper that loads state and renders in one call."""
    from monitoring.sources import read_decisions, read_trades

    return render_metrics(
        read_trades(ledger_path),
        read_decisions(decisions_path, limit=decision_limit),
        now=now,
    )


def iter_metric_names(exposition: str) -> Iterable[str]:
    """Metric names present in an exposition, for tests and debugging."""
    for line in exposition.splitlines():
        if line.startswith("# TYPE "):
            yield line.split()[2]
