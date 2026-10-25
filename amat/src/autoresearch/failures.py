"""Evidence-grounded failure reviews, retained separately from LLM interpretation."""

# ruff: noqa: E501 - complete research explanations and standalone HTML

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from autoresearch.workspace import digest_file, write_json

FAILURE_STATES = frozenset({"discard", "crash"})


def diagnose(row: dict[str, Any]) -> dict[str, Any]:
    """Explain observed failure conditions without claiming untested causal mechanisms."""
    result = json.loads(row["result"]) if row.get("result") else {}
    proposal = json.loads(row["proposal"]) if row["proposal"] != "baseline" else {}
    gates = result.get("failed_gates", [])
    perspectives: list[dict[str, Any]] = []

    def add(area: str, finding: str, evidence: Any, next_test: str) -> None:
        perspectives.append(
            {"standpoint": area, "finding": finding, "evidence": evidence, "next_test": next_test}
        )

    control_warnings = {
        name: {"count": len(items), "examples": items[:2]}
        for name, items in result.get("control_summary", {}).get("runner_warnings", {}).items()
        if items
    }
    error = row.get("error")
    add(
        "implementation",
        ("The attempt did not produce a valid measurement: " + str(error or "interrupted"))
        if not result
        else "The primary backtest completed, but control runner warnings invalidate part of the comparison evidence."
        if control_warnings
        else "The backtest completed; rejection is an evaluation outcome, not a runtime failure.",
        {
            "status": row["status"],
            "error": error,
            "structural_audit": result.get("structural_audit_passed"),
            "control_runner_warnings": control_warnings,
        },
        "Repair the recorded contract/data/runtime error, then rerun the same hypothesis."
        if not result
        else "Repair the recorded control inputs and rerun the same hypothesis before interpreting timing sensitivity."
        if control_warnings
        else "Keep the same execution contract when testing the next change.",
    )
    score, control, margin = (
        result.get("score"),
        result.get("selection_control_score"),
        result.get("required_improvement"),
    )
    objective = "No comparable score was recorded; performance cannot be judged."
    if score is not None and control is not None and margin is not None:
        objective = f"Score {score:.6f} must exceed {control:.6f} + {margin:.6f}; " + (
            "the required improvement was not achieved."
            if score <= control + margin
            else "the score hurdle passed, but other retention checks must also pass."
        )
    add(
        "objective",
        objective,
        {
            "score": score,
            "score_version": result.get("score_version", "annual-fold-consistency/1"),
            "score_components": result.get("score_components"),
            "absolute_performance_score": result.get("absolute_performance_score"),
            "incumbent_score": control,
            "required_margin": margin,
            "failed_gates": gates,
            "complexity_penalty": result.get("complexity_penalty"),
        },
        "Ablate added complexity and compare the same frozen score against the incumbent.",
    )
    train, stress = result.get("train", {}), result.get("stress", {})
    risk = {
        key: train.get(key)
        for key in ("max_drawdown", "average_gross_exposure", "annualized_volatility")
    }
    risk.update(result.get("analysis_metrics", {}).get("primary", {}))
    risk_gates = [g for g in gates if g in ("drawdown", "exposure")]
    add(
        "risk",
        "Risk gates failed: " + ", ".join(risk_gates)
        if risk_gates
        else "No recorded drawdown/exposure gate failed."
        if train
        else "Risk was not measured.",
        risk,
        "Compare drawdown and exposure under the same risk budget; inspect underwater periods.",
    )
    delta = (
        train["sharpe"] - stress["sharpe"]
        if train.get("sharpe") is not None and stress.get("sharpe") is not None
        else None
    )
    add(
        "execution",
        f"Sharpe fell by {delta:.6f} under higher execution costs."
        if delta is not None and delta > 0
        else "Cost stress did not reduce recorded Sharpe."
        if delta is not None
        else "Cost sensitivity was not measured.",
        {
            "primary_sharpe": train.get("sharpe"),
            "stress_sharpe": stress.get("sharpe"),
            "cost_drag_annualized": train.get("cost_drag_annualized"),
            "fills": train.get("total_fills"),
            "missed_fills": train.get("total_missed_fills"),
        },
        "Test slower confirmation or a wider entry/exit band, holding the signal and costs fixed.",
    )
    folds = result.get("folds", [])
    losing = [f["year"] for f in folds if f.get("total_return", 0) < 0]
    scenarios = result.get("robustness", {}).get("scenarios", {})
    weaker = [
        {"name": name, "kind": s["kind"], "sharpe_difference": s.get("sharpe_difference")}
        for name, s in scenarios.items()
        if s.get("sharpe_difference", 0) < 0
    ]
    add(
        "market_conditions",
        f"Negative-return years: {losing}; {len(weaker)}/{len(scenarios)} paired scenarios lagged the baseline."
        if folds or scenarios
        else "Yearly and scenario evidence is unavailable.",
        {
            "negative_years": losing,
            "weaker_scenarios": weaker,
            "nearby_stable": result.get("control_summary", {}).get("nearby_stable"),
        },
        "Test one regime or parameter-stability hypothesis; scenario losses alone do not establish causation.",
    )
    features = result.get("diagnostics", {}).get("features", [])
    signal_evidence = [
        {
            "feature": f.get("recipe"),
            "coverage": f.get("coverage"),
            "baseline_rank_correlation": f.get("baseline_rank_correlation"),
            "horizons": [
                {
                    "horizon": h.get("horizon"),
                    "rank_ic": h.get("mean_daily_rank_ic"),
                    "spread": h.get("high_minus_low"),
                }
                for h in f.get("horizons", [])
            ],
        }
        for f in features
    ]
    add(
        "signal",
        "Feature association and exposure controls are available; association alone does not identify why a strategy failed."
        if features
        else "No per-feature attribution was recorded; the mechanism remains untested.",
        {
            "features": signal_evidence,
            "exposure_match": result.get("control_summary", {}).get("exposure_match"),
        },
        "Remove or reverse one term and compare its incremental value using the recorded exposure control.",
    )
    uncertainty = result.get("uncertainty")
    skipped = uncertainty and uncertainty.get("reason") == "eligibility_or_objective_margin"
    add(
        "uncertainty",
        "Uncertainty testing was skipped because eligibility or the score margin failed."
        if skipped
        else "The recorded uncertainty check passed."
        if uncertainty and uncertainty.get("passes")
        else "The uncertainty check did not pass."
        if uncertainty
        else "No uncertainty result was recorded.",
        uncertainty,
        "Use a new falsifiable hypothesis; repeated development trials are not independent validation.",
    )
    return {
        "experiment_id": row.get("id"),
        "candidate_id": row["candidate"],
        "parent_id": row["parent"],
        "status": row["status"],
        "hypothesis": proposal.get("hypothesis", "Baseline"),
        "proposal": proposal,
        "failed_gates": gates,
        "perspectives": perspectives,
        "evidence_limits": "Observed diagnostics explain the decision. Proposed mechanisms require controlled tests; no live-performance or causal proof.",
    }


