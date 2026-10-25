"""Small fixed joint-downside ablation using native execution; no order authority."""
# ruff: noqa: E501, PLR2004 -- fixed preregistered experiment literals/prose

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from run_qqq_foundation import END, START, explain

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.types import SessionDate, Symbol
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.nested_inventory import file_hash
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, write
from autoresearch.improvements.validation_study import (
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

ARMS = ("baseline", "joint_downside75", "static75")
SPEC = {
    "lookback": 60,
    "loss_lookback": 20,
    "correlation_threshold": 0.60,
    "minimum_weight": 0.05,
    "minimum_assets": 3,
    "risk_multiplier": 0.75,
}


def make_policy(
    panel: MarketPanel, arm: str, spec: dict[str, Any] = SPEC
) -> tuple[Callable, list[dict[str, Any]]]:
    """Read only completed adjusted returns at each decision; never increases targets."""
    indices = {day: i for i, day in enumerate(panel.sessions)}
    returns = np.diff(np.log(panel.adjusted_closes), axis=0)
    observations = []

    def policy(
        targets: dict[Symbol, float],
        ledger: LedgerSnapshot,
        session: SessionDate,
        covariance: np.ndarray,
    ) -> dict[Symbol, float]:
        del ledger, covariance
        index = indices[session]
        scale, correlation, trailing = 1.0, None, None
        weights = np.array([targets[s] for s in panel.symbols])
        chosen = np.flatnonzero(weights >= spec["minimum_weight"])
        if arm == "static75":
            scale = spec["risk_multiplier"]
        elif index >= spec["lookback"] and len(chosen) >= spec["minimum_assets"]:
            history = returns[index - spec["lookback"] : index, :][:, chosen]
            if np.isfinite(history).all():
                downside = np.minimum(history, 0)
                if np.all(downside.std(axis=0) > 1e-12):
                    corr = np.corrcoef(downside, rowvar=False)
                    pairweights = weights[chosen, None] * weights[None, chosen]
                    upper = np.triu(np.ones(corr.shape, dtype=bool), 1)
                    correlation = float(
                        np.sum(corr[upper] * pairweights[upper]) / pairweights[upper].sum()
                    )
                    trailing = float(np.sum(history[-spec["loss_lookback"] :] @ weights[chosen]))
                    if correlation >= spec["correlation_threshold"] and trailing < 0:
                        scale = spec["risk_multiplier"]
        observations.append(
            {
                "session": str(session),
                "scale": scale,
                "downside_correlation": correlation,
                "trailing_log_return": trailing,
            }
        )
        return {s: w * scale for s, w in targets.items()}

    return policy, observations


def register(foundation: Path, study: Path, attribution: Path) -> None:
    if study.exists():
        raise ValueError("Use a new immutable study directory")
    parent = sealed(foundation / "protocol.json")
    evidence = sealed(attribution)
    if evidence["arm"] != "baseline" or evidence["cost_bps"] != 6:
        raise ValueError("Requires exact six-bps baseline attribution")
    months: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in evidence["sessions"]:
        for symbol, asset in row["assets"].items():
            months[row["session"][:7]][symbol] += Decimal(asset["net_pnl"])
    joint_loss_months = {
        month: {symbol: str(pnl) for symbol, pnl in assets.items() if pnl < 0}
        for month, assets in months.items()
        if sum(assets.values()) < 0
        and sum(pnl < 0 for pnl in assets.values()) >= SPEC["minimum_assets"]
    }
    if not joint_loss_months:
        raise ValueError("Holdings attribution does not substantiate joint multi-asset losses")
    body = {
        **parent,
        "schema": "joint-downside-correlation/1",
        "study_id": study.name,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "driver_sha256": file_hash(Path(__file__)),
        "helper_sha256": file_hash(Path(__file__).with_name("run_qqq_foundation.py")),
        "foundation_protocol_sha256": digest(parent),
        "attribution_sha256": digest(evidence),
        "configs": dict.fromkeys(ARMS, parent["config"]),
        "arms": {arm: arm for arm in ARMS},
        "fixed_spec": SPEC,
        "attribution_prerequisite": {
            "joint_loss_months": joint_loss_months,
            "interpretation": "Observed additive joint losses justify testing the hypothesis; they do not prove correlation caused losses.",
        },
        "mechanism": "Reduce all positive target weights by25% only when at least3 >=5% positions show weighted downside-return correlation >=0.60 over60 completed sessions and negative20-session target-weighted return.",
        "ablation": "Static75% targets uses identical25% reduction always; no outcome-fitted exposure match.",
        "invariants": [
            "past-only adjusted closes",
            "reduce-only targets",
            "native mandatory exits",
            "whole shares",
            "unchanged observed liquidity and costs",
        ],
        "acceptance": "Match/exceed current baseline full+2020forward at2/6/10, strictgain; no worse baseline/original drawdown and belowQQQ; preserve2023; independent stress/recovery/uncertainty and native planning/execution parity. Conditional benefit must survive static reduction comparison.",
        "economic_configuration_sha256": digest(
            {
                "mechanism": "joint-downside-correlation/1",
                "spec": SPEC,
                "baseline": parent["baseline_config_sha256"],
            }
        ),
        "prior_search": "Reviewed existing archived protocol/source names for joint/downside correlation; static cap and inverse-volatility variants are distinct. No historical outcome threshold search.",
        "promotion_authority": False,
    }
    study.mkdir(parents=True)
    write(study / "protocol.json", body)
    (study / "protocol.sha256").write_text(digest(body))


def measure(study: Path, arm: str, cost: int, output: Path, run_id: str) -> None:
    p = sealed(study / "protocol.json")
    if arm not in ARMS or cost not in p["costs_bps"] or output.exists():
        raise ValueError("Invalid arm/cost or immutable output exists")
    if (
        file_hash(Path(__file__)) != p["driver_sha256"]
        or file_hash(Path(__file__).with_name("run_qqq_foundation.py")) != p["helper_sha256"]
    ):
        raise ValueError("Frozen driver/helper changed")
    root = Path(p["root"])
    attest_loaded_sources(root / "src", p["source_sha256"], dict(sys.modules))
    snapshot = Path(p["snapshot"])
    if load_manifest(snapshot).partition_sha256 != p["partition_sha256"]:
        raise ValueError("Data identity changed")
    config = StrategyConfig.model_validate(p["configs"][arm])
    if config.adaptive_rotation.switch_cost_bps > 0:
        config = config.model_copy(
            update={
                "adaptive_rotation": config.adaptive_rotation.model_copy(
                    update={"switch_cost_bps": cost}
                )
            }
        )
    panel, _ = load_panel(
        snapshot, list(map(str, config.universe.symbols)), end=END, with_context=True
    )
    actions = load_corporate_actions(snapshot, panel.symbols, sessions=panel.sessions)
    policy, observations = make_policy(panel, arm, p["fixed_spec"])
    result = run_backtest(
        config,
        panel,
        config.to_universe(),
        execution_start=START,
        one_way_bps=cost,
        actions_by_session=actions,
        max_entry_window_participation=0.01,
        whole_share_buys=True,
        decision_policy=None if arm == "baseline" else policy,
    )
    rows, accounting = explain(result, panel, actions)
    record = {
        "schema": "joint-downside-result/1",
        "arm": arm,
        "cost_bps": cost,
        "finalized": True,
        "run_id": run_id,
        "study_id": p["study_id"],
        "profile_sha256": p["baseline_config_sha256"],
        "effective_config_sha256": digest(config.model_dump(mode="json")),
        "sessions_sha256": digest(rows),
        "protocol_sha256": digest(p),
        "sessions": rows,
        "metrics": sliced_metrics(rows),
        "monthly": monthly_metrics(rows),
        "recovery_episodes": recovery_episodes(rows),
        "accounting": accounting,
        "fractional_buy_count": sum(
            f.side.value == "buy" and f.quantity != f.quantity.to_integral_value()
            for s in result.sessions
            for f in s.fills
        ),
        "warnings": list(result.warnings),
        "policy_observations": observations,
        "policy_triggered_sessions": sum(o["scale"] < 1 for o in observations),
        "promotion_authority": False,
    }
    attest_loaded_sources(root / "src", p["source_sha256"], dict(sys.modules))
    write(output, record)
    output.with_suffix(".sha256").write_text(digest(record))
    print(f"{arm} {cost}bps {record['metrics']['full']['net_return']:.4%}", flush=True)


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--foundation", type=Path, required=True)
    reg.add_argument("--study", type=Path, required=True)
    reg.add_argument("--attribution", type=Path, required=True)
    run = sub.add_parser("measure")
    run.add_argument("--study", type=Path, required=True)
    run.add_argument("--arm", choices=ARMS, required=True)
    run.add_argument("--cost", type=int, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    a = cli.parse_args()
    if a.command == "register":
        register(a.foundation, a.study, a.attribution)
    else:
        measure(a.study, a.arm, a.cost, a.output, a.run_id)
