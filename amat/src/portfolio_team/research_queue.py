"""Typed, operator-gated follow-up experiments for portfolio-team reviews.

The queue is deliberately declarative: it contains no shell commands and no
model-generated executable text.  A later worker may dispatch only the
allow-listed operations represented here after an operator changes status.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from portfolio_team.contracts import Bundle, Decision, Record

ExperimentOperation = Literal[
    "matched_backtest",
    "cost_stress",
    "ablation",
    "walkforward",
    "session_pattern",
    "stat_arb",
]
ExperimentStatus = Literal["proposed", "approved", "running", "completed", "failed", "blocked"]


class ExperimentTask(Record):
    id: str = Field(pattern=r"^[a-f0-9]{16}$")
    source_review_id: str
    comparison: str
    operation: ExperimentOperation
    objective: str = Field(min_length=1, max_length=1000)
    acceptance_criteria: str = Field(min_length=1, max_length=2000)
    check_references: tuple[str, ...] = Field(min_length=1, max_length=20)
    candidate: str
    control: str
    candidate_config: str | None = None
    control_config: str | None = None
    snapshot: str | None = None
    start: str | None = None
    end: str | None = None
    parameters_json: str = "{}"
    status: ExperimentStatus = "proposed"
    execution_authority: Literal["none"] = "none"


class ExperimentQueue(Record):
    schema_version: Literal["portfolio-team-experiment-queue/1"] = (
        "portfolio-team-experiment-queue/1"
    )
    source_review_id: str
    evidence_sha256: str
    generated_from: Literal["failed_checks_and_evidence_requests"] = (
        "failed_checks_and_evidence_requests"
    )
    tasks: tuple[ExperimentTask, ...]


class TaskApproval(Record):
    schema_version: Literal["portfolio-team-task-approval/1"] = "portfolio-team-task-approval/1"
    review_id: str
    queue_sha256: str
    task_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    approved_by: str = Field(min_length=1, max_length=200)
    approved_at: str
    execution_authority: Literal["none"] = "none"


class DispatchResult(Record):
    schema_version: Literal["portfolio-team-dispatch-result/1"] = "portfolio-team-dispatch-result/1"
    review_id: str
    task_id: str
    operation: ExperimentOperation
    status: Literal["blocked", "completed", "failed"] = "blocked"
    reason: str = "adapter_not_registered"
    execution_authority: Literal["none"] = "none"
    return_code: int | None = None
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None
    catalog_path: str | None = None
    evidence_sha256: str | None = None


def load_queue(path: Path) -> ExperimentQueue:
    return ExperimentQueue.model_validate_json(path.read_text())


def approve_task(queue: ExperimentQueue, task_id: str, approved_by: str) -> TaskApproval:
    task = next((item for item in queue.tasks if item.id == task_id), None)
    if task is None:
        raise ValueError(f"unknown experiment task: {task_id}")
    if task.source_review_id != queue.source_review_id:
        raise ValueError("task does not belong to queue review")
    if task.status != "proposed":
        raise ValueError(f"task {task_id} is not proposed")
    return TaskApproval(
        review_id=queue.source_review_id,
        queue_sha256=queue.digest(),
        task_id=task.id,
        approved_by=approved_by,
        approved_at=datetime.now(UTC).isoformat(),
    )


def validate_approval(
    queue: ExperimentQueue, task_id: str, approval: TaskApproval
) -> ExperimentTask:
    """Bind authorization to the exact review, queue bytes and proposed task."""
    task = next((item for item in queue.tasks if item.id == task_id), None)
    if (
        task is None
        or approval.review_id != queue.source_review_id
        or approval.queue_sha256 != queue.digest()
        or approval.task_id != task_id
        or task.source_review_id != queue.source_review_id
        or task.status != "proposed"
    ):
        raise ValueError("approval does not match queue and proposed task")
    return task


def dispatch_spec(task: ExperimentTask) -> dict[str, object]:
    """Describe the gate without inventing commands for unregistered adapters.

    Imported first/last sessions are *measured* dates, not study load-window
    arguments: warm-up must be resolved separately for candidate and control.
    A candidate-only study cannot satisfy a matched-backtest request.
    """
    return {
        "operation": task.operation,
        "argv_template": (),
        "execute": False,
        "reason": "adapter_not_registered",
    }


def execute_approved_task(
    queue: ExperimentQueue, task_id: str, approval: TaskApproval
) -> DispatchResult:
    """Validate approval and produce a blocked result until an adapter exists."""
    task = validate_approval(queue, task_id, approval)
    return DispatchResult(
        review_id=queue.source_review_id, task_id=task.id, operation=task.operation
    )


def run_registered_task(
    root: Path, queue: ExperimentQueue, task_id: str, approval: TaskApproval, timeout: float = 1800
) -> DispatchResult:
    """Dispatch supported frozen research tasks to the broker-free worker.

    Unsupported operations and legacy source envelopes retain the blocked gate.
    The registered adapter validates and records both sides of a matched study.
    """
    del timeout
    validate_approval(queue, task_id, approval)
    task = next(item for item in queue.tasks if item.id == task_id)
    if task.operation == "matched_backtest" and (root / "program.json").is_file():
        from autoresearch.followup import run_matched_task  # noqa: PLC0415 - explicit adapter

        return run_matched_task(root, queue, task_id, approval)
    return execute_approved_task(queue, task_id, approval)


def _operation(check_id: str, text: str) -> ExperimentOperation:
    value = f"{check_id} {text}".lower()
    rules: tuple[tuple[ExperimentOperation, tuple[str, ...]], ...] = (
        ("matched_backtest", ("accounting", "parity", "execution capability")),
        ("cost_stress", ("cost", "fee", "slippage", "risk")),
        ("session_pattern", ("session", "open", "close", "intraday")),
        ("stat_arb", ("arb", "cointegr", "spread", "residual")),
        ("walkforward", ("walk", "holdout", "partition", "daily_series")),
        ("ablation", ("configuration", "settings", "ablation")),
    )
    return next(
        (operation for operation, words in rules if any(word in value for word in words)),
        "matched_backtest",
    )


def build_queue(bundle: Bundle, decision: Decision) -> ExperimentQueue:
    """Build reproducible proposed work from a completed, blocked review."""
    if decision.evidence_sha256 != bundle.digest():
        raise ValueError("decision does not match evidence bundle")
    requests = [request for assessment in decision.assessments for request in assessment.requests]
    checks = {check.id: check for check in bundle.checks if check.status != "pass"}
    rows: list[ExperimentTask] = []
    # Every unresolved check gets one deterministic task; requests enrich the
    # objective but are never interpreted as executable instructions.
    for check_id in sorted(checks):
        check = checks[check_id]
        request = next((item for item in requests if check_id in item.references), None)
        text = request.requirement if request else check.message
        criteria = (
            request.acceptance_criteria
            if request
            else (
                "Persist candidate/control metrics on identical dates and report costs, return, "
                "Sharpe, drawdown, and fills."
            )
        )
        operation = _operation(check_id, f"{text} {criteria}")
        seed = f"{decision.review_id}|{bundle.digest()}|{check_id}|{operation}"
        task_id = hashlib.sha256(seed.encode()).hexdigest()[:16]
        rows.append(
            ExperimentTask(
                id=task_id,
                source_review_id=decision.review_id,
                comparison=bundle.comparison.id,
                operation=operation,
                objective=f"Resolve {check_id}: {text}",
                acceptance_criteria=criteria,
                check_references=(check_id,),
                candidate=bundle.comparison.candidate,
                control=bundle.comparison.control,
                candidate_config=bundle.candidate.manifest.config_source,
                control_config=bundle.control.manifest.config_source,
                snapshot=bundle.candidate.manifest.datasets[0].snapshot_id
                if bundle.candidate.manifest.datasets
                else None,
                start=bundle.candidate.first_session,
                end=bundle.candidate.last_session,
            )
        )
    if not rows and decision.effective_disposition == "request_evidence":
        raise ValueError("request-evidence review has no unresolved check to queue")
    return ExperimentQueue(
        source_review_id=decision.review_id, evidence_sha256=bundle.digest(), tasks=tuple(rows)
    )
