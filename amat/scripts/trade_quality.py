"""Round-trip trade quality of a strategy run: profit factor, average trade.

Answers the backtest-checklist questions no aggregate return statistic can:
is the profit factor above 1.5, and does the average round trip clear its own
transaction costs by a real margin? Uses the same deterministic engine and
the same round-trip coalescing as the study reports, so the numbers cite
cleanly against a study run record produced from the identical inputs.

Usage:
    uv run python scripts/trade_quality.py \
        --config configs/strategy/amat_v1.toml \
        --snapshot data/snapshots/alpaca-sip-2026-08-11
"""

from __future__ import annotations

import argparse
import statistics
from decimal import Decimal
from pathlib import Path

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.reporting.insights import _coalesce_round_trips, replay_trades


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/strategy/amat_v1.toml"))
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    args = parser.parse_args()

    strategy = load_strategy_config(args.config).strategy
    symbols = [str(symbol) for symbol in strategy.universe.symbols]
    panel, manifest = load_panel(args.snapshot, symbols)
    actions = load_corporate_actions(args.snapshot, symbols, sessions=panel.sessions)
    result = run_backtest(
        strategy,
        panel,
        strategy.to_universe(),
        initial_cash=args.initial_cash,
        actions_by_session=actions,
    )

    trades = _coalesce_round_trips(list(replay_trades(result, panel)))
    if not trades:
        print("no completed round trips; nothing to measure")
        return

    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl <= 0]
    gross_wins = sum(trade.net_pnl for trade in wins)
    gross_losses = -sum(trade.net_pnl for trade in losses)
    nets = [float(trade.net_pnl) for trade in trades]
    costs = [float(trade.costs) for trade in trades]

    print(f"TRADE QUALITY — {strategy.name} v{strategy.version} on {manifest.snapshot_id}")
    print(f"  completed round trips        {len(trades)}")
    print(f"  win rate (net of costs)      {len(wins) / len(trades):.1%}")
    if gross_losses > 0:
        print(f"  profit factor                {float(gross_wins / gross_losses):.2f}")
    else:
        print("  profit factor                undefined (no losing round trips)")
    print(f"  average net trade            {statistics.mean(nets):+,.2f}")
    print(f"  median net trade             {statistics.median(nets):+,.2f}")
    print(f"  average round-trip cost      {statistics.mean(costs):,.2f}")
    print(f"  avg net / avg cost           {statistics.mean(nets) / statistics.mean(costs):+.1f}x")
    print(
        f"  average hold                 "
        f"{statistics.mean(trade.sessions_held for trade in trades):.0f} sessions"
    )
    print(
        "  (round trips coalesced per (symbol, entry); rebalance partials are not separate trades)"
    )


if __name__ == "__main__":
    main()
