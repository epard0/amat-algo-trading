"""Cost-aware random-window comparison and conservative v6 refinement study."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import RunResult, run_backtest
from amat.backtest.stances import assemble_stance
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.config.switching import load_switching_config
from amat.config.v6 import RiskLearningConfig, load_v6_config
from amat.data.scalars import load_weight_scalars
from amat.data.snapshot import SnapshotManifest, load_corporate_actions, load_panel
from amat.domain.actions import CorporateAction
from amat.domain.types import SessionDate, Symbol
from amat.regime.asset import build_asset_regime_history
from amat.reporting.insights import _coalesce_round_trips, replay_trades
from amat.validation.randomwindows import (
    RandomWindow,
    WindowMeasurement,
    measure_window,
    sample_random_windows,
)
from amat.validation.riskrl import learn_asset_risk_policies

ALL_IN_COST_BPS = (2.0, 4.0, 6.0, 10.0)
REFINEMENT_COST_BPS = 6.0
INITIAL_CASH = Decimal("100000")
BASELINE_TARGET = 0.06
BASELINE_DEADBAND = 0.20
BASELINE_REBALANCE_DELTA = 0.02


@dataclass(frozen=True, slots=True)
class StrategyRun:
    label: str
    config_path: str
    config_sha256: str
    strategy: StrategyConfig
    panel: MarketPanel
    actions: Mapping[SessionDate, Sequence[CorporateAction]]
    stance: object | None = None
    weight_scalars: np.ndarray | None = None
    decision_overlay: object | None = None
    max_entry_window_participation: float | None = None
    caveat: str | None = None


def _subtract_months(value: dt.date, months: int) -> dt.date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(month_index, 12)
    return dt.date(year, month_index + 1, 1)


def _narrow_actions(
    actions: Mapping[SessionDate, Sequence[CorporateAction]], symbols: Sequence[Symbol]
) -> dict[SessionDate, tuple[CorporateAction, ...]]:
    wanted = set(symbols)
    return {
        session: kept
        for session, values in actions.items()
        if (kept := tuple(value for value in values if value.symbol in wanted))
    }


def _run(spec: StrategyRun, cost_bps: float) -> RunResult:
    return run_backtest(
        spec.strategy,
        spec.panel,
        spec.strategy.to_universe(),
        initial_cash=INITIAL_CASH,
        one_way_bps=cost_bps,
        actions_by_session=spec.actions,
        stance=spec.stance,  # type: ignore[arg-type]
        weight_scalars=spec.weight_scalars,
        decision_overlay=spec.decision_overlay,  # type: ignore[arg-type]
        max_entry_window_participation=spec.max_entry_window_participation,
    )


def _aggregate(rows: Sequence[WindowMeasurement]) -> dict[str, object]:
    if not rows:
        return {}

    def values(name: str) -> np.ndarray:
        return np.asarray([float(getattr(row, name)) for row in rows], dtype=float)

    returns = values("total_return")
    sharpes = values("sharpe")
    drawdowns = values("max_drawdown")
    costs = values("cost_fraction_of_start")
    trades = sum(row.completed_round_trips for row in rows)
    winners = sum(row.profitable_round_trips for row in rows)
    small_wins = sum(row.small_profitable_round_trips for row in rows)
    fills = sum(row.fills for row in rows)
    return {
        "windows": len(rows),
        "positive_window_rate": float(np.mean(returns > 0.0)),
        "median_total_return": float(np.median(returns)),
        "mean_total_return": float(np.mean(returns)),
        "p10_total_return": float(np.percentile(returns, 10.0)),
        "worst_total_return": float(returns.min()),
        "median_sharpe": float(np.median(sharpes)),
        "worst_drawdown": float(drawdowns.min()),
        "fills": fills,
        "fills_per_window": fills / len(rows),
        "completed_round_trips": trades,
        "profitable_round_trips": winners,
        "win_rate": winners / trades if trades else 0.0,
        "small_profitable_round_trips": small_wins,
        "small_win_rate": small_wins / trades if trades else 0.0,
        "median_expectancy": float(np.median(values("expectancy"))),
        "median_cost_fraction": float(np.median(costs)),
        "total_costs": float(sum(row.costs for row in rows)),
        "median_fill_notional": float(np.median(values("median_fill_notional"))),
        "p95_window_participation": float(np.percentile(values("p95_window_participation"), 95)),
        "max_window_participation": float(values("max_window_participation").max()),
    }


def _summaries(
    detailed: Sequence[dict[str, Any]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, float, int, str], list[WindowMeasurement]] = defaultdict(list)
    for row in detailed:
        measurement = WindowMeasurement(**row["measurement"])
        grouped[
            (
                str(row["strategy"]),
                float(row["cost_bps"]),
                measurement.duration_months,
                measurement.volatility_bucket,
            )
        ].append(measurement)
        grouped[
            (
                str(row["strategy"]),
                float(row["cost_bps"]),
                measurement.duration_months,
                "all",
            )
        ].append(measurement)
    return [
        {
            "strategy": key[0],
            "cost_bps": key[1],
            "duration_months": key[2],
            "volatility_bucket": key[3],
            **_aggregate(rows),
        }
        for key, rows in sorted(grouped.items())
    ]


def _strategy_specs(
    panel: MarketPanel,
    actions: Mapping[SessionDate, Sequence[CorporateAction]],
) -> tuple[StrategyRun, ...]:
    specs: list[StrategyRun] = []
    generic = (
        ("v1", "configs/strategy/amat_v1.toml", None),
        (
            "v2-managed-core-only",
            "configs/strategy/amat_managed_v2.toml",
            "The rejected managed overlay implementation was removed; this measures its "
            "29-asset core configuration only.",
        ),
        (
            "v2-no-kelly-core-only",
            "configs/strategy/amat_managed_v2_no_kelly.toml",
            "The rejected managed overlay implementation was removed; use_kelly is inert.",
        ),
        (
            "v3-news",
            "configs/strategy/amat_v3.toml",
            "The scalar file contains no de-risked sessions.",
        ),
        ("v5", "configs/strategy/amat_v5.toml", None),
    )
    for label, path, caveat in generic:
        resolved = load_strategy_config(path)
        strategy = resolved.strategy
        symbols = strategy.to_universe().symbols
        narrowed = panel.slice_symbols(symbols)
        scalars = (
            load_weight_scalars(Path("outputs/scalars/news_v3_2026-08-11.csv"), panel.sessions)
            if label == "v3-news"
            else None
        )
        specs.append(
            StrategyRun(
                label=label,
                config_path=path,
                config_sha256=resolved.content_sha256,
                strategy=strategy,
                panel=narrowed,
                actions=_narrow_actions(actions, symbols),
                weight_scalars=scalars,
                caveat=caveat,
            )
        )

    v4 = load_switching_config("configs/strategy/amat_v4.toml")
    v4_symbols = v4.strategy.trend.to_universe().symbols
    v4_panel = panel.slice_symbols(v4_symbols)
    specs.append(
        StrategyRun(
            label="v4-switching",
            config_path="configs/strategy/amat_v4.toml",
            config_sha256=v4.content_sha256,
            strategy=v4.strategy.trend,
            panel=v4_panel,
            actions=_narrow_actions(actions, v4_symbols),
            stance=assemble_stance(v4.strategy, v4_panel),
        )
    )

    v6 = load_v6_config("configs/strategy/amat_v6.toml")
    v6_symbols = v6.config.strategy.to_universe().symbols
    v6_panel = panel.slice_symbols(v6_symbols)
    v6_actions = _narrow_actions(actions, v6_symbols)
    specs.append(
        StrategyRun(
            label="v6-static",
            config_path="configs/strategy/amat_v6.toml",
            config_sha256=v6.content_sha256,
            strategy=v6.config.strategy,
            panel=v6_panel,
            actions=v6_actions,
        )
    )
    regime = build_asset_regime_history(v6_panel, v6.config.asset_regime, v6.config.liquidity)
    specs.append(
        StrategyRun(
            label="v6-regime-experiment",
            config_path="configs/strategy/amat_v6.toml",
            config_sha256=v6.content_sha256,
            strategy=v6.config.strategy,
            panel=v6_panel,
            actions=v6_actions,
            decision_overlay=regime.overlay,
            caveat="Rejected timing overlay retained as an explicit control.",
        )
    )
    return tuple(specs)


def _variant(
    base: StrategyConfig, *, target: float, deadband: float, rebalance_delta: float
) -> StrategyConfig:
    payload = base.model_dump()
    payload["name"] = f"amat-v6-rw-tv{target:.3f}-db{deadband:.3f}-rb{rebalance_delta:.3f}"
    payload["constraints"]["target_annualized_volatility"] = target
    payload["trend"]["deadband_abs"] = deadband
    payload["rebalance"]["min_weight_delta"] = rebalance_delta
    return StrategyConfig.model_validate(payload)


def _refinement_summary(rows: Sequence[WindowMeasurement], split: str) -> dict[str, object]:
    return _aggregate([row for row in rows if row.selection_split == split])


def _parameter_refinement(
    base: StrategyRun,
    windows: Sequence[RandomWindow],
) -> dict[str, object]:
    candidates: list[dict[str, Any]] = []
    for target in (0.04, 0.05, 0.06, 0.07):
        for deadband in (0.15, 0.20, 0.25):
            for rebalance_delta in (0.01, 0.02, 0.03):
                strategy = _variant(
                    base.strategy,
                    target=target,
                    deadband=deadband,
                    rebalance_delta=rebalance_delta,
                )
                spec = dataclasses.replace(base, strategy=strategy)
                result = _run(spec, REFINEMENT_COST_BPS)
                trades = _coalesce_round_trips(
                    replay_trades(result, spec.panel, actions_by_session=spec.actions)
                )
                measured = [
                    measure_window(result, spec.panel, trades, window) for window in windows
                ]
                development = _refinement_summary(measured, "development")
                validation = _refinement_summary(measured, "validation")
                candidates.append(
                    {
                        "target_volatility": target,
                        "deadband": deadband,
                        "rebalance_delta": rebalance_delta,
                        "development": development,
                        "validation": validation,
                    }
                )

    def rank_key(candidate: dict[str, Any]) -> tuple[float, ...]:
        measured = candidate["development"]
        return (
            float(measured["positive_window_rate"]),
            float(measured["p10_total_return"]),
            float(measured["median_sharpe"]),
            float(measured["median_total_return"]),
            -float(measured["median_cost_fraction"]),
        )

    ranked = sorted(candidates, key=rank_key, reverse=True)
    winner = ranked[0]
    baseline = next(
        row
        for row in candidates
        if row["target_volatility"] == BASELINE_TARGET
        and row["deadband"] == BASELINE_DEADBAND
        and row["rebalance_delta"] == BASELINE_REBALANCE_DELTA
    )
    winner_validation = winner["validation"]
    baseline_validation = baseline["validation"]
    gates = {
        "positive_window_rate": (
            winner_validation["positive_window_rate"] >= baseline_validation["positive_window_rate"]
        ),
        "median_total_return": (
            winner_validation["median_total_return"] >= baseline_validation["median_total_return"]
        ),
        "p10_total_return": (
            winner_validation["p10_total_return"] >= baseline_validation["p10_total_return"]
        ),
        "worst_drawdown": (
            winner_validation["worst_drawdown"] >= baseline_validation["worst_drawdown"]
        ),
        "win_rate": winner_validation["win_rate"] >= baseline_validation["win_rate"],
    }
    changed = any(
        winner[name] != baseline[name]
        for name in ("target_volatility", "deadband", "rebalance_delta")
    )
    accepted = changed and all(gates.values())
    return {
        "cost_bps": REFINEMENT_COST_BPS,
        "candidate_count": len(candidates),
        "ranking_rule": [
            "development positive-window rate",
            "development 10th-percentile return",
            "development median Sharpe",
            "development median return",
            "lower development cost fraction",
        ],
        "baseline": baseline,
        "training_winner": winner,
        "validation_gates": gates,
        "accepted": accepted,
        "decision": (
            "register as a separate v6.x research candidate; canonical v6 remains unchanged"
            if accepted
            else "rejected or tied on validation; canonical v6 remains unchanged"
        ),
        "ranked_candidates": ranked,
    }


def _expanded_risk_learning(base: StrategyRun) -> dict[str, object]:
    result = _run(base, REFINEMENT_COST_BPS)
    trades = _coalesce_round_trips(
        replay_trades(result, base.panel, actions_by_session=base.actions)
    )
    config = RiskLearningConfig(
        enabled=False,
        train_end=dt.date(2024, 12, 31),
        stop_losses=(0.03, 0.05, 0.08, 0.10, 0.15),
        reward_risk_ratios=(0.75, 1.0, 1.25, 1.5, 2.0, 3.0),
        reentry_embargo_sessions=21,
        min_training_episodes=10,
        prior_strength=20.0,
        downside_aversion=0.5,
        min_validation_improvement=0.001,
        validation_confidence_z=2.64,
    )
    learned = learn_asset_risk_policies(
        trades,
        base.panel,
        config,
        one_way_bps=REFINEMENT_COST_BPS,
    )
    return {
        "action_count_per_asset": 1 + len(config.stop_losses) * len(config.reward_risk_ratios),
        "cost_bps": REFINEMENT_COST_BPS,
        "config": config.model_dump(mode="json"),
        "activated_symbols": sorted(str(symbol) for symbol in learned.exit_rules),
        "policies": [dataclasses.asdict(policy) for policy in learned.policies],
        "caveat": learned.caveat,
    }


def _participation_sensitivity(
    base: StrategyRun,
    windows: Sequence[RandomWindow],
) -> dict[str, object]:
    details: list[dict[str, Any]] = []
    for cap in (None, 0.005, 0.01, 0.02):
        spec = dataclasses.replace(base, max_entry_window_participation=cap)
        label = "uncapped" if cap is None else f"cap-{cap:.2%}"
        for cost_bps in (2.0, 6.0, 10.0):
            print(f"v6-participation-{label}: {cost_bps:g} bps")
            result = _run(spec, cost_bps)
            trades = _coalesce_round_trips(
                replay_trades(result, spec.panel, actions_by_session=spec.actions)
            )
            for window in windows:
                details.append(
                    {
                        "strategy": label,
                        "cost_bps": cost_bps,
                        "measurement": measure_window(result, spec.panel, trades, window).as_dict(),
                    }
                )
    return {
        "caveat": (
            "Caps apply to buys only and leave sells unrestricted. A clipped buy records "
            "the unfilled remainder; the normal target logic may retry later."
        ),
        "summary": _summaries(details),
        "details": details,
    }


def _write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# V6 random-window robustness study",
        "",
        f"Generated `{payload['generated_at']}` from snapshot "
        f"`{payload['dataset']['snapshot_id']}`.",
        "All history is burned research; none of these randomized slices is a live holdout.",
        "",
        "## Cost model",
        "",
        "Observed next-session 09:30-09:35 raw VWAP, corporate actions, missed fills, and an",
        "all-in one-way cost ladder of 2/4/6/10 bps are applied. Market impact, queue position,",
        "taxes, and broker-specific fee schedules are not separately calibrated.",
        "",
        "## Aggregate all-window results at 2 bps",
        "",
        "| Strategy | Months | Positive | Median return | P10 return | Median Sharpe | "
        "Win rate | Fills/window |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows = [
        row
        for row in payload["summary"]
        if row["cost_bps"] == ALL_IN_COST_BPS[0] and row["volatility_bucket"] == "all"
    ]
    for row in rows:
        lines.append(
            f"| {row['strategy']} | {row['duration_months']} | "
            f"{row['positive_window_rate']:.1%} | {row['median_total_return']:+.2%} | "
            f"{row['p10_total_return']:+.2%} | {row['median_sharpe']:.2f} | "
            f"{row['win_rate']:.1%} | {row['fills_per_window']:.1f} |"
        )
    refinement = payload["v6_parameter_refinement"]
    winner = refinement["training_winner"]
    lines.extend(
        [
            "",
            "## V6 refinement decision",
            "",
            f"Development winner: target `{winner['target_volatility']:.0%}`, deadband "
            f"`{winner['deadband']:.2f}`, rebalance delta `{winner['rebalance_delta']:.2%}`.",
            f"Decision: **{refinement['decision']}**.",
            "",
            f"Expanded per-asset stop/target search activated "
            f"`{len(payload['expanded_risk_learning']['activated_symbols'])}` symbols.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:  # noqa: PLR0915 - one orchestration path persists one atomic artifact
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, default=Path("data/snapshots/alpaca-sip-2026-08-11")
    )
    parser.add_argument("--seed", type=int, default=20_260_813)
    parser.add_argument("--samples-per-duration", type=int, default=12)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/v6/random-windows"))
    parser.add_argument("--participation-only", action="store_true")
    args = parser.parse_args()

    manifest = SnapshotManifest.model_validate_json(
        (args.snapshot / "manifest.json").read_text(encoding="utf-8")
    )
    last_three_years = manifest.last_session.replace(year=manifest.last_session.year - 3)
    history_start = _subtract_months(last_three_years, 15)
    panel, loaded_manifest = load_panel(
        args.snapshot,
        list(manifest.symbols),
        start=history_start,
        with_context=True,
    )
    actions = load_corporate_actions(
        args.snapshot,
        list(manifest.symbols),
        sessions=panel.sessions,
    )
    spy_column = panel.symbols.index(Symbol("SPY"))
    windows = sample_random_windows(
        panel.sessions,
        panel.adjusted_closes[:, spy_column],
        last_years=3,
        samples_per_duration=args.samples_per_duration,
        seed=args.seed,
    )
    specs = _strategy_specs(panel, actions)
    if args.participation_only:
        generated = dt.datetime.now(dt.UTC)
        v6_static = next(spec for spec in specs if spec.label == "v6-static")
        participation_payload = {
            "generated_at": generated.isoformat(),
            "status": "burned_history_research_not_promoted",
            "seed": args.seed,
            "dataset": {"snapshot_id": loaded_manifest.snapshot_id},
            "windows": [window.as_dict() for window in windows],
            "participation_sensitivity": _participation_sensitivity(v6_static, windows),
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        target = args.output_dir / generated.strftime(
            "v6-participation-sensitivity-%Y%m%dT%H%M%SZ.json"
        )
        target.write_text(
            json.dumps(participation_payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print(target)
        return
    details: list[dict[str, Any]] = []
    for spec in specs:
        for cost_bps in ALL_IN_COST_BPS:
            print(f"{spec.label}: {cost_bps:g} bps")
            result = _run(spec, cost_bps)
            trades = _coalesce_round_trips(
                replay_trades(result, spec.panel, actions_by_session=spec.actions)
            )
            for window in windows:
                details.append(
                    {
                        "strategy": spec.label,
                        "cost_bps": cost_bps,
                        "measurement": measure_window(result, spec.panel, trades, window).as_dict(),
                    }
                )

    v6_static = next(spec for spec in specs if spec.label == "v6-static")
    refinement = _parameter_refinement(v6_static, windows)
    expanded_risk = _expanded_risk_learning(v6_static)
    generated = dt.datetime.now(dt.UTC)
    payload: dict[str, Any] = {
        "generated_at": generated.isoformat(),
        "status": "burned_history_research_not_promoted",
        "seed": args.seed,
        "window_protocol": {
            "last_years": 3,
            "durations_months": [1, 3, 6],
            "samples_per_duration": args.samples_per_duration,
            "stratification": "equal random samples from SPY realized-volatility terciles",
            "selection_split": (
                "half of each duration/volatility bucket development, half validation"
            ),
        },
        "cost_model": {
            "execution_price": "next-session observed raw 09:30-09:35 VWAP",
            "all_in_one_way_bps": list(ALL_IN_COST_BPS),
            "baseline_interpretation": "combined commission, spread, and slippage proxy",
            "corporate_actions": True,
            "missed_fills": True,
            "cash_rate": 0.0,
            "separate_market_impact_model": False,
            "not_modelled": ["queue position", "taxes", "broker-specific fees"],
        },
        "dataset": {
            "snapshot_id": loaded_manifest.snapshot_id,
            "history_loaded_from": history_start.isoformat(),
            "last_session": loaded_manifest.last_session.isoformat(),
            "partition_sha256": loaded_manifest.partition_sha256,
        },
        "strategies": [
            {
                "label": spec.label,
                "config_path": spec.config_path,
                "config_sha256": spec.config_sha256,
                "caveat": spec.caveat,
            }
            for spec in specs
        ],
        "windows": [window.as_dict() for window in windows],
        "summary": _summaries(details),
        "details": details,
        "v6_parameter_refinement": refinement,
        "expanded_risk_learning": expanded_risk,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = generated.strftime("v6-random-windows-%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"{stem}.json"
    summary_path = args.output_dir / f"{stem}-summary.csv"
    detail_path = args.output_dir / f"{stem}-details.csv"
    markdown_path = args.output_dir / f"{stem}.md"
    json_text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    json_path.write_text(json_text, encoding="utf-8")
    _write_csv(summary_path, payload["summary"])
    flattened = [
        {"strategy": row["strategy"], "cost_bps": row["cost_bps"], **row["measurement"]}
        for row in details
    ]
    _write_csv(detail_path, flattened)
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    digest = hashlib.sha256(json_text.encode("utf-8")).hexdigest()
    print(json_path)
    print(summary_path)
    print(detail_path)
    print(markdown_path)
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
