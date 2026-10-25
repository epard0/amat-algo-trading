"""Frozen small candidate study for breakout and momentum; ETF diagnostics only."""
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

import prop_strategy_audit as audit
from autoresearch.intraday.data import Panel

OUT = audit.old.ROOT / "outputs/prop-candidate-improvements-2026-10-14"
CANDIDATES = [{"family": family, "risk": risk, "stop": stop, "max_entries": 1}
              for family in ("opening_range", "noise_momentum")
              for risk in (25., 50.) for stop in ("fixed", "structure")]
PERIODS = {"train": ("2022-01-01", "2023-12-31"), "validation": ("2024-01-01", "2024-12-31"),
           "test": ("2025-01-01", "2026-09-13")}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    protocol = {"candidates": CANDIDATES, "periods": PERIODS,
                "scenarios": {"base": .01, "adverse": .03},
                "selection": "train and validation both profitable at adverse costs; >=30 entries each; DD < $1000; remaining floor buffer > $500",
                "execution": "Inherited whole-share ETF simulation; $50K cash/$2K EOD floor; max one entry/day; daily loss guard 2x risk",
                "stops": "fixed=max($0.10,0.15% open); structure=opening range width for ORB or prior14 mean daily range for momentum; fixed at entry",
                "status": "Exploratory ETF diagnosis, not futures validation; historical dates previously examined",
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "engine_sha256": hashlib.sha256(Path(audit.__file__).read_bytes()).hexdigest()}
    (OUT / "protocol.json").write_text(json.dumps(protocol, indent=2))
    panel = Panel(audit.old.ROOT / "data/minute/alpaca-sip-2022-2026-09-14", start="2022-01-01", end="2026-09-13")
    a = panel.symbols.index("QQQ")
    history, previous, sessions = [], None, []
    for d, date in enumerate(panel.dates):
        bars = panel.values[d, :, a]
        eligible = panel.valid[d, a] and len(history) >= 14 and previous is not None
        if eligible:
            h = np.array(history[-14:])
            signals = {f: audit.corrected_targets(bars, h, previous, f, None) for f in ("opening_range", "noise_momentum")}
            distances = {"fixed": max(.10, .0015 * bars[0, 0]),
                         "opening_range": max(.10, bars[:15, 1].max() - bars[:15, 2].min()),
                         "noise_momentum": max(.10, float(np.mean(h[:, :, 1].max(axis=1) - h[:, :, 2].min(axis=1))))}
            sessions.append((date, bars, signals, distances))
        else:
            sessions.append((date, None, None, None))
        previous = float(bars[-1, 3]) if panel.valid[d, a] else None
        if panel.valid[d, a]:
            history.append(bars.copy())
    rows, records = [], []
    for i, candidate in enumerate(CANDIDATES):
        print(f"Candidate {i+1}/{len(CANDIDATES)}: {candidate}", flush=True)
        for scenario, slip in protocol["scenarios"].items():
            for period, (start, end) in PERIODS.items():
                cash, high, floor, alive, entries = 50000., 50000., 48000., True, 0
                equity, pnls = [cash], []
                for date, bars, signals, distances in sessions:
                    if not start <= date <= end:
                        continue
                    pnl = 0.
                    if bars is not None and alive:
                        distance = distances["fixed" if candidate["stop"] == "fixed" else candidate["family"]]
                        r = audit.risk_day(bars, signals[candidate["family"]], cash, floor, slip, distance,
                                           risk_per_trade=candidate["risk"], daily_limit=2*candidate["risk"], max_entries=1)
                        cash, pnl = r["equity"], r["pnl"]
                        alive = not r["breach"]
                        entries += r["trades"]
                        high = max(high, cash)
                        floor = min(50000., max(floor, high - 2000))
                    equity.append(cash)
                    pnls.append(pnl)
                    records.append({"candidate": i, "scenario": scenario, "period": period, "date": date,
                                    "equity": cash, "pnl": pnl, "floor": floor})
                e = np.array(equity)
                p = np.array(pnls)
                rows.append({"candidate": i, **candidate, "scenario": scenario, "period": period,
                             "pnl": cash-50000, "max_dd": float(np.max(np.maximum.accumulate(e)-e)),
                             "floor_buffer": cash-floor, "entries": entries,
                             "sharpe": float(p.mean()/p.std(ddof=1)*np.sqrt(252)) if p.std() else None,
                             "breached": not alive})
    qualified = []
    for i in range(len(CANDIDATES)):
        fit = [r for r in rows if r["candidate"] == i and r["scenario"] == "adverse" and r["period"] in ("train", "validation")]
        if all(r["pnl"] > 0 and r["entries"] >= 30 and r["max_dd"] < 1000 and r["floor_buffer"] > 500 and not r["breached"] for r in fit):
            qualified.append(i)
    selected = max(qualified, key=lambda i: min(r["sharpe"] for r in rows if r["candidate"]==i and r["scenario"]=="adverse" and r["period"] in ("train", "validation"))) if qualified else None
    (OUT / "selection.json").write_text(json.dumps({"qualified": qualified, "selected": selected, "selection_uses_test": False,
                "deployment": "DISABLED", "reason": "ETF proxies and previously inspected history cannot authorize futures deployment"}, indent=2))
    (OUT / "summary.json").write_text(json.dumps(rows, indent=2))
    for name, data in (("summary.csv", rows), ("daily.csv", records)):
        with (OUT / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    text = ["# Focused improvement study", "", "QQQ ETF diagnostics; no futures results. Risk and stop alternatives frozen before execution. No parameter chosen using test results. Selection uses 2022–2023 training and 2024 validation, at adverse costs. Historical periods have prior research exposure.", "", f"Qualified candidates: {qualified}; selected: {selected}. Deployment disabled.", "", "| ID | Strategy | Risk $ | Stop | Train adverse $ | Validation adverse $ | Test adverse $ | Test DD $ | Test floor buffer $ |", "|---|---|---:|---|---:|---:|---:|---:|---:|"]
    for i, c in enumerate(CANDIDATES):
        results = {r["period"]: r for r in rows if r["candidate"]==i and r["scenario"]=="adverse"}
        t = results["test"]
        text.append(f"| {i} | {c['family']} | {c['risk']} | {c['stop']} | {results['train']['pnl']:.2f} | {results['validation']['pnl']:.2f} | {t['pnl']:.2f} | {t['max_dd']:.2f} | {t['floor_buffer']:.2f} |")
    text += ["", "Structure stop is a fixed protective distance based on the opening range width (ORB) or prior average daily range (momentum); it is not a dynamically maintained opposite-boundary stop. Slippage assumptions, short-side borrow and OHLC uncertainty remain unresolved. One position/entry per day avoids stop-driven re-entry. No joint portfolio test; combining candidates would share the same account risk budget."]
    (OUT / "REPORT.md").write_text("\n".join(text)+"\n")
    print(OUT / "REPORT.md")


if __name__ == "__main__":
    main()
