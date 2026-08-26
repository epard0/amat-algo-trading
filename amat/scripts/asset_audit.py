"""Per-asset performance audit: why does one sleeve win or lose?

For one symbol, from the same deterministic engine the studies use:

* the independent sleeve result (the raw signal on this asset alone);
* every coalesced round trip of the symbol inside the COMBINED portfolio,
  with per-year net P&L attribution;
* whipsaw share (losses held <= 21 sessions) and tail concentration;
* trend-score occupancy (how often the signal was long the asset at all);
* the buy-and-hold comparison on the identical adjusted series.

Usage::

    uv run python scripts/asset_audit.py --symbol TLT

Output: a JSON artifact under ``outputs/experiments/`` plus a printed
summary. Measurement only -- this script changes nothing and selects
nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from amat import features
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.backtest.sleeves import sleeve_strategy
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.domain.actions import CorporateAction
from amat.domain.types import Symbol
from amat.reporting.insights import _coalesce_round_trips, replay_trades
from amat.validation.optimize import window_statistics

WHIPSAW_MAX_SESSIONS = 21


def _sleeve_result(
    strategy: StrategyConfig,
    panel: MarketPanel,
    actions: Mapping[dt.date, Sequence[CorporateAction]],
    symbol: str,
) -> dict[str, Any]:
    config = sleeve_strategy(strategy, symbol)
    sliced = panel.slice_symbols([Symbol(symbol)])
    narrowed = {
        session: tuple(a for a in acts if str(a.symbol) == symbol)
        for session, acts in actions.items()
        if any(str(a.symbol) == symbol for a in acts)
    }
    result = run_backtest(config, sliced, config.to_universe(), actions_by_session=narrowed)
    stats = window_statistics(
        result.equity_curve,
        result.session_dates,
        trades=result.total_fills,
        win_rate=0.0,
        expectancy_r=0.0,
    )
    return {
        "total_return": stats.total_return,
        "annualized_return": stats.annualized_return,
        "sharpe": stats.sharpe,
        "max_drawdown": stats.max_drawdown,
        "fills": result.total_fills,
        "costs": float(result.total_costs),
    }


def _signal_occupancy(strategy: StrategyConfig, panel: MarketPanel, symbol: str) -> dict[str, Any]:
    column = [str(s) for s in panel.symbols].index(symbol)
    returns = features.log_returns(panel.adjusted_closes)
    sigma = features.annualized_volatility(returns, strategy.volatility)
    scores = features.trend_scores(
        panel.adjusted_closes,
        sigma,
        strategy.trend,
        annualization_sessions=strategy.volatility.annualization_sessions,
    )
    long = features.long_scores(scores, strategy.trend)[:, column]
    available = long[np.isfinite(long)]
    return {
        "sessions_with_signal": int(available.size),
        "share_long": float(np.mean(available > 0)) if available.size else 0.0,
        "mean_score_when_long": (
            float(available[available > 0].mean()) if np.any(available > 0) else 0.0
        ),
        "raw_score_mean": float(scores[np.isfinite(scores[:, column]), column].mean())
        if np.any(np.isfinite(scores[:, column]))
        else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument(
        "--snapshot", type=Path, default=Path("data/snapshots/alpaca-sip-2026-08-11")
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    symbol = args.symbol.upper()

    strategy = load_strategy_config("configs/strategy/amat_v1.toml").strategy
    symbols = [str(entry) for entry in strategy.universe.symbols]
    if symbol not in symbols:
        message = f"{symbol} is not in the v1 universe {symbols}"
        raise SystemExit(message)
    panel, manifest = load_panel(args.snapshot, symbols)
    actions = load_corporate_actions(args.snapshot, symbols, sessions=panel.sessions)

    combined = run_backtest(strategy, panel, strategy.to_universe(), actions_by_session=actions)
    trades = [
        trade
        for trade in _coalesce_round_trips(
            replay_trades(combined, panel, actions_by_session=actions)
        )
        if str(trade.symbol) == symbol
    ]
    net = np.array([float(trade.net_pnl) for trade in trades])
    wins = net[net > 0]
    losses = net[net <= 0]
    whipsaws = [
        trade
        for trade in trades
        if float(trade.net_pnl) < 0 and trade.sessions_held <= WHIPSAW_MAX_SESSIONS
    ]
    by_year: dict[int, float] = defaultdict(float)
    for trade in trades:
        by_year[trade.exit_session.year] += float(trade.net_pnl)

    column = symbols.index(symbol)
    first, last = panel.adjusted_closes[0, column], panel.adjusted_closes[-1, column]

    payload = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "symbol": symbol,
        "snapshot_id": manifest.snapshot_id,
        "combined_portfolio_round_trips": {
            "count": len(trades),
            "net_pnl": float(net.sum()),
            "win_rate": float(np.mean(net > 0)) if net.size else 0.0,
            "profit_factor": (
                float(wins.sum() / -losses.sum()) if losses.size and losses.sum() < 0 else None
            ),
            "average_net": float(net.mean()) if net.size else 0.0,
            "median_net": float(np.median(net)) if net.size else 0.0,
            "average_hold_sessions": (
                float(np.mean([trade.sessions_held for trade in trades])) if trades else 0.0
            ),
            "whipsaws": len(whipsaws),
            "whipsaw_net_pnl": float(sum(float(t.net_pnl) for t in whipsaws)),
            "net_pnl_by_exit_year": {str(year): by_year[year] for year in sorted(by_year)},
            "worst_10": [
                {
                    "exit": trade.exit_session.isoformat(),
                    "net_pnl": float(trade.net_pnl),
                    "sessions_held": trade.sessions_held,
                }
                for trade in sorted(trades, key=lambda t: float(t.net_pnl))[:10]
            ],
            "best_5": [
                {
                    "exit": trade.exit_session.isoformat(),
                    "net_pnl": float(trade.net_pnl),
                    "sessions_held": trade.sessions_held,
                }
                for trade in sorted(trades, key=lambda t: float(t.net_pnl), reverse=True)[:5]
            ],
        },
        "independent_sleeve": _sleeve_result(strategy, panel, actions, symbol),
        "signal_occupancy": _signal_occupancy(strategy, panel, symbol),
        "buy_and_hold_total_return": float(last / first - 1.0),
        "caveat": (
            "Burned-sample measurement of one asset inside a portfolio "
            "strategy; nothing here justifies removing or resizing the asset "
            "outside a registered experiment (see 0004)."
        ),
    }

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path("outputs/experiments") / f"asset-audit-{symbol}-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    trip = payload["combined_portfolio_round_trips"]
    print(f"\nASSET AUDIT — {symbol} ({manifest.snapshot_id})")
    print(
        f"  combined round trips: {trip['count']}  net {trip['net_pnl']:+,.0f}  "
        f"win rate {trip['win_rate']:.1%}  PF {trip['profit_factor']}  "
        f"avg {trip['average_net']:+,.0f} / median {trip['median_net']:+,.0f}"
    )
    print(
        f"  whipsaws (<= {WHIPSAW_MAX_SESSIONS} sessions, net loss): {trip['whipsaws']} "
        f"costing {trip['whipsaw_net_pnl']:+,.0f}"
    )
    print(f"  net by exit year: {trip['net_pnl_by_exit_year']}")
    sleeve = payload["independent_sleeve"]
    print(
        f"  independent sleeve: {sleeve['total_return']:+.2%} total, "
        f"sharpe {sleeve['sharpe']:.2f}, maxdd {sleeve['max_drawdown']:+.2%}"
    )
    occupancy = payload["signal_occupancy"]
    print(
        f"  signal: long {occupancy['share_long']:.1%} of sessions, mean score "
        f"when long {occupancy['mean_score_when_long']:.3f}, raw mean "
        f"{occupancy['raw_score_mean']:+.3f}"
    )
    print(f"  buy-and-hold same series: {payload['buy_and_hold_total_return']:+.2%}")
    print(f"  artifact: {output}")


if __name__ == "__main__":
    main()
