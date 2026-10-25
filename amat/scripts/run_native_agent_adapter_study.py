"""Sealed synthetic adapter/native-accounting parity; never sends or exports orders."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from amat.backtest.accounting import SessionAccountant
from amat.config.models import CostConfig, ExecutionConfig
from amat.domain.bars import ExecutionWindowBar
from amat.domain.fills import Fill, MissedFill
from amat.domain.ledgers import LedgerSnapshot, PeriodAttribution
from amat.domain.orders import SimulatedOrder, TradeReason
from amat.domain.positions import Position
from amat.domain.types import Side
from amat.execution import SimulatedExecutionModel
from autoresearch.improvements.agent_native_adapter import (
    ObservedWindowEvidence,
    canonical_sha,
    compile_adapter,
    execute_adapter,
)
from autoresearch.improvements.agent_portfolio_arm import (
    ArmPolicy,
    AuthorizedIntent,
    MarketEvidence,
    ResearchContext,
    hold_contract,
)
from autoresearch.improvements.incremental_gates import read_sealed
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, source_hashes

CASES = (
    "resize",
    "stop_untriggered",
    "stop_triggered",
    "stop_gap",
    "capped_buy",
    "pending_hold",
    "expiry",
    "take_profit",
)
D = Decimal
DECISION = dt.datetime(2026, 10, 23, 20, tzinfo=dt.UTC)
START = dt.datetime(2026, 10, 26, 13, 30, tzinfo=dt.UTC)
END = START + dt.timedelta(minutes=5)


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def seal(p: Path, value: Any) -> None:
    for target, body in [
        (p, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"),
        (p.with_suffix(".sha256"), digest(value) + "\n"),
    ]:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(p.parent, os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)


def fixture(
    cost: int, case: str
) -> tuple[ResearchContext, dict[str, Any], dict[str, ObservedWindowEvidence], dict[str, Any]]:
    if cost not in (2, 6, 10) or case not in CASES:
        raise ValueError("Fixed cases/costs only")
    ctx = ResearchContext(
        DECISION,
        DECISION,
        START,
        "next_session_open",
        END,
        "SYNTHETIC-ADAPTER-" + case,
        D(10000),
        D(9000),
        D(0),
        (("QQQ", D(10)),),
        tuple(
            MarketEvidence(s, "synthetic-" + s, DECISION, D(100), D(100)) for s in ("QQQ", "SPY")
        ),
        (),
        ArmPolicy(one_way_bps=D(cost)),
    )
    if case == "expiry":
        ctx = replace(ctx, expires_at=END - dt.timedelta(seconds=1))
    if case == "pending_hold":
        ctx = replace(
            ctx,
            authorized_intents=(
                AuthorizedIntent(
                    "synthetic-original",
                    "QQQ",
                    "buy",
                    D(7),
                    D(3),
                    D(4),
                    D(400),
                    END,
                    ctx.account_id,
                    ctx.policy.identity,
                ),
            ),
        )
    proposal = hold_contract(
        ctx,
        model_id="synthetic/no-inference:free",
        reason="Fixed isolated synthetic parity fixture",
    )
    price, low, high, quote, volume = "100.5", "100", "101", "100", "10000"
    kind, side, quantity, limit, trigger = "resize", "buy", "1", "101", None
    if case in ("stop_untriggered", "stop_triggered", "stop_gap"):
        kind, side, quantity, limit, trigger = "stop_loss", "sell", "10", "89", "90"
        if case == "stop_untriggered":
            price, low, high, quote = "95", "88", "101", "95"
        elif case == "stop_triggered":
            price, low, high, quote = "90", "89", "91", "90"
        else:
            price, low, high, quote = "85", "84", "86", "85"
    if case == "capped_buy":
        quantity, price, volume = "20", "100", "550"
    if case == "take_profit":
        kind, side, quantity, limit, trigger = "take_profit", "sell", "10", "109", "110"
        price, low, high, quote = "110", "109", "111", "110"
    if case != "pending_hold":
        action = {
            "kind": kind,
            "side": side,
            "quantity": quantity,
            "limit_price": limit,
            "symbol": "QQQ",
            "evidence_id": "synthetic-QQQ",
            "reason": "Preregistered synthetic native parity",
        }
        if trigger is not None:
            action["trigger_price"] = trigger
        proposal["actions"] = [action]
    bar = ExecutionWindowBar(
        "QQQ", START.date(), START, END, D(price), D(volume), "SYNTHETIC-native-adapter"
    )
    evidence = {
        "QQQ": ObservedWindowEvidence(
            bar, END, D(low), D(high), bar.source, D(quote), START, True, "raw"
        )
    }
    coverage = {
        "from_session": str(DECISION.date()),
        "to_session": str(START.date()),
        "source_id": "SYNTHETIC-empty-actions",
        "actions": [],
        "actions_sha256": canonical_sha([]),
    }
    return ctx, proposal, evidence, coverage


def ledger_json(ledger: LedgerSnapshot) -> dict[str, Any]:
    return {
        "session_date": str(ledger.session_date),
        "cash": str(ledger.cash),
        "equity": str(ledger.equity),
        "positions": {
            str(s): {"quantity": str(p.quantity), "mark": str(p.mark)}
            for s, p in ledger.positions.items()
        },
    }


def native_control(
    ctx: ResearchContext, evidence: dict[str, ObservedWindowEvidence], case: str, cost: int
) -> tuple[list[Fill], list[MissedFill], LedgerSnapshot, PeriodAttribution | None]:
    # Independently fixed expected eligibility, not the adapter's filter output.
    quantity = {"resize": "1", "stop_triggered": "10", "capped_buy": "20", "take_profit": "10"}.get(
        case
    )
    side = Side.SELL if case in ("stop_triggered", "take_profit") else Side.BUY
    orders = (
        []
        if quantity is None
        else [
            SimulatedOrder(
                "QQQ",
                DECISION.date(),
                START.date(),
                side,
                D(quantity),
                TradeReason.POSITION_TRANSITION if side == Side.SELL else TradeReason.WEIGHT_DELTA,
            )
        ]
    )
    original = LedgerSnapshot(
        DECISION.date(),
        ctx.confirmed_cash,
        {"QQQ": Position("QQQ", D(10), D(100), DECISION.date())},
    )
    if case == "expiry":
        return [], [], original, None
    cash = (
        ctx.confirmed_cash
        - sum((i.pending_cash for i in ctx.authorized_intents), D(0))
        - ctx.restricted_cash
        - ctx.equity * ctx.policy.cash_reserve_fraction
    )
    model = SimulatedExecutionModel(
        ExecutionConfig(),
        CostConfig(),
        one_way_bps=cost,
        max_entry_window_participation=0.01,
        whole_share_buys=True,
    )
    fills, missed = model.execute(
        orders,
        {s: r.bar for s, r in evidence.items()},
        available_cash=max(D(0), cash),
        account_equity=ctx.equity,
    )
    ledger, attr = SessionAccountant().advance(
        original,
        START.date(),
        fills,
        {"QQQ": evidence["QQQ"].bar.vwap},
        actions=(),
        cash_rate_annualized=0.0,
    )
    return fills, missed, ledger, attr


def observe(cost: int, case: str) -> dict[str, Any]:
    ctx, p, e, c = fixture(cost, case)
    original = json.loads(json.dumps(asdict(ctx), default=str))
    plan = compile_adapter(p, ctx)
    result = execute_adapter(plan, e, action_coverage=c)
    fills, missed, ledger, attr = native_control(ctx, e, case, cost)
    expected_fills = json.loads(json.dumps([asdict(f) for f in fills], default=str))
    expected_missed = json.loads(json.dumps([asdict(f) for f in missed], default=str))
    expected_attr = json.loads(json.dumps(asdict(attr), default=str)) if attr else None
    checks = {
        "native_fills_exact": result["fills"] == expected_fills,
        "native_missed_exact": result["missed"] == expected_missed,
        "native_ledger_exact": result["ledger"] == ledger_json(ledger),
        "native_attribution_exact": result["accounting"] == expected_attr,
        "context_unchanged": original == json.loads(json.dumps(asdict(ctx), default=str)),
        "original_pending_intents_preserved": result["original_authorized_intents"]
        == original["authorized_intents"],
        "original_expiry_preserved": all(
            x["expires_at"] == p["expires_at"] for x in result["actions"]
        ),
        "original_quantities_conserved": all(
            D(x["original_quantity"]) == D(x["cumulative_fills"]) + D(x["remaining_quantity"])
            for x in result["actions"]
        ),
    }
    try:
        execute_adapter(plan, e, previous_receipt=result, action_coverage=c)
    except ValueError:
        checks["prior_receipt_duplicate_refused"] = True
    else:
        checks["prior_receipt_duplicate_refused"] = False
    if not all(checks.values()):
        raise ValueError(f"Fixed native parity failed: {case}: {checks}")
    return {
        "case": case,
        "cost_bps": cost,
        "receipt": result,
        "checks": checks,
        "independent_native_control": {
            "fills": expected_fills,
            "missed": expected_missed,
            "ledger": ledger_json(ledger),
            "accounting": expected_attr,
        },
        "fixture_sha256": canonical_sha(
            {
                "context": original,
                "proposal": p,
                "evidence": {s: asdict(r) for s, r in e.items()},
                "coverage": c,
            }
        ),
        "synthetic": True,
        "performance_evidence": False,
        "execution_authority": "none",
    }


def verify(protocol_path: Path, output: Path, cost: int) -> tuple[dict[str, Any], dict[str, Any]]:
    p = read_sealed(protocol_path)
    if (
        p.get("schema") != "native-agent-adapter-study/1"
        or p["cases"] != list(CASES)
        or p["costs_bps"] != [2, 6, 10]
        or cost not in p["costs_bps"]
    ):
        raise ValueError("Frozen shape differs")
    now = dt.datetime.now(dt.UTC)
    if now >= min(
        dt.datetime.fromisoformat(p[k].replace("Z", "+00:00"))
        for k in ("hard_stop_utc", "campaign_deadline_utc")
    ):
        raise ValueError("Deadline expired")
    campaign = Path(p["campaign_root"])
    state = json.loads((campaign / "state.json").read_text())
    active = state.get("active_run")
    if (
        not active
        or active["run_id"] != os.environ.get("AMAT_RESEARCH_RUN_ID")
        or not os.environ.get("AMAT_RESEARCH_RUN_ID")
    ):
        raise ValueError("Managed identity required")
    manifest = read_sealed(Path(active["manifest_path"]))
    cp = read_sealed(campaign / "protocol.json")
    if (
        digest(manifest) != active["manifest_sha256"]
        or digest(cp) != active["protocol_sha256"]
        or p["campaign_protocol_sha256"] != digest(cp)
        or cp["deadline_utc"] != p["campaign_deadline_utc"]
    ):
        raise ValueError("Campaign binding differs")
    if (
        manifest.get("campaign_root") != str(campaign)
        or output.parent != Path(active["output"])
        or output.parent != campaign / "cycles" / active["cycle_id"]
        or output.resolve() != output
    ):
        raise ValueError("Managed output required")
    required = {**p["source_files"], str(protocol_path): sha(protocol_path)}
    pins = {x["path"]: x["sha256"] for x in manifest["inputs"]}
    for raw, value in required.items():
        f = Path(raw)
        if (
            not f.is_absolute()
            or f.is_symlink()
            or not f.is_file()
            or sha(f) != value
            or pins.get(raw) != value
        ):
            raise ValueError("Frozen source differs")
    root = Path(__file__).resolve().parents[1]
    native = source_hashes(root)
    if required.get(str(Path(__file__).resolve())) != sha(Path(__file__)) or any(
        required.get(str(root / raw)) != value for raw, value in native.items()
    ):
        raise ValueError("Entire source set required")
    attest_loaded_sources(root / "src", native, dict(sys.modules))
    return p, active


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--cost", type=int, required=True)
    args = ap.parse_args()
    if not args.protocol.is_absolute() or not args.output.is_absolute():
        raise ValueError("Absolute paths required")
    p, active = verify(args.protocol, args.output, args.cost)
    records = []
    for case in CASES:
        verify(args.protocol, args.output, args.cost)
        reservation = args.output.parent / f"{args.cost}bps-{case}.attempt.json"
        seal(
            reservation,
            {
                "protocol_sha256": digest(p),
                "run_id": active["run_id"],
                "cost": args.cost,
                "case": case,
                "reserved_utc": dt.datetime.now(dt.UTC).isoformat(),
                "execution_authority": "none",
            },
        )
        verify(args.protocol, args.output, args.cost)
        records.append(observe(args.cost, case))
    after, again = verify(args.protocol, args.output, args.cost)
    if p != after or active != again:
        raise ValueError("Run identity changed")
    result = {
        "schema": "native-agent-adapter-validation/1",
        "protocol_sha256": digest(p),
        "run_id": active["run_id"],
        "cost_bps": args.cost,
        "cases": records,
        "completed_utc": dt.datetime.now(dt.UTC).isoformat(),
        "synthetic": True,
        "performance_evidence": False,
        "execution_authority": "none",
        "promotion_authority": False,
        "real_market_or_broker_parity_verified": False,
    }
    seal(args.output, result)
    print(
        f"Synthetic native adapter parity: {args.cost}bps, "
        f"{len(records)} cases; no execution authority",
        flush=True,
    )


if __name__ == "__main__":
    main()
