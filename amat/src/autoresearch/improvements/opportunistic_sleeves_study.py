"""Residual-cash index overlays, with a unified netted signed research ledger."""

from __future__ import annotations

# Fixed, preregistered rules and costs. No optimization after seeing results.
# ruff: noqa: PLR2004, E501
import json
from pathlib import Path
from typing import Any

import numpy as np

from amat.config.loading import load_strategy_config
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.portfolio import decide, ledoit_wolf_covariance, precompute_decision_inputs
from autoresearch.improvements import index_sleeves_study as previous
from autoresearch.improvements.study import digest, verify, write
from autoresearch.improvements.switching_study import paired_monthly_interval

LABELS = {
    "core_daily": "Daily core groups · native reference",
    "conditional_reference": "Retained conditional switching reference",
    "matched_control": "Core in netted ledger · execution control",
    "qqq_trend": "Unused cash · QQQ directional trend",
    "spy_trend": "Unused cash · SPY slow trend",
    "iwm_breakout": "Unused cash · IWM breakout",
    "index_basket": "Unused cash · tailored index basket",
    "qqq_pullback": "Unused cash · QQQ pullback/bounce",
    "relative_pair": "Unused cash · QQQ/SPY relative momentum",
    "qqq_long_only": "Unused cash · QQQ long-only control",
    "basket_long_only": "Unused cash · index long-only control",
}
CANDIDATES = previous.CANDIDATES


def net_cash_target(
    core: np.ndarray,
    signal: np.ndarray,
    covariance: np.ndarray,
    *,
    index_columns: list[int],
    short_available: bool = True,
) -> tuple[np.ndarray, float]:
    """Reserve all core target capital, then scale a single netted overlay into residual cash.

    Net positions obey 45% symbol, 85% US-index cluster and 100% gross caps;
    joint volatility cannot exceed 20% or the original core risk, whichever is
    higher. Two percent of NAV is not assigned to the sleeve. No re-leverage.
    A coupled pair scales as one intent, including when borrowing is unavailable.
    """
    if (
        core.shape != signal.shape
        or covariance.shape != (len(core), len(core))
        or not np.isfinite(core).all()
        or not np.isfinite(signal).all()
        or not np.isfinite(covariance).all()
        or np.any(core < 0)
        or abs(signal).sum() > 1 + 1e-10
    ):
        raise ValueError("Invalid core, signal or covariance")
    free = max(0.0, 0.98 - float(core.sum()))
    overlay = free * signal
    cap = max(0.2**2, float(core @ covariance @ core))

    def feasible(scale: float) -> bool:
        net = core + scale * overlay
        return bool(
            np.max(np.abs(net)) <= 0.45 + 1e-10
            and np.abs(net[index_columns]).sum() <= 0.85 + 1e-10
            and np.abs(net).sum() <= 1 + 1e-10
            and float(net @ covariance @ net) <= cap + 1e-10
            and (short_available or np.all(net >= -1e-10))
        )

    if not free or not np.any(overlay):
        return core.copy(), 0.0
    if feasible(1.0):
        return core + overlay, free * float(np.abs(signal).sum())
    low, high = 0.0, 1.0
    for _ in range(36):
        mid = (low + high) / 2
        if feasible(mid):
            low = mid
        else:
            high = mid
    return core + low * overlay, low * free * float(np.abs(signal).sum())


def native_targets(strategy: Any, panel: Any) -> tuple[np.ndarray, np.ndarray]:
    cache = precompute_decision_inputs(strategy, panel.adjusted_closes, sessions=panel.sessions)
    targets = np.zeros_like(panel.adjusted_closes)
    covariance = np.zeros((len(targets), targets.shape[1], targets.shape[1]))
    for t in range(strategy.warmup.min_completed_sessions - 1, len(targets)):
        allocation = decide(
            strategy,
            panel.adjusted_closes,
            strategy.to_universe(),
            decision_index=t,
            precomputed=cache,
        )
        targets[t] = allocation.weights
        covariance[t] = ledoit_wolf_covariance(cache.returns[1 : t + 1], strategy.covariance)
    return targets, covariance


