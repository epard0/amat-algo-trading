"""Read-only inventory and reproducible calculations for the alpha review.

Does not run strategies, rewrite historical evidence, or contact a broker.
Run with the workspace's amat/.venv/bin/python.
"""
from pathlib import Path
from collections import Counter
from decimal import Decimal
import csv
import hashlib
import json
import re

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
ROOTS = [WORKSPACE / "amat/outputs", WORKSPACE / "amat/docs",
         WORKSPACE / "executor/state", WORKSPACE / "docs",
         Path("/home/supasuge/Documents/amat-research"),
         Path("/home/supasuge/Documents/amat-p13-workspace/amat/outputs")]
inventory, reports, totals = [], [], []
for root in ROOTS:
    files = [p for p in root.rglob("*") if p.is_file() and not p.is_relative_to(HERE)]
    totals.append({"root": str(root), "files": len(files),
                   "bytes": sum(p.stat().st_size for p in files)})
    for p in files:
        inventory.append({"path": str(p), "bytes": p.stat().st_size,
                          "suffix": p.suffix, "mtime_ns": p.stat().st_mtime_ns})
        if p.suffix == ".md":
            raw = p.read_bytes()
            lines = raw.decode("utf-8", errors="replace").splitlines()
            excerpts = [{"line": i + 1, "text": line} for i, line in enumerate(lines)
                        if re.search(r"CAGR|Sharpe|drawdown|baseline|alpha|suspend|failed|not implemented", line, re.I)]
            reports.append({"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(),
                            "lines": len(lines), "evidence_excerpts": excerpts})
with (HERE / "inventory.csv").open("w") as out:
    writer = csv.DictWriter(out, fieldnames=["path", "bytes", "suffix", "mtime_ns"])
    writer.writeheader()
    writer.writerows(inventory)
(HERE / "report-screen.json").write_text(json.dumps(reports, indent=2))

p13 = ROOTS[-1] / "genetic-patterns/2026-09-30-p13-foundation/study"
records_path = p13 / "runs/baseline-2bps/state-and-execution.jsonl"
records = [json.loads(line) for line in records_path.open()]
returns = [(Decimal(records[i]["equity"]) / Decimal(records[i-1]["equity"]) - 1, i)
           for i in range(1, len(records))]
worst, index = min(returns)
before, after = records[index-1], records[index]
marks = {s: Decimal(mark) for s, _, mark in after["held_positions"]}
attribution = []
assert not after["fills"] and not Decimal(after["attribution"]["distributions"])
for symbol, quantity, mark in before["held_positions"]:
    quantity, mark = Decimal(quantity), Decimal(mark)
    pnl = quantity * (marks[symbol] - mark)
    attribution.append({"symbol": symbol, "prior_weight": float(quantity * mark / Decimal(before["equity"])),
                        "pnl": float(pnl), "portfolio_return_contribution": float(pnl / Decimal(before["equity"]))})
assert abs(sum(Decimal(str(x["pnl"])) for x in attribution) - Decimal(after["attribution"]["market_pnl"])) < Decimal("0.000001")
misses = Counter(item["reason"] for row in records for item in row["missed"])
comparisons = list(csv.DictReader((p13 / "comparison.csv").open()))
verified_runs = []
for row in comparisons:
    if row["scope"] != "full_history":
        continue
    run_records = [json.loads(line) for line in
                   (p13 / "runs" / row["run"] / "state-and-execution.jsonl").open()]
    measured = float(run_records[-1]["equity"]) / float(run_records[0]["equity"]) - 1
    assert abs(measured - float(row["net_return"])) < 1e-12
    verified_runs.append(row["run"])
assert len(verified_runs) == 16
source_comparison = {}
for relative in ["amat/backtest/runner.py", "amat/backtest/exits.py", "autoresearch/genetic/walkforward_study.py"]:
    a = WORKSPACE / "amat/src" / relative
    b = Path("/home/supasuge/Documents/amat-p13-workspace/amat/src") / relative
    source_comparison[relative] = {"checkout_sha256": hashlib.sha256(a.read_bytes()).hexdigest(),
                                   "p13_sha256": hashlib.sha256(b.read_bytes()).hexdigest()}
evidence = {"scope": totals, "markdown_files_screened": len(reports),
            "unique_markdown_contents": len({x["sha256"] for x in reports}),
            "p13_baseline": {"sessions": len(records), "fills": sum(len(x["fills"]) for x in records),
                             "missed_records": dict(misses), "worst_day": after["session"],
                             "worst_day_return": float(worst), "worst_day_attribution": attribution},
            "p13_comparisons": comparisons, "verified_p13_full_history_returns": verified_runs,
            "source_comparison": source_comparison,
            "limits": "Inventory is exhaustive within these roots; markdown screening is mechanical. It is not a claim of manually reviewing every raw file or rerunning every historical strategy. P13 is a separate isolated runtime."}
(HERE / "calculations.json").write_text(json.dumps(evidence, indent=2))
print(json.dumps({k: v for k, v in evidence.items() if k not in {"p13_comparisons", "source_comparison"}}, indent=2))
