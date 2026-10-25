"""Controlled 2x2x2 group-selection study on the same observed execution period."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from autoresearch.improvements.study import digest, run_arm, source_hashes, write

LABELS = {
    "baseline": "v13 · core ETFs",
    "B1": "Daily selection · core ETFs",
    "groups": "Groups · core ETFs",
    "core_daily": "Groups + daily · core ETFs",
    "core_quality": "Groups + quality · core ETFs",
    "core_both": "Groups + daily + quality · core ETFs",
    "wide_baseline": "v13-style · broader ETFs",
    "wide_daily_baseline": "Daily selection · broader ETFs",
    "wide_groups": "Groups · broader ETFs",
    "wide_daily": "Groups + daily · broader ETFs",
    "wide_quality": "Groups + quality · broader ETFs",
    "wide_both": "Groups + daily + quality · broader ETFs",
}


def recipes(base: StrategyConfig, design: dict[str, Any]) -> dict[str, Any]:
    output = {}
    clusters = design["broad_clusters"]
    defensive = set(design["defensive_equity"])
    growth = [
        s
        for c in clusters
        if c["name"] in {"us_equity", "international"}
        for s in c["symbols"]
        if s not in defensive
    ]
    real = [
        s
        for c in clusters
        if c["name"] in {"metals", "commodities", "real_estate"}
        for s in c["symbols"]
    ]
    defense = [
        s for c in clusters if c["name"] in {"rates", "us_dollar"} for s in c["symbols"]
    ] + design["defensive_equity"]
    for arm in LABELS:
        value = base.model_dump(mode="json")
        wide = arm.startswith("wide_")
        if wide:
            value["universe"]["clusters"] = clusters
        if arm != "baseline":
            value["name"] = "amat-group-study-" + arm
        if arm in {
            "B1",
            "core_daily",
            "core_both",
            "wide_daily_baseline",
            "wide_daily",
            "wide_both",
        }:
            value["cross_sectional"]["selection_interval_sessions"] = 1
        if arm not in {"baseline", "B1", "wide_baseline", "wide_daily_baseline"}:
            value["adaptive_rotation"] = {"enabled": True, "group_selection": True}
            if wide:
                value["adaptive_rotation"]["groups"] = [
                    {"name": name, "symbols": symbols}
                    for name, symbols in [
                        ("growth", growth),
                        ("real_assets", real),
                        ("defensive", defense),
                    ]
                ]
        if arm in {"core_quality", "core_both", "wide_quality", "wide_both"}:
            value["structure"]["enabled"] = True
        output[arm] = StrategyConfig.model_validate(value).model_dump(mode="json")
    return output


def register(root: Path, snapshot: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("Group study already exists")
    definition = root / "configs/experiments/group-selection-expansion.json"
    design = json.loads(definition.read_text())
    base = load_strategy_config(root / "configs/strategy/amat_v13_return_target.toml")
    configs = recipes(base.strategy, design)
    manifest = load_manifest(snapshot)
    broad = StrategyConfig.model_validate(configs["wide_groups"])
    symbols = list(map(str, broad.universe.symbols))
    frame = pl.read_parquet(snapshot / "daily.parquet")
    calendar = frame.filter(pl.col("symbol") == "SPY")["session_date"].unique().sort().to_list()
    audit = []
    for symbol in symbols:
        observed = frame.filter(pl.col("symbol") == symbol)["session_date"].sort().to_list()
        if observed != calendar:
            raise ValueError(f"{symbol} does not meet frozen complete-history rule")
        audit.append({"symbol": symbol, "daily_sessions": len(observed)})
    panel, _ = load_panel(snapshot, symbols, with_context=True)
    load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
    if list(panel.sessions) != calendar:
        raise ValueError("Broader panel changed the reference calendar")
    start = dt.date.fromisoformat(design["execution_start"])
    windows = pl.read_parquet(snapshot / "window.parquet").filter(
        pl.col("symbol").is_in(symbols) & (pl.col("session_date") >= start)
    )
    expected = len([d for d in calendar if d >= start]) * len(symbols)
    protocol = {
        "schema": "group-selection-study/1",
        "root": str(root.resolve()),
        "snapshot": str(snapshot.resolve()),
        "snapshot_id": manifest.snapshot_id,
        "partition_sha256": manifest.partition_sha256,
        "source_sha256": source_hashes(root),
        "design_sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
        "baseline_config_sha256": base.content_sha256,
        "configs": configs,
        "labels": LABELS,
        "costs_bps": [2, 6, 10],
        "primary_cost_bps": 2,
        "execution_start": design["execution_start"],
        "first_session": str(manifest.first_session),
        "end": str(manifest.last_session),
        "research_only": True,
        "promotion_authority": False,
        "initial_cash": 100000,
        "historical_scope": design["historical_scope"],
        "execution_scope": (
            "All accounts start flat in January 2024; native next-session "
            "observed windows, costs, dividends and 1% participation cap. "
            "No broker-event parity claim."
        ),
        "weekly_scope": (
            "Prior completed weeks only; daily refresh does not use partial-week rankings."
        ),
        "comparison_scope": (
            "Earlier daily history supplies signals only. "
            "Do not compare directly with accounts invested since 2016."
        ),
        "universe_audit": audit,
        "broad_symbols": symbols,
        "core_symbols": list(map(str, base.strategy.universe.symbols)),
        "window_audit": {
            "expected_cells": expected,
            "observed_cells": windows.height,
            "missing_cells": expected - windows.height,
        },
        "factors": design["factors"],
        "controls": design["controls"],
    }
    output.mkdir(parents=True)
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def report(output: Path) -> Path:
    records = [run_arm(output, arm, cost) for arm in LABELS for cost in [2, 6, 10]]
    text = [
        "# Group selection: universe, daily refresh and trend quality",
        "",
        "Common flat-account start: January 2, 2024. "
        "All historical periods are previously examined.",
        "",
        "| Approach | Cost bps | 2024 | 2025 | 2026 YTD | YTD drawdown | YTD fills | YTD missed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in records:
        a, b, m = (r["metrics"][p] for p in ["2024", "2025", "2026_ytd"])
        text.append(
            f"| {LABELS[r['arm']]} | {r['cost_bps']} | {a['net_return']:.2%} | "
            f"{b['net_return']:.2%} | {m['net_return']:.2%} | {m['max_drawdown']:.2%} | "
            f"{m['fills']} | {m['missed']} |"
        )
    path = output / "report.md"
    path.write_text("\n".join(text) + "\n")
    return path
