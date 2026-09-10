"""Provider protocol and explicitly simulated, deterministic committee roles."""

from __future__ import annotations

from typing import Protocol

from portfolio_team.contracts import Assessment, Bundle, EvidenceRequest, Finding, Role

PROMPTS: dict[Role, str] = {
    "research": (
        "Assess mechanism, evaluation reuse and missing validation. Cite evidence IDs. "
        "Do not invent metrics or treat inspected history as untouched. Request specific evidence."
    ),
    "portfolio": (
        "Assess the declared comparison: dates, risk targets, costs, and concentration. "
        "Distinguish replacement from diversification; neither causal attribution nor joint "
        "portfolio benefit follows from aggregate statistics alone. Cite evidence IDs."
    ),
    "risk_operations": (
        "Assess code/config/data provenance and accounting/capability evidence. "
        "A manifest link does not verify source data or execution readiness. Cite evidence IDs."
    ),
    "chief_pm": (
        "Resolve all three independent reviews using the frozen evidence. Account for every "
        "reviewer finding. Missing evidence prevents admission. Preserve dissent. "
        "No execution authority exists; output only a structured research recommendation."
    ),
}


class Provider(Protocol):
    """Implementations return bounded assessments, never executable instructions."""

    id: str

    def assess(
        self, role: Role, bundle: Bundle, previous: tuple[Assessment, ...] = ()
    ) -> Assessment: ...


class FakeProvider:
    """Test double. These are scripted outputs, not independent AI analysis."""

    id = "fake-scripted/1"

    def assess(
        self, role: Role, bundle: Bundle, previous: tuple[Assessment, ...] = ()
    ) -> Assessment:
        scopes = {
            "research": {"research"},
            "portfolio": {"comparison"},
            "risk_operations": {"readiness"},
            "chief_pm": {"research", "comparison", "readiness"},
        }
        selected = [
            check
            for check in bundle.checks
            if check.scope in scopes[role] and check.status != "pass"
        ]
        findings = tuple(
            Finding(
                id=f"{role}.{check.id}",
                statement=check.message,
                references=(check.id,),
            )
            for check in selected
        )
        requests = tuple(
            EvidenceRequest(
                requirement=check.message,
                acceptance_criteria=(
                    f"Attach reproducible evidence resolving {check.id}; re-review a new bundle."
                ),
                references=(check.id,),
            )
            for check in selected
        )
        return Assessment(
            role=role,
            evidence_sha256=bundle.digest(),
            disposition="request_evidence",
            findings=findings,
            requests=requests,
            addressed_findings=tuple(
                finding.id for report in previous for finding in report.findings
            ),
        )
