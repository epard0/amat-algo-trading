"""Sequential ETF proxy experiments; no broker access or futures profitability claims.

Run from amat: .venv/bin/python scripts/prop_strategy_sequence.py
Signals observe completed bars and execute at next opens. Flat at 15:59 open.
All rules are exploratory approximations, not exact paper replications.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from autoresearch.intraday.data import Panel

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/prop-strategy-sequence-2026-10-14"
FAMILIES = ("noise_momentum", "opening_range", "closing_momentum", "vwap_reversion", "tabular_rl")


def targets(bars, history, previous_close, family, q=None):
    """Target at each bar close; position applied at following bar open."""
    o, h, low, c, volume = bars.T
    vwap = np.cumsum((h + low + c) / 3 * volume) / np.cumsum(volume)
    result = np.zeros(390)
    if family == "noise_momentum":
        # Fourteen complete prior sessions; no overnight gap adjustment.
        noise = np.mean(np.abs(history[:, :, 3] / history[:, :1, 0] - 1), axis=0)
        pos = 0
        for m in range(29, 389):
            if (m + 1) % 30 == 0:
                pos = 1 if c[m] > o[0] * (1 + noise[m]) and c[m] > vwap[m] else -1 if c[m] < o[0] * (1 - noise[m]) and c[m] < vwap[m] else 0
            result[m] = pos
    elif family == "opening_range":
        top, bottom = h[:15].max(), low[:15].min()
        expected = np.mean(history[:, :15, 4].sum(axis=1))
        prior_vol = np.mean(np.abs(history[:, -1, 3] / history[:, 0, 0] - 1))
        width = (top - bottom) / o[0]
        # Activity >= prior mean; avoid unusually narrow/wide ranges.
        if volume[:15].sum() >= expected and 0.25 * prior_vol <= width <= 2 * prior_vol:
            pos, used = 0, False
            for m in range(14, 389):
                if not used and m < 120 and (c[m] > top or c[m] < bottom):
                    pos, used = (1 if c[m] > top else -1), True
                elif pos and ((pos == 1 and c[m] < bottom) or (pos == -1 and c[m] > top)):
                    pos = 0
                result[m] = pos
    elif family == "closing_momentum":
        result[359:389] = np.sign(c[29] / previous_close - 1)
    elif family == "vwap_reversion":
        pos, entered, entry = 0, 0, 0.0
        for m in range(59, 389):
            if pos and (pos * (c[m] - vwap[m]) >= 0 or m - entered >= 30 or pos * (c[m] / entry - 1) <= -0.003):
                pos = 0
            elif not pos and m < 330 and abs(c[m] / vwap[m] - 1) >= 0.002:
                pos, entered, entry = int(-np.sign(c[m] - vwap[m])), m, c[m]
            result[m] = pos
    elif family == "tabular_rl":
        for m in range(29, 360, 30):
            state = int(np.digitize(c[m] / c[m - 29] - 1, [-0.002, -0.0005, 0.0005, 0.002]))
            result[m:min(m + 30, 389)] = [-1, 0, 1][int(np.argmax(q[state]))]
    else:
        raise ValueError(family)
    return result


def replay(bars, target, cost_bps):
    """Dollar-neutral arithmetic P&L per fixed $1 notional; turnover charged at fills.

    Fractional ETF shares, no leverage. Close-based exits are not resting stops.
    """
    position = np.zeros(390)
    position[1:] = target[:-1]
    position[-1] = 0  # liquidation at 15:59, avoiding unknown closing-auction fills
    opens = bars[:, 0]
    units = position / opens[0]
    gross = float(np.sum(units[:-1] * np.diff(opens)))
    turnover = float(np.sum(np.abs(np.diff(np.r_[0, units])) * opens))
    return gross - turnover * cost_bps / 10000, turnover, int(np.count_nonzero(np.diff(np.r_[0, position])))


def train_q(panel, asset):
    # Contextual bandit (one-step Q-learning): finite action space and no exploration
    # on test data. This is a basic RL baseline, not FinRL or a deep-RL replication.
    totals, counts = np.zeros((5, 3)), np.zeros((5, 3))
    for d, date in enumerate(panel.dates):
        if date > "2024-12-31" or not panel.valid[d, asset]:
            continue
        bars = panel.values[d, :, asset]
        for m in range(29, 360, 30):
            state = int(np.digitize(bars[m, 3] / bars[m - 29, 3] - 1, [-0.002, -0.0005, 0.0005, 0.002]))
            forward = (bars[min(m + 31, 389), 0] - bars[m + 1, 0]) / bars[0, 0]
            for a, direction in enumerate([-1, 0, 1]):
                totals[state, a] += direction * forward - abs(direction) * 0.0002
                counts[state, a] += 1
    return totals / np.maximum(counts, 1)


def summarize(values):
    x = np.asarray(values)
    equity = np.r_[0.0, np.cumsum(x)]
    return {"sessions": len(x), "net_pnl_pct_fixed_notional": float(x.sum() * 100),
            "annualized_mean_pct": float(x.mean() * 252 * 100),
            "sharpe": float(x.mean() / x.std(ddof=1) * np.sqrt(252)) if x.std() else None,
            "max_drawdown_pct_fixed_notional": float(np.max(np.maximum.accumulate(equity) - equity) * 100)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = ROOT / "data/minute/alpaca-sip-2022-2026-09-14"
    protocol = {"families": FAMILIES, "symbols": ["SPY", "QQQ", "IWM"],
                "train_end": "2024-12-31", "test_start": "2025-01-01", "test_end": "2026-09-13",
                "one_way_cost_bps": [1, 3, 5], "no_parameter_search": True,
                "status": "ETF proxy exploration; dates have prior workspace research exposure",
                "source_manifest_sha256": hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (OUT / "protocol.json").write_text(json.dumps(protocol, indent=2))
    panel = Panel(source, start="2022-01-01", end="2026-09-13")
    (OUT / "data-audit.json").write_text(json.dumps(panel.audit, indent=2))
    rows, daily = [], []
    for family in FAMILIES:
        print(f"Running {family}", flush=True)
        for symbol in protocol["symbols"]:
            a = panel.symbols.index(symbol)
            q = train_q(panel, a) if family == "tabular_rl" else None
            if q is not None:
                (OUT / f"q-{symbol}.json").write_text(json.dumps(q.tolist()))
            histories = []
            previous_close = None
            for d, date in enumerate(panel.dates):
                valid = panel.valid[d, a]
                eligible = valid and len(histories) >= 14 and previous_close is not None
                bars = panel.values[d, :, a]
                t = targets(bars, np.array(histories[-14:]), previous_close, family, q) if eligible else np.zeros(390)
                for cost in protocol["one_way_cost_bps"]:
                    pnl, turnover, fills = replay(bars, t, cost) if eligible else (0.0, 0.0, 0)
                    daily.append({"family": family, "symbol": symbol, "date": date, "cost_bps": cost,
                                  "eligible": bool(eligible), "pnl": pnl, "turnover": turnover, "fills": fills})
                # A missing session breaks previous-close availability.
                previous_close = float(bars[-1, 3]) if valid else None
                if valid:
                    histories.append(bars.copy())
            for cost in protocol["one_way_cost_bps"]:
                for period in ("train", "test"):
                    selected = [r for r in daily if r["family"] == family and r["symbol"] == symbol and r["cost_bps"] == cost and (r["date"] < "2025-01-01") == (period == "train")]
                    rows.append({"family": family, "symbol": symbol, "cost_bps": cost, "period": period,
                                 **summarize([r["pnl"] for r in selected]), "fills": sum(r["fills"] for r in selected),
                                 "eligible_sessions": sum(r["eligible"] for r in selected)})
        (OUT / "summary.json").write_text(json.dumps(rows, indent=2))
    for name, records in (("summary.csv", rows), ("daily.csv", daily)):
        with (OUT / name).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    report = ["# Sequential strategy experiments", "", "ETF proxies only; no futures or prop payout validation. Fixed-notional, unlevered long/short ETF simulation. Rules are approximations, not exact paper replications. Costs include an assumed all-in one-way bps allowance; borrow and fractional-share execution are not verified. Close-triggered exits execute next open. No pristine holdout: historical dates have prior research exposure.", "", "| Family | Symbol | Test net P&L % | Sharpe | Max DD % |", "|---|---|---:|---:|---:|"]
    for r in rows:
        if r["period"] == "test" and r["cost_bps"] == 1:
            report.append(f"| {r['family']} | {r['symbol']} | {r['net_pnl_pct_fixed_notional']:.2f} | {r['sharpe'] if r['sharpe'] is None else round(r['sharpe'], 2)} | {r['max_drawdown_pct_fixed_notional']:.2f} |")
    report += ["", "Not run: order-book prediction and market making require quotes/order-book events, latency and queue assumptions. Stocks-in-play ORB requires a point-in-time equity universe and activity data. Deep RL/FinRL not replicated; tabular contextual-bandit baseline tested instead. Futures and firm-rule evaluation require contract-level futures data and a selected account. No broker orders submitted.", "", "Training results for tabular RL are in-sample fitted diagnostics. Other rule parameters are fixed. Test signals and Q tables never fit test outcomes. Missing/invalid/early-close asset sessions abstain and remain as zero-return days; see data-audit.json. Zero returns reflect abstention, not forward-filled prices."]
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n")
    print(OUT / "REPORT.md")


if __name__ == "__main__":
    main()