def register(root: Path, snapshot: Path, output: Path, reference: Path) -> dict[str, Any]:
    protocol = previous.register(root, snapshot, output, reference)
    protocol.update(
        schema="opportunistic-netted-index-sleeves/1",
        labels=LABELS,
        allocation="One $100k netted portfolio. Core target capital, including planned purchases, is reserved first. Sleeve gross budget is max(0,98% minus core target gross); no fixed allocation or book transfers. Overlay and core targets net by instrument before orders, share-volume limits and borrowing costs. Core-increase commitments reserve cash ahead of sleeve increases. Prior-close sizing, next-session fills; sleeve liquidation/refunding trades pay normal costs. Single coupled scale preserves pair ratios.",
        portfolio_limits="45% absolute symbol exposure; 85% absolute US-index cluster; 100% target gross, 98% fill-time collateral policy; joint forecast volatility <= max(20%, native core risk). Core weights are never increased to recover foregone risk. If original core already consumes a limit, no incremental violating overlay is allowed. Gap-driven excess is disclosed, not backdated.",
        retention="Match or exceed native daily core, retained conditional-double reference AND same-ledger no-sleeve control returns in full history and 2020 onward at every 2/6/10bps; 6bps full drawdown no worse than all three. Repeat full/walk-forward return and drawdown floors at 6bps with 6% annual borrow. No-borrow scenario is disclosed. No threshold tuning. At least one strict return gain over same-ledger control required.",
        control_scope="Signed research ledger uses whole shares, collateral reserve and conservative sale-volume caps; its execution mechanics differ from native fractional long-or-cash ledger. A matched no-sleeve control prevents crediting those differences to overlay alpha. Native and conditional references remain independent absolute return floors. This does not assert live execution parity.",
        research_only=True,
        promotion_authority=False,
    )
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def run(output: Path) -> None:
    protocol = verify(output)
    root = Path(protocol["root"])
    snapshot = Path(protocol["snapshot"])
    strategy = load_strategy_config(
        root / "configs/strategy/amat_group_selection_daily.toml"
    ).strategy
    symbols = list(map(str, strategy.universe.symbols))
    panel, _ = load_panel(snapshot, symbols, with_context=True)
    actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
    core, covariance = native_targets(strategy, panel)
    index_columns = [symbols.index(s) for s in ["QQQ", "SPY", "IWM"]]
    signals = previous.signal_weights(panel.adjusted_closes, symbols)
    records: dict[str, dict[int, dict[str, Any]]] = {a: {} for a in LABELS}
    for c in [2, 6, 10]:
        for arm, key in [
            ("core_daily", "core_daily"),
            ("conditional_reference", "combined_double"),
        ]:
            ref = protocol["reference_records"][f"{key}-{c}"]
            value = json.loads(Path(ref["path"]).read_text())
            if digest(value) != ref["sha256"]:
                raise ValueError("Reference changed")
            records[arm][c] = {**value, "arm": arm, "protocol_sha256": digest(protocol)}
            previous.seal(output, f"{arm}-{c}bps", records[arm][c])
        rows = previous.signed_sleeve(
            panel, core, actions, cost_bps=c, initial_cash=100000, core_targets=core
        )
        value = previous.record(rows, "matched_control", c, protocol)
        records["matched_control"][c] = value
        previous.seal(output, f"matched_control-{c}bps", value)
    for arm in CANDIDATES:
        for c, borrow, available, suffix in [(c, 0.03, True, "") for c in [2, 6, 10]] + [
            (6, 0.06, True, "-borrow6"),
            (6, 0.03, False, "-no-borrow"),
        ]:
            targets = core.copy()
            budget = np.zeros(len(core))
            offset = np.zeros(len(core))
            for t in range(252, len(targets)):
                targets[t], budget[t] = net_cash_target(
                    core[t],
                    signals[arm][t],
                    covariance[t],
                    index_columns=index_columns,
                    short_available=available,
                )
                offset[t] = float(
                    np.abs(core[t]).sum()
                    + np.abs(targets[t] - core[t]).sum()
                    - np.abs(targets[t]).sum()
                )
            rows = previous.signed_sleeve(
                panel,
                targets,
                actions,
                cost_bps=c,
                borrow_rate=borrow,
                initial_cash=100000,
                core_targets=core,
            )
            value = previous.record(
                rows,
                arm,
                c,
                protocol,
                borrow_rate=borrow,
                short_available=available,
                borrow_total=sum(r["borrow_cost"] for r in rows),
                sleeve_short_days=int(sum(r["short_exposure"] > 0 for r in rows)),
                sleeve_margin_breach_days=sum(r["margin_breaches"] for r in rows),
                average_sleeve_budget=float(np.mean(budget[252:])),
                average_netting_offset=float(np.mean(offset[252:])),
                active_overlay_days=int(np.sum(budget[252:] > 1e-8)),
                target_sha256=digest(targets.tolist()),
                core_target_sha256=digest(core.tolist()),
            )
            previous.seal(output, f"{arm}-{c}bps{suffix}", value)
            if not suffix:
                records[arm][c] = value
    references = ["core_daily", "conditional_reference", "matched_control"]
    decisions: dict[str, dict[str, Any]] = {
        a: {
            "status": "incumbent" if a == "core_daily" else "reference",
            "reason": "Independent execution/performance reference",
        }
        for a in references
    }
    for arm in CANDIDATES:
        checks = []
        for c in [2, 6, 10]:
            for period in ["full", "walk_forward"]:
                for ref in references:
                    delta = (
                        records[arm][c]["metrics"][period]["net_return"]
                        - records[ref][c]["metrics"][period]["net_return"]
                    )
                    checks.append(
                        {
                            "cost_bps": c,
                            "period": period,
                            "reference": ref,
                            "difference": delta,
                            "passes": delta >= 0,
                        }
                    )
        ddpass = all(
            records[arm][6]["metrics"]["full"]["max_drawdown"]
            >= records[r][6]["metrics"]["full"]["max_drawdown"]
            for r in references
        )
        stress = json.loads((output / f"{arm}-6bps-borrow6.json").read_text())
        stresspass = all(
            stress["metrics"][p]["net_return"] >= records[r][6]["metrics"][p]["net_return"]
            for p in ["full", "walk_forward"]
            for r in references
        ) and all(
            stress["metrics"]["full"]["max_drawdown"]
            >= records[r][6]["metrics"]["full"]["max_drawdown"]
            for r in references
        )
        strict = any(r["difference"] > 0 and r["reference"] == "matched_control" for r in checks)
        keep = all(c["passes"] for c in checks) and ddpass and stresspass and strict
        decisions[arm] = {
            "status": "retained" if keep else "rejected",
            "return_checks": checks,
            "drawdown_passes": ddpass,
            "borrow_stress_passes": stresspass,
            "annualized_log_return_delta_ci95": paired_monthly_interval(
                records[arm][6], records["core_daily"][6]
            ),
            "reason": "Passes all three references and borrow stress"
            if keep
            else "Fails a return, drawdown, strict improvement or borrow-stress floor",
        }
    previous.seal(output, "decisions", {"protocol_sha256": digest(protocol), "decisions": decisions})
    lines = [
        "# Opportunistic netted index sleeve study",
        "",
        "| Approach | Status | Return 2bps | Return 6bps | Return 10bps | Drawdown 6bps |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for arm, label in LABELS.items():
        m = [records[arm][c]["metrics"]["full"] for c in [2, 6, 10]]
        lines.append(
            f"| {label} | {decisions[arm]['status']} | {m[0]['net_return']:.2%} | {m[1]['net_return']:.2%} | {m[2]['net_return']:.2%} | {abs(m[1]['max_drawdown']):.2%} |"
        )
    (output / "report.md").write_text("\n".join(lines) + "\n")
