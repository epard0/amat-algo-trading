"""Source-bound fixed-recipe studies from the actual promoted v13 baseline."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.execution.budget import CashBudgetPolicy

ARMS = ("baseline", "A1", "A2", "A3", "B1", "B2", "C1", "C2", "D1")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def source_hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((root / "src").rglob("*.py"))
    }


def recipes(base: StrategyConfig) -> dict[str, dict[str, Any]]:
    output = {}
    for arm in ARMS:
        config = base.model_dump(mode="json")
        if arm == "A1":
            for cluster in config["universe"]["clusters"]:
                if cluster["name"] == "metals":
                    cluster["gross_cap"] = 0.30
        elif arm == "A2":
            config["constraints"]["max_symbol_weight"] = 0.25
        elif arm == "A3":
            config["allocation"]["volatility_power"] = 1.0
        elif arm == "B1":
            config["cross_sectional"]["selection_interval_sessions"] = 1
        elif arm == "B2":
            config["rebalance"]["min_weight_delta"] = 0.01
        elif arm in {"C1", "C2"}:
            config["trend"]["horizons_sessions"] = [21, 63, 126] if arm == "C1" else [10, 21, 63]
        if arm != "baseline":
            config["name"] = f"amat-improvements-{arm}"
        output[arm] = StrategyConfig.model_validate(config).model_dump(mode="json")
    return output


def register(root: Path, snapshot: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("study output already exists")
    base = load_strategy_config(root / "configs/strategy/amat_v13_return_target.toml")
    manifest = load_manifest(snapshot)
    body = {
        "schema": "strategy-improvement-study/1",
        "baseline_config_sha256": base.content_sha256,
        "root": str(root.resolve()),
        "snapshot": str(snapshot.resolve()),
        "partition_sha256": manifest.partition_sha256,
        "source_sha256": source_hashes(root),
        "configs": recipes(base.strategy),
        "costs_bps": [2, 6, 10],
        "primary_cost_bps": 2,
        "full_history_start": str(manifest.first_session),
        "end": str(manifest.last_session),
        "research_only": True,
        "promotion_authority": False,
        "selection": "descriptive fixed-arm comparison; no automatic promotion",
        "exposure_to_prior_research": "All historical dates have prior research exposure.",
        "D1_policy": {
            "reserve_fraction": "0.01",
            "price_buffer_fraction": "0.002",
            "execution": "confirmed simulated sales; aggregate-window budget proxy",
        },
    }
    output.mkdir(parents=True)
    write(output / "protocol.json", body)
    (output / "protocol.sha256").write_text(digest(body))
    return body


def verify(output: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((output / "protocol.json").read_text())
    if digest(body) != (output / "protocol.sha256").read_text():
        raise ValueError("protocol identity changed")
    if source_hashes(Path(body["root"])) != body["source_sha256"]:
        raise ValueError("source changed after registration")
    if load_manifest(Path(body["snapshot"])).partition_sha256 != body["partition_sha256"]:
        raise ValueError("snapshot changed")
    return body


def metrics(rows: list[dict[str, Any]], opening: float) -> dict[str, Any]:
    if not rows:
        return {"observations": 0}
    equity = np.array([opening, *[r["equity"] for r in rows]])
    returns = equity[1:] / equity[:-1] - 1
    volatility = float(returns.std(ddof=1) * np.sqrt(252)) if len(rows) > 1 else 0
    return {
        "observations": len(rows),
        "net_return": float(equity[-1] / opening - 1),
        "max_drawdown": float((equity / np.maximum.accumulate(equity) - 1).min()),
        "annualized_volatility": volatility,
        "sharpe_zero_rf": float(returns.mean() * 252 / volatility) if volatility else None,
        "fills": sum(r["fills"] for r in rows),
        "costs": sum(r["fees"] for r in rows),
        "missed": sum(r["missed"] for r in rows),
        "average_exposure": float(np.mean([r["exposure"] for r in rows])),
    }


def run_arm(output: Path, arm: str, cost: int) -> dict[str, Any]:
    protocol = verify(output)
    if arm not in protocol["configs"] or cost not in protocol["costs_bps"]:
        raise ValueError("unregistered arm or cost")
    destination = output / f"{arm}-{cost}bps.json"
    if destination.exists():
        saved: dict[str, Any] = json.loads(destination.read_text())
        seal = destination.with_suffix(".sha256")
        if not seal.exists() or digest(saved) != seal.read_text():
            raise ValueError("retained run changed")
        return saved
    strategy = StrategyConfig.model_validate(protocol["configs"][arm])
    snapshot = Path(protocol["snapshot"])
    symbols = list(map(str, strategy.universe.symbols))
    panel, _ = load_panel(snapshot, symbols, with_context=True)
    actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
    result = run_backtest(
        strategy,
        panel,
        strategy.to_universe(),
        actions_by_session=actions,
        one_way_bps=cost,
        execution_start=dt.date.fromisoformat(protocol["execution_start"])
        if protocol.get("execution_start")
        else None,
        max_entry_window_participation=0.01,
        execution_cash_policy=CashBudgetPolicy() if arm == "D1" else None,
    )
    rows = [
        {
            "session": str(r.session),
            "equity": float(r.equity),
            "fees": float(r.attribution.fees),
            "exposure": r.gross_exposure,
            "fills": len(r.fills),
            "missed": len(r.missed),
        }
        for r in result.sessions
    ]
    slices = {}
    for name, start in [
        ("full", None),
        ("2024", dt.date(2024, 1, 1)),
        ("2025", dt.date(2025, 1, 1)),
        ("2026_ytd", dt.date(2026, 1, 1)),
        ("recent", dt.date(2026, 7, 13)),
    ]:
        indices = [
            i
            for i, r in enumerate(result.sessions)
            if (start is None or r.session >= start)
            and (name not in {"2024", "2025"} or r.session.year == int(name))
        ]
        selected = [rows[i] for i in indices]
        opening = (
            float(result.sessions[indices[0] - 1].equity) if indices and indices[0] else 100000.0
        )
        slices[name] = metrics(selected, opening)
    record = {
        "arm": arm,
        "cost_bps": cost,
        "protocol_sha256": digest(protocol),
        "metrics": slices,
        "warnings": list(result.warnings),
        "sessions": rows,
        "record_sha256": hashlib.sha256(
            json.dumps(
                [dataclasses.asdict(r) for r in result.sessions], default=str, sort_keys=True
            ).encode()
        ).hexdigest(),
    }
    write(destination, record)
    destination.with_suffix(".sha256").write_text(digest(record))
    return record


def report(output: Path) -> Path:
    protocol = verify(output)
    text = [
        "# Fixed strategy improvement comparison",
        "",
        "Retrospective research on previously inspected history. No strategy is promoted.",
        "",
        "| Arm | Cost bps | 2026 YTD net | YTD drawdown | YTD fills | Recent net |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    failed = []
    for arm in ARMS:
        for cost in protocol["costs_bps"]:
            path = output / f"{arm}-{cost}bps.json"
            if not path.exists():
                failed.append(f"{arm}-{cost}")
                continue
            record = run_arm(output, arm, cost)
            m = record["metrics"]["2026_ytd"]
            recent = record["metrics"]["recent"]
            text.append(
                f"| {arm} | {cost} | {m['net_return']:.2%} | "
                f"{m['max_drawdown']:.2%} | {m['fills']} | {recent['net_return']:.2%} |"
            )
    text += [
        "",
        f"Incomplete/missing runs: {failed}.",
        "",
        "D1 is an aggregate-window cash-policy proxy, not tick-level paper parity.",
        "Full paths and 2024/2025 comparisons are retained in the run JSON files.",
        "Risk triggers and minute adaptation are separately evaluated capabilities.",
    ]
    path = output / "report.md"
    path.write_text("\n".join(text) + "\n")
    return path
