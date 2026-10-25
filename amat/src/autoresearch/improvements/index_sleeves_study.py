"""Preregistered, isolated index long/short research; never grants live authority."""

from __future__ import annotations

# Fixed economic assumptions and literal strategy rules, not a fitted search grid.
# ruff: noqa: PLR2004, E501, PLR0912, PLR0915, T201
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.domain.actions import ActionKind
from autoresearch.improvements.study import digest, source_hashes, verify, write
from autoresearch.improvements.switching_study import paired_monthly_interval
from autoresearch.improvements.validation_study import (
    SLICES,
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

LABELS = {
    "core_daily": "Daily core groups · default reference",
    "conditional_reference": "Retained conditional switching reference",
    "qqq_trend": "Core + QQQ directional trend",
    "spy_trend": "Core + SPY slow trend",
    "iwm_breakout": "Core + IWM breakout",
    "index_basket": "Core + tailored index basket",
    "qqq_pullback": "Core + QQQ pullback/bounce",
    "relative_pair": "Core + QQQ/SPY relative momentum",
    "qqq_long_only": "Core + QQQ long-only control",
    "basket_long_only": "Core + index long-only control",
}
CANDIDATES = tuple(k for k in LABELS if k not in {"core_daily", "conditional_reference"})


def signal_weights(prices: np.ndarray, symbols: list[str]) -> dict[str, np.ndarray]:
    """Completed-close signals, annual vol sizing, no full-sample normalization."""
    if not np.isfinite(prices).all() or np.any(prices <= 0):
        raise ValueError("Positive finite signal prices required")
    q, s, i = [symbols.index(k) for k in ["QQQ", "SPY", "IWM"]]
    out = {k: np.zeros_like(prices) for k in CANDIDATES}
    logret = np.diff(np.log(prices), axis=0)
    breakout = 0.0
    for t in range(252, len(prices)):
        vol = np.maximum(np.std(logret[t - 60 : t], axis=0, ddof=1) * np.sqrt(252), 0.05)
        size = np.minimum(0.15 / vol, 0.98)
        market_weak = prices[t, s] < np.mean(prices[t - 199 : t + 1, s])
        for col, arm, horizon in [(q, "qqq_trend", 63), (s, "spy_trend", 126)]:
            direction = 0.0
            slow = np.mean(prices[t - 199 : t + 1, col])
            if prices[t, col] > slow and prices[t, col] > prices[t - horizon, col]:
                direction = 1.0
            elif (
                market_weak
                and prices[t, col] < slow
                and prices[t, col] < prices[t - 63, col]
                and prices[t, col] < prices[t - 20, col]
            ):
                direction = -1.0
            out[arm][t, col] = direction * size[col]
        if prices[t, i] > np.max(prices[t - 55 : t, i]):
            breakout = 1.0
        elif market_weak and prices[t, i] < np.min(prices[t - 55 : t, i]):
            breakout = -1.0
        elif (breakout > 0 and prices[t, i] < np.mean(prices[t - 19 : t + 1, i])) or (
            breakout < 0 and (not market_weak or prices[t, i] > np.mean(prices[t - 19 : t + 1, i]))
        ):
            breakout = 0.0
        out["iwm_breakout"][t, i] = breakout * size[i]
        out["index_basket"][t] = (
            out["qqq_trend"][t] + out["spy_trend"][t] + out["iwm_breakout"][t]
        ) / 3
        z5 = np.log(prices[t, q] / prices[t - 5, q]) / max(vol[q] / np.sqrt(252) * np.sqrt(5), 1e-8)
        if prices[t, q] > np.mean(prices[t - 125 : t + 1, q]) and z5 < -1:
            out["qqq_pullback"][t, q] = size[q]
        elif market_weak and prices[t, q] < np.mean(prices[t - 125 : t + 1, q]) and z5 > 1:
            out["qqq_pullback"][t, q] = -size[q]
        # Trailing beta hedge; gross-normalized and signed in both directions.
        r = logret[t - 126 : t]
        beta = float(
            np.clip(
                np.cov(r[:, q], r[:, s], ddof=1)[0, 1] / max(np.var(r[:, s], ddof=1), 1e-8), 0.5, 2
            )
        )
        spread63 = np.log(prices[t, q] / prices[t - 63, q]) - beta * np.log(
            prices[t, s] / prices[t - 63, s]
        )
        spread21 = np.log(prices[t, q] / prices[t - 21, q]) - beta * np.log(
            prices[t, s] / prices[t - 21, s]
        )
        if abs(spread63) > 0.02 and spread63 * spread21 > 0:
            direction = np.sign(spread63)
            out["relative_pair"][t, q] = direction * 0.98 / (1 + beta)
            out["relative_pair"][t, s] = -direction * 0.98 * beta / (1 + beta)
        out["qqq_long_only"][t] = np.maximum(out["qqq_trend"][t], 0)
        out["basket_long_only"][t] = np.maximum(out["index_basket"][t], 0)
    return out


def signed_sleeve(
    panel: MarketPanel,
    targets: np.ndarray,
    actions: dict[dt.date, Any],
    *,
    cost_bps: float,
    borrow_rate: float = 0.03,
    short_available: bool = True,
    initial_cash: float = 20000.0,
    paired: bool = False,
    core_targets: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Raw-share collateralized ledger. Overnight shorts pay borrow and distributions.

    Orders use previous-close quantities, next-session observed VWAP and a 1%
    volume budget shared across closing/opening legs. Short proceeds remain
    collateral: gross marked exposure may not exceed 98% of opening equity on
    risk increases. Gap-driven excess exposure is reduced at the next observed
    window, not retrospectively. No cash yield, margin loan or borrow rebate.
    """
    if (
        targets.shape != panel.raw_closes.shape
        or not np.isfinite(targets).all()
        or np.any(np.abs(targets).sum(axis=1) > 1 + 1e-10)
    ):
        raise ValueError("Finite aligned gross-one signed targets required")
    if core_targets is not None and (
        core_targets.shape != targets.shape
        or not np.isfinite(core_targets).all()
        or np.any(core_targets < 0)
    ):
        raise ValueError("Core commitments require aligned nonnegative targets")
    if min(cost_bps, borrow_rate) < 0:
        raise ValueError("Negative costs")
    start = panel.sessions.index(dt.date(2017, 1, 3))
    positions = np.zeros(len(panel.symbols))
    cash = initial_cash
    previous = initial_cash
    columns = {str(s): j for j, s in enumerate(panel.symbols)}
    rows = [
        {
            "session": str(panel.sessions[start - 1]),
            "equity": initial_cash,
            "fees": 0.0,
            "exposure": 0.0,
            "fills": 0,
            "missed": 0,
            "traded_notional": 0.0,
            "turnover": 0.0,
            "borrow_cost": 0.0,
            "short_exposure": 0.0,
            "distributions": 0.0,
            "margin_breaches": 0,
        }
    ]
    for t in range(start, len(panel.sessions)):
        date = panel.sessions[t]
        days = (date - panel.sessions[t - 1]).days
        borrow = float(
            np.sum(np.maximum(-positions, 0) * panel.raw_closes[t - 1]) * borrow_rate * days / 365
        )
        cash -= borrow
        desired = np.trunc(targets[t - 1] * previous / panel.raw_closes[t - 1])
        if not short_available:
            desired = np.zeros_like(desired) if paired else np.maximum(desired, 0)
        core_desired = (
            np.trunc(core_targets[t - 1] * previous / panel.raw_closes[t - 1])
            if core_targets is not None
            else np.zeros_like(desired)
        )
        distribution = 0.0
        for action in actions.get(date, ()):
            j = columns.get(str(action.symbol))
            if j is None:
                continue
            if action.kind == ActionKind.SPLIT:
                positions[j] *= float(action.ratio)
                desired[j] *= float(action.ratio)
                core_desired[j] *= float(action.ratio)
            else:
                credit = positions[j] * float(action.cash_per_share)
                cash += credit
                distribution += credit
        prices = panel.window_vwaps[t]
        capacity = np.floor(0.01 * panel.window_volumes[t])
        used = np.zeros(len(positions))
        transactions: list[dict[str, Any]] = []
        fees = 0.0
        notional = 0.0
        fills = 0
        missed = 0
        # Close/reduce first; reversals explicitly cover before opening opposite.
        # Net commitments, not virtual sleeve gross: an opposing sleeve may
        # offset an existing core long without needing a stock loan.
        core_desired = np.minimum(core_desired, np.maximum(desired, 0))
        for phase in [0, 1]:
            order = list(range(len(positions)))
            if phase == 1 and core_targets is not None:
                order.sort(key=lambda j: (-(core_desired[j] - max(positions[j], 0)) * prices[j], j))
            for j in order:
                target = desired[j]
                if phase == 0:
                    if positions[j] * target <= 0:
                        target = 0.0
                    elif abs(target) >= abs(positions[j]):
                        continue
                elif positions[j] * target > 0 and abs(target) <= abs(positions[j]):
                    continue
                delta = target - positions[j]
                if abs(delta) < 1e-9:
                    continue
                if phase == 1 and (
                    abs(delta * prices[j]) < 25 or abs(delta * prices[j]) / max(previous, 1) < 0.02
                ):
                    continue
                quantity = min(abs(delta), max(capacity[j] - used[j], 0))
                quantity = np.floor(quantity)
                if quantity < abs(delta) - 1e-6:
                    missed += 1
                if quantity <= 0:
                    continue
                signed = np.sign(delta) * quantity
                if phase == 1:
                    # Maximal feasible whole-share increase, after execution fees.
                    low, high = 0, int(quantity)
                    for _ in range(32):
                        if low >= high:
                            break
                        mid = (low + high + 1) // 2
                        trial = positions.copy()
                        trial[j] += np.sign(signed) * mid
                        trialcash = (
                            cash
                            - np.sign(signed) * mid * prices[j]
                            - mid * prices[j] * cost_bps / 10000
                        )
                        equity = trialcash + float(trial @ prices)
                        reserved = np.maximum(core_desired - np.maximum(trial, 0), 0)
                        reserved[j] = 0
                        reserve_notional = float(reserved @ prices) * (1 + cost_bps / 10000)
                        if (
                            equity > 0
                            and float(np.abs(trial) @ prices) + reserve_notional
                            <= 0.98 * equity + 1e-8
                        ):
                            low = mid
                        else:
                            high = mid - 1
                    if low < quantity:
                        missed += 1
                    signed = np.sign(signed) * low
                if not signed:
                    continue
                amount = abs(signed) * prices[j]
                fee = amount * cost_bps / 10000
                cash -= signed * prices[j] + fee
                positions[j] += signed
                used[j] += abs(signed)
                fees += fee
                notional += amount
                fills += 1
                transactions.append(
                    {
                        "symbol": str(panel.symbols[j]),
                        "signed_quantity": float(signed),
                        "price": float(prices[j]),
                        "fee": float(fee),
                    }
                )
        equity = cash + float(positions @ panel.raw_closes[t])
        if not np.isfinite(equity) or equity <= 0:
            raise ValueError("Sleeve insolvency")
        gross = float(np.abs(positions) @ panel.raw_closes[t]) / equity
        rows.append(
            {
                "session": str(date),
                "equity": equity,
                "fees": fees + borrow,
                "exposure": gross,
                "fills": fills,
                "missed": missed,
                "traded_notional": notional,
                "turnover": notional / previous,
                "borrow_cost": borrow,
                "short_exposure": float(np.maximum(-positions, 0) @ panel.raw_closes[t]) / equity,
                "distributions": distribution,
                "margin_breaches": int(gross > 1),
                "transactions": transactions,
                "quantities": positions.tolist(),
                "cash": cash,
                "net_position_value": float(positions @ panel.raw_closes[t]),
                "short_liability": float(np.maximum(-positions, 0) @ panel.raw_closes[t]),
                "collateral_free_cash": cash
                - 2 * float(np.maximum(-positions, 0) @ panel.raw_closes[t]),
            }
        )
        previous = equity
    return rows


def record(
    rows: list[dict[str, Any]], arm: str, cost: int, protocol: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    metrics = sliced_metrics(rows)
    for key, value in metrics.items():
        lo, hi = SLICES[key]
        selected = [r for r in rows if lo <= r["session"] <= hi]
        value["annualized_turnover"] = (
            float(np.mean([r["turnover"] for r in selected]) * 252) if selected else 0.0
        )
        value["traded_notional"] = sum(r["traded_notional"] for r in selected)
    return dict(
        arm=arm,
        cost_bps=cost,
        protocol_sha256=digest(protocol),
        metrics=metrics,
        sessions=rows,
        monthly=monthly_metrics(rows),
        recovery_episodes=recovery_episodes(rows),
        **extra,
    )


def register(root: Path, snapshot: Path, output: Path, reference: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("New immutable directory required")
    manifest = load_manifest(snapshot)
    reference_protocol = json.loads((reference / "protocol.json").read_text())
    if (
        digest(reference_protocol) != (reference / "protocol.sha256").read_text().strip()
        or reference_protocol["partition_sha256"] != manifest.partition_sha256
    ):
        raise ValueError("Reference protocol or data identity differs")
    baseline = load_strategy_config(root / "configs/strategy/amat_group_selection_daily.toml")
    if reference_protocol["baseline_config_sha256"] != baseline.content_sha256:
        raise ValueError("Reference baseline configuration changed")
    refs = {}
    for c in [2, 6, 10]:
        for arm in ["core_daily", "combined_double"]:
            p = reference / f"{arm}-{c}bps.json"
            value = json.loads(p.read_text())
            if digest(value) != p.with_suffix(".sha256").read_text().strip() or value[
                "protocol_sha256"
            ] != digest(reference_protocol):
                raise ValueError("Unsealed reference")
            refs[f"{arm}-{c}"] = {"path": str(p.resolve()), "sha256": digest(value)}
    protocol = {
        "schema": "index-signed-sleeves/1",
        "root": str(root.resolve()),
        "snapshot": str(snapshot.resolve()),
        "snapshot_id": manifest.snapshot_id,
        "source_sha256": source_hashes(root),
        "partition_sha256": manifest.partition_sha256,
        "labels": LABELS,
        "costs_bps": [2, 6, 10],
        "primary_cost_bps": 2,
        "reference_records": refs,
        "retention": "Match or exceed BOTH daily core and retained combined-double conditional reference returns in full history and 2020 onward at each 2/6/10bps; 6bps full drawdown no worse than either reference; repeat return/drawdown floors with 6% annual borrow at 6bps. No-borrow scenario is disclosure, not a return gate. No threshold tuning.",
        "allocation": "Segregated accounts: $80k native core plus $20k index sleeve at inception. No subsequent transfers or implicit daily rebalancing. Weights drift with returns. Each account has its own execution/collateral budget; no cross-account netting benefit. Baseline is $100k native core. Short proceeds are collateral, never additional buying power.",
        "history_scope": "2016 warmup; flat account January 3 2017 through October 12 2026; continuing annual forward slices 2020-2026, all previously researched history, no untouched holdout.",
        "cost_scope": "Next-session observed 09:30-09:35 VWAP; 1% window volume cap; 2/6/10bps per side; borrow 3% annual, stressed at 6% and unavailable, calendar-day accrual; raw shares, splits and signed cash dividends; zero cash yield or short rebate. Historical borrow inventory/rates absent: conditional research only, no live short authorization.",
        "rules": "QQQ 63-day/200-day trend; SPY 126-day/200-day trend; shorts also require SPY below 200-day mean and own 20/63-day decline. IWM prior-55-day breakout/20-day mean exit. QQQ 5-day pullback/bounce in 126-day regime. QQQ/SPY 126-day beta-hedged 63/21-day relative momentum. Trend positions target 15% annual vol using trailing 60-day sigma, capped 98% gross. Basket equally averages tailored QQQ/SPY/IWM rules. Long-only ablations clip shorts; 2% drift/$25 increase minimum. Fixed 20% seed funding, no optimized mixing weights.",
        "research_only": True,
        "promotion_authority": False,
    }
    output.mkdir(parents=True)
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def seal(output: Path, name: str, value: dict[str, Any]) -> None:
    p = output / f"{name}.json"
    if p.exists():
        raise ValueError("Refuse overwriting evidence")
    write(p, value)
    p.with_suffix(".sha256").write_text(digest(value))


def run(output: Path) -> None:
    protocol = verify(output)
    root = Path(protocol["root"])
    snapshot = Path(protocol["snapshot"])
    base = load_strategy_config(root / "configs/strategy/amat_group_selection_daily.toml").strategy
    panel, _ = load_panel(snapshot, ["QQQ", "SPY", "IWM"], with_context=True)
    weights = signal_weights(panel.adjusted_closes, list(map(str, panel.symbols)))
    actions = load_corporate_actions(
        snapshot, list(map(str, panel.symbols)), sessions=panel.sessions
    )
    corepanel, _ = load_panel(snapshot, list(map(str, base.universe.symbols)), with_context=True)
    coreactions = load_corporate_actions(
        snapshot, list(map(str, base.universe.symbols)), sessions=corepanel.sessions
    )
    refs = {}
    records: dict[str, dict[int, dict[str, Any]]] = {a: {} for a in LABELS}
    core80: dict[int, list[dict[str, Any]]] = {}
    for c in [2, 6, 10]:
        for arm, key in [
            ("core_daily", "core_daily"),
            ("conditional_reference", "combined_double"),
        ]:
            ref = protocol["reference_records"][f"{key}-{c}"]
            value = json.loads(Path(ref["path"]).read_text())
            if digest(value) != ref["sha256"]:
                raise ValueError("Reference changed")
            refs[arm, c] = value
            records[arm][c] = {**value, "arm": arm, "protocol_sha256": digest(protocol)}
            seal(output, f"{arm}-{c}bps", records[arm][c])
        result = run_backtest(
            base,
            corepanel,
            base.to_universe(),
            initial_cash=Decimal(80000),
            execution_start=dt.date(2017, 1, 3),
            one_way_bps=c,
            actions_by_session=coreactions,
            max_entry_window_participation=0.01,
        )
        rows: list[dict[str, Any]] = []
        prev = 80000.0
        for r in result.sessions:
            n = sum(float(f.price * f.quantity) for f in r.fills)
            rows.append(
                {
                    "session": str(r.session),
                    "equity": float(r.equity),
                    "fees": float(r.attribution.fees),
                    "exposure": r.gross_exposure,
                    "fills": len(r.fills),
                    "missed": len(r.missed),
                    "traded_notional": n,
                    "turnover": n / prev,
                }
            )
            prev = float(r.equity)
        core80[c] = rows
        seal(
            output,
            f"funded-core-{c}bps",
            record(rows, "funded_core", c, protocol, initial_cash=80000),
        )
    for arm in CANDIDATES:
        for c, borrow, available, suffix in [(c, 0.03, True, "") for c in [2, 6, 10]] + [
            (6, 0.06, True, "-borrow6"),
            (6, 0.03, False, "-no-borrow"),
        ]:
            sleeve = signed_sleeve(
                panel,
                weights[arm],
                actions,
                cost_bps=c,
                borrow_rate=borrow,
                short_available=available,
                paired=arm == "relative_pair",
            )
            rows = []
            prev = 100000.0
            if [r["session"] for r in sleeve] != [r["session"] for r in core80[c]]:
                raise ValueError("Account calendars differ")
            for a, b in zip(core80[c], sleeve, strict=True):
                equity = a["equity"] + b["equity"]
                n = a["traded_notional"] + b["traded_notional"]
                rows.append(
                    {
                        "session": a["session"],
                        "equity": equity,
                        "fees": a["fees"] + b["fees"],
                        "exposure": (a["exposure"] * a["equity"] + b["exposure"] * b["equity"])
                        / equity,
                        "fills": a["fills"] + b["fills"],
                        "missed": a["missed"] + b["missed"],
                        "traded_notional": n,
                        "turnover": n / prev,
                        "borrow_cost": b["borrow_cost"],
                        "short_exposure": b["short_exposure"] * b["equity"] / equity,
                        "margin_breaches": b["margin_breaches"],
                    }
                )
                prev = equity
            value = record(
                rows,
                arm,
                c,
                protocol,
                borrow_rate=borrow,
                short_available=available,
                sleeve_metrics=record(sleeve, arm, c, protocol)["metrics"],
                sleeve_sessions=sleeve,
                sleeve_symbols=list(map(str, panel.symbols)),
                borrow_total=sum(r["borrow_cost"] for r in sleeve),
                sleeve_short_days=int(sum(r["short_exposure"] > 0 for r in sleeve)),
                sleeve_margin_breach_days=sum(r["margin_breaches"] for r in sleeve),
            )
            seal(output, f"{arm}-{c}bps{suffix}", value)
            if not suffix:
                records[arm][c] = value
            print(
                f"Completed {arm} {c}bps{suffix}: {value['metrics']['full']['net_return']:.2%}",
                flush=True,
            )
    decisions: dict[str, dict[str, Any]] = {
        a: {
            "status": "incumbent" if a == "core_daily" else "reference",
            "reason": "Unchanged comparison reference",
        }
        for a in ["core_daily", "conditional_reference"]
    }
    for arm in CANDIDATES:
        checks = []
        for c in [2, 6, 10]:
            for period in ["full", "walk_forward"]:
                for ref in ["core_daily", "conditional_reference"]:
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
        dd = records[arm][6]["metrics"]["full"]["max_drawdown"]
        ddpass = all(
            dd >= records[r][6]["metrics"]["full"]["max_drawdown"]
            for r in ["core_daily", "conditional_reference"]
        )
        stress = json.loads((output / f"{arm}-6bps-borrow6.json").read_text())
        stresspass = all(
            stress["metrics"][p]["net_return"] >= records[r][6]["metrics"][p]["net_return"]
            for p in ["full", "walk_forward"]
            for r in ["core_daily", "conditional_reference"]
        ) and all(
            stress["metrics"]["full"]["max_drawdown"]
            >= records[r][6]["metrics"]["full"]["max_drawdown"]
            for r in ["core_daily", "conditional_reference"]
        )
        keep = all(c["passes"] for c in checks) and ddpass and stresspass
        decisions[arm] = {
            "status": "retained" if keep else "rejected",
            "return_checks": checks,
            "drawdown_passes": ddpass,
            "borrow_stress_passes": stresspass,
            "annualized_log_return_delta_ci95": paired_monthly_interval(
                records[arm][6], records["core_daily"][6]
            ),
            "reason": "Passes both references and borrow stress"
            if keep
            else "Fails a reference return, drawdown or borrow-stress floor",
        }
    seal(output, "decisions", {"protocol_sha256": digest(protocol), "decisions": decisions})
    lines = [
        "# Index long/short sleeve study",
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
