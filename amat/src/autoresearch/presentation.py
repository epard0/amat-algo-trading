"""Offline comparison reports from recorded backtest decisions and LLM reviews."""

# ruff: noqa: E501 - embedded HTML and CSS remain readable as complete elements

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from autoresearch.observability import agent_panel


def batch_results(rows: list[dict[str, Any]], batch: dict[str, Any]) -> dict[str, Any]:
    attempts = [row for row in rows if row["id"] > batch.get("start_id", 0) and row["parent"]]
    completed = [row for row in attempts if row["status"] in ("keep", "discard") and row["result"]]
    return {
        "requested": batch.get("requested", len(completed)),
        "completed": len({row["candidate"] for row in completed}),
        "experiment_ids": [row["id"] for row in completed],
        "failed_attempts": len(attempts) - len(completed),
        "escalated": sum(row["status"] == "keep" for row in completed),
    }


def number(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%" if percent else f"{float(value):.3f}"


def escalation_reason(row: dict[str, Any], result: dict[str, Any]) -> str:
    if row["status"] == "baseline":
        return "Starting benchmark. Each new strategy competes against the best retained strategy."
    if row["status"] == "keep":
        return "Beat the previous best by the required score margin and passed quality and uncertainty checks."
    if not result:
        return str(row.get("error") or "No completed backtest.")
    reasons = [str(gate).replace("_", " ") for gate in result.get("failed_gates", [])]
    score, control = result.get("score"), result.get("selection_control_score")
    if score is None or control is None or score <= control + result.get("required_improvement", 0):
        reasons.append("did not clear the required improvement over the previous best")
    elif not result.get("uncertainty", {}).get("passes", False):
        reasons.append("improvement did not pass the uncertainty check")
    return "; ".join(reasons) or "Did not pass the retention checks."


def review_html(envelope: dict[str, Any]) -> str:
    if envelope.get("status") != "complete":
        return (
            "<p>LLM review unavailable: "
            + escape(envelope.get("reason", "not yet generated"))
            + "</p>"
        )
    review = envelope["summary"]
    paragraphs = [
        "<h3>" + escape(review["verdict"].upper() + ": " + review["headline"]) + "</h3>",
        "<p>" + escape(review["explanation"]) + "</p>",
    ]
    for label, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses")):
        paragraphs.append("<h4>" + label + "</h4><ul>")
        paragraphs.extend("<li>" + escape(item) + "</li>" for item in review[key])
        paragraphs.append("</ul>")
    paragraphs.extend(
        [
            "<p><b>Next experiment:</b> " + escape(review["next_experiment"]) + "</p>",
            "<p><b>Evidence limits:</b> " + escape(review["evidence_limits"]) + "</p>",
            '<p class="muted">LLM: '
            + escape(envelope["provider"] + " / " + envelope["model"])
            + ". Commentary; the recorded backtest checks decide escalation.</p>",
        ]
    )
    return "".join(paragraphs)


def write_html_report(root: Path, report: dict[str, Any]) -> None:
    rows = report["experiments"]
    names: dict[str, str] = {}
    for row in rows:
        if row["status"] in ("keep", "discard") and row["candidate"] not in names:
            names[row["candidate"]] = f"Strategy {len(names) + 1}"
    period = ""
    for row in rows:
        if row["status"] == "baseline":
            promotion = report.get("starting_strategy")
            names[row["candidate"]] = (
                "Baseline · " + promotion["label"] if promotion else "Baseline"
            )
            metrics = json.loads(row["result"])["train"]
            if metrics.get("first_session") and metrics.get("last_session"):
                period = (
                    "Measured period: "
                    + metrics["first_session"]
                    + " to "
                    + metrics["last_session"]
                )
    incremental = any(
        json.loads(row["result"]).get("score_version") == "exposure-matched-incremental/1"
        for row in rows
        if row.get("result")
    )
    score_explanation = (
        "The research score is exposure-matched Sharpe improvement over the fixed original "
        "baseline, less 0.01 per added complexity unit. Baseline = 0. The more exposed strategy "
        "is scaled down retrospectively; this is a development comparison, not a tradeable "
        "benchmark. A positive score alone does not pass quality or uncertainty checks."
        if incremental
        else "The research score combines yearly risk-adjusted performance with penalties for weak "
        "years, drawdown, costs and complexity."
    )
    batch = report["batch"]
    selected = [
        row for row in rows if row["status"] == "baseline" or row["id"] in batch["experiment_ids"]
    ]
    reviews = {item["experiment"]: item for item in report["llm_summaries"]}
    table, details = [], []
    for row in selected:
        result = json.loads(row["result"]) if row["result"] else {}
        proposal = json.loads(row["proposal"]) if row["proposal"] != "baseline" else {}
        train, stress = result.get("train", {}), result.get("stress", {})
        name = names[row["candidate"]]
        decision = {"keep": "Escalated", "discard": "Not escalated", "baseline": "Benchmark"}[
            row["status"]
        ]
        components = result.get("score_components", {})
        table.append(
            f'<tr><td><a href="#trial-{row["id"]}">{escape(name)}</a></td>'
            f"<td>{number(train.get('annualized_return'), percent=True)}</td>"
            f"<td>{number(train.get('sharpe'))}</td>"
            f"<td>{number(train.get('max_drawdown'), percent=True)}</td>"
            f"<td>{number(stress.get('sharpe'))}</td><td>{number(result.get('score'))}</td>"
            f"<td>{number(components.get('matched_sharpe_difference'))}</td>"
            f"<td>{number(components.get('incremental_complexity_penalty'))}</td>"
            f'<td class="{row["status"]}">{decision}</td></tr>'
        )
        control = names.get(str(result.get("selection_control", "")), "Baseline")
        delta = (
            None
            if result.get("score") is None or result.get("selection_control_score") is None
            else (result["score"] - result["selection_control_score"])
        )
        details.append(
            f'<article id="trial-{row["id"]}"><h2>{escape(name)} · {decision}</h2>'
            "<p>" + escape(proposal.get("hypothesis", "Frozen starting strategy.")) + "</p>"
            "<p><b>Why:</b> " + escape(escalation_reason(row, result)) + "</p>"
            '<p class="muted">Compared with '
            + escape(control)
            + "; score difference: "
            + number(delta)
            + "; required score improvement: "
            + number(result.get("required_improvement"))
            + ". Proposal source: "
            + escape(result.get("proposal_source", "baseline"))
            + ".</p><details><summary>Read the LLM explanation and backtest details</summary>"
            + diagnostic_html(report, row["id"])
            + review_html(reviews.get(row["id"], {}))
            + '<p><a href="outputs/runs/'
            + escape(row["candidate"], quote=True)
            + '/primary/results.json">Backtest metrics</a> · '
            + '<a href="outputs/runs/'
            + escape(row["candidate"], quote=True)
            + '/robustness.json">Market-condition tests</a></p></details></article>'
        )
    failed = [row for row in rows if row["status"] == "crash"]
    errors = "".join(
        "<li>Attempt " + str(row["id"]) + ": " + escape(str(row["error"])) + "</li>"
        for row in failed
    )
    content = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Strategy research · {escape(report["program"])}</title>
<style>
:root{{color-scheme:dark;font-family:system-ui,sans-serif;background:#101820;color:#e7eef3}}
body{{max-width:1140px;margin:0 auto;padding:42px 24px;line-height:1.6}}
h1{{font-size:clamp(28px,4vw,44px);line-height:1.2;margin:12px 0}}h2{{font-size:22px}}
a{{color:#8bcfff}}.muted,small{{color:#a6b8c5}}.eyebrow{{letter-spacing:.12em;text-transform:uppercase;color:#80cfc0}}
.cards{{display:flex;flex-wrap:wrap;gap:16px;margin:28px 0}}.card{{flex:1;min-width:170px;padding:20px;background:#1b2a36;border-radius:12px}}
.card strong{{display:block;font-size:25px}}.table{{overflow:auto;border:1px solid #354655;border-radius:12px}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{text-align:right;padding:14px 16px;border-bottom:1px solid #354655}}
th:first-child,td:first-child,th:last-child,td:last-child{{text-align:left}}th{{font-size:13px;color:#a6b8c5}}
.keep{{color:#89e6b7}}.discard{{color:#f4c788}}article{{padding:24px;background:#18242e;border:1px solid #354655;border-radius:12px;margin:20px 0;scroll-margin:20px}}
summary{{cursor:pointer;color:#8bcfff;padding:12px 0}}details[open] summary{{border-bottom:1px solid #354655}}
footer{{border-top:1px solid #354655;padding-top:24px;margin-top:32px}}@media print{{:root{{color-scheme:light;background:white;color:black}}body{{max-width:none}}}}
</style></head><body><div class="eyebrow">AMAT · Strategy research</div>
<h1>{batch["completed"]} of {batch["requested"]} strategies backtested</h1>
<p class="muted">{escape(report["program"])} · {escape(report["status"].replace("_", " "))}</p>
<p class="muted">{escape(period)}</p>
{monitoring_html(report)}
{agent_panel(report)}
<p class="muted">Engineering: {escape(str((report.get("engineering") or {}).get("status", "not requested")))}. Proposed code requires isolated validation before use.</p>
<div class="cards"><div class="card"><small>Completed strategies</small><strong>{batch["completed"]} / {batch["requested"]}</strong>Baseline is additional</div>
<div class="card"><small>Escalated this batch</small><strong>{batch["escalated"]}</strong>New research incumbents</div>
<div class="card"><small>Current best retained</small><strong>{escape(names.get(report["incumbent"], "Not established"))}</strong>Reference for the next comparison</div></div>
<p>Each candidate competes against the best retained strategy. A better score with passing quality and uncertainty checks is escalated automatically.</p>
<div class="table"><table><thead><tr><th>Strategy</th><th>Annual return</th><th>Sharpe</th><th>Max drawdown</th><th>6 bps Sharpe</th><th>Research score</th><th>Matched Sharpe gain</th><th>Complexity penalty</th><th>Outcome</th></tr></thead><tbody>{"".join(table)}</tbody></table></div>
<p class="muted">Returns and drawdowns include baseline execution costs. {score_explanation} All strategies use the same dates and evaluation rules.</p>
{"".join(details)}
<p><a href="analysis/failures.html">Review every unsuccessful strategy from seven standpoints</a></p>
<details><summary>Failed attempts ({len(failed)})</summary><ul>{errors}</ul></details>
<footer><p>Escalation advances strategy research; it does not authorize live trades. These are development backtests. Synthetic scenarios are diagnostics, not forecasts.</p>
<p><a href="report.md">Text report</a> · <a href="report.json">Complete recorded results</a></p></footer></body></html>"""
    temp = root / "report.html.tmp"
    temp.write_text(content)
    temp.replace(root / "report.html")


def monitoring_html(report: dict[str, Any]) -> str:
    analysis = report.get("analysis")
    if not analysis:
        return ""
    monitor = analysis["monitoring"]
    links = [
        ("failures.html", "Failure analysis library"),
        ("failures.json", "Failure records"),
        ("strategies.csv", "Strategy metrics"),
        ("trades/trade-monitor.html", "Trade performance monitor"),
        ("trades/trades.csv", "Trade diagnostics CSV"),
        ("daily.csv", "Daily accounts"),
        ("periods.csv", "Calendar quarters and four historical blocks"),
        ("fills.csv", "Fills"),
        ("missed.csv", "Missed fills"),
        ("folds.csv", "Year folds"),
        ("scenarios.csv", "Scenarios"),
        ("providers.csv", "Provider usage"),
        ("events.jsonl", "Run log"),
        ("timings.csv", "Phase timings"),
        ("feature_diagnostics.json", "Features and controls"),
        ("manifest.json", "Definitions and provenance"),
    ]
    return (
        '<details class="card"><summary>Run monitoring and analysis downloads</summary><p>Phase: '
        + escape(str(monitor["phase"]))
        + " · provider calls: "
        + str(monitor["provider_calls"])
        + " · errors: "
        + str(monitor["provider_errors"])
        + "</p><p>Known input / output tokens: "
        + str(monitor["known_input_tokens"])
        + " / "
        + str(monitor["known_output_tokens"])
        + " · calls with unknown usage: "
        + str(monitor["unknown_usage_calls"])
        + "</p><p>"
        + " · ".join('<a href="analysis/' + path + '">' + title + "</a>" for path, title in links)
        + "</p></details>"
    )


def diagnostic_html(report: dict[str, Any], experiment: int) -> str:
    metrics = next(
        (
            row
            for row in report.get("analysis", {}).get("strategies", [])
            if row["experiment_id"] == experiment
        ),
        None,
    )
    if not metrics:
        return ""
    return (
        "<p>Sortino (MAR 0): "
        + number(metrics.get("primary_sortino_mar0"))
        + " · Calmar: "
        + number(metrics.get("primary_calmar"))
        + " · worst day: "
        + number(metrics.get("primary_worst_day"), percent=True)
        + " · annual one-way turnover: "
        + number(metrics.get("primary_annualized_one_way_turnover"), percent=True)
        + " · total costs: "
        + number(metrics.get("primary_total_costs"))
        + "</p>"
    )
