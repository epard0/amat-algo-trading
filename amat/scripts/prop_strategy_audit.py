"""Audit and risk-constrained ETF rerun. No futures prices are synthesized."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np

import prop_strategy_sequence as old
from autoresearch.intraday.data import Panel

OUT = old.ROOT / "outputs/prop-strategy-audit-2026-10-14"
SCENARIOS = {"base": 0.01, "adverse": 0.03, "stress": 0.05}


def corrected_targets(bars, history, previous_close, family, q):
    result = old.targets(bars, history, previous_close, family, q)
    if family != "noise_momentum":
        return result
    o, h, low, c, volume = bars.T
    noise = np.mean(np.abs(history[:, :, 3] / history[:, :1, 0] - 1), axis=0)
    upper = max(o[0], previous_close) * (1 + noise)
    lower = min(o[0], previous_close) * (1 - noise)
    vwap = np.cumsum((h + low + c) / 3 * volume) / np.cumsum(volume)
    pos = 0
    for m in range(29, 389):
        if (m + 1) % 30 == 0:
            pos = 1 if c[m] > max(upper[m], vwap[m]) else -1 if c[m] < min(lower[m], vwap[m]) else 0
        result[m] = pos
    return result


def fee(shares):
    return max(1.0, abs(shares) * 0.005) if shares else 0.0


def raw_dollar_replay(bars, target, slip):
    """Same original exposure, whole shares and explicit commissions; no risk overlay."""
    quantity = np.floor(50000 / bars[0, 0])
    position = np.r_[0, target[:-1]] * quantity
    position[-1] = 0
    changes = np.diff(np.r_[0, position])
    gross = float(np.sum(position[:-1] * np.diff(bars[:, 0])))
    costs = float(np.abs(changes).sum() * slip + sum(fee(x) for x in changes if x))
    return gross, costs


def risk_day(bars, target, equity, floor, slip, stop_distance, *, risk_per_trade=100., daily_limit=200., max_entries=3, cooldown_minutes=15):
    """One-minute OHLC stop replay with gap fills and frozen sizing.

    Internal daily loss control at $200, hard risk stop, <=3 entries, >=15 minute
    cooldown. EOD trailing floor is a prop-inspired diagnostic on ETF prices.
    It is not an actual prop account. Conservative low/high marking precedes
    a possible favorable signal exit; no take-profit/stop ordering ambiguity.
    """
    cash, position, entry, stop = equity, 0, 0.0, 0.0
    start = equity
    trades, costs, gross, stopped, cooldown, fills, breach = 0, 0.0, 0.0, 0, 0, 0, False
    def transact(price, reason):
        nonlocal cash, position, costs, gross, fills, cooldown, breach
        commission = fee(position)
        cost = abs(position) * slip + commission
        profit = position * (price - entry)
        cash += profit - cost
        gross += profit
        costs += cost
        fills += 1
        position = 0
        cooldown = minute + cooldown_minutes
        breach = reason == "account_floor" or cash <= floor

    for minute in range(1, 390):
        o, h, low, c, _ = bars[minute]
        wanted = int(target[minute - 1]) if minute < 389 else 0
        # Existing orders can gap through a protective stop.
        if position:
            if (position > 0 and o <= stop) or (position < 0 and o >= stop):
                transact(o, "stop_gap")
                stopped += 1
            elif wanted != int(np.sign(position)):
                transact(o, "signal")
        if not position and wanted and minute < 389 and minute >= cooldown and trades < max_entries and cash - start > -daily_limit and not breach:
            risk_budget = min(risk_per_trade, daily_limit + cash - start, cash - floor - 25)
            quantity = int(min(risk_budget / (stop_distance + 2 * slip + 0.01), cash / o, bars[minute - 1, 4] * 0.01))
            if quantity > 0:
                position = wanted * quantity
                entry = o
                stop = o - wanted * stop_distance
                cost = quantity * slip + fee(quantity)
                cash -= cost
                costs += cost
                trades += 1
                fills += 1
        if position:
            # Account/daily guards become protective liquidation levels.
            threshold = max(floor, start - daily_limit)
            guard = entry + (threshold - cash + fee(position) + abs(position) * slip) / position
            active = max(stop, guard) if position > 0 else min(stop, guard)
            hit = low <= active if position > 0 else h >= active
            if hit:
                price = min(o, active) if position > 0 else max(o, active)
                reason = "account_floor" if threshold == floor and active == guard else "risk_stop"
                transact(price, reason)
                stopped += 1
        if cash <= floor:
            breach = True
        if breach:
            break
    return {"equity": cash, "pnl": cash - start, "gross": gross, "costs": costs,
            "trades": trades, "fills": fills, "stops": stopped, "breach": breach}


def train_q(panel, asset, slip):
    totals, counts = np.zeros((5, 3)), np.zeros((5, 3))
    for d, date in enumerate(panel.dates):
        if date > "2024-12-31" or not panel.valid[d, asset]:
            continue
        bars = panel.values[d, :, asset]
        quantity = max(1, int(50000 / bars[0, 0]))
        for m in range(29, 360, 30):
            state = int(np.digitize(bars[m, 3] / bars[m - 29, 3] - 1, [-0.002, -0.0005, 0.0005, 0.002]))
            forward = (bars[min(m + 31, 389), 0] - bars[m + 1, 0]) / bars[0, 0]
            cost = 2 * (quantity * slip + fee(quantity)) / (quantity * bars[0, 0])
            for a, direction in enumerate([-1, 0, 1]):
                totals[state, a] += direction * forward - abs(direction) * cost
                counts[state, a] += 1
    return totals / np.maximum(counts, 1)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = old.ROOT / "data/minute/alpaca-sip-2022-2026-09-14"
    protocol = {"commission": "$0.005/share, $1 minimum per order; IBKR Pro Fixed reference",
                "slippage_per_share_per_fill": SCENARIOS,
                "slippage_status": "Assumed spread+impact allowances; quotes unavailable, not measured",
                "equity": 50000, "risk_per_trade": 100, "daily_loss_control": 200,
                "max_entries_per_day": 3, "cooldown_minutes": 15,
                "max_gross_leverage": 1, "max_bar_volume_participation": 0.01,
                "trailing_risk_budget": 2000, "floor_update": "EOD high watermark minus 2000 capped at 50000",
                "stop_distance": "max($0.10, 0.15% of session open, 2 x prior-14-session mean minute high-low)",
                "test": ["2025-01-01", "2026-09-11"],
                "not_modeled": ["borrow availability and fees", "measured bid/ask", "futures contract execution", "payout criteria", "news-calendar filter"],
                "interpretation": "ETF execution diagnostics; prop-inspired risk constraint only; not a funded-account pass probability",
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "source_sha256": hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest(),
                "sources": ["https://www.interactivebrokers.com/en/pricing/commissions-stocks.php", "https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit", "https://concretumgroup.com/wp-content/uploads/2026/02/Beat-the-Market.pdf"]}
    (OUT / "protocol.json").write_text(json.dumps(protocol, indent=2))
    panel = Panel(source, start="2022-01-01", end="2026-09-13")
    rows, days = [], []
    for family in old.FAMILIES:
        print(f"Auditing {family}", flush=True)
        for symbol in ("SPY", "QQQ", "IWM"):
            a = panel.symbols.index(symbol)
            for scenario, slip in SCENARIOS.items():
                q = train_q(panel, a, slip) if family == "tabular_rl" else None
                if q is not None:
                    (OUT / f"q-{symbol}-{scenario}.json").write_text(json.dumps(q.tolist()))
                equity, high, floor, alive = 50000.0, 50000.0, 48000.0, True
                history, previous = [], None
                gross_old, cents_old, total_cost, trades, net_series, stopped = 0., 0., 0., 0, [], 0
                for d, date in enumerate(panel.dates):
                    valid = panel.valid[d, a]
                    bars = panel.values[d, :, a]
                    eligible = valid and len(history) >= 14 and previous is not None
                    if date >= "2025-01-01":
                        pnl = 0.
                        if eligible:
                            h = np.array(history[-14:])
                            original = old.targets(bars, h, previous, family, q if family != "tabular_rl" else np.array(json.loads((old.OUT / f"q-{symbol}.json").read_text())))
                            g, cost = raw_dollar_replay(bars, original, slip)
                            gross_old += g
                            cents_old += g - cost
                            if alive:
                                target = corrected_targets(bars, h, previous, family, q)
                                distance = max(.10, .0015 * bars[0, 0], 2 * float(np.mean(h[:, :, 1] - h[:, :, 2])))
                                result = risk_day(bars, target, equity, floor, slip, distance)
                                equity = result["equity"]
                                pnl = result["pnl"]
                                total_cost += result["costs"]
                                trades += result["trades"]
                                stopped += result["stops"]
                                alive = not result["breach"]
                                high = max(high, equity)
                                floor = min(50000., max(floor, high - 2000))
                        days.append({"family": family, "symbol": symbol, "scenario": scenario, "date": date,
                                     "pnl": pnl, "equity": equity, "floor": floor, "alive": alive, "eligible": bool(eligible)})
                        net_series.append(pnl)
                    previous = float(bars[-1, 3]) if valid else None
                    if valid:
                        history.append(bars.copy())
                eq = np.r_[50000., 50000. + np.cumsum(net_series)]
                rows.append({"family": family, "symbol": symbol, "scenario": scenario,
                             "original_gross_dollars": gross_old, "original_with_cents_costs_dollars": cents_old,
                             "corrected_net_dollars": equity - 50000, "costs_dollars": total_cost,
                             "max_drawdown_dollars": float(np.max(np.maximum.accumulate(eq) - eq)),
                             "entries": trades, "stops": stopped, "risk_budget_breached": not alive, "remaining_floor_buffer_dollars": equity - floor,
                             "risk_budget_exhausted": bool(equity - floor <= 26)})
    (OUT / "summary.json").write_text(json.dumps(rows, indent=2))
    for name, records in (("summary.csv", rows), ("daily.csv", days)):
        with (OUT / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(records[0]))
            w.writeheader()
            w.writerows(records)
    report = ["# Strategy audit and constrained rerun", "", "ETF diagnostics only. Original artifacts preserved. No futures or funded payout claims. Cost-only ablation keeps original rules; corrected run changes rules/risk and therefore is a new exploratory experiment. No parameters chosen by maximizing test profit.", "", "| Family | Symbol | Original gross $ | Original with cents costs $ | Corrected net $ | Entries | Budget exhausted |", "|---|---|---:|---:|---:|---:|---|"]
    for r in rows:
        if r["scenario"] == "base":
            report.append(f"| {r['family']} | {r['symbol']} | {r['original_gross_dollars']:.2f} | {r['original_with_cents_costs_dollars']:.2f} | {r['corrected_net_dollars']:.2f} | {r['entries']} | {r['risk_budget_exhausted']} |")
    report += ["", "Base/adverse/stress: $0.01/$0.03/$0.05 spread+impact per share per fill, plus $0.005/share commission ($1 order minimum). These are explicit uncalibrated scenarios, not observed spread estimates. All-in costs must eventually be calibrated from quotes/fills. Integer shares; no leverage; $100 risk per entry; $200 daily guard; three entries maximum; 15-minute cooldown; intrabar protective stops with adverse gap execution; conservative EOD trailing risk budget. Gap/slippage can exceed risk targets. A breached account stays stopped, without free resets. Budget exhausted means <=$26 remains above the floor: the $25 safety reserve effectively prevents further entries. No breach is not evidence of success; a stalled account is unusable.", "", "Corrections: noise bands use max/min(open, previous close) for overnight gaps. RL reward uses the scenario cost instead of hard-coded 2bp round trip; policy remains a basic contextual bandit. The risk overlay is an additional experiment, not part of the original papers. Mean reversion's repeated entries are constrained by actual fill-based cooldown and entry limits. Positions and stops use execution prices, not signal closes. Cash includes both entry and exit costs.", "", "Limitations: previous-close input is last regular-session minute trade close, not official auction close; missing/early-close sessions abstain. No borrow checks, quote data, precise tick path, dividends, platform outages, futures basis/roll or payout rules. EOD drawdown uses conservative minute OHLC guards, not tick replay. RL training fits unrestricted half-hour actions, so the risk overlay introduces train/execution mismatch. Historical test dates were already examined: rerun is exploratory, not independent validation."]
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n")
    print(OUT / "REPORT.md")


if __name__ == "__main__":
    main()
