"""Shared causal per-asset score-routing ablation; no execution authority."""
# ruff: noqa: E501, PLR2004 -- fixed preregistered economic contract

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
from run_qqq_foundation import END, START, explain

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import DecisionOverlay, run_backtest
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.portfolio import precompute_decision_inputs
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.nested_inventory import file_hash
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, write
from autoresearch.improvements.validation_study import (
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

ARMS = ("baseline", "stall_score_route", "stall_allocation_only")
SPEC = {
    "short_return_sessions": 21,
    "intermediate_return_sessions": 63,
    "base_horizons": [63, 126, 252],
    "fast_horizons": [21, 63, 126],
    "gate": "own_21_return < 0 AND own_63_return > 0",
    "routed_score": "minimum(base_long_score, fast_long_score) only at gate",
    "score_stage": "native long score before adaptive rotation/membership/ranking",
    "allocation_ratio": "routed/base where finite base>0; otherwise1",
    "eligibility": "preserve base warmup, base horizon agreement and all other native guards",
}


def combine_scores(
    prices: np.ndarray,
    base: np.ndarray,
    fast: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """No trained state or ticker parameters; decisions read only their own past."""
    if prices.shape != base.shape or base.shape != fast.shape or prices.ndim != 2:
        raise ValueError("Aligned session-by-asset prices and scores required")
    gate = np.zeros(base.shape, dtype=bool)
    for t in range(SPEC["intermediate_return_sessions"], len(prices)):
        current = prices[t]
        short = prices[t - SPEC["short_return_sessions"]]
        intermediate = prices[t - SPEC["intermediate_return_sessions"]]
        valid = (
            np.isfinite(current)
            & np.isfinite(short)
            & np.isfinite(intermediate)
            & (current > 0)
            & (short > 0)
            & (intermediate > 0)
            & np.isfinite(base[t])
            & np.isfinite(fast[t])
        )
        gate[t] = valid & (current < short) & (current > intermediate)
    routed = base.copy()
    routed[gate] = np.minimum(base[gate], fast[gate])
    ratio = np.ones(base.shape)
    positive = gate & np.isfinite(base) & (base > 0)
    ratio[positive] = routed[positive] / base[positive]
    # Override API requires finite inputs and restores the base unavailable mask.
    override = np.where(np.isfinite(routed), routed, 0.0)
    if (
        not np.isfinite(override).all()
        or np.any(override < 0)
        or np.any(override > 1)
        or not np.isfinite(ratio).all()
        or np.any(ratio < 0)
        or np.any(ratio > 1)
    ):
        raise ValueError("Long-score reduction contract violated")
    return override, ratio, gate


def routing_inputs(
    strategy: StrategyConfig, panel: MarketPanel
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if list(strategy.trend.horizons_sessions) != SPEC["base_horizons"]:
        raise ValueError("Study requires the unchanged current-default trend horizons")
    base = precompute_decision_inputs(strategy, panel.adjusted_closes, sessions=panel.sessions)
    fast_strategy = strategy.model_copy(
        update={
            "trend": strategy.trend.model_copy(
                update={"horizons_sessions": tuple(SPEC["fast_horizons"])}
            )
        }
    )
    fast = precompute_decision_inputs(fast_strategy, panel.adjusted_closes, sessions=panel.sessions)
    return combine_scores(panel.adjusted_closes, base.long_scores, fast.long_scores)


def approved_inputs(review: Path, dedup: Path) -> None:
    """Require an independent pre-outcome approval of these exact driver bytes."""
    verdict = sealed(review)
    sealed(dedup)
    if (
        verdict.get("implementation_approved") is not True
        or verdict.get("candidate_outcomes_seen") is not False
        or verdict.get("driver_sha256") != file_hash(Path(__file__))
    ):
        raise ValueError(
            "Exact driver needs sealed independent pre-outcome implementation approval"
        )


def register(foundation: Path, study: Path, review: Path, dedup: Path) -> None:
    if study.exists():
        raise ValueError("Use a new immutable study")
    parent = sealed(foundation / "protocol.json")
    if not review.is_file() or not dedup.is_file():
        raise ValueError("Independent review and economic dedup evidence required")
    approved_inputs(review, dedup)
    body = {
        **parent,
        "schema": "asset-stall-score-routing/1",
        "study_id": study.name,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "driver_sha256": file_hash(Path(__file__)),
        "helper_sha256": file_hash(Path(__file__).with_name("run_qqq_foundation.py")),
        "foundation_protocol_sha256": digest(parent),
        "configs": dict.fromkeys(ARMS, parent["config"]),
        "arms": dict.fromkeys(ARMS, "native long-or-cash research; no production activation"),
        "fixed_spec": SPEC,
        "independent_review": {"path": str(review.resolve()), "sha256": file_hash(review)},
        "economic_deduplication": {"path": str(dedup.resolve()), "sha256": file_hash(dedup)},
        "economic_configuration_sha256": digest(
            {"baseline": parent["baseline_config_sha256"], "spec": SPEC}
        ),
        "mechanism": "Shared own-asset stalled-trend detection switches only signal strength toward an archived faster recipe, never increasing a base score. Native reranking and normalization may redistribute capital and increase another asset target; this is score routing, not universally reduce-only portfolio weights or full strategy-context switching.",
        "ablation": "Unchanged score/rank/membership selection, same causal per-asset routed/base ratio applied to allocation targets via native DecisionOverlay.",
        "invariants": [
            "one shared gate for all assets",
            "no fit or ticker search",
            "base252warmup",
            "basehorizonagreement",
            "nativecash/actions/costs/volume",
            "mandatoryexits",
        ],
        "acceptance": "All campaign incremental return/drawdown/2023/stress/accounting/causality/nativeparity/reproduction/uncertainty gates; signal routing must add value beyond allocation-only ablation.",
        "promotion_authority": False,
        "display_limitation": "Score-only per-asset routing preserves base horizons/eligibility. Fixed past-only close inputs and previously researched history; exported native planning/profile support remains unverified and blocks promotion.",
        "limitations": [
            *parent["limitations"],
            "A winning score override requires a versioned native planning/profile implementation and separate parity verification before promotion",
        ],
    }
    study.mkdir(parents=True)
    write(study / "protocol.json", body)
    (study / "protocol.sha256").write_text(digest(body))


def measure(study: Path, arm: str, cost: int, output: Path, run_id: str) -> None:
    p = sealed(study / "protocol.json")
    if p["fixed_spec"] != SPEC:
        raise ValueError("Preregistered economic spec differs from frozen implementation")
    if (
        arm not in ARMS
        or cost not in p["costs_bps"]
        or output.exists()
        or output.with_suffix(".sha256").exists()
        or not run_id
    ):
        raise ValueError("Unregistered identity or immutable output exists")
    if (
        file_hash(Path(__file__)) != p["driver_sha256"]
        or file_hash(Path(__file__).with_name("run_qqq_foundation.py")) != p["helper_sha256"]
    ):
        raise ValueError("Frozen driver/helper changed")
    for key in ("independent_review", "economic_deduplication"):
        if file_hash(Path(p[key]["path"])) != p[key]["sha256"]:
            raise ValueError("Preregistered review/dedup evidence changed")
    approved_inputs(
        Path(p["independent_review"]["path"]), Path(p["economic_deduplication"]["path"])
    )
    root = Path(p["root"])
    attest_loaded_sources(root / "src", p["source_sha256"], dict(sys.modules))
    snapshot = Path(p["snapshot"])
    if load_manifest(snapshot).partition_sha256 != p["partition_sha256"]:
        raise ValueError("Data identity changed")
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
    override, ratio, gate = routing_inputs(config, panel)
    overlay = DecisionOverlay(ratio, np.ones(ratio.shape, dtype=bool))
    result = run_backtest(
        config,
        panel,
        config.to_universe(),
        execution_start=START,
        one_way_bps=cost,
        actions_by_session=actions,
        max_entry_window_participation=0.01,
        whole_share_buys=True,
        signal_override=override if arm == "stall_score_route" else None,
        decision_overlay=overlay if arm == "stall_allocation_only" else None,
    )
    rows, accounting = explain(result, panel, actions)
    if digest([r["session"] for r in rows]) != p["session_calendar_sha256"]:
        raise ValueError("Execution calendar differs")
    record = {
        "schema": "asset-stall-score-routing-result/1",
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
        "fractional_buy_count": sum(
            f.side.value == "buy" and f.quantity != f.quantity.to_integral_value()
            for s in result.sessions
            for f in s.fills
        ),
        "warnings": list(result.warnings),
        "routing_inputs_sha256": digest(
            {"score": override.tolist(), "ratio": ratio.tolist(), "gate": gate.tolist()}
        ),
        "routing_observations": {
            "gate_asset_sessions": int(gate.sum()),
            "reduced_asset_sessions": int((ratio < 1).sum()),
            "gate_per_asset": dict(
                zip(map(str, panel.symbols), map(int, gate.sum(axis=0)), strict=True)
            ),
        },
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
    for name in ("foundation", "study", "review", "dedup"):
        reg.add_argument(f"--{name}", type=Path, required=True)
    run = sub.add_parser("measure")
    run.add_argument("--study", type=Path, required=True)
    run.add_argument("--arm", choices=ARMS, required=True)
    run.add_argument("--cost", choices=(2, 6, 10), type=int, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "register":
        register(args.foundation, args.study, args.review, args.dedup)
    else:
        measure(args.study, args.arm, args.cost, args.output, args.run_id)


if __name__ == "__main__":
    main()
