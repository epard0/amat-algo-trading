"""Fixed optional-buy portfolio marginal-cost experiment; no broker authority."""

# ruff: noqa: E501, PLR2004 -- literal preregistered economic specification
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

import numpy as np
from run_qqq_foundation import END, START, explain

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.types import SessionDate, Side, Symbol
from amat.execution import build_instructions, one_way_cost
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.nested_inventory import file_hash
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, write
from autoresearch.improvements.validation_study import (
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

ARMS = ("baseline", "logutility_optional_buys", "return_only_optional_buys")
SPEC = {
    "horizon_sessions": 5,
    "mean_lookback_sessions": 63,
    "annualization": 252,
    "logutility_risk_aversion": 1,
    "quantity_rounding": "floor whole requested BUY shares at decision close",
    "benefit_units": "NAV fraction over five assumed sessions",
    "cost_units": "immediate one-way estimated optional BUY cost divided by actual NAV",
    "decision": "joint all-or-none optional BUY set; accept iff predicted benefit strictly exceeds estimated cost",
    "ablation": "same arithmetic mean including variance conversion; omit only quadratic covariance penalty",
}


def safe_held_weight(ledger: LedgerSnapshot, symbol: Symbol, desired: float) -> float:
    """Round outward so Decimal intent sizing cannot create a microscopic sale."""
    position = ledger.positions.get(symbol)
    if position is None or position.quantity == 0:
        return 0.0
    value = float(position.market_value / ledger.equity)
    if not np.isfinite(value):
        raise ValueError("Finite held weight required")
    for _ in range(8):
        if Decimal(str(value)) * ledger.equity / position.mark >= position.quantity:
            break
        value = float(np.nextafter(value, np.inf))
    else:
        raise ValueError("Unable to represent held weight without a microscopic sale")
    if value > desired:
        raise ValueError("Rounded held weight exceeds original discretionary buy target")
    return value


def make_policy(
    config: StrategyConfig, panel: MarketPanel, arm: str, cost: int
) -> tuple[Callable, list[dict[str, Any]]]:
    """Observed settled ledger only; pending-state support deliberately restricted to lag1."""
    if arm not in ARMS[1:] or cost not in (2, 6, 10):
        raise ValueError("Unregistered candidate/cost")
    if config.execution.signal_to_execution_lag_sessions != 1:
        raise ValueError(
            "Optional-buy policy requires lag1, with native pending instructions already settled"
        )
    if config.covariance.annualization_sessions != SPEC["annualization"]:
        raise ValueError("Covariance annualization differs from utility units")
    if tuple(panel.symbols) != tuple(config.to_universe().symbols):
        raise ValueError("Policy universe and panel differ")
    indices = {s: i for i, s in enumerate(panel.sessions)}
    logreturns = np.diff(np.log(panel.adjusted_closes), axis=0)
    observations = []

    def policy(
        targets: dict[Symbol, float],
        ledger: LedgerSnapshot,
        session: SessionDate,
        covariance: np.ndarray,
    ) -> dict[Symbol, float]:
        index = indices[session]
        if index < SPEC["mean_lookback_sessions"] or index + 1 >= len(panel):
            raise ValueError("Completed forecast history and next session required")
        if ledger.equity <= 0 or ledger.session_date != session:
            raise ValueError("Actual positive decision-close NAV required")
        marks = panel.marks_at(index)
        if any(s not in marks or p.mark != marks[s] for s, p in ledger.positions.items()):
            raise ValueError("Observed ledger marks differ from decision close")
        n = len(panel.symbols)
        if (
            covariance.shape != (n, n)
            or not np.isfinite(covariance).all()
            or not np.allclose(covariance, covariance.T, atol=1e-12)
            or np.linalg.eigvalsh(covariance).min() < -1e-10
        ):
            raise ValueError("Finite symmetric positive-semidefinite past covariance required")
        returns = logreturns[index - SPEC["mean_lookback_sessions"] : index]
        if returns.shape != (SPEC["mean_lookback_sessions"], n) or not np.isfinite(returns).all():
            raise ValueError("Complete trailing forecast returns required")
        mu = SPEC["annualization"] * returns.mean(axis=0) + 0.5 * np.diag(covariance)
        orders, _ = build_instructions(
            targets,
            ledger.positions,
            marks,
            ledger.equity,
            signal_session=session,
            execution_session=panel.sessions[index + 1],
            rebalance=config.rebalance,
            execution=config.execution,
            in_flight={},
        )
        buys = {
            o.symbol: o.quantity.to_integral_value(rounding=ROUND_DOWN)
            for o in orders
            if o.side == Side.BUY
        }
        buys = {s: q for s, q in buys.items() if q > 0}
        wait = dict(targets)
        for s in buys:
            wait[s] = safe_held_weight(ledger, s, targets[s])
        w0 = np.array([wait[s] for s in panel.symbols])
        delta = np.array(
            [float(buys.get(s, Decimal(0)) * marks[s] / ledger.equity) for s in panel.symbols]
        )
        w1 = w0 + delta
        desired = np.array([targets[s] for s in panel.symbols])
        if np.any(delta < 0) or np.any(w1 > desired + 1e-10):
            raise ValueError("Whole planned increment exceeds original target")
        expected_return = float(delta @ mu)
        variance_difference = float(w1 @ covariance @ w1 - w0 @ covariance @ w0)
        annual_benefit = expected_return - (
            0.5 * variance_difference if arm == "logutility_optional_buys" else 0.0
        )
        benefit = SPEC["horizon_sessions"] / SPEC["annualization"] * annual_benefit
        estimated_fee = sum((one_way_cost(q * marks[s], cost) for s, q in buys.items()), Decimal(0))
        fee_fraction = float(estimated_fee / ledger.equity)
        permit = not buys or benefit > fee_fraction
        observations.append(
            {
                "session": str(session),
                "optional_buy_symbols": list(map(str, buys)),
                "planned_whole_quantities": {str(s): str(q) for s, q in buys.items()},
                "planned_turnover_nav": float(delta.sum()),
                "predicted_five_session_benefit_nav": benefit,
                "estimated_immediate_buy_cost_nav": fee_fraction,
                "predicted_net_benefit_nav": benefit - fee_fraction,
                "annual_variance_difference": variance_difference,
                "optional_buys_permitted": permit,
                "forecast_cutoff": str(session),
                "fitted_model": False,
            }
        )
        return dict(targets) if permit else wait

    return policy, observations


def approved_inputs(review: Path, dedup: Path) -> None:
    verdict = sealed(review)
    duplication = sealed(dedup)
    if (
        verdict.get("implementation_approved") is not True
        or verdict.get("candidate_outcomes_seen") is not False
        or verdict.get("driver_sha256") != file_hash(Path(__file__))
    ):
        raise ValueError("Sealed exact-source pre-outcome implementation approval required")
    if duplication.get("economic_duplication_found") is not False:
        raise ValueError("Sealed explicit economic nonduplication finding required")


def register(foundation: Path, study: Path, review: Path, dedup: Path, campaign: Path) -> None:
    if study.exists():
        raise ValueError("Use new immutable study")
    parent = sealed(foundation / "protocol.json")
    campaign_protocol = sealed(campaign / "protocol.json")
    approved_inputs(review, dedup)
    body = {
        **parent,
        "schema": "portfolio-marginal-optional-buy/1",
        "study_id": study.name,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "driver_sha256": file_hash(Path(__file__)),
        "helper_sha256": file_hash(Path(__file__).with_name("run_qqq_foundation.py")),
        "campaign_protocol_sha256": digest(campaign_protocol),
        "campaign_protocol_path": str((campaign / "protocol.json").resolve()),
        "campaign_id": campaign_protocol["campaign_id"],
        "foundation_protocol_sha256": digest(parent),
        "configs": dict.fromkeys(ARMS, parent["config"]),
        "arms": dict.fromkeys(ARMS, "native research measurement only"),
        "fixed_spec": SPEC,
        "independent_review": {"path": str(review.resolve()), "sha256": file_hash(review)},
        "economic_deduplication": {"path": str(dedup.resolve()), "sha256": file_hash(dedup)},
        "economic_configuration_sha256": digest(
            {"baseline": parent["baseline_config_sha256"], "spec": SPEC}
        ),
        "mechanism": "Evaluate whole-share decision-close optional BUY batch expected portfolio logutility gain against immediate estimated one-way buy costs. Never defer a native reduction or exit.",
        "predicted_failure": "Noisy trailing means can underinvest, delay entries and miss rebounds; covariance term may add no value beyond return-only gating.",
        "fit": None,
        "labels": None,
        "forecast_cutoff": "completed decision close only",
        "display_limitation": "Five-session opportunity proxy, not calibrated expected alpha or guaranteed fills. Lag1 settled state only; costs estimated at decision marks. Native actual fills retain cash rejection, observed volume cap and future price uncertainty. Reused history; native profile/planner export unverified blocks promotion.",
        "acceptance": "All incremental campaign return/drawdown/2023/stress/accounting/causality/reproduction/uncertainty/native parity gates; covariance benefit must survive return-only ablation.",
        "promotion_authority": False,
    }
    study.mkdir(parents=True)
    write(study / "protocol.json", body)
    (study / "protocol.sha256").write_text(digest(body))


def measure(study: Path, arm: str, cost: int, output: Path, run_id: str) -> None:
    p = sealed(study / "protocol.json")
    if (
        p["fixed_spec"] != SPEC
        or arm not in ARMS
        or cost not in p["costs_bps"]
        or not run_id
        or output.exists()
        or output.with_suffix(".sha256").exists()
    ):
        raise ValueError("Unregistered inputs or existing immutable measurement")
    if (
        file_hash(Path(__file__)) != p["driver_sha256"]
        or file_hash(Path(__file__).with_name("run_qqq_foundation.py")) != p["helper_sha256"]
    ):
        raise ValueError("Driver/helper changed")
    for key in ("independent_review", "economic_deduplication"):
        if file_hash(Path(p[key]["path"])) != p[key]["sha256"]:
            raise ValueError("Review/dedup changed")
    approved_inputs(
        Path(p["independent_review"]["path"]), Path(p["economic_deduplication"]["path"])
    )
    if digest(sealed(Path(p["campaign_protocol_path"]))) != p["campaign_protocol_sha256"]:
        raise ValueError("Campaign authorization identity changed")
    root = Path(p["root"])
    attest_loaded_sources(root / "src", p["source_sha256"], dict(sys.modules))
    snapshot = Path(p["snapshot"])
    if load_manifest(snapshot).partition_sha256 != p["partition_sha256"]:
        raise ValueError("Data changed")
    config = StrategyConfig.model_validate(p["configs"][arm])
    if config.adaptive_rotation.switch_condition == "volatility":
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
    observations = []
    policy = None
    if arm != "baseline":
        policy, observations = make_policy(config, panel, arm, cost)
    result = run_backtest(
        config,
        panel,
        config.to_universe(),
        execution_start=START,
        one_way_bps=cost,
        actions_by_session=actions,
        max_entry_window_participation=0.01,
        whole_share_buys=True,
        decision_policy=policy,
    )
    rows, accounting = explain(result, panel, actions)
    if digest([r["session"] for r in rows]) != p["session_calendar_sha256"]:
        raise ValueError("Calendar changed")
    record = {
        "schema": "portfolio-marginal-optional-buy-result/1",
        "finalized": True,
        "arm": arm,
        "cost_bps": cost,
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
        "policy_observations": observations,
        "fractional_buy_count": sum(
            f.side == Side.BUY and f.quantity != f.quantity.to_integral_value()
            for r in result.sessions
            for f in r.fills
        ),
        "warnings": list(result.warnings),
        "promotion_authority": False,
    }
    if record["fractional_buy_count"]:
        raise ValueError("Whole-share contract violated")
    attest_loaded_sources(root / "src", p["source_sha256"], dict(sys.modules))
    output.parent.mkdir(parents=True, exist_ok=True)
    write(output, record)
    output.with_suffix(".sha256").write_text(digest(record))
    print(f"{arm} {cost}bps {record['metrics']['full']['net_return']:.4%}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    for name in ("foundation", "study", "review", "dedup", "campaign"):
        reg.add_argument("--" + name, type=Path, required=True)
    run = sub.add_parser("measure")
    run.add_argument("--study", type=Path, required=True)
    run.add_argument("--arm", choices=ARMS, required=True)
    run.add_argument("--cost", type=int, choices=(2, 6, 10), required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "register":
        register(args.foundation, args.study, args.review, args.dedup, args.campaign)
    else:
        measure(args.study, args.arm, args.cost, args.output, args.run_id)


if __name__ == "__main__":
    main()
