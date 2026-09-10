"""Validate evidence references independently of provider orchestration."""

from portfolio_team.contracts import Assessment, Bundle, Role
from portfolio_team.evidence import EvidenceError


def validate_assessment(
    assessment: Assessment, role: Role, bundle: Bundle, previous: tuple[Assessment, ...] = ()
) -> None:
    if assessment.role != role or assessment.evidence_sha256 != bundle.digest():
        raise EvidenceError("assessment role/evidence identity mismatch")
    references = {check.id for check in bundle.checks} | {
        fact.id for run in (bundle.candidate, bundle.control) for fact in run.facts
    }
    cited = [item.references for item in assessment.findings]
    cited.extend(item.references for item in assessment.requests)
    for refs in cited:
        if not set(refs).issubset(references):
            raise EvidenceError("assessment cites unknown evidence")
    ids = [finding.id for finding in assessment.findings]
    if len(ids) != len(set(ids)) or any(not key.startswith(f"{role}.") for key in ids):
        raise EvidenceError("finding IDs must be unique and role-prefixed")
    expected = {finding.id for report in previous for finding in report.findings}
    if set(assessment.addressed_findings) != expected:
        raise EvidenceError("chief must account for every reviewer finding")
