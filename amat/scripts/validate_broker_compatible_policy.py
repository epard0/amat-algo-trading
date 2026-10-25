"""Frozen causal DAY-policy engineering cases versus matched native controls.

Synthetic order observations are bookkeeping fixtures, never market evidence.
No original opening-VWAP policy/QQQ comparison or strategy promotion is made.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from amat.backtest.accounting import SessionAccountant
from amat.config.models import CostConfig, ExecutionConfig
from amat.domain.bars import ExecutionWindowBar
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.orders import SimulatedOrder, TradeReason
from amat.domain.positions import Position
from amat.domain.types import Side
from amat.execution import SimulatedExecutionModel
from autoresearch.improvements.agent_native_adapter import canonical_sha, compile_adapter
from autoresearch.improvements.agent_portfolio_arm import AuthorizedIntent, hold_contract
from autoresearch.improvements.broker_compatible_policy import compile_broker_policy

from run_native_agent_adapter_study import DECISION, END, START, fixture
from validate_agent_native_plan import digest, read_sealed, seal, verify_sources

ROOT = Path(__file__).resolve().parents[2]
CASES = (
    "buy_full",
    "cap_partial_late",
    "stop_untriggered",
    "stop_triggered",
    "stop_gap",
    "take_profit",
    "expired",
    "stale_quote",
    "future_prefix",
    "zero_prefix",
    "pending_hold",
    "unknown_ack",
    "fractional_exit",
    "partial_trim",
    "bad_fill",
    "missing_actions",
)
NOW = START + dt.timedelta(seconds=61)
D = Decimal


def scenario(name: str, cost: int) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
    if name not in CASES or cost not in (2, 6, 10):
        raise ValueError("Fixed cases/costs only")
    ctx, _, _, _ = fixture(cost, "resize")
    ctx = replace(ctx, account_id=f"SYNTHETIC-CAUSAL-{name}-{cost}")
    if name in {"fractional_exit", "partial_trim"}:
        ctx = replace(ctx, positions=(("QQQ", D("10.25")),), confirmed_cash=D("8975"))
    if name == "expired":
        ctx = replace(ctx, expires_at=START + dt.timedelta(seconds=30))
    if name == "pending_hold":
        ctx = replace(
            ctx,
            authorized_intents=(
                AuthorizedIntent(
                    "original-partial",
                    "SPY",
                    "buy",
                    D(7),
                    D(3),
                    D(4),
                    D(400),
                    START,
                    ctx.account_id,
                    ctx.policy.identity,
                ),
            ),
        )
    p = hold_contract(
        ctx, model_id="synthetic/no-inference:free", reason="Fixed causal policy validation"
    )
    action = {
        "kind": "resize",
        "reason": "Frozen synthetic case",
        "symbol": "QQQ",
        "side": "buy",
        "quantity": "1",
        "limit_price": "101",
        "evidence_id": "synthetic-QQQ",
    }
    bid, ask, volume = "100", "100.5", "1000"
    expected: list[dict[str, Any]] = []
    timeline: list[dict[str, str]] = []
    submitted = "1"
    if name == "cap_partial_late":
        action["quantity"], volume, submitted = "20", "550", "5"
    if name in {"stop_untriggered", "stop_triggered", "stop_gap", "take_profit"}:
        action.update(
            kind="stop_loss", side="sell", quantity="10", limit_price="89", trigger_price="90"
        )
        bid = {
            "stop_untriggered": "95",
            "stop_triggered": "90",
            "stop_gap": "85",
            "take_profit": "110",
        }[name]
        ask = str(D(bid) + D(".5"))
        submitted = "10"
        if name == "take_profit":
            action.update(kind="take_profit", limit_price="109", trigger_price="110")
    if name in {"fractional_exit", "partial_trim"}:
        action.update(
            kind="close" if name == "fractional_exit" else "resize",
            side="sell",
            quantity="10.25" if name == "fractional_exit" else "2.8",
            limit_price="99",
        )
        submitted = "10.25" if name == "fractional_exit" else "2"
    if name == "zero_prefix":
        volume = "0"
    if name != "pending_hold":
        p["actions"] = [action]
    plan = compile_broker_policy(compile_adapter(p, ctx))
    quote_time = START + dt.timedelta(seconds=60)
    quote = {
        "source_id": "SYNTHETIC-SIP",
        "quote_source_id": "SYNTHETIC-SIP",
        "price_basis": "raw",
        "volume_unit": "shares",
        "tradable": True,
        "quote_time": quote_time.isoformat(),
        "quote_available_at": quote_time.isoformat(),
        "bid": bid,
        "ask": ask,
        "prefix_start": START.isoformat(),
        "prefix_end": quote_time.isoformat(),
        "prefix_available_at": quote_time.isoformat(),
        "prefix_volume": volume,
        "prefix_complete": True,
    }
    observation = {
        "account": {
            "account_id": ctx.account_id,
            "confirmed_cash": str(ctx.confirmed_cash),
            "positions": {s: str(q) for s, q in ctx.positions},
            "source_id": "SYNTHETIC-ACCOUNT",
            "observed_at": NOW.isoformat(),
            "trading_allowed": True,
        },
        "calendar": {
            "source_id": "SYNTHETIC-CALENDAR",
            "available_at": DECISION.isoformat(),
            "session_date": str(START.date()),
            "open": START.isoformat(),
            "close": (START + dt.timedelta(hours=6, minutes=30)).isoformat(),
        },
        "corporate_actions": {
            "source_id": "SYNTHETIC-NO-ACTIONS",
            "actions": [],
            "actions_sha256": canonical_sha([]),
            "from_session": str(DECISION.date()),
            "to_session": str(START.date()),
        },
        "symbols": {row.symbol: copy.deepcopy(quote) for row in ctx.evidence},
    }
    if name == "stale_quote":
        observation["symbols"]["QQQ"]["quote_time"] = START.isoformat()
    if name == "future_prefix":
        observation["symbols"]["QQQ"]["prefix_end"] = (
            START + dt.timedelta(seconds=120)
        ).isoformat()
    if name == "missing_actions":
        del observation["corporate_actions"]
    blocked = name in {
        "stop_untriggered",
        "stop_gap",
        "expired",
        "stale_quote",
        "future_prefix",
        "zero_prefix",
        "pending_hold",
        "missing_actions",
    }
    if blocked:
        submitted = "0"
    if name == "cap_partial_late":
        timeline = [
            {
                "kind": "observe",
                "quantity": "2",
                "price": "100.5",
                "status": "partially_filled",
                "at": (START + dt.timedelta(seconds=62)).isoformat(),
            },
            {"kind": "expire", "at": END.isoformat()},
            {
                "kind": "observe",
                "quantity": "3",
                "price": "100.5",
                "status": "pending_cancel",
                "at": (START + dt.timedelta(seconds=301)).isoformat(),
            },
            {
                "kind": "observe",
                "quantity": "3",
                "price": "100.5",
                "status": "canceled",
                "at": (START + dt.timedelta(seconds=303)).isoformat(),
            },
        ]
        expected = [
            {"quantity": "2", "price": "100.5", "seconds": 62},
            {"quantity": "1", "price": "100.5", "seconds": 301},
        ]
    elif name == "unknown_ack":
        timeline = [{"kind": "expire", "at": END.isoformat()}]
    elif not blocked:
        price = (
            bid
            if action["side"] == "sell" and name in {"stop_triggered", "take_profit"}
            else "100.5"
        )
        timeline = [
            {
                "kind": "observe",
                "quantity": submitted,
                "price": "102" if name == "bad_fill" else price,
                "status": "filled",
                "at": (START + dt.timedelta(seconds=62)).isoformat(),
            }
        ]
        if name != "bad_fill":
            expected = [{"quantity": submitted, "price": price, "seconds": 62}]
    return {
        "name": name,
        "cost_bps": str(cost),
        "plan": plan,
        "observation": observation,
        "admit_at": NOW.isoformat(),
        "timeline": timeline,
        "expected_fills": expected,
        "expected_submitted": submitted,
        "expected_finality": name not in {"unknown_ack", "bad_fill"},
        "synthetic": True,
    }


def native_control(case: dict[str, Any]) -> dict[str, Any]:
    """Independent fixed-event native replay under the NEW measurement contract."""
    ctx = case["plan"]["original_plan"]["context"]
    action = case["plan"]["original_plan"]["proposal"]["actions"][0]
    native_fills = []
    old = LedgerSnapshot(
        DECISION.date(),
        D(ctx["confirmed_cash"]),
        {s: Position(s, D(q), D(100), DECISION.date()) for s, q in ctx["positions"] if D(q)},
    )
    for row in case["expected_fills"]:
        event_time = START + dt.timedelta(seconds=row["seconds"])
        end = event_time + dt.timedelta(seconds=1)
        local = (
            event_time.astimezone(dt.timezone(dt.timedelta(hours=-4))).time().replace(tzinfo=None)
        )
        local_end = end.astimezone(dt.timezone(dt.timedelta(hours=-4))).time().replace(tzinfo=None)
        reason = (
            TradeReason.POSITION_TRANSITION
            if action["side"] == "sell"
            and D(action["quantity"]) == D(dict(ctx["positions"])[action["symbol"]])
            else TradeReason.WEIGHT_DELTA
        )
        order = SimulatedOrder(
            "QQQ", DECISION.date(), START.date(), Side(action["side"]), D(row["quantity"]), reason
        )
        model = SimulatedExecutionModel(
            ExecutionConfig(window_start=local, window_end=local_end),
            CostConfig(),
            one_way_bps=int(case["cost_bps"]),
            whole_share_buys=True,
        )
        fills, misses = model.execute(
            [order],
            {
                "QQQ": ExecutionWindowBar(
                    "QQQ",
                    START.date(),
                    event_time,
                    end,
                    D(row["price"]),
                    D(row["quantity"]),
                    "SYNTHETIC-order-observation",
                )
            },
            available_cash=D(ctx["confirmed_cash"]),
            account_equity=D(ctx["equity"]),
        )
        if misses:
            raise ValueError("Declared native confirmed-fill control unexpectedly missed")
        native_fills.extend(fills)
    ledger, accounting = SessionAccountant().advance(
        old,
        START.date(),
        native_fills,
        {r["symbol"]: D(r["mark"]) for r in ctx["evidence"]},
        actions=(),
        cash_rate_annualized=0.0,
    )
    return {
        "fills": json.loads(json.dumps([asdict(f) for f in native_fills], default=str)),
        "cash": str(ledger.cash),
        "positions": {str(s): str(p.quantity) for s, p in ledger.positions.items() if p.quantity},
        "accounting": json.loads(json.dumps(asdict(accounting), default=str)),
        "account_finality_known": case["expected_finality"],
        "measurement_contract": "confirmed order-event prices; no future full-window VWAP/volume admission",
    }


def validate(manifest_path: Path, output: Path) -> dict[str, Any]:  # noqa: PLR0915
    manifest = read_sealed(manifest_path)
    if (
        manifest["cases"] != list(CASES)
        or manifest["costs_bps"] != [2, 6, 10]
        or manifest["output"] != str(output)
    ):
        raise ValueError("Frozen validation contract differs")
    output.mkdir(exist_ok=False)
    verify_sources(manifest)
    inputs = [scenario(name, cost) for cost in manifest["costs_bps"] for name in CASES]
    seal(output / "inputs.json", inputs)
    completed = subprocess.run(  # noqa: S603 -- fixed pinned argv, sanitized environment, no shell
        [
            sys.executable,
            str(ROOT / "executor/scripts/validate_research_day_policy.py"),
            "--output-root",
            str(output / "isolated_research_accounts"),
        ],
        input=json.dumps(inputs),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "executor/src")},
    )
    actual = json.loads(completed.stdout)
    if len(actual) != len(inputs):
        raise ValueError("Incomplete lifecycle observations")
    summaries = []
    for case, result in zip(inputs, actual, strict=True):
        control = native_control(case)
        state = result["state"]
        compare = lambda rows: [
            (r["symbol"], r["side"], D(r["quantity"]), D(r["price"]), D(r["cost_cash"]))
            for r in rows
        ]  # noqa: E731 -- local exact normalized record comparison
        positions = {s: D(q) for s, q in state["positions"].items() if D(q)}
        checks = {
            "native_fills": compare(state["fills"]) == compare(control["fills"]),
            "native_cash": D(state["cash"]) == D(control["cash"]),
            "native_positions": positions == {s: D(q) for s, q in control["positions"].items()},
            "native_modeled_cost": D(state["modeled_fees"]) == D(control["accounting"]["fees"]),
            "original_intents": state["original_authorized_intents"]
            == case["plan"]["original_plan"]["context"]["authorized_intents"],
            "quantity_conserved": all(
                D(r["original_quantity"]) == D(r["cumulative_fills"]) + D(r["remaining_quantity"])
                for r in state["orders"]
            ),
            "original_expiry": all(
                r["expires_at"] == case["plan"]["original_plan"]["context"]["expires_at"]
                for r in state["orders"]
            ),
            "native_admission_quantity": sum(
                (D(r["submitted_quantity"]) for r in state["orders"]), D(0)
            )
            == D(case["expected_submitted"]),
            "legacy_boundary": result["legacy_refused"],
            "restart_no_resend": result["restart_no_resend"],
            "no_network": result["network_calls"] == 0,
            "expected_quarantine": bool(state["quarantine"]) == (case["name"] == "bad_fill"),
        }
        if case["name"] in {"bad_fill", "unknown_ack"}:
            checks["unknown_commitment_retained"] = D(state["pending_cash"]) == D(101) * (
                1 + D(case["cost_bps"]) / D(10000)
            )
        if case["name"] == "pending_hold":
            checks["old_expired_pending_retained"] = state["pending_cash"] == "400"
        if case["name"] == "cap_partial_late":
            checks["cancel_once_late_fill_counted"] = (
                len(result["cancel_requests"]) == 1
                and state["fills"][-1]["late_after_submission_cutoff"]
                and state["orders"][0]["remaining_quantity"] == "17"
                and D(state["pending_cash"]) == 0
            )
        if not all(checks.values()):
            raise ValueError(f"Matched native control differs: {case['name']}: {checks}")
        record = {
            "input": case,
            "actual": result,
            "independent_native_control": control,
            "checks": checks,
            "performance_evidence": False,
        }
        seal(output / f"{case['name']}-{case['cost_bps']}bps.json", record)
        summaries.append(
            {
                "name": case["name"],
                "cost_bps": case["cost_bps"],
                "checks": checks,
                "modeled_fees": state["modeled_fees"],
                "pending_cash": state["pending_cash"],
                "account_finality_known": case["expected_finality"],
            }
        )
    verify_sources(manifest)
    summary = {
        "schema": "research-broker-policy-validation/1",
        "completed_utc": dt.datetime.now(dt.UTC).isoformat(),
        "manifest_sha256": digest(manifest),
        "passed": len(summaries),
        "cases": summaries,
        "network_calls": 0,
        "paper_execution_changes": False,
        "broker_wire_contract": "DAY limit and cancel payloads exercised offline",
        "native_parity": "matched confirmed-event control at 2/6/10bps",
        "real_broker_acceptance_fills": "unavailable",
        "performance_evidence": False,
    }
    seal(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.manifest, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))
