"""Run and audit the isolated v6 candidate against the latest v1 baseline.

The artifact includes aggregate performance, all reconstructed baseline and v6
round trips, per-trade decision inputs, regime/liquidity distributions, and the
offline per-asset stop/take-profit learning result.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from amat import features
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import RunResult, run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.config.v6 import load_v6_config
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.domain.actions import CorporateAction
from amat.domain.types import SessionDate
from amat.metrics import summarize
from amat.regime.asset import AssetRegimeHistory, build_asset_regime_history
from amat.reporting.insights import _coalesce_round_trips, replay_trades
from amat.validation.riskrl import learn_asset_risk_policies

WHIPSAW_MAX_SESSIONS = 21


def _trade_reasons(result: RunResult) -> dict[tuple[str, dt.date], list[str]]:
    reasons: dict[tuple[str, dt.date], list[str]] = defaultdict(list)
    for record in result.sessions:
        for fill in record.fills:
            if fill.side.value == "buy":
                reason = fill.reason.value if fill.reason is not None else "unspecified"
                reasons[(str(fill.symbol), fill.execution_session)].append(reason)
    return reasons


def _trade_payload(
    result: RunResult,
    panel: MarketPanel,
    regimes: AssetRegimeHistory,
    strategy: StrategyConfig,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]],
) -> list[dict[str, Any]]:
    trades = _coalesce_round_trips(
        replay_trades(result, panel, actions_by_session=actions_by_session)
    )
    reasons = _trade_reasons(result)
    session_index = {session: row for row, session in enumerate(panel.sessions)}
    symbol_index = {str(symbol): column for column, symbol in enumerate(panel.symbols)}
    returns = features.log_returns(panel.adjusted_closes)
    sigma = features.annualized_volatility(returns, strategy.volatility)
    scores = features.trend_scores(
        panel.adjusted_closes,
        sigma,
        strategy.trend,
        annualization_sessions=strategy.volatility.annualization_sessions,
    )
    payload: list[dict[str, Any]] = []
    for trade in trades:
        symbol = str(trade.symbol)
        execution_row = session_index[trade.entry_session]
        decision_row = max(0, execution_row - strategy.execution.signal_to_execution_lag_sessions)
        column = symbol_index[symbol]
        horizon_returns = {
            str(horizon): (
                float(
                    panel.adjusted_closes[decision_row, column]
                    / panel.adjusted_closes[decision_row - horizon, column]
                    - 1.0
                )
                if decision_row >= horizon
                else None
            )
            for horizon in strategy.trend.horizons_sessions
        }
        row = trade.as_dict()
        row.update(
            {
                "entry_reasons": reasons.get((symbol, trade.entry_session), []),
                "decision_session": panel.sessions[decision_row].isoformat(),
                "trend_score": float(scores[decision_row, column]),
                "annualized_sigma": float(sigma[decision_row, column]),
                "horizon_simple_returns": horizon_returns,
                "v6_regime": str(regimes.labels[decision_row, column]),
                "v6_regime_scalar": float(regimes.overlay.risk_multipliers[decision_row, column]),
                "v6_entry_allowed": bool(regimes.overlay.allow_increase[decision_row, column]),
                "adx": float(regimes.adx[decision_row, column]),
                "volatility_percentile": float(regimes.volatility_percentile[decision_row, column]),
                "efficiency_ratio": float(regimes.efficiency[decision_row, column]),
                "volume_percentile": float(regimes.volume_percentile[decision_row, column]),
                "dollar_volume": float(regimes.dollar_volume[decision_row, column]),
                "change_point": bool(regimes.transitions[decision_row, column]),
            }
        )
        payload.append(row)
    return payload


def _quality(trades: list[dict[str, Any]]) -> dict[str, Any]:
    net = np.asarray([float(row["net_pnl"]) for row in trades])
    wins = net[net > 0.0]
    losses = net[net <= 0.0]
    whipsaws = [
        row
        for row in trades
        if float(row["net_pnl"]) < 0 and int(row["sessions_held"]) <= WHIPSAW_MAX_SESSIONS
    ]
    by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in sorted({str(row["symbol"]) for row in trades}):
        subset = [row for row in trades if str(row["symbol"]) == symbol]
        values = np.asarray([float(row["net_pnl"]) for row in subset])
        positive = values[values > 0.0].sum()
        negative = -values[values <= 0.0].sum()
        by_symbol[symbol] = {
            "trades": len(subset),
            "win_rate": float(np.mean(values > 0.0)),
            "net_pnl": float(values.sum()),
            "profit_factor": float(positive / negative) if negative > 0 else None,
            "whipsaws": sum(
                1
                for row in subset
                if float(row["net_pnl"]) < 0 and int(row["sessions_held"]) <= WHIPSAW_MAX_SESSIONS
            ),
            "blocked_by_v6_entry_quality": sum(
                1 for row in subset if not bool(row["v6_entry_allowed"])
            ),
        }
    return {
        "trades": len(trades),
        "win_rate": float(np.mean(net > 0.0)),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.size else None,
        "average_net": float(net.mean()),
        "median_net": float(np.median(net)),
        "whipsaws": len(whipsaws),
        "whipsaw_net_pnl": float(sum(float(row["net_pnl"]) for row in whipsaws)),
        "regime_at_entry": dict(Counter(str(row["v6_regime"]) for row in trades)),
        "entry_reason": dict(Counter(reason for row in trades for reason in row["entry_reasons"])),
        "by_symbol": by_symbol,
        "worst_25": sorted(trades, key=lambda row: float(row["net_pnl"]))[:25],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/strategy/amat_v6.toml"))
    parser.add_argument("--baseline", type=Path, default=Path("configs/strategy/amat_v1.toml"))
    parser.add_argument(
        "--snapshot", type=Path, default=Path("data/snapshots/alpaca-sip-2026-08-11")
    )
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    resolved = load_v6_config(args.config)
    config = resolved.config
    strategy = config.strategy
    symbols = [str(symbol) for symbol in strategy.universe.symbols]
    panel, manifest = load_panel(args.snapshot, symbols, with_context=True)
    actions = load_corporate_actions(args.snapshot, symbols, sessions=panel.sessions)
    regimes = build_asset_regime_history(panel, config.asset_regime, config.liquidity)

    baseline_strategy = load_strategy_config(args.baseline).strategy
    baseline = run_backtest(
        baseline_strategy,
        panel,
        baseline_strategy.to_universe(),
        initial_cash=args.initial_cash,
        actions_by_session=actions,
    )
    regime_overlay_experiment = run_backtest(
        strategy,
        panel,
        strategy.to_universe(),
        initial_cash=args.initial_cash,
        actions_by_session=actions,
        decision_overlay=regimes.overlay,
    )
    static_risk_control = run_backtest(
        strategy,
        panel,
        strategy.to_universe(),
        initial_cash=args.initial_cash,
        actions_by_session=actions,
    )
    candidate = (
        regime_overlay_experiment
        if config.asset_regime.capital_control_enabled
        else static_risk_control
    )

    baseline_trades = _trade_payload(baseline, panel, regimes, baseline_strategy, actions)
    candidate_trades = _trade_payload(candidate, panel, regimes, strategy, actions)
    learned = learn_asset_risk_policies(
        _coalesce_round_trips(replay_trades(baseline, panel, actions_by_session=actions)),
        panel,
        config.risk_learning,
        one_way_bps=baseline.one_way_bps,
    )
    payload: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "status": "research_only_burned_history_not_promoted",
        "config": {
            "path": str(resolved.source_path),
            "sha256": resolved.content_sha256,
            "strategy": f"{strategy.name} v{strategy.version}",
        },
        "dataset": {
            "snapshot_id": manifest.snapshot_id,
            "first_session": manifest.first_session.isoformat(),
            "last_session": manifest.last_session.isoformat(),
            "partition_sha256": manifest.partition_sha256,
        },
        "baseline": {
            "performance": summarize(baseline).as_dict(),
            "trade_quality": _quality(baseline_trades),
            "trades": baseline_trades,
        },
        "static_6pct_control": {
            "performance": summarize(static_risk_control).as_dict(),
            "purpose": (
                "Same v6 core and 6% target with no regime/liquidity overlay; "
                "separates lower constant risk from timing value."
            ),
        },
        "regime_overlay_experiment": {
            "performance": summarize(regime_overlay_experiment).as_dict(),
            "selected_for_v6": config.asset_regime.capital_control_enabled,
            "verdict": (
                "selected only when explicitly enabled"
                if config.asset_regime.capital_control_enabled
                else "rejected as capital control: lost to the static 6% control"
            ),
        },
        "v6": {
            "performance": summarize(candidate).as_dict(),
            "trade_quality": _quality(candidate_trades),
            "trades": candidate_trades,
            "regime_distribution": dict(Counter(regimes.labels.ravel().tolist())),
            "entry_permission_rate": float(regimes.overlay.allow_increase.mean()),
            "mean_risk_scalar": float(regimes.overlay.risk_multipliers.mean()),
        },
        "risk_learning": {
            "enabled_in_canonical_v6": config.risk_learning.enabled,
            "caveat": learned.caveat,
            "policies": [dataclasses.asdict(policy) for policy in learned.policies],
            "activated_symbols": sorted(str(symbol) for symbol in learned.exit_rules),
        },
    }
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path("outputs/v6") / f"v6-audit-{timestamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    summary = {
        "baseline": payload["baseline"]["performance"],
        "static_6pct_control": payload["static_6pct_control"]["performance"],
        "regime_overlay_experiment": payload["regime_overlay_experiment"]["performance"],
        "v6": payload["v6"]["performance"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
