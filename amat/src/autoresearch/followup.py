"""Approved matched reproduction using the frozen, credential-free worker."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from autoresearch.contracts import Candidate
from autoresearch.ledger import Ledger
from autoresearch.loop import execute
from autoresearch.telemetry import daily_metrics
from autoresearch.worker import ENGINE
from autoresearch.workspace import digest_file, load_program, write_json
from portfolio_team.contracts import Bundle, Catalog, ComparisonSpec, RunSpec
from portfolio_team.evidence import load_catalog, make_bundle
from portfolio_team.research_queue import (
    DispatchResult,
    ExperimentQueue,
    TaskApproval,
    build_queue,
    validate_approval,
)
from portfolio_team.store import Store

MAX_ATTEMPTS = 2


def _check_hashes(root: Path, hashes: dict[str, str]) -> None:
    if not hashes:
        raise ValueError("follow-up evidence has no artifact hashes")
    for relative, expected in hashes.items():
        path = root / relative
        if (
            not path.resolve().is_relative_to(root)
            or path.is_symlink()
            or digest_file(path) != expected
        ):
            raise ValueError("follow-up artifact changed")


def _source_bundle(root: Path, queue: ExperimentQueue) -> Bundle:
    with Store(root / "data/governance/pmteam") as store:
        decision = store.decision(queue.source_review_id)
        if decision is None or decision.evidence_sha256 != queue.evidence_sha256:
            raise ValueError("follow-up requires the completed source review")
        bundle = store.bundle(queue.evidence_sha256)
        if build_queue(bundle, decision) != queue:
            raise ValueError("queue differs from the source review's proposed work")
    current, _ = make_bundle(load_catalog(root / "catalog.toml"), bundle.comparison.id, root)
    if current.digest() != bundle.digest():
        raise ValueError("reviewed source evidence changed")
    return bundle


def run_matched_task(
    root: Path,
    queue: ExperimentQueue,
    task_id: str,
    approval: TaskApproval,
) -> DispatchResult:
    root = root.resolve()
    task = validate_approval(queue, task_id, approval)
    program = load_program(root)
    if task.operation != "matched_backtest" or json.loads(task.parameters_json) != {}:
        raise ValueError("only parameter-free matched reproduction is registered")
    bundle = _source_bundle(root, queue)
    if task.start != str(program.measured_start) or task.end != str(program.train_end):
        raise ValueError("task measured dates must match the frozen development period")
    candidates = {}
    for role, evidence in (("candidate", bundle.candidate), ("control", bundle.control)):
        if re.fullmatch(r"r_[a-f0-9]{64}", evidence.alias) is None:
            raise ValueError("matched adapter requires a registered research candidate")
        key = evidence.alias[2:]
        candidate = Candidate.model_validate_json((root / "candidates" / f"{key}.json").read_text())
        if candidate.digest() != key:
            raise ValueError("reviewed candidate identity changed")
        path = f"outputs/runs/{key}/primary"
        if evidence.manifest.config_source != path + "/config.toml":
            raise ValueError("unsupported source configuration envelope")
        envelope = json.loads((root / path / "results.json").read_text())
        if (
            envelope.get("program_sha256") != program.digest()
            or envelope.get("candidate_sha256") != key
        ):
            raise ValueError("reviewed run belongs to a different program or candidate")
        candidates[role] = candidate
    # The ledger lock serializes follow-ups with ordinary search and checkpoints.
    with Ledger(root, program.max_model_tokens_per_day) as ledger:
        return _run_locked(root, queue, task_id, approval, candidates, ledger=ledger)


def _run_locked(
    root: Path,
    queue: ExperimentQueue,
    task_id: str,
    approval: TaskApproval,
    candidates: dict[str, Candidate],
    *,
    ledger: Ledger,
) -> DispatchResult:
    program = load_program(root)
    identity = f"{queue.digest()}-{task_id}"
    key = "followup:" + identity
    state = ledger.get(key, {})
    target = root / "outputs/followups" / identity
    if state.get("approval") not in (None, approval.digest()):
        raise ValueError("follow-up approval changed")
    if state.get("completed"):
        _check_hashes(root, state["hashes"])
        return DispatchResult.model_validate(state["receipt"])
    if state.get("attempts", 0) >= MAX_ATTEMPTS:
        raise ValueError("follow-up exhausted its two execution attempts")
    state.update(approval=approval.digest(), attempts=state.get("attempts", 0) + 1)
    ledger.put(key, state)
    ledger.event("followup_started", task_id=task_id, attempt=state["attempts"])
    try:
        for role, candidate in candidates.items():
            if role in state:
                _check_hashes(root, state[role]["hashes"])
                continue
            run_target = target / "runs" / role
            result = execute(root, program, candidate, run_target)
            hashes = {
                str(p.relative_to(root)): digest_file(p)
                for p in run_target.rglob("*")
                if p.is_file()
            }
            state[role] = {"result": result, "hashes": hashes}
            ledger.put(key, state)
        daily = {
            role: json.loads((target / "runs" / role / "primary/daily.json").read_text())
            for role in candidates
        }
        dates = {role: [r["session"] for r in rows] for role, rows in daily.items()}
        if (
            dates["candidate"] != dates["control"]
            or dates["candidate"][0] != str(program.measured_start)
            or dates["candidate"][-1] != str(program.train_end)
        ):
            raise ValueError("follow-up measured sessions are not matched")
        for rows in daily.values():
            daily_metrics(rows)  # equity/return and chronological integrity
        catalog = _catalog(root, target, task_id)
        write_json(
            target / "comparison.json",
            {
                "schema_version": "matched-reproduction/1",
                "program_sha256": program.digest(),
                "queue_sha256": queue.digest(),
                "approval_sha256": approval.digest(),
                "load_start": str(program.load_start),
                "measured_start": str(program.measured_start),
                "train_end": str(program.train_end),
                "candidate": state["candidate"]["result"],
                "control": state["control"]["result"],
                "interpretation": (
                    "Matched development reproduction; not independent validation or promotion."
                ),
                "execution_authority": "none",
            },
        )
        catalog_path = target / "catalog.toml"
        catalog_path.write_text(_catalog_text(catalog))
        new_bundle, blobs = make_bundle(catalog, f"followup_{task_id}", root)
        with Store(root / "data/governance/pmteam") as store:
            store.import_bundle(new_bundle, blobs)
        hashes = {
            str(p.relative_to(root)): digest_file(p) for p in target.rglob("*") if p.is_file()
        }
        receipt = DispatchResult(
            review_id=queue.source_review_id,
            task_id=task_id,
            operation="matched_backtest",
            status="completed",
            reason="matched_reproduction_registered",
            return_code=0,
            catalog_path=str(catalog_path.relative_to(root)),
            evidence_sha256=new_bundle.digest(),
        )
        state.update(completed=True, hashes=hashes, receipt=receipt.model_dump(mode="json"))
        ledger.put(key, state)
        ledger.event("followup_completed", task_id=task_id, evidence_sha256=new_bundle.digest())
        return receipt
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        ledger.event("followup_failed", task_id=task_id, attempt=state["attempts"])
        if isinstance(exc, subprocess.TimeoutExpired):
            raise ValueError("matched reproduction exceeded its frozen worker timeout") from None
        raise


def _catalog(root: Path, target: Path, task_id: str) -> Catalog:
    runs = tuple(
        RunSpec(
            id=f"followup_{task_id}_{role}",
            path=str((target / "runs" / role / "primary").relative_to(root)),
            config_path=str((target / "runs" / role / "primary/config.toml").relative_to(root)),
            study_index=0,
            engine=ENGINE,
        )
        for role in ("candidate", "control")
    )
    return Catalog(
        schema_version="portfolio-team-catalog/1",
        default_evaluation_use="previously_inspected_history",
        runs=runs,
        comparisons=(
            ComparisonSpec(
                id=f"followup_{task_id}",
                candidate=runs[0].id,
                control=runs[1].id,
                purpose="mechanism_test",
            ),
        ),
    )


def _catalog_text(catalog: Catalog) -> str:
    lines = [
        'schema_version = "portfolio-team-catalog/1"',
        'default_evaluation_use = "previously_inspected_history"',
    ]
    for run in catalog.runs:
        lines.append("\n[[runs]]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in run.model_dump().items())
    for comparison in catalog.comparisons:
        lines.append("\n[[comparisons]]")
        lines.extend(
            f"{key} = {json.dumps(value)}" for key, value in comparison.model_dump().items()
        )
    return "\n".join(lines) + "\n"
