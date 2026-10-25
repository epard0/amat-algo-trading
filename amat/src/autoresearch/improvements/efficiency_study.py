"""Preregistered trade-reduction and group-specific risk ablations against daily core."""

from __future__ import annotations

import datetime as dt
import importlib.metadata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.runner import DecisionOverlay, run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from autoresearch.improvements.study import digest, source_hashes, verify, write
from autoresearch.improvements.validation_study import (
    SLICES,
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

# Literal values are the small fixed experiment, not fitted hyperparameters.
# ruff: noqa: PLR2004
LABELS = {
    "core_daily": "Daily core groups · reference",
    "band3": "Trade beyond 3% drift",
    "band4": "Trade beyond 4% drift",
    "band5": "Trade beyond 5% drift",
    "group_trend": "Group weakness filter",
    "group_shock": "Group shock filter",
    "group_stress": "Group volatility filter",
    "band3_trend": "3% drift + group weakness",
    "band4_trend": "4% drift + group weakness",
}


def group_risk_multipliers(
    prices: np.ndarray, symbols: list[str], groups: Sequence[Any], kind: str
) -> np.ndarray:
    """Close-time, group-local features; no fitted transforms, forward prices or redistribution."""
    if kind not in {"trend", "shock", "stress"}:
        raise ValueError("Unregistered group risk rule")
    if not np.isfinite(prices).all() or np.any(prices <= 0):
        raise ValueError("Group features require positive finite prices")
    out = np.ones_like(prices, dtype=float)
    returns = np.diff(np.log(prices), axis=0)
    for group in groups:
        columns = [symbols.index(str(s)) for s in group.symbols]
        for t in range(63, len(prices)):
            mean20 = float(np.mean(np.log(prices[t, columns] / prices[t - 20, columns])))
            if kind == "trend":
                breadth = np.mean(
                    prices[t, columns] > prices[t - 62 : t + 1][:, columns].mean(axis=0)
                )
                danger: bool = bool(mean20 < 0 and breadth < 0.5)
            elif kind == "shock":
                danger = bool(
                    np.mean(np.log(prices[t, columns] / prices[t - 5, columns])) < np.log(0.96)
                )
            else:
                group_returns = returns[t - 60 : t][:, columns].mean(axis=1)
                fast = float(np.std(group_returns[-20:], ddof=1))
                slow = float(np.std(group_returns, ddof=1))
                danger = mean20 < 0 and fast > 1.5 * max(slow, 1e-8)
            if danger:
                out[t, columns] = 0.5
    return out


def recipes(base: StrategyConfig) -> dict[str, Any]:
    configs = {}
    for arm in LABELS:
        value = base.model_dump(mode="json")
        if arm.startswith("band"):
            value["rebalance"]["min_weight_delta"] = int(arm[4]) / 100
        configs[arm] = StrategyConfig.model_validate(value).model_dump(mode="json")
    return configs


def register(root: Path, snapshot: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("Choose a new immutable study directory")
    base = load_strategy_config(root / "configs/strategy/amat_group_selection_daily.toml")
    manifest = load_manifest(snapshot)
    protocol = {
        "schema": "daily-group-efficiency/1",
        "root": str(root.resolve()),
        "snapshot": str(snapshot.resolve()),
        "snapshot_id": manifest.snapshot_id,
        "source_sha256": source_hashes(root),
        "partition_sha256": manifest.partition_sha256,
        "baseline_config_sha256": base.content_sha256,
        "configs": recipes(base.strategy),
        "labels": LABELS,
        "costs_bps": [2, 6, 10],
        "primary_cost_bps": 2,
        "execution_start": "2017-01-03",
        "initial_cash": 100000,
        "participation_cap": 0.01,
        "group_filter": {
            "target_multiplier": 0.5,
            "trend": (
                "Group mean 20-session log return < 0 AND fewer than half of members above "
                "their trailing 63-close mean"
            ),
            "shock": "Group mean 5-session log return < log(0.96)",
            "stress": (
                "Group mean 20-session log return < 0 AND 20-session volatility of equal- "
                "weight daily group log returns > 1.5 times trailing 60-session volatility"
            ),
        },
        "retention": (
            "Match or exceed daily core net return in full history AND 2020-onward at "
            "each of 2/6/10bps; 6bps full-history max drawdown must be no worse. No "
            "threshold fitting or validation-driven switching."
        ),
        "cost_scope": (
            "Observed 09:30-09:35 VWAP, next-session execution, dividends/splits, 1% "
            "opening-volume participation and recorded unfilled instructions. 2/6/10bps "
            "combined spread/commission/impact assumptions, not measured quotes. Zero "
            "cash yield."
        ),
        "history_scope": (
            "2016 signal warmup; flat $100,000 account Jan 3, 2017 through Oct 12, 2026. "
            "Fixed rules, annual forward evaluation 2020-2026 on continuing accounts; no "
            "model fitting or yearly account resets. All dates have prior research "
            "exposure."
        ),
        "group_scope": (
            "Only the affected group is attenuated, after native sizing; foregone "
            "allocation stays in cash. Daily asset refresh and prior-completed-week "
            "group rankings are preserved. Targets are not guaranteed fills."
        ),
        "research_only": True,
        "promotion_authority": False,
        "packages": {
            p: importlib.metadata.version(p)
            for p in ["numpy", "polars", "pydantic", "scikit-learn"]
        },
    }
    output.mkdir(parents=True)
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def paired_monthly_interval(candidate: dict[str, Any], baseline: dict[str, Any]) -> list[float]:
    """Paired three-month block bootstrap, annualized log-return difference; descriptive only."""
    a = {r["month"]: r["net_return"] for r in candidate["monthly"] if r["month"] >= "2017-01"}
    b = {r["month"]: r["net_return"] for r in baseline["monthly"] if r["month"] >= "2017-01"}
    if a.keys() != b.keys():
        raise ValueError("Monthly comparison calendar changed")
    delta = np.array([np.log1p(a[k]) - np.log1p(b[k]) for k in sorted(a)])
    rng = np.random.default_rng(20261019)
    starts = rng.integers(0, len(delta), size=(5000, (len(delta) + 2) // 3))
    indices = ((starts[:, :, None] + np.arange(3)) % len(delta)).reshape(5000, -1)[:, : len(delta)]
    return [float(n) for n in np.quantile(delta[indices].mean(axis=1) * 12, [0.025, 0.975])]


def retention(records: dict[str, dict[int, dict[str, Any]]]) -> dict[str, Any]:
    decisions: dict[str, Any] = {}
    for arm in LABELS:
        if arm == "core_daily":
            decisions[arm] = {
                "status": "incumbent",
                "reason": "User-selected current best reference",
            }
            continue
        checks = []
        for cost in [2, 6, 10]:
            for period in ["full", "walk_forward"]:
                left = records[arm][cost]["metrics"][period]["net_return"]
                right = records["core_daily"][cost]["metrics"][period]["net_return"]
                checks.append(
                    {
                        "cost_bps": cost,
                        "period": period,
                        "difference": left - right,
                        "passes": left >= right,
                    }
                )
        drawdown = records[arm][6]["metrics"]["full"]["max_drawdown"]
        reference_dd = records["core_daily"][6]["metrics"]["full"]["max_drawdown"]
        passes = all(r["passes"] for r in checks) and drawdown >= reference_dd
        decisions[arm] = {
            "status": "retained" if passes else "rejected",
            "return_checks": checks,
            "drawdown_passes": drawdown >= reference_dd,
            "annualized_log_return_delta_ci95": paired_monthly_interval(
                records[arm][6], records["core_daily"][6]
            ),
            "reason": "Passes every matched return floor and drawdown guard"
            if passes
            else "Fails a matched return floor or drawdown guard",
        }
    return decisions


def run(output: Path) -> None:
    protocol = verify(output)
    base = StrategyConfig.model_validate(protocol["configs"]["core_daily"])
    symbols = list(map(str, base.universe.symbols))
    panel, _ = load_panel(Path(protocol["snapshot"]), symbols, with_context=True)
    if not np.isfinite(panel.window_vwaps).all() or np.any(panel.window_volumes <= 0):
        raise ValueError("Incomplete execution-window observations")
    actions = load_corporate_actions(Path(protocol["snapshot"]), symbols, sessions=panel.sessions)
    multipliers = {
        k: group_risk_multipliers(panel.adjusted_closes, symbols, base.adaptive_rotation.groups, k)
        for k in ["trend", "shock", "stress"]
    }
    records: dict[str, dict[int, dict[str, Any]]] = {}
    for arm in LABELS:
        records[arm] = {}
        strategy = StrategyConfig.model_validate(protocol["configs"][arm])
        rule = (
            "trend"
            if arm.endswith("_trend")
            else arm.removeprefix("group_")
            if arm.startswith("group_")
            else None
        )
        overlay = (
            DecisionOverlay(multipliers[rule], np.ones_like(panel.adjusted_closes, dtype=bool))
            if rule
            else None
        )
        for cost in [2, 6, 10]:
            result = run_backtest(
                strategy,
                panel,
                strategy.to_universe(),
                actions_by_session=actions,
                execution_start=dt.date(2017, 1, 3),
                one_way_bps=cost,
                max_entry_window_participation=0.01,
                decision_overlay=overlay,
            )
            rows: list[dict[str, Any]] = []
            previous = 100000.0
            for r in result.sessions:
                notional = sum(float(f.price * f.quantity) for f in r.fills)
                rows.append(
                    {
                        "session": str(r.session),
                        "equity": float(r.equity),
                        "fees": float(r.attribution.fees),
                        "exposure": r.gross_exposure,
                        "fills": len(r.fills),
                        "missed": len(r.missed),
                        "traded_notional": notional,
                        "turnover": notional / previous,
                    }
                )
                previous = float(r.equity)
            slices = sliced_metrics(rows)
            for key, metric in slices.items():
                start, end = SLICES[key]
                selected = [r for r in rows if start <= r["session"] <= end]
                metric["annualized_turnover"] = (
                    float(np.mean([r["turnover"] for r in selected]) * 252) if selected else 0.0
                )
                metric["traded_notional"] = sum(r["traded_notional"] for r in selected)
            record = {
                "arm": arm,
                "cost_bps": cost,
                "protocol_sha256": digest(protocol),
                "metrics": slices,
                "sessions": rows,
                "monthly": monthly_metrics(rows),
                "recovery_episodes": recovery_episodes(rows),
                "warnings": list(result.warnings),
            }
            path = output / f"{arm}-{cost}bps.json"
            if path.exists():
                raise ValueError("Refuse overwriting a retained trial")
            write(path, record)
            path.with_suffix(".sha256").write_text(digest(record))
            records[arm][cost] = record
            print(f"Completed {arm} {cost}bps: {slices['full']['net_return']:.2%}", flush=True)  # noqa: T201  # noqa: E501 - Markdown table row
    decisions: dict[str, Any] = {
        "protocol_sha256": digest(protocol),
        "decisions": retention(records),
        "window_cells": int(panel.window_vwaps.size),
        "missing_window_cells": 0,
    }
    write(output / "decisions.json", decisions)
    (output / "decisions.sha256").write_text(digest(decisions))
    lines = [
        "# Trade reduction and group risk study",
        "",
        "| Approach | Status | Full return (2bps) | Full return (6bps) | Full return (10bps) | Drawdown (6bps) | Fills (6bps) |",  # noqa: E501 - Markdown table row
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for arm, label in LABELS.items():
        values = [records[arm][c]["metrics"]["full"] for c in [2, 6, 10]]
        lines.append(
            f"| {label} | {decisions['decisions'][arm]['status']} | {values[0]['net_return']:.2%} | {values[1]['net_return']:.2%} | {values[2]['net_return']:.2%} | {abs(values[1]['max_drawdown']):.2%} | {values[1]['fills']} |"  # noqa: E501 - Markdown table row
        )
    (output / "report.md").write_text("\n".join(lines) + "\n")
