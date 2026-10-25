"""Standalone, escaped intraday research report and complete machine-readable export."""

# ruff: noqa: E501, RUF001 - standalone HTML templates
from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from autoresearch.workspace import digest_file, write_json


def render(root: Path, state: dict[str, Any]) -> None:
    write_json(root / "report.json", state)
    (root / "report.sha256").write_text(digest_file(root / "report.json"))
    rows = state["experiments"]
    table, details, export = [], [], []
    for row in rows:
        m = row["result"]["train"]
        recipe = row["recipe"]
        review = row.get("review", {})
        score = row["result"].get("score")
        score_text = f"{score:.3f}" if score is not None else "unavailable"
        style = html.escape(recipe["style"])
        validation = row.get("validation", {})
        later_return = f"{validation['annualized_return']:.2%}" if validation else "pending"
        later_sharpe = f"{validation['sharpe']:.2f}" if validation else "pending"
        table.append(
            f'<tr><td>{row["id"]}</td><td><a href="#s{row["id"]}">{style}</a></td><td>{recipe["decision_minutes"]}m / {recipe["max_hold_minutes"]}m</td><td>{html.escape(row["source"])}</td><td>{row["status"]}</td><td>{m["annualized_return"]:.2%}</td><td>{m["sharpe"]:.2f}</td><td>{m["max_drawdown"]:.2%}</td><td>{m["total_trades"]:,}</td><td>{score_text}</td><td>{later_return}</td><td>{later_sharpe}</td></tr>'
        )
        summary = review.get("summary", {})
        review_html = f"<h4>{html.escape(summary.get('headline', 'LLM review unavailable'))}</h4><p>{html.escape(summary.get('explanation', review.get('reason', 'Pending')))}</p>"
        details.append(
            f'<details id="s{row["id"]}"><summary>#{row["id"]} {style} · {row["status"]}</summary><p>{html.escape(row.get("hypothesis", "Baseline"))}</p><p>Parent: {html.escape(row.get("parent", ""))}</p>{review_html}<h4>Full metrics, configuration, and failure analysis</h4><pre>{html.escape(json.dumps(row, indent=2))}</pre><p><a href="runs/{row["candidate"]}/primary/trades.csv">Trade log</a> · <a href="runs/{row["candidate"]}/primary/daily.json">Daily returns</a></p></details>'
        )
        export.append(
            {
                "experiment": row["id"],
                "candidate": row["candidate"],
                "parent": row["parent"],
                "style": recipe["style"],
                "decision_minutes": recipe["decision_minutes"],
                "max_hold_minutes": recipe["max_hold_minutes"],
                "direction": recipe["direction"],
                "source": row["source"],
                "status": row["status"],
                "score": score,
                "validation_annualized_return": validation.get("annualized_return"),
                "validation_sharpe": validation.get("sharpe"),
                **m,
            }
        )
    if export:
        with (root / "metrics.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(export[0]))
            writer.writeheader()
            writer.writerows(export)
    failure_rows = [row for row in rows if row["status"] == "discard"]
    write_json(root / "failed-strategies.json", failure_rows)
    completed = max(0, len(rows) - 1)
    body = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Intraday strategy research</title>
<style>body{{font:16px system-ui;background:#101820;color:#e5eef3;margin:3vw;line-height:1.5}}a{{color:#72d6cd}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid #334652;text-align:left}}th{{background:#243642;position:sticky;top:0}}details{{background:#1b2a35;padding:16px;margin:14px 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}.scroll{{overflow-x:auto}}h1{{font-size:36px}}.note{{color:#adc1cf;max-width:1000px}}</style>
<h1>Intraday strategy research</h1><p>{completed}/{state["requested"]} strategies completed · {html.escape(state["status"])} · {sum(r["status"] == "keep" for r in rows)} escalated</p>
<p>Retained best: {html.escape(state["incumbent"])}</p><p class="note">Actual one-minute SIP bars. Development 2022–2024; 2025 is checked only after search. The initial benchmark is a new E73-inspired minute recipe, not the promoted daily E73. Arithmetic daily Sharpe × √252, zero cash yield; results are simulated. Full replacement recipes always descend from the retained best. No execution authority.</p>
<p class="note">Incomplete asset-days and early closes abstain and remain in the daily return calendar. Signals reset each day and fill at subsequent opens. Fixed $100,000 reference capacity check; fractional notional sleeves, 2 bps each side, 6 bps stress. Shorts use a 1 bp entry fee proxy without observed borrow availability. No quote/order-book fill validation. Exposure matching is retrospective. Adaptive search and repeated testing limit inference.</p>
<p><a href="metrics.csv">Metrics CSV</a> · <a href="report.json">Full report JSON</a> · <a href="failed-strategies.json">All failed strategies</a> · <a href="data-quality.json">Data quality</a> · <a href="final-validation.json">Post-search 2025 comparison</a></p>
<div class="scroll"><table><thead><tr><th>#</th><th>Strategy</th><th>Decision / max hold</th><th>Proposed by</th><th>Decision</th><th>Annual return</th><th>Sharpe</th><th>Drawdown</th><th>Trades</th><th>Score</th><th>2025 return</th><th>2025 Sharpe</th></tr></thead><tbody>{"".join(table)}</tbody></table></div>{"".join(details)}</html>"""
    (root / "report.html").write_text(body)
    lines = [
        "# Intraday research results",
        f"Completed: {completed}/{state['requested']}; status: {state['status']}",
        f"Retained best: {state['incumbent']}",
        "",
        "| # | Style | Decision/hold | Status | Annual return | Sharpe | Trades |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in rows:
        m = row["result"]["train"]
        r = row["recipe"]
        lines.append(
            f"| {row['id']} | {r['style']} | {r['decision_minutes']}/{r['max_hold_minutes']}m | {row['status']} | {m['annualized_return']:.2%} | {m['sharpe']:.2f} | {m['total_trades']} |"
        )
    (root / "report.md").write_text("\n".join(lines) + "\n")