def failure_memory(history: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [r for r in history if r["status"] in FAILURE_STATES]
    reasons: Counter[str] = Counter()
    recent = []
    for row in failures:
        d = diagnose(row)
        reasons.update(
            d["failed_gates"]
            or ["runtime_or_contract" if not row["result"] else "retention_comparison"]
        )
    for row in failures[-4:]:
        d = diagnose(row)
        recent.append(
            {
                "candidate": row["candidate"],
                "hypothesis": d["hypothesis"][:200],
                "findings": [p["finding"][:240] for p in d["perspectives"]],
            }
        )
    return {"failed_attempts": len(failures), "reason_counts": dict(reasons), "recent": recent}


def export_failures(root: Path, report: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = {r["experiment"]: r for r in report.get("llm_summaries", [])}
    records = []
    for row in report["experiments"]:
        if row["status"] not in FAILURE_STATES:
            continue
        result = json.loads(row["result"]) if row["result"] else {}
        records.append(
            {
                **diagnose(row),
                "session": str(root),
                "program_sha256": report["program_sha256"],
                "measurement_artifacts": result.get("artifact_hashes", {}),
                "llm_review": reviews.get(row["id"], {"status": "unavailable"}),
            }
        )
    target = root / "analysis"
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "failures.json", records)
    write_failure_html(target / "failures.html", records)
    return records


def write_failure_html(path: Path, records: list[dict[str, Any]]) -> None:
    articles = []
    for r in records:
        views = "".join(
            "<h3>"
            + escape(p["standpoint"])
            + "</h3><p>"
            + escape(p["finding"])
            + "</p><p>Next test: "
            + escape(p["next_test"])
            + "</p><details><summary>Evidence</summary><pre>"
            + escape(json.dumps(p["evidence"], indent=2))
            + "</pre></details>"
            for p in r["perspectives"]
        )
        review = r.get("llm_review", {})
        articles.append(
            "<details><summary>"
            + escape(f"Experiment {r['experiment_id']} · {r['status']} · {r['hypothesis']}")
            + "</summary><p>Session: "
            + escape(r["session"])
            + "</p><p>Candidate: "
            + escape(r["candidate_id"])
            + "</p>"
            + views
            + "<h3>LLM interpretation</h3><pre>"
            + escape(json.dumps(review, indent=2))
            + "</pre><p>"
            + escape(r["evidence_limits"])
            + "</p></details>"
        )
    html = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Strategy failure library</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:20px;line-height:1.6;background:#101820;color:#e7eef3}details{padding:16px;border:1px solid #456;margin:12px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Strategy failure library</h1>'
    html += (
        f"<p>{len(records)} unsuccessful attempts. Rejected strategies and runtime failures remain distinct. Search this page with your browser's Find command.</p>"
        + "".join(articles)
        + "</html>"
    )
    temporary = path.with_suffix(".html.tmp")
    temporary.write_text(html)
    temporary.replace(path)


def recorded_failures(root: Path) -> list[dict[str, Any]]:
    """Read original ledgers across program versions without upgrading their schema."""
    # Delayed import avoids a cycle: the analysis exporter also uses diagnose().
    from autoresearch.telemetry import (  # noqa: PLC0415 - cycle avoidance
        analysis_lock,
        read_telemetry,
    )

    with analysis_lock(root):
        program_path = root / "program.json"
        if digest_file(program_path) != (root / "program.sha256").read_text():
            raise ValueError("recorded program hash mismatch")
        program = json.loads(program_path.read_text())
        program_digest = hashlib.sha256(
            json.dumps(program, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        telemetry = read_telemetry(root)
        records = []
        for row in telemetry["experiments"]:
            if row["status"] not in FAILURE_STATES:
                continue
            result = json.loads(row["result"]) if row["result"] else {}
            for relative, expected in result.get("artifact_hashes", {}).items():
                path = root / relative
                if (
                    not path.resolve().is_relative_to(root.resolve())
                    or digest_file(path) != expected
                ):
                    raise ValueError("recorded measurement changed: " + relative)
            summary = {"status": "unavailable", "reason": "no saved review"}
            saved = telemetry["state"].get(f"summary:{row['id']}")
            if saved:
                path = root / saved["path"]
                if (
                    not path.resolve().is_relative_to(root.resolve())
                    or digest_file(path) != saved["sha256"]
                ):
                    raise ValueError("saved review hash mismatch")
                summary = json.loads(path.read_text())
            records.append(
                {
                    **diagnose(row),
                    "session": str(root),
                    "program_sha256": program_digest,
                    "program_schema": program.get("schema_version"),
                    "measurement_artifacts": result.get("artifact_hashes", {}),
                    "measurement_hashes_available": bool(result.get("artifact_hashes")),
                    "llm_review": summary,
                }
            )
        return records


def collect_failures(parent: Path) -> dict[str, Any]:
    """Aggregate recorded sessions; keep identities and disclose any verification gaps."""
    records, skipped = [], []
    roots = {p.parent for p in parent.glob("*/research.sqlite3")}
    roots.update(p.parent.parent for p in parent.glob("*/analysis/failures.json"))
    for root in sorted(roots):
        try:
            if (root / "research.sqlite3").exists():
                records.extend(recorded_failures(root))
            else:
                path = root / "analysis/failures.json"
                manifest = json.loads((path.parent / "manifest.json").read_text())
                if digest_file(path) != manifest["exports"]["failures.json"]:
                    raise ValueError("failure export hash mismatch")
                records.extend(json.loads(path.read_text()))
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            skipped.append({"session": str(root), "reason": str(exc)})
    target = parent / "failure-library"
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "failures.json", records)
    write_json(target / "coverage.json", {"records": len(records), "skipped_sessions": skipped})
    write_failure_html(target / "index.html", records)
    return {
        "path": str(target / "index.html"),
        "records": len(records),
        "skipped_sessions": skipped,
    }
