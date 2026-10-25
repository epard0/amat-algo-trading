"""Preregistered cost-aware switching and rank-stability ablations against daily core."""

from __future__ import annotations

import datetime as dt
import importlib.metadata
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import DecisionOverlay, run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.domain.types import Side
from amat.execution.budget import CashBudgetPolicy
from amat.portfolio import precompute_decision_inputs
from autoresearch.improvements.study import digest, source_hashes, verify, write
from autoresearch.improvements.validation_study import (
    SLICES,
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

# Literal values are the small fixed experiment, not fitted hyperparameters.
# ruff: noqa: E501
LABELS = {
    "core_daily": "Daily core groups · reference",
    "confirm2": "Confirm rank for two days",
    "confirm3": "Confirm rank for three days",
    "cost1": "Switch only above round-trip cost",
    "cost2": "Switch above twice round-trip cost",
    "confirm2_cost1": "Two days + cost hurdle",
    "confirm3_cost1": "Three days + cost hurdle",
    "confirm2_cost2": "Two days + double cost hurdle",
    "confirm3_cost2": "Three days + double cost hurdle",
}


CASH_POLICY_SPEC = {
    "reserve_fraction": "0.01",
    "price_buffer_fraction": "0.002",
    "quantity_quantum": "0.000001",
}


def research_cash_policy(value: dict[str, str] | None) -> CashBudgetPolicy | None:
    """Only the existing D1 policy is admitted; absent means the original native path."""
    if value is None:
        return None
    if value != CASH_POLICY_SPEC:
        raise ValueError("Unregistered execution cash policy")
    return CashBudgetPolicy(**{key: Decimal(value) for key, value in value.items()})


def verify_registered_switch_gates(protocol: dict[str, Any], masks: dict[str, np.ndarray]) -> None:
    """Reject gate/parent drift before any metrics when mask digests were preregistered."""
    expected = protocol.get("registered_switch_gate_sha256")
    if expected is None:
        return
    if set(expected) != set(masks) or set(expected) - set(protocol["labels"]):
        raise ValueError("Registered switch-gate identities differ")
    if any(mask.dtype != np.bool_ for mask in masks.values()):
        raise ValueError("Registered switch gates must be boolean")
    actual = {arm: digest(mask.tolist()) for arm, mask in masks.items()}
    if actual != expected:
        raise ValueError("Registered switch-gate bytes differ; check original parent preferences")


def decision_overlay_digest(overlay: DecisionOverlay) -> str:
    return digest(
        {
            "risk_multipliers": overlay.risk_multipliers.tolist(),
            "allow_increase": overlay.allow_increase.tolist(),
        }
    )


def verify_registered_decision_overlays(
    protocol: dict[str, Any], overlays: dict[str, DecisionOverlay], shape: tuple[int, ...]
) -> None:
    """Verify bounded, fully aligned target/entry arrays before native measurement."""
    expected = protocol.get("registered_decision_overlay_sha256", {})
    if set(expected) != set(overlays) or set(overlays) - set(protocol["labels"]):
        raise ValueError("Registered decision-overlay identities differ")
    for overlay in overlays.values():
        risk, allow = overlay.risk_multipliers, overlay.allow_increase
        if (
            risk.shape != shape
            or allow.shape != shape
            or allow.dtype != np.bool_
            or not np.isfinite(risk).all()
            or np.any(risk < 0)
            or np.any(risk > 1)
        ):
            raise ValueError(
                "Decision overlay requires aligned finite [0,1] risk and boolean entries"
            )
    if {arm: decision_overlay_digest(value) for arm, value in overlays.items()} != expected:
        raise ValueError("Registered decision-overlay bytes differ")


def recipes(base: StrategyConfig) -> dict[str, Any]:
    configs = {}
    for arm in LABELS:
        value = base.model_dump(mode="json")
        rotation = value["adaptive_rotation"]
        if "confirm2" in arm:
            rotation["switch_confirmation_sessions"] = 2
        if "confirm3" in arm:
            rotation["switch_confirmation_sessions"] = 3
        if "cost" in arm:
            rotation["switch_cost_bps"] = 2
            rotation["switch_cost_multiple"] = 2 if "cost2" in arm else 1
        configs[arm] = StrategyConfig.model_validate(value).model_dump(mode="json")
    return configs


def register(
    root: Path,
    snapshot: Path,
    output: Path,
    *,
    labels: dict[str, str] | None = None,
    recipe_builder: Callable[[StrategyConfig], dict[str, Any]] = recipes,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("Choose a new immutable study directory")
    base = load_strategy_config(root / "configs/strategy/amat_group_selection_daily.toml")
    manifest = load_manifest(snapshot)
    protocol = {
        "schema": "daily-group-switching/1",
        "root": str(root.resolve()),
        "snapshot": str(snapshot.resolve()),
        "snapshot_id": manifest.snapshot_id,
        "source_sha256": source_hashes(root),
        "partition_sha256": manifest.partition_sha256,
        "baseline_config_sha256": base.content_sha256,
        "configs": recipe_builder(base.strategy),
        "labels": LABELS if labels is None else labels,
        "costs_bps": [2, 6, 10],
        "primary_cost_bps": 2,
        "execution_start": "2017-01-03",
        "initial_cash": 100000,
        "participation_cap": 0.01,
        "whole_share_buys": True,
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
        "switching_rules": {
            "confirmation": "Two or three consecutive selection sessions preferred before replacing an eligible incumbent within the same selected group",
            "cost_hurdle": "Five-day projected trailing 20-session mean log-return advantage must exceed two one-way costs, optionally multiplied by two; each trial uses its known cost assumption",
            "exits": "Eligibility loss and group exclusions exit immediately; vacant slots fill without confirmation",
        },
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


def retention(
    records: dict[str, dict[int, dict[str, Any]]], *, labels: dict[str, str] | None = None
) -> dict[str, Any]:
    decisions: dict[str, Any] = {}
    for arm in LABELS if labels is None else labels:
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


def load_study_panel(
    snapshot: Path,
    symbols: list[str],
    reference_sessions: tuple[Any, ...] | None = None,
) -> tuple[MarketPanel, Any]:
    """Load actual observations/actions for one exact ordered universe."""
    panel, _ = load_panel(snapshot, symbols, with_context=True)
    if tuple(map(str, panel.symbols)) != tuple(symbols):
        raise ValueError("Study panel symbol order differs from strategy universe")
    if reference_sessions is not None and panel.sessions != reference_sessions:
        raise ValueError("Study universes have different session calendars")
    if (
        not np.isfinite(panel.window_vwaps).all()
        or np.any(panel.window_vwaps <= 0)
        or not np.isfinite(panel.window_volumes).all()
        or np.any(panel.window_volumes <= 0)
    ):
        raise ValueError("Incomplete execution-window observations")
    actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
    return panel, actions


def run(  # noqa: PLR0912, PLR0915 - one frozen native ledger measurement pass
    output: Path,
    *,
    labels: dict[str, str] | None = None,
    mask_builder: Callable[[MarketPanel, np.ndarray], dict[str, np.ndarray]] | None = None,
    overlay_builder: Callable[[MarketPanel], dict[str, DecisionOverlay]] | None = None,
    record_validator: Callable[[str, int, dict[str, Any]], None] | None = None,
    retention_builder: Callable[[dict[str, dict[int, dict[str, Any]]]], dict[str, Any]]
    | None = None,
) -> None:
    labels = LABELS if labels is None else labels
    protocol = verify(output)
    policies = protocol.get("execution_cash_policies", {})
    if set(policies) - set(labels):
        raise ValueError("Cash policy references an unregistered arm")
    for value in policies.values():
        research_cash_policy(value)
    base = StrategyConfig.model_validate(protocol["configs"]["core_daily"])
    symbols = list(map(str, base.universe.symbols))
    if mask_builder is not None or overlay_builder is not None:
        for arm in labels:
            configured = StrategyConfig.model_validate(protocol["configs"][arm])
            if list(map(str, configured.universe.symbols)) != symbols:
                raise ValueError(
                    "Mixed-universe studies require arm-specific external overlay alignment"
                )
    panel, actions = load_study_panel(Path(protocol["snapshot"]), symbols)
    reference_sessions = panel.sessions
    panel_cache = {tuple(symbols): (panel, actions)}
    masks = {}
    if mask_builder is not None:
        cache = precompute_decision_inputs(base, panel.adjusted_closes, sessions=panel.sessions)
        if cache.adaptive_scores is None:
            raise ValueError("Condition gates require native group membership")
        masks = mask_builder(panel, cache.adaptive_scores > 0)
    verify_registered_switch_gates(protocol, masks)
    overlays = overlay_builder(panel) if overlay_builder else {}
    verify_registered_decision_overlays(protocol, overlays, panel.adjusted_closes.shape)
    records: dict[str, dict[int, dict[str, Any]]] = {}
    for arm in labels:
        records[arm] = {}
        strategy = StrategyConfig.model_validate(protocol["configs"][arm])
        arm_symbols = list(map(str, strategy.universe.symbols))
        if arm_symbols != symbols and (mask_builder is not None or overlay_builder is not None):
            raise ValueError(
                "Mixed-universe studies require arm-specific external overlay alignment"
            )
        universe_key = tuple(arm_symbols)
        if universe_key not in panel_cache:
            panel_cache[universe_key] = load_study_panel(
                Path(protocol["snapshot"]), arm_symbols, reference_sessions
            )
        panel, actions = panel_cache[universe_key]
        for cost in [2, 6, 10]:
            if strategy.adaptive_rotation.switch_cost_bps > 0:
                rotation = strategy.adaptive_rotation.model_copy(update={"switch_cost_bps": cost})
                strategy = strategy.model_copy(update={"adaptive_rotation": rotation})
            result = run_backtest(
                strategy,
                panel,
                strategy.to_universe(),
                actions_by_session=actions,
                execution_start=dt.date(2017, 1, 3),
                one_way_bps=cost,
                max_entry_window_participation=0.01,
                switch_cost_gate=masks.get(arm),
                decision_overlay=overlays.get(arm),
                execution_cash_policy=research_cash_policy(policies.get(arm)),
                whole_share_buys=protocol.get("whole_share_buys_by_arm", {}).get(
                    arm, protocol.get("whole_share_buys", False)
                ),
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
            fractions = [
                f
                for session in result.sessions
                for f in session.fills
                if f.quantity != f.quantity.to_integral_value()
            ]
            if protocol.get("whole_share_buys_by_arm", {}).get(
                arm, protocol.get("whole_share_buys", False)
            ) and any(f.side == Side.BUY for f in fractions):
                raise ValueError("Whole-share contract produced a fractional purchase")
            record = {
                "fractional_fill_count": len(fractions),
                "fractional_buy_count": sum(f.side == Side.BUY for f in fractions),
                "arm": arm,
                "cost_bps": cost,
                "protocol_sha256": digest(protocol),
                "metrics": slices,
                "sessions": rows,
                "monthly": monthly_metrics(rows),
                "recovery_episodes": recovery_episodes(rows),
                "warnings": list(result.warnings),
                "switch_gate_sha256": digest(masks[arm].tolist()) if arm in masks else None,
                "condition_fraction": float(masks[arm][252:].mean()) if arm in masks else None,
                "effective_config_sha256": digest(strategy.model_dump(mode="json")),
            }
            if arm in overlays:
                record["decision_overlay_sha256"] = decision_overlay_digest(overlays[arm])
            if arm in policies:
                record["execution_cash_policy_sha256"] = digest(policies[arm])
            if record_validator is not None:
                record_validator(arm, cost, record)
            path = output / f"{arm}-{cost}bps.json"
            if path.exists():
                raise ValueError("Refuse overwriting a retained trial")
            write(path, record)
            path.with_suffix(".sha256").write_text(digest(record))
            records[arm][cost] = record
            print(f"Completed {arm} {cost}bps: {slices['full']['net_return']:.2%}", flush=True)  # noqa: T201  # noqa: E501 - Markdown table row
    decisions: dict[str, Any] = {
        "protocol_sha256": digest(protocol),
        "decisions": retention_builder(records)
        if retention_builder
        else retention(records, labels=labels),
        "window_cells": int(panel.window_vwaps.size),
        "missing_window_cells": 0,
    }
    write(output / "decisions.json", decisions)
    (output / "decisions.sha256").write_text(digest(decisions))
    lines = [
        "# Conditional cost-aware switching study"
        if mask_builder
        else "# Cost-aware switching and rank-stability study",
        "",
        "| Approach | Status | Full return (2bps) | Full return (6bps) | Full return (10bps) | Drawdown (6bps) | Fills (6bps) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for arm, label in labels.items():
        values = [records[arm][c]["metrics"]["full"] for c in [2, 6, 10]]
        lines.append(
            f"| {label} | {decisions['decisions'][arm]['status']} | {values[0]['net_return']:.2%} | {values[1]['net_return']:.2%} | {values[2]['net_return']:.2%} | {abs(values[1]['max_drawdown']):.2%} | {values[1]['fills']} |"
        )
    (output / "report.md").write_text("\n".join(lines) + "\n")
