"""Performance reporting for bounded paper-trading sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from execution.trade_ledger import TradeRecord


@dataclass(frozen=True)
class AssetSessionStats:
    asset: str
    evaluations: int
    trades: int
    wins: int
    losses: int
    voids: int
    fees: Decimal
    pnl: Decimal

    @property
    def win_rate(self) -> Decimal:
        decided = self.wins + self.losses
        return (
            Decimal(self.wins) / Decimal(decided)
            if decided
            else Decimal("0")
        )


@dataclass(frozen=True)
class PaperSessionReport:
    started_at: datetime
    ended_at: datetime
    requested_duration_seconds: float
    scheduled_passes: int
    completed_passes: int
    skipped_passes: int
    starting_balance: Decimal
    ending_balance: Decimal
    trades: int
    open_trades: int
    wins: int
    losses: int
    voids: int
    fees: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    net_pnl: Decimal
    assets: tuple[AssetSessionStats, ...]

    @property
    def win_rate(self) -> Decimal:
        decided = self.wins + self.losses
        return (
            Decimal(self.wins) / Decimal(decided)
            if decided
            else Decimal("0")
        )


def build_paper_session_report(
    *,
    started_at: datetime,
    ended_at: datetime,
    requested_duration_seconds: float,
    scheduled_passes: int,
    completed_passes: int,
    skipped_passes: int,
    starting_balance: Decimal,
    ending_balance: Decimal,
    configured_assets: Iterable[str],
    evaluation_counts: dict[str, int],
    trades: Iterable[TradeRecord],
) -> PaperSessionReport:
    session_trades = tuple(trades)
    # EXITED belongs here. A position sold back into the book has a realised
    # P&L exactly like a settled one, and leaving it out made the headline read
    # "Net P&L +0.00" over a session whose own per-asset table said -2.35.
    realized = tuple(
        trade
        for trade in session_trades
        if trade.status in {"WON", "LOST", "VOID", "EXITED"}
    )
    pnls = tuple(
        trade.pnl for trade in realized if trade.pnl is not None
    )
    asset_stats: list[AssetSessionStats] = []
    for asset in configured_assets:
        matching = tuple(
            trade for trade in session_trades if trade.asset == asset
        )
        asset_stats.append(
            AssetSessionStats(
                asset=asset,
                evaluations=evaluation_counts.get(asset, 0),
                trades=len(matching),
                # An early exit counts as a win or a loss on the sign of its
                # P&L. Booking it as neither would let a strategy that exits
                # every loser report a spotless record.
                wins=sum(
                    trade.status == "WON"
                    or (trade.status == "EXITED" and (trade.pnl or Decimal("0")) > 0)
                    for trade in matching
                ),
                losses=sum(
                    trade.status == "LOST"
                    or (trade.status == "EXITED" and (trade.pnl or Decimal("0")) <= 0)
                    for trade in matching
                ),
                voids=sum(trade.status == "VOID" for trade in matching),
                fees=sum(
                    (
                        trade.fees_paid + (trade.exit_fees or Decimal("0"))
                        for trade in matching
                    ),
                    Decimal("0"),
                ),
                pnl=sum(
                    (
                        trade.pnl
                        for trade in matching
                        if trade.pnl is not None
                    ),
                    Decimal("0"),
                ),
            )
        )

    return PaperSessionReport(
        started_at=started_at,
        ended_at=ended_at,
        requested_duration_seconds=requested_duration_seconds,
        scheduled_passes=scheduled_passes,
        completed_passes=completed_passes,
        skipped_passes=skipped_passes,
        starting_balance=starting_balance,
        ending_balance=ending_balance,
        trades=len(session_trades),
        open_trades=sum(trade.status == "OPEN" for trade in session_trades),
        wins=sum(
            trade.status == "WON"
            or (trade.status == "EXITED" and (trade.pnl or Decimal("0")) > 0)
            for trade in session_trades
        ),
        losses=sum(
            trade.status == "LOST"
            or (trade.status == "EXITED" and (trade.pnl or Decimal("0")) <= 0)
            for trade in session_trades
        ),
        voids=sum(trade.status == "VOID" for trade in session_trades),
        fees=sum(
            (trade.fees_paid for trade in session_trades),
            Decimal("0"),
        ),
        gross_profit=sum((pnl for pnl in pnls if pnl > 0), Decimal("0")),
        gross_loss=-sum((pnl for pnl in pnls if pnl < 0), Decimal("0")),
        net_pnl=sum(pnls, Decimal("0")),
        assets=tuple(asset_stats),
    )


def format_paper_session_report(report: PaperSessionReport) -> str:
    elapsed_seconds = (
        report.ended_at - report.started_at
    ).total_seconds()
    lines = [
        "",
        "PAPER SESSION RESULTS",
        "=" * 104,
        (
            f"Started: {report.started_at.isoformat()}  "
            f"Ended: {report.ended_at.isoformat()}"
        ),
        (
            f"Runtime: {elapsed_seconds / 60:.2f} min "
            f"(requested {report.requested_duration_seconds / 60:.2f} min)"
        ),
        (
            f"Evaluation passes: {report.completed_passes}/"
            f"{report.scheduled_passes} completed, "
            f"{report.skipped_passes} skipped while a trade was open"
        ),
        (
            f"Starting balance: ${report.starting_balance:.2f}  "
            f"Ending balance: ${report.ending_balance:.2f}  "
            f"Net P&L: {report.net_pnl:+.2f}"
        ),
        (
            f"Trades: {report.trades}  Open: {report.open_trades}  "
            f"W/L/V: {report.wins}/{report.losses}/{report.voids}  "
            f"Win rate: {report.win_rate:.1%}"
        ),
        (
            f"Gross profit: ${report.gross_profit:.2f}  "
            f"Gross loss: ${report.gross_loss:.2f}  "
            f"Fees: ${report.fees:.2f}"
        ),
        "-" * 104,
        (
            f"{'ASSET':5} {'EVALS':>5} {'TRADES':>6} {'W':>3} "
            f"{'L':>3} {'V':>3} {'WIN %':>7} {'FEES':>9} {'P&L':>10}"
        ),
    ]
    for asset in report.assets:
        lines.append(
            f"{asset.asset:5} {asset.evaluations:>5} {asset.trades:>6} "
            f"{asset.wins:>3} {asset.losses:>3} {asset.voids:>3} "
            f"{asset.win_rate:>7.1%} ${asset.fees:>8.2f} "
            f"{asset.pnl:>+10.2f}"
        )
    lines.append("=" * 104)
    return "\n".join(lines)
