"""Frozen fractional investor QQQ total-return reference, never a strategy candidate.

Uses observed initial raw opening VWAP and initial transaction costs. Subsequent
growth is the provider's adjusted-close ratio, an idealized dividend-reinvestment
approximation. No cash distribution is added a second time.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.data.snapshot import load_manifest, load_panel
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.nested_inventory import file_hash
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, write
from autoresearch.improvements.validation_study import (
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

START = dt.date(2017, 1, 3)
END = dt.date(2026, 10, 12)
INITIAL = Decimal("100000")
ARM = "qqq_total_return_reference"
COSTS = (2, 6, 10)


def investor_rows(panel: MarketPanel, cost: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """A total-return-index holding with explicit, observed entry valuation."""
    if cost not in COSTS:
        raise ValueError("Unregistered reference cost")
    column = tuple(map(str, panel.symbols)).index("QQQ")
    first = panel.sessions.index(START)
    end = panel.sessions.index(END)
    vwap = panel.window_vwaps[first, column]
    volume = panel.window_volumes[first, column]
    raw = panel.raw_closes[first, column]
    adjusted = panel.adjusted_closes[first : end + 1, column]
    if (
        not all(np.isfinite(value) and value > 0 for value in (vwap, volume, raw))
        or not np.isfinite(adjusted).all()
        or np.any(adjusted <= 0)
    ):
        raise ValueError("Observed initial VWAP/volume and complete positive prices required")
    entry_price = Decimal(str(float(vwap)))
    first_close = Decimal(str(float(raw)))
    cost_fraction = Decimal(cost) / Decimal(10000)
    units = INITIAL / (entry_price * (Decimal(1) + cost_fraction))
    notional = units * entry_price
    fee = notional * cost_fraction
    initial_close_nav = units * first_close
    initial_adjusted = Decimal(str(float(adjusted[0])))
    rows = []
    for offset, index in enumerate(range(first, end + 1)):
        ratio = Decimal(str(float(panel.adjusted_closes[index, column]))) / initial_adjusted
        equity = initial_close_nav * ratio
        rows.append(
            {
                "session": str(panel.sessions[index]),
                "equity": float(equity),
                "equity_decimal": str(equity),
                "fees": float(fee) if offset == 0 else 0.0,
                "exposure": 1.0,
                "fills": 1 if offset == 0 else 0,
                "missed": 0,
                "traded_notional": float(notional) if offset == 0 else 0.0,
                "turnover": float(notional / INITIAL) if offset == 0 else 0.0,
            }
        )
    return rows, {
        "initial_raw_vwap": str(entry_price),
        "initial_raw_close": str(first_close),
        "initial_adjusted_close": str(initial_adjusted),
        "initial_fractional_units": str(units),
        "initial_notional": str(notional),
        "initial_fee": str(fee),
        "initial_cash_residual": str(INITIAL - notional - fee),
    }


def verified_foundation(path: Path) -> dict[str, Any]:
    value = sealed(path)
    if (
        value.get("schema") != "qqq-native-foundation/1"
        or value.get("execution_start") != START.isoformat()
        or value.get("execution_end") != END.isoformat()
        or value.get("initial_cash") != str(INITIAL)
        or value.get("costs_bps") != list(COSTS)
    ):
        raise ValueError("Incompatible foundation protocol")
    attest_loaded_sources(Path(value["root"]) / "src", value["source_sha256"], dict(sys.modules))
    if load_manifest(Path(value["snapshot"])).partition_sha256 != value["partition_sha256"]:
        raise ValueError("Foundation snapshot differs")
    return value


def register(foundation_path: Path, study: Path) -> dict[str, Any]:
    if study.exists():
        raise ValueError("Use a new immutable study directory")
    foundation = verified_foundation(foundation_path)
    protocol = {
        "schema": "qqq-total-return-reference/1",
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "study_id": study.name,
        "foundation_path": str(foundation_path.resolve()),
        "foundation_sha256": digest(foundation),
        "driver_sha256": file_hash(Path(__file__)),
        "root": foundation["root"],
        "snapshot": foundation["snapshot"],
        "source_sha256": foundation["source_sha256"],
        "partition_sha256": foundation["partition_sha256"],
        "session_calendar_sha256": foundation["session_calendar_sha256"],
        "baseline_config_sha256": foundation["baseline_config_sha256"],
        "symbols": foundation["config"]["universe"]["clusters"],
        "costs_bps": list(COSTS),
        "execution_start": START.isoformat(),
        "execution_end": END.isoformat(),
        "initial_cash": str(INITIAL),
        "arm": ARM,
        "formula": (
            "100000/(raw_Jan3_opening_VWAP*(1+bps/10000))*raw_Jan3_close"
            "*(adjusted_close_t/adjusted_close_Jan3)"
        ),
        "initial_execution": "Observed 09:30-09:35 New York raw VWAP; positive observed volume",
        "distributions": "Provider adjusted-close ratio implies idealized fractional reinvestment",
        "actions_cash_credits": False,
        "fractional_units": True,
        "entry_volume_cap": None,
        "rebalance_band": None,
        "terminal_liquidation": False,
        "cash_yield": 0,
        "promotion_authority": False,
        "comparable_native_candidate": False,
        "limitations": [
            "Provider adjusted-price total-return approximation, not native cash/action accounting",
            "Idealized fractional reinvestment; actual payable dates/reinvestment prices absent",
            "No AMAT entry-volume cap, allocation caps, rebalance band or tactical exits",
            "Initial fee only; no assumed terminal sale or liquidation fee",
            "History has prior research exposure; not an untouched holdout",
        ],
    }
    study.mkdir(parents=True)
    write(study / "protocol.json", protocol)
    (study / "protocol.sha256").write_text(digest(protocol))
    return protocol


def measure(study: Path, cost: int, destination: Path, run_id: str) -> dict[str, Any]:
    protocol = sealed(study / "protocol.json")
    if cost not in protocol["costs_bps"] or not run_id:
        raise ValueError("Unregistered cost or missing run identity")
    if destination.exists() or destination.with_suffix(".sha256").exists():
        raise ValueError("Immutable result already exists")
    if file_hash(Path(__file__)) != protocol["driver_sha256"]:
        raise ValueError("Reference driver changed after registration")
    foundation = verified_foundation(Path(protocol["foundation_path"]))
    if digest(foundation) != protocol["foundation_sha256"]:
        raise ValueError("Foundation identity differs")
    symbols = [s for cluster in protocol["symbols"] for s in cluster["symbols"]]
    panel, _ = load_panel(Path(protocol["snapshot"]), symbols, end=END)
    rows, entry = investor_rows(panel, cost)
    if digest([r["session"] for r in rows]) != protocol["session_calendar_sha256"]:
        raise ValueError("Reference calendar differs from native foundation")
    record = {
        "schema": "qqq-total-return-reference-result/1",
        "finalized": True,
        "arm": ARM,
        "cost_bps": cost,
        "run_id": run_id,
        "study_id": protocol["study_id"],
        "protocol_sha256": digest(protocol),
        "foundation_sha256": protocol["foundation_sha256"],
        "sessions_sha256": digest(rows),
        "sessions": rows,
        "entry": entry,
        "metrics": sliced_metrics(rows),
        "monthly": monthly_metrics(rows),
        "recovery_episodes": recovery_episodes(rows),
        "limitations": protocol["limitations"],
        "comparable_native_candidate": False,
        "promotion_authority": False,
    }
    verified_foundation(Path(protocol["foundation_path"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    write(destination, record)
    destination.with_suffix(".sha256").write_text(digest(record))
    print(f"{ARM} {cost} bps: {record['metrics']['full']['net_return']:.4%}", flush=True)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--foundation-protocol", type=Path, required=True)
    reg.add_argument("--study", type=Path, required=True)
    run = sub.add_parser("measure")
    run.add_argument("--study", type=Path, required=True)
    run.add_argument("--cost", type=int, choices=COSTS, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "register":
        register(args.foundation_protocol, args.study)
    else:
        measure(args.study, args.cost, args.output, args.run_id)


if __name__ == "__main__":
    main()
