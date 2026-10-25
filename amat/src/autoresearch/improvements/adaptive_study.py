"""Registered ablations of native adaptive rotation; no model or trading calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_manifest
from autoresearch.improvements.study import digest, run_arm, source_hashes, write

LABELS = {
    "baseline": "v13 reference",
    "B1": "Daily selection",
    "groups": "Group selection only",
    "residual": "Group-adjusted ranking only",
    "regime": "Slow/fast price-risk only",
    "hysteresis": "Turnover buffer only",
    "combined": "Adaptive rotation combined",
    "structure": "Existing trend-quality filter",
    "allocation": "Existing volatility-aware allocation",
    "daily_buffer": "Daily selection + turnover buffer",
}


def recipes(base: StrategyConfig) -> dict[str, Any]:
    values = {}
    flags = {
        "groups": "group_selection",
        "residual": "residual_ranking",
        "regime": "regime_overlay",
        "hysteresis": "hysteresis",
    }
    for arm in LABELS:
        config = base.model_dump(mode="json")
        if arm != "baseline":
            config["name"] = f"amat-adaptive-study-{arm}"
        if arm in {"B1", "daily_buffer"}:
            config["cross_sectional"]["selection_interval_sessions"] = 1
        if arm in flags:
            config["adaptive_rotation"] = {"enabled": True, flags[arm]: True}
        if arm == "daily_buffer":
            config["adaptive_rotation"] = {"enabled": True, "hysteresis": True}
        if arm == "combined":
            config["adaptive_rotation"] = {"enabled": True, **dict.fromkeys(flags.values(), True)}
        if arm == "structure":
            config["structure"]["enabled"] = True
        if arm == "allocation":
            config["allocation"]["volatility_power"] = 1.0
        values[arm] = StrategyConfig.model_validate(config).model_dump(mode="json")
    return values


def register(root: Path, snapshot: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("adaptive study already exists")
    base = load_strategy_config(root / "configs/strategy/amat_v13_return_target.toml")
    design = root / "configs/experiments/adaptive-rotation-next.json"
    manifest = load_manifest(snapshot)
    protocol = {
        "schema": "adaptive-rotation-study/1",
        "root": str(root.resolve()),
        "snapshot": str(snapshot.resolve()),
        "partition_sha256": manifest.partition_sha256,
        "source_sha256": source_hashes(root),
        "baseline_config_sha256": base.content_sha256,
        "design_sha256": hashlib.sha256(design.read_bytes()).hexdigest(),
        "configs": recipes(base.strategy),
        "labels": LABELS,
        "costs_bps": [2, 6, 10],
        "end": str(manifest.last_session),
        "full_history_start": str(manifest.first_session),
        "primary_cost_bps": 2,
        "research_only": True,
        "promotion_authority": False,
        "selection": "All fixed arms reported; no search or automatic promotion",
        "historical_scope": "Previously examined history; no fresh holdout or prospective claim",
        "weekly_scope": (
            "Prior completed week becomes available at first observed session of following week"
        ),
        "regime_scope": "QQQ price-only slow trend plus 3-session shock. No VIX proxy claim.",
        "execution_scope": (
            "Native next-session opening-window fills, corporate actions, "
            "1% participation cap; not broker-event parity"
        ),
        "parents": {
            arm: ("B1" if arm == "daily_buffer" else "baseline")
            for arm in LABELS
            if arm != "baseline"
        },
        "deferred": [
            "Broader tradable ETF universe",
            "Dated cash carry; no verified cash-yield series",
        ],
    }
    output.mkdir(parents=True)
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def report(output: Path) -> Path:
    protocol = json.loads((output / "protocol.json").read_text())
    records = [run_arm(output, arm, cost) for arm in LABELS for cost in protocol["costs_bps"]]
    text = [
        "# Adaptive rotation ablation study",
        "",
        protocol["historical_scope"],
        "",
        "| Approach | Cost bps | YTD return | YTD drawdown | Fills |",
        "|---|---:|---:|---:|---:|",
    ]
    for record in records:
        m = record["metrics"]["2026_ytd"]
        text.append(
            f"| {LABELS[record['arm']]} | {record['cost_bps']} | "
            f"{m['net_return']:.2%} | {m['max_drawdown']:.2%} | {m['fills']} |"
        )
    path = output / "report.md"
    path.write_text("\n".join(text) + "\n")
    return path
