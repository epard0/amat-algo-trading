"""Frozen annual recipe replay with source-bound controls; never submits orders."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np

import amat.backtest.runner as native
from amat.backtest.exits import ExitRules
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.domain.types import Side
from amat.portfolio import precompute_decision_inputs
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.symbolic import evaluate_symbolic
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.nested_inventory import file_hash
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, write
from autoresearch.improvements.validation_study import (
    SLICES,
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)


def configured(base: StrategyConfig, recipe: dict) -> StrategyConfig:
    value = base.model_dump(mode="json")
    value["trend"].update(horizons_sessions=recipe["horizons"], deadband_abs=recipe["deadband"])
    value["rebalance"]["min_weight_delta"] = recipe["rebalance"]
    value["constraints"]["target_annualized_volatility"] = recipe["target_vol"]
    value["cross_sectional"].update(
        selection_interval_sessions=recipe["cadence"], max_positions=recipe["positions"]
    )
    value.setdefault("allocation", {})["volatility_power"] = recipe["vol_power"]
    return StrategyConfig.model_validate(value)


def measure(study: Path, arm: str, cost: int, destination: Path, before: bool = False) -> None:  # noqa: PLR0915
    protocol = sealed(study / "protocol.json")
    if file_hash(Path(__file__)) != protocol["driver_sha256"]:
        raise ValueError("Study driver differs from preregistration")
    source_root = Path(protocol["root"]) if not before else study.parent / "runtime-before"
    manifest = protocol["source_sha256"] if not before else protocol["before_source_sha256"]
    attest_loaded_sources(source_root / "src", manifest, dict(sys.modules))
    strategy = StrategyConfig.model_validate(protocol["configs"][arm])
    symbols = list(map(str, strategy.universe.symbols))
    panel, _ = load_panel(Path(protocol["snapshot"]), symbols, with_context=True)
    actions = load_corporate_actions(Path(protocol["snapshot"]), symbols, sessions=panel.sessions)
    rotation = strategy.adaptive_rotation
    if rotation.switch_cost_bps > 0:
        strategy = strategy.model_copy(
            update={"adaptive_rotation": rotation.model_copy(update={"switch_cost_bps": cost})}
        )
    schedule = []
    declarations = protocol["schedules"].get(arm, [])
    if before and declarations:
        raise ValueError("Before-engine controls cannot contain new schedules")
    for spec in declarations:
        recipe = spec["recipe"]
        active = configured(strategy, recipe)
        features = {}
        for name in {recipe["feature_a"], recipe["feature_b"]} - {"none"}:
            values = evaluate_symbolic(parse_pattern(spec["patterns"][name]), panel)
            eligible = np.array(spec["eligible_assets"][name], dtype=bool)
            if len(eligible) != len(symbols) or spec["symbols"] != symbols:
                raise ValueError("Scheduled fitted features have different asset identity")
            values[:, ~eligible] = np.nan
            features[name] = values
        scores = None
        if recipe["effect"] != 0:
            scores = precompute_decision_inputs(
                active, panel.adjusted_closes, sessions=panel.sessions
            ).long_scores.copy()
            a = features[recipe["feature_a"]]
            b = features[recipe["feature_b"]] if recipe["feature_b"] != "none" else a
            contribution = recipe["mix"] * np.nan_to_num(a) + (1 - recipe["mix"]) * np.nan_to_num(b)
            positive = np.isfinite(scores) & (scores > 0)
            scores[positive] = np.clip(
                scores[positive] + recipe["effect"] * recipe["polarity"] * contribution[positive],
                0,
                1,
            )
            scores = np.where(np.isfinite(scores), scores, 0.0)
        exits = (
            None
            if recipe["exit_mode"] == "none"
            else ExitRules(
                stop_loss=recipe["stop"],
                take_profit=recipe["stop"] * recipe["reward"]
                if recipe["exit_mode"] == "both"
                else None,
                reentry_embargo_sessions=recipe["embargo"],
            )
        )
        activation = next(day for day in panel.sessions if str(day) > spec["selection_end"])
        cutoff = panel.sessions[panel.sessions.index(activation) - 1]
        schedule.append(
            native.ScheduledDecisionContext(
                spec["context_id"],
                activation,
                cutoff,
                active,
                signal_override=scores,
                exit_rules=exits,
            )
        )
    kwargs = {} if before else {"decision_schedule": schedule}
    result = native.run_backtest(
        strategy,
        panel,
        strategy.to_universe(),
        actions_by_session=actions,
        execution_start=dt.date(2017, 1, 3),
        one_way_bps=cost,
        max_entry_window_participation=0.01,
        whole_share_buys=True,
        **kwargs,
    )
    rows = []
    previous = 100000.0
    for session in result.sessions:
        notional = sum(float(fill.price * fill.quantity) for fill in session.fills)
        rows.append(
            {
                "session": str(session.session),
                "equity": float(session.equity),
                "fees": float(session.attribution.fees),
                "exposure": session.gross_exposure,
                "fills": len(session.fills),
                "missed": len(session.missed),
                "traded_notional": notional,
                "turnover": notional / previous,
            }
        )
        previous = float(session.equity)
    metrics = sliced_metrics(rows)
    for name, metric in metrics.items():
        start, end = SLICES[name]
        subset = [row for row in rows if start <= row["session"] <= end]
        metric["annualized_turnover"] = (
            float(np.mean([row["turnover"] for row in subset]) * 252) if subset else 0.0
        )
        metric["traded_notional"] = sum(row["traded_notional"] for row in subset)
    fractional_buys = sum(
        fill.side == Side.BUY and fill.quantity != fill.quantity.to_integral_value()
        for session in result.sessions
        for fill in session.fills
    )
    if fractional_buys != 0:
        raise ValueError("Whole-share purchase contract violated")
    record = {
        "arm": arm,
        "cost_bps": cost,
        "protocol_sha256": digest(protocol),
        "sessions": rows,
        "metrics": metrics,
        "monthly": monthly_metrics(rows),
        "recovery_episodes": recovery_episodes(rows),
        "warnings": list(result.warnings),
        "fractional_buy_count": fractional_buys,
        "schedule_sha256": digest(declarations),
        "activated_contexts": [
            {"session": str(session.session), "context_id": session.decision_context_id}
            for session in result.sessions
            if getattr(session, "activated_context", False)
        ],
    }
    attest_loaded_sources(source_root / "src", manifest, dict(sys.modules))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Immutable result already exists")
    write(destination, record)
    destination.with_suffix(".sha256").write_text(digest(record))
    print(f"{arm} {cost}bps {metrics['full']['net_return']:.4%}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--cost", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before", action="store_true")
    args = parser.parse_args()
    measure(args.study, args.arm, args.cost, args.output, args.before)
