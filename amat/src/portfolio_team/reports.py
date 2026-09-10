"""Render measured tables from frozen facts and export replayable committee records."""

from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path

from portfolio_team.contracts import Bundle, Decision, Fact
from portfolio_team.evidence import EvidenceError
from portfolio_team.store import Store


def _text(value: str) -> str:
    return html.escape(value).replace("|", "&#124;").replace("\n", " ")


def _value(fact: Fact, value: float | None = None) -> str:
    number = fact.value if value is None else value
    if fact.unit == "fraction":
        return f"{number * 100:.2f}%"
    if fact.unit == "USD":
        return f"${number:,.2f}"
    if fact.unit == "count":
        return f"{number:,.0f}"
    return f"{number:.4f}"


def render(bundle: Bundle, decision: Decision) -> str:
    """All numerical table entries come from facts, not provider narration."""
    aligned = next(
        check.status == "pass" for check in bundle.checks if check.id == "sample_alignment"
    )
    rows = [
        f"# Committee review: {_text(bundle.comparison.id)}",
        "",
        "**OFFLINE SIMULATION — scripted fake-provider responses; not independent AI analysis.**"
        if decision.provider_id.startswith("fake-")
        else "**MODEL SHADOW REVIEW — historical evidence; no execution authority.**",
        "",
        f"Research disposition: `{decision.effective_disposition}`. Execution authority: **none**.",
        f"Readiness: `{decision.readiness_status}`. {_text(decision.resolution_reason)}",
        "",
        f"Review ID: `{decision.review_id}`",
        f"Evidence: `{bundle.digest()}`",
        f"Provider: `{_text(decision.provider_id)}`",
        "",
        "## Verified artifact measurements",
        "",
        "These are internally checked artifact values, not an independent backtest rerun.",
        "",
        f"Candidate: {_text(bundle.candidate.alias)}, {bundle.candidate.first_session} "
        f"to {bundle.candidate.last_session} ({bundle.candidate.sessions} sessions).",
        f"Control: {_text(bundle.control.alias)}, {bundle.control.first_session} "
        f"to {bundle.control.last_session} ({bundle.control.sessions} sessions).",
        "",
        "Dates/counts match; risk and settings may differ."
        if aligned
        else "**Samples differ. Deltas suppressed; request a persisted matched control.**",
        "History status: " + _text(bundle.evaluation_use) + ".",
        "",
        "| Metric | Candidate | Control | Difference |",
        "|---|---:|---:|---:|",
    ]
    control = {fact.metric: fact for fact in bundle.control.facts}
    for fact in bundle.candidate.facts:
        baseline = control[fact.metric]
        name = "return_interval_hit_rate" if fact.metric == "hit_rate" else fact.metric
        difference = _value(fact, fact.value - baseline.value) if aligned else "not comparable"
        if aligned and fact.unit == "fraction":
            difference = f"{(fact.value - baseline.value) * 100:.2f} pp"
        rows.append(f"| {name} | {_value(fact)} | {_value(baseline)} | {difference} |")
    rows.extend(
        (
            "",
            "Hit rate measures return intervals, not completed trades. "
            "Sharpe uses AMAT's reported annualized-return/volatility convention.",
            "",
            "## Deterministic checks",
            "",
        )
    )
    for check in bundle.checks:
        rows.append(f"- `{check.id}` — **{check.status}** ({check.scope}): {_text(check.message)}")
    rows.extend(
        (
            "",
            "## Committee assessments — simulated interpretation"
            if decision.provider_id.startswith("fake-")
            else "## Model assessments — interpretation, not verified facts",
            "",
        )
    )
    for assessment in decision.assessments:
        rows.extend(
            (f"### {assessment.role}", "", f"Recommendation: `{assessment.disposition}`.", "")
        )
        for finding in assessment.findings:
            rows.append(
                f"- `{_text(finding.id)}`: {_text(finding.statement)} "
                f"References: {', '.join(finding.references)}."
            )
        if not assessment.findings:
            rows.append("No additional findings from this scripted role.")
        rows.append("")
        for request in assessment.requests:
            rows.append(
                f"- Requested evidence: {_text(request.requirement)} "
                f"Acceptance: {_text(request.acceptance_criteria)}"
            )
        rows.append("")
    rows.extend(
        (
            "## Provenance and replay",
            "",
            "`evidence.json` contains each fact's artifact digest and JSON pointer, "
            "selected studies, configuration differences, and manifests.",
            "`decision.json` preserves all assessments and addressed findings. "
            "Exact inputs are under `artifacts/` by SHA-256.",
            "Raw-file hashes differ from AMAT's canonical manifest digest. "
            "Dataset partitions and historical dirty code were not independently verified.",
            "",
        )
    )
    return "\n".join(rows)


def _write_once(path: Path, content: bytes) -> None:
    """Atomically publish without overwriting an existing review artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pmteam-", delete=False) as temp:
        temporary = Path(temp.name)
        temp.write(content)
        temp.flush()
        os.fsync(temp.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise EvidenceError(
                    f"refusing to overwrite differing export: {path.name}"
                ) from None
    finally:
        temporary.unlink()


def export_review(store: Store, decision: Decision, output_root: Path) -> Path:
    bundle = store.bundle(decision.evidence_sha256)
    target = output_root / decision.review_id
    for run in (bundle.candidate, bundle.control):
        for key in (run.manifest_file_sha256, run.results_file_sha256, run.config_file_sha256):
            if key:
                _write_once(target / "artifacts" / key, store.artifact(key))
    _write_once(target / "evidence.json", (bundle.canonical() + "\n").encode())
    _write_once(target / "decision.json", (decision.canonical() + "\n").encode())
    _write_once(target / "policy.json", store.artifact(decision.policy_sha256))
    _write_once(target / "protocol.json", store.artifact(decision.protocol_sha256))
    calls = store.model_calls(decision.review_id)
    if calls:
        _write_once(target / "model_calls.json", json.dumps(calls, indent=2).encode())
        for call in calls:
            for key in (call["request_hash"], call["response_hash"]):
                if key:
                    _write_once(target / "artifacts" / key, store.artifact(key))
    _write_once(target / "report.md", render(bundle, decision).encode())
    return target
