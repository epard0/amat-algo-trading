"""Immutable contracts for the first, offline committee implementation."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from amat.provenance import RunManifest

Role = Literal["research", "portfolio", "risk_operations", "chief_pm"]
Disposition = Literal[
    "reject_current_proposal",
    "request_evidence",
    "continue_research",
    "shortlist_for_forward_shadow",
]
ROLES: tuple[Role, ...] = ("research", "portfolio", "risk_operations", "chief_pm")


class Record(BaseModel):
    """Reject unknown fields and nonfinite numbers at persistence boundaries."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, revalidate_instances="always"
    )

    def canonical(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()


class RunSpec(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    path: str
    config_path: str
    study_index: int = Field(ge=0)
    engine: str


class ComparisonSpec(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    candidate: str
    control: str
    purpose: Literal["replacement", "addition", "mechanism_test"]
    require_matched_sample: Literal[True] = True
    require_settings_diff: Literal[True] = True
    causal_component_attribution: Literal[False] = False


class Catalog(Record):
    schema_version: Literal["portfolio-team-catalog/1"]
    default_evaluation_use: Literal["previously_inspected_history", "unknown"]
    runs: tuple[RunSpec, ...]
    comparisons: tuple[ComparisonSpec, ...]


class Fact(Record):
    id: str
    metric: str
    value: float
    unit: Literal["fraction", "ratio", "USD", "count", "bps"]
    artifact_sha256: str
    pointer: str


class RunEvidence(Record):
    alias: str
    manifest: RunManifest
    manifest_file_sha256: str
    results_file_sha256: str
    config_file_sha256: str | None
    config_status: Literal["verified", "missing", "mismatch"]
    config_json: str | None
    first_session: str
    last_session: str
    sessions: int
    facts: tuple[Fact, ...]
    # Exact selected study is retained for future, richer read-only evidence views.
    selected_study_json: str


class Check(Record):
    id: str
    status: Literal["pass", "fail", "missing"]
    scope: Literal["comparison", "readiness", "research"]
    message: str
    references: tuple[str, ...] = ()


class Bundle(Record):
    schema_version: Literal["portfolio-team-evidence/1"] = "portfolio-team-evidence/1"
    adapter_version: Literal["amat-study-envelope/1"] = "amat-study-envelope/1"
    comparison: ComparisonSpec
    candidate: RunEvidence
    control: RunEvidence
    evaluation_use: str
    settings_differences: tuple[str, ...]
    checks: tuple[Check, ...]


class Finding(Record):
    id: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=2000)
    references: tuple[str, ...] = Field(min_length=1, max_length=20)


class EvidenceRequest(Record):
    requirement: str = Field(min_length=1, max_length=1000)
    acceptance_criteria: str = Field(min_length=1, max_length=2000)
    references: tuple[str, ...] = Field(min_length=1, max_length=20)


class Assessment(Record):
    role: Role
    evidence_sha256: str
    disposition: Disposition
    findings: tuple[Finding, ...] = Field(max_length=40)
    requests: tuple[EvidenceRequest, ...] = Field(max_length=40)
    # Chief must account for every reviewer finding, preserving dissent.
    addressed_findings: tuple[str, ...] = Field(max_length=120)


class Policy(Record):
    schema_version: Literal["portfolio-team-offline-policy/1"] = "portfolio-team-offline-policy/1"
    policy_id: str = "amat-offline-review-v1"
    execution_authority: Literal["none"] = "none"
    # This adapter imports aggregate historical studies, not admission evidence.
    shortlist_supported: Literal[False] = False
    max_attempts_per_role: int = Field(default=2, ge=1, le=2)


class Decision(Record):
    schema_version: Literal["portfolio-team-decision/1"] = "portfolio-team-decision/1"
    review_id: str
    evidence_sha256: str
    policy_sha256: str
    provider_id: str
    protocol_sha256: str
    processing_status: Literal["completed"] = "completed"
    proposed_disposition: Disposition
    effective_disposition: Disposition
    resolution_reason: str
    readiness_status: Literal["blocked"] = "blocked"
    execution_authority: Literal["none"] = "none"
    assessments: tuple[Assessment, ...]
