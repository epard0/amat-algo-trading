"""Compare deterministic checks, one reviewer, and the committee on frozen evidence.

Scoring is reference coverage, not semantic correctness or investment accuracy.
Expected issue labels stay in the evaluator and never enter model requests.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from portfolio_team.contracts import Record
from portfolio_team.coordinator import PROTOCOL_SHA256, run_review
from portfolio_team.evidence import EvidenceError, sha256
from portfolio_team.model_runtime import RemoteProvider
from portfolio_team.providers import Provider
from portfolio_team.reports import _write_once, export_review
from portfolio_team.store import Store


class EvalCase(Record):
    id: str
    comparison: str
    expected_issue_ids: tuple[str, ...] = Field(min_length=1)


class EvalCatalog(Record):
    schema_version: Literal["pmteam-reference-eval/1"]
    cases: tuple[EvalCase, ...] = Field(min_length=1)


def load_cases(path: Path) -> EvalCatalog:
    return EvalCatalog.model_validate(tomllib.loads(path.read_text()))


def evaluate(
    store: Store, catalog: EvalCatalog, provider: Provider | RemoteProvider, output_root: Path
) -> Path:
    snapshots = [(case, store.resolve(case.comparison)) for case in catalog.cases]
    for case, bundle in snapshots:
        known = {check.id for check in bundle.checks}
        if not set(case.expected_issue_ids).issubset(known):
            raise EvidenceError("evaluation labels must name existing evidence checks")
    protocol = (
        sha256(provider.protocol()) if isinstance(provider, RemoteProvider) else PROTOCOL_SHA256
    )
    identity = {
        "evaluator_version": "reference-coverage/1",
        "catalog": catalog.model_dump(),
        "provider": provider.id,
        "protocol": protocol,
        "evidence": [bundle.digest() for _, bundle in snapshots],
    }
    evaluation_id = sha256(json.dumps(identity, sort_keys=True).encode())
    rows: list[dict[str, Any]] = []
    for case, bundle in snapshots:
        expected = set(case.expected_issue_ids)
        flagged = {check.id for check in bundle.checks if check.status != "pass"}
        rows.append(
            {
                "case": case.id,
                "mode": "deterministic",
                "covered": len(expected & flagged),
                "expected": len(expected),
                "calls": 0,
                "review_id": None,
            }
        )
        for single in (True, False):
            mode = "single" if single else "committee"
            try:
                decision = run_review(store, bundle, provider, single_reviewer=single)
                export_review(store, decision, output_root.parent)
                references = {
                    ref
                    for assessment in decision.assessments
                    for finding in assessment.findings
                    for ref in finding.references
                }
                calls = store.model_calls(decision.review_id)
                rows.append(
                    {
                        "case": case.id,
                        "mode": mode,
                        "covered": len(expected & references),
                        "expected": len(expected),
                        "calls": len(calls),
                        "unadjudicated_references": sorted(references - expected),
                        "review_id": decision.review_id,
                        "input_tokens": sum(call["charged_input"] for call in calls),
                        "output_tokens": sum(call["charged_output"] for call in calls),
                        "call_seconds": sum(call["elapsed_seconds"] or 0 for call in calls),
                    }
                )
            except ValueError as exc:
                rows.append(
                    {
                        "case": case.id,
                        "mode": mode,
                        "covered": None,
                        "expected": len(expected),
                        "error": type(exc).__name__,
                    }
                )
    payload = {
        **identity,
        "rows": rows,
        "limitation": "Citation coverage only; semantic correctness needs adjudication. "
        "Development examples are not a held-out quality benchmark.",
    }
    # A retry can complete previously failed rows; preserve both evaluation snapshots.
    payload["evaluation_key"] = evaluation_id
    result_id = sha256(json.dumps(payload, sort_keys=True).encode())
    target = output_root / result_id
    _write_once(target / "results.json", json.dumps(payload, indent=2, sort_keys=True).encode())
    lines: list[str] = [
        "# Reviewer reference-coverage evaluation",
        "",
        str(payload["limitation"]),
        "",
        f"Provider: `{provider.id}`. Labels were withheld from inference requests.",
        "",
        "Calls/usage include cached reviews; they are not necessarily new calls.",
        "",
        "| Case | Mode | Expected references covered | Calls |",
        "|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['case']} | {row['mode']} | {row['covered']}/{row['expected']} "
            f"| {row.get('calls', 'failed')} |"
        )
    _write_once(target / "report.md", "\n".join(lines).encode())
    return target
