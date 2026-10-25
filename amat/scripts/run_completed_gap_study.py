"""Completed-session gap reversal BUY deferral; no execution-time forecast or broker authority."""

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
from amat.execution import build_instructions
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.nested_inventory import file_hash
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, source_hashes, write
from autoresearch.improvements.validation_study import (
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

ARMS = ("baseline", "completed_gap_reversal", "completed_positive_gap")
SPEC = {
    "lookback_sessions": 20,
    "threshold": "strictly positive log adjusted_open[t]/adjusted_close[t-1] > population SD of 20 daily log close returns ending t-1",
    "reversal": "completed adjusted_close[t] < adjusted_open[t]",
    "candidate": "defer only each affected asset optional whole BUY at completed close t; preserve reductions/exits and all other assets",
    "ablation": "same completed large positive gap without reversal condition",
    "duration": "stateless one decision; recompute from each completed session; no saved authorization or automatic retry",
    "unavailable": "missing/nonpositive inputs fail closed as invalid experiment; no substitution",
    "execution": "unchanged next-session observed VWAP, cash/whole-share/1%entryvolume/cost contract",
}

EVALUATOR_CHANGE = {
    "src/autoresearch/improvements/incremental_gates.py": {
        "before": "432fe4074c3040cd9ac700af974c0047b17390490129c0cb592ae3892d069044",
        "after": "326170c20756f415310a4b1b164ffbc963f0e649f00ae63787ee5d59f0d1a12d",
    }
}


def reviewed_source_difference(
    parent: dict[str, str],
    current: dict[str, str],
) -> dict[str, dict[str, str | None]]:
    """Permit only the independently reviewed, exact evaluator-only correction."""
    changes = {
        key: {"before": parent.get(key), "after": current.get(key)}
        for key in sorted(set(parent) | set(current))
        if parent.get(key) != current.get(key)
    }
    if changes and changes != EVALUATOR_CHANGE:
        raise ValueError(
            "Unreviewed library/engine source drift; register a separately reviewed foundation"
        )
    return changes


def gap_features(panel: MarketPanel, index: int) -> dict[str, np.ndarray]:
    """Use open/close t and 20 completed close returns through t-1 only."""
    if panel.adjusted_opens is None or not 21 <= index < len(panel):
        raise ValueError("Actual opens and complete prior20 returns required")
    close = panel.adjusted_closes[index - 21 : index + 1]
    opening = panel.adjusted_opens[index]
    if (
        not np.isfinite(close).all()
        or not np.isfinite(opening).all()
        or np.any(close <= 0)
        or np.any(opening <= 0)
    ):
        raise ValueError("Observed finite positive gap inputs required")
    sigma = np.std(np.diff(np.log(close[:-1]), axis=0), axis=0, ddof=0)
    gap = np.log(opening / close[-2])
    large = gap > sigma
    reversal = close[-1] < opening
    return {"gap_log": gap, "prior20_sigma": sigma, "large_positive": large, "reversal": reversal}


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
    if arm not in ARMS[1:] or cost not in (2, 6, 10):
        raise ValueError("Unregistered candidate/cost")
    if config.execution.signal_to_execution_lag_sessions != 1:
        raise ValueError("Gap BUY deferral requires lag1 settled pending instructions")
    if tuple(panel.symbols) != tuple(config.to_universe().symbols):
        raise ValueError("Policy universe differs")
    indices = {s: i for i, s in enumerate(panel.sessions)}
    observations = []

    def policy(
        targets: dict[Symbol, float],
        ledger: LedgerSnapshot,
        session: SessionDate,
        _covariance: np.ndarray,
    ) -> dict[Symbol, float]:
        index = indices[session]
        if ledger.equity <= 0 or ledger.session_date != session or index + 1 >= len(panel):
            raise ValueError("Positive actual close ledger and next session required")
        marks = panel.marks_at(index)
        if any(s not in marks or p.mark != marks[s] for s, p in ledger.positions.items()):
            raise ValueError("Ledger marks differ from completed close")
        features = gap_features(panel, index)
        blocked = (
            features["large_positive"] & features["reversal"]
            if arm == "completed_gap_reversal"
            else features["large_positive"]
        )
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
        result = dict(targets)
        deferred = []
        for j, symbol in enumerate(panel.symbols):
            if symbol in buys and blocked[j]:
                result[symbol] = safe_held_weight(ledger, symbol, targets[symbol])
                deferred.append(str(symbol))
        observations.append(
            {
                "session": str(session),
                "input_cutoff": str(session),
                "optional_buy_symbols": list(map(str, buys)),
                "planned_whole_quantities": {str(s): str(q) for s, q in buys.items()},
                "deferred_buy_symbols": deferred,
                "gap_log": features["gap_log"].tolist(),
                "prior20_sigma": features["prior20_sigma"].tolist(),
                "reversal": features["reversal"].tolist(),
                "fitted_model": False,
            }
        )
        return result

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
    current_sources = source_hashes(Path(parent["root"]))
    changes = reviewed_source_difference(parent["source_sha256"], current_sources)
    body = {
        **parent,
        "schema": "completed-gap-buy-deferral/1",
        "study_id": study.name,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "driver_sha256": file_hash(Path(__file__)),
        "helper_sha256": file_hash(Path(__file__).with_name("run_qqq_foundation.py")),
        "campaign_protocol_sha256": digest(campaign_protocol),
        "campaign_protocol_path": str((campaign / "protocol.json").resolve()),
        "campaign_id": campaign_protocol["campaign_id"],
        "foundation_protocol_sha256": digest(parent),
        "foundation_source_sha256": parent["source_sha256"],
        "source_sha256": current_sources,
        "source_manifest_difference": changes,
        "source_manifest_difference_explanation": "Exact independently reviewed evaluator-only correction; all native economic runtime files unchanged. Parent measurements used loaded-source attestation and remain immutable. This study pins every current library source before and after measurement.",
        "configs": dict.fromkeys(ARMS, parent["config"]),
        "arms": dict.fromkeys(ARMS, "native research measurement only"),
        "fixed_spec": SPEC,
        "independent_review": {"path": str(review.resolve()), "sha256": file_hash(review)},
        "economic_deduplication": {"path": str(dedup.resolve()), "sha256": file_hash(dedup)},
        "economic_configuration_sha256": digest(
            {"baseline": parent["baseline_config_sha256"], "spec": SPEC}
        ),
        "mechanism": "A completed large positive opening gap followed by a down candle may indicate exhausted buying; defer only affected asset discretionary BUY one decision. Gap-only arm tests whether reversal condition adds information. All data are completed before the decision; no future execution window read.",
        "predicted_failure": "Gaps can continue and gap reversals can recover; delayed buys may miss rebounds, increase idle cash or shift future orders. Historical entry-price drag alone is not proof of predictable alpha.",
        "fit": None,
        "labels": None,
        "forecast_cutoff": "completed decision close; threshold prior20 returns excludes current",
        "display_limitation": "Completed-session gap pattern, not an execution-time opening limit. Prior20 daily-return SD is a fixed scale, not calibrated gap volatility. One-decision asset BUY deferral preserves native reductions but can miss rebounds and change later cash/fills. Reused history; no untouched holdout, fitted model or timing alpha claim. Native profile/planner export remains unverified; promotion disabled.",
        "acceptance": "All incremental campaign return/drawdown/2023/stress/accounting/causality/reproduction/uncertainty/native parity gates; reversal interpretation must be compared with fixed gap-only ablation.",
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
    if source_hashes(root) != p["source_sha256"]:
        raise ValueError("Registered full library source manifest changed before measurement")
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
        "schema": "completed-gap-buy-deferral-result/1",
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
    if source_hashes(root) != p["source_sha256"]:
        raise ValueError("Registered full library source manifest changed during measurement")
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
