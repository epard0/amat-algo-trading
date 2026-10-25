"""Inert synthetic counterexamples: naive weight projection is not an action exporter."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import inspect
import json
import os
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from amat.config.loading import load_strategy_config
from amat.execution.intent import build_execution_plan
from amat.planning import build_plan
from autoresearch.improvements.agent_portfolio_arm import (
    ArmPolicy,
    MarketEvidence,
    ResearchContext,
    hold_contract,
    validate_action_contract,
)
from autoresearch.improvements.incremental_gates import read_sealed
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, source_hashes

COSTS = (2, 6, 10)
CASES = ("hold", "resize_buy", "stop_loss")
DECISION = dt.datetime(2026, 10, 23, 20, tzinfo=dt.UTC)
EXECUTION = dt.datetime(2026, 10, 26, 13, 30, tzinfo=dt.UTC)
EXPIRY = dt.datetime(2026, 10, 26, 13, 35, tzinfo=dt.UTC)
MODEL = "synthetic-diagnostic/no-model:free"
LIMITATIONS = (
    "SYNTHETIC DIAGNOSTIC ONLY: no model inference, prices, fills or performance measured.",
    "NAIVE target projection is deliberately lossy; not an adapter, export or apply path.",
    "Matching a quantity cannot establish action compatibility, safety or execution parity.",
    "Conditional stops must not become unconditional exits; native shadow support is unverified.",
    "Cost scenarios configure proposal validation; no realized fees or net returns are measured.",
    (
        "The strategy decision engine is not evaluated: only v14.3.0 profile-parameter "
        "weight-to-plan construction."
    ),
    (
        "Native earliest date is a calendar lower bound resolved by the executor, "
        "not a proven session-date defect."
    ),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instant(value: str) -> dt.datetime:
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timezone-aware deadline required")
    return result


def assert_time(protocol: dict) -> None:
    if dt.datetime.now(dt.UTC) >= min(
        instant(protocol["hard_stop_utc"]), instant(protocol["campaign_deadline_utc"])
    ):
        raise ValueError("Diagnostic deadline expired")


def fixture(cost: int) -> ResearchContext:
    if type(cost) is not int or cost not in COSTS:
        raise ValueError("Exactly three preregistered costs permitted")
    return ResearchContext(
        decision_at=DECISION,
        account_observed_at=DECISION,
        execution_at=EXECUTION,
        execution_clock="next_session_open",
        expires_at=EXPIRY,
        account_id="synthetic-shadow-plan-diagnostic-only",
        equity=Decimal("10000"),
        confirmed_cash=Decimal("9000"),
        restricted_cash=Decimal("0"),
        positions=(("QQQ", Decimal("10")),),
        evidence=tuple(
            MarketEvidence(symbol, "synthetic-" + symbol, DECISION, Decimal("100"), Decimal("100"))
            for symbol in ("QQQ", "SPY")
        ),
        authorized_intents=(),
        policy=ArmPolicy(one_way_bps=Decimal(cost)),
    )


def proposal_for(context: ResearchContext, case: str) -> dict:
    proposal = hold_contract(
        context, model_id=MODEL, reason="Fixed synthetic compatibility diagnostic"
    )
    if case == "hold":
        return proposal
    if case not in CASES:
        raise ValueError("Unknown diagnostic case")
    proposal["actions"] = [
        {
            "kind": "resize" if case == "resize_buy" else "stop_loss",
            "reason": "Fixed synthetic compatibility counterexample",
            "symbol": "QQQ",
            "side": "buy" if case == "resize_buy" else "sell",
            "quantity": "1" if case == "resize_buy" else "10",
            "limit_price": "101" if case == "resize_buy" else "89",
            "evidence_id": "synthetic-QQQ",
        }
    ]
    if case == "stop_loss":
        proposal["actions"][0]["trigger_price"] = "90"
    return proposal


def naive_targets(context: ResearchContext, proposal: dict) -> dict[str, float]:
    """Intentionally lossy projection for counterexamples, never an export API."""
    quantities = dict(context.positions)  # retain all unchanged holdings
    marks = {row.symbol: row.mark for row in context.evidence}
    for action in proposal["actions"]:
        if action["kind"] == "hold":
            continue
        delta = Decimal(action["quantity"]) * (1 if action["side"] == "buy" else -1)
        quantities[action["symbol"]] = quantities.get(action["symbol"], Decimal(0)) + delta
    return {
        symbol: float(quantity * marks[symbol] / context.equity)
        for symbol, quantity in quantities.items()
    }


def diagnose_case(strategy: Any, config_sha: str, context: ResearchContext, case: str) -> dict:
    """Validate original first, then expose information discarded by naive projection."""
    proposal = proposal_for(context, case)
    before = context.evidence_sha256, digest(proposal)
    validation = validate_action_contract(proposal, context)
    targets = naive_targets(context, proposal)
    plan = build_execution_plan(
        strategy,
        engine="synthetic-naive-shadow-diagnostic",
        as_of_session=DECISION.date(),
        target_weights=targets,
        marks={row.symbol: row.mark for row in context.evidence},
        current_positions=dict(context.positions),
        account_equity=context.equity,
        config_sha256=config_sha,
        snapshot_id="SYNTHETIC-NO-MARKET-DATA",
        code_revision="synthetic-diagnostic-only",
        generated_at=DECISION,
        extra_caveats=LIMITATIONS,
    ).as_dict()
    native_delta = {
        row["symbol"]: str(Decimal(row["quantity"]) * (1 if row["side"] == "buy" else -1))
        for row in plan["intents"]
    }
    original_delta = {
        row["symbol"]: str(Decimal(row["quantity"]) * (1 if row["side"] == "buy" else -1))
        for row in proposal["actions"]
        if row["kind"] != "hold"
    }
    if before != (context.evidence_sha256, digest(proposal)):
        raise ValueError("Diagnostic mutated trusted context or original proposal")
    missing_parameters = sorted(
        {
            "account_id",
            "policy_id",
            "authorized_intents",
            "confirmed_cash",
            "restricted_cash",
            "pending_cash",
            "limit_price",
            "trigger_price",
            "expires_at",
        }
        - set(inspect.signature(build_execution_plan).parameters)
    )
    return {
        "case": case,
        "cost_bps": int(context.policy.one_way_bps),
        "original_context": json.loads(json.dumps(asdict(context), default=str)),
        "original_proposal": proposal,
        "original_validation": validation,
        "original_context_sha256": before[0],
        "original_proposal_sha256": before[1],
        "NAIVE_target_projection": targets,
        "NAIVE_native_plan": plan,
        "requested_action_delta": original_delta,
        "native_intent_delta": native_delta,
        "same_emitted_quantities": (
            {s: Decimal(q) for s, q in native_delta.items()}
            == {s: Decimal(q) for s, q in original_delta.items()}
        ),
        "native_earliest_date_is_calendar_lower_bound": True,
        "native_withheld": plan["withheld"],
        "conditionality_lost": case == "stop_loss" and bool(plan["intents"]),
        "context_or_action_parameters_absent_from_builder": missing_parameters,
        "original_expiry": context.expires_at.isoformat(),
        "native_expiry": plan["expires_at"],
        "expiry_preserved": context.expires_at.isoformat() == plan["expires_at"],
        "original_pending_cash": str(
            sum((intent.pending_cash for intent in context.authorized_intents), Decimal(0))
        ),
        "native_reference_prices_are_limits": False,
        "native_compatible_export_verified": False,
        "execution_authority": "none",
        "promotion_authority": False,
        "performance_evidence": False,
        "limitations": list(LIMITATIONS),
    }


def diagnose(config: Path, cost: int) -> dict:
    resolved = load_strategy_config(config)
    if resolved.strategy.version != "14.3.0":
        raise ValueError("Current pinned v14.3.0 config required")
    context = fixture(cost)
    return {
        "schema": "shadow-plan-compatibility-result/1",
        "cost_bps": cost,
        "config_sha256": sha(config),
        "strategy_version": resolved.strategy.version,
        "cases": [diagnose_case(resolved.strategy, sha(config), context, case) for case in CASES],
        "build_plan_accepts_target_weights": "target_weights"
        in inspect.signature(build_plan).parameters,
        "execution_authority": "none",
        "promotion_authority": False,
        "performance_evidence": False,
        "native_compatible_export_verified": False,
        "limitations": list(LIMITATIONS),
    }


def verify_context(protocol_path: Path, output: Path, cost: int) -> tuple[dict, dict]:
    protocol = read_sealed(protocol_path)
    if (
        protocol.get("schema") != "shadow-plan-compatibility/1"
        or protocol.get("costs_bps") != list(COSTS)
        or protocol.get("cases") != list(CASES)
    ):
        raise ValueError("Preregistered diagnostic shape differs")
    fixture(cost)
    campaign = Path(protocol["campaign_root"])
    state = json.loads((campaign / "state.json").read_text())
    active = state.get("active_run")
    token = os.environ.get("AMAT_RESEARCH_RUN_ID")
    if not active or not token or active.get("run_id") != token:
        raise ValueError("Diagnostic requires active managed controller identity")
    manifest = read_sealed(Path(active["manifest_path"]))
    campaign_protocol = read_sealed(campaign / "protocol.json")
    if (
        digest(manifest) != active["manifest_sha256"]
        or digest(campaign_protocol) != active["protocol_sha256"]
        or protocol["campaign_protocol_sha256"] != digest(campaign_protocol)
        or protocol["campaign_deadline_utc"] != campaign_protocol["deadline_utc"]
    ):
        raise ValueError("Controller protocol/manifest identity differs")
    if (
        not campaign.is_absolute()
        or manifest.get("campaign_root") != str(campaign)
        or output.parent != campaign / "cycles" / active["cycle_id"]
        or output.parent != Path(active["output"])
        or output.resolve() != output
    ):
        raise ValueError("Diagnostic output outside managed cycle")
    if (
        output.exists()
        or output.is_symlink()
        or output.with_suffix(".sha256").exists()
        or output.with_suffix(".sha256").is_symlink()
    ):
        raise ValueError("Immutable diagnostic output already present")
    sources = dict(protocol["source_files"])
    sources[str(protocol_path)] = sha(protocol_path)
    config = protocol["config"]
    sources[config["path"]] = config["sha256"]
    if str(Path(__file__).resolve()) not in sources:
        raise ValueError("Diagnostic driver is not frozen")
    pins = {item["path"]: item["sha256"] for item in manifest["inputs"]}
    for raw, expected in sources.items():
        path = Path(raw)
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or sha(path) != expected
            or pins.get(raw) != expected
        ):
            raise ValueError("Frozen diagnostic source/config differs")
    library = Path(__file__).resolve().parents[1]
    native_sources = source_hashes(library)
    if any(sources.get(str(library / raw)) != value for raw, value in native_sources.items()):
        raise ValueError("Entire native source set must be frozen")
    attest_loaded_sources(library / "src", native_sources, dict(sys.modules))
    assert_time(protocol)
    return protocol, active


def publish(path: Path, result: dict) -> None:
    seal = path.with_suffix(".sha256")
    if path.exists() or path.is_symlink() or seal.exists() or seal.is_symlink():
        raise ValueError("Immutable diagnostic output already present")
    for target, body in (
        (path, json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"),
        (seal, digest(result) + "\n"),
    ):
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cost", type=int, required=True)
    args = parser.parse_args()
    if not args.protocol.is_absolute() or not args.output.is_absolute():
        raise ValueError("Absolute diagnostic paths required")
    protocol, active = verify_context(args.protocol, args.output, args.cost)
    result = diagnose(Path(protocol["config"]["path"]), args.cost)
    result.update(
        protocol_sha256=digest(protocol),
        run_id=active["run_id"],
        cycle_id=active["cycle_id"],
        completed_at=dt.datetime.now(dt.UTC).isoformat(),
    )
    after_protocol, after_active = verify_context(args.protocol, args.output, args.cost)
    if protocol != after_protocol or active != after_active:
        raise ValueError("Diagnostic managed identity changed")
    publish(args.output, result)
    print(
        f"Synthetic native plan diagnostic cost={args.cost}: unsupported export; no authority",
        flush=True,
    )


if __name__ == "__main__":
    main()
