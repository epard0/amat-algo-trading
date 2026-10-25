"""Explicit operator selections, separate from immutable automatic decisions."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from autoresearch.contracts import Candidate, Program, validate_candidate
from autoresearch.telemetry import analysis_lock, read_telemetry
from autoresearch.workspace import digest_file, write_json


def active_promotion(directory: Path) -> dict[str, Any] | None:
    pointer = directory / "active-strategy.json"
    if not pointer.exists():
        return None
    selection = json.loads(pointer.read_text())
    path = directory / selection["record"]
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("promotion record escapes research directory")
    if digest_file(path) != selection["sha256"]:
        raise ValueError("active promotion record changed")
    record = json.loads(path.read_text())
    candidate = Candidate.model_validate(record["candidate"])
    if candidate.digest() != record["candidate_id"]:
        original = record["candidate"]
        original_digest = hashlib.sha256(
            json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        migrated = candidate.model_dump(mode="json")
        # Only the known additive optional field may account for the difference.
        if isinstance(migrated.get("signal"), dict) and migrated["signal"].get("selection") is None:
            migrated["signal"].pop("selection")
        if original_digest != record["candidate_id"] or migrated != original:
            raise ValueError("promoted candidate digest mismatch")
        record["runtime_compatibility"] = {
            "migration": "optional_signal_selection_default",
            "source_candidate_id": record["candidate_id"],
            "runtime_candidate_id": candidate.digest(),
        }
    if record["execution_authority"] != "none" or record["status"] != "operator_selected":
        raise ValueError("invalid research promotion")
    return dict(record)


def promote(
    root: Path, directory: Path, candidate_id: str, *, label: str, reason: str
) -> dict[str, Any]:
    """Select a measured recipe without relabeling a failed experiment as passing."""
    if not re.fullmatch(r"[a-f0-9]{64}", candidate_id):
        raise ValueError("promotion requires an exact candidate digest")
    if not label.strip() or not reason.strip():
        raise ValueError("promotion requires a label and explicit operator reason")
    root, directory = root.resolve(), directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "promotion.lock").open("a") as lock, analysis_lock(root):
        fcntl.flock(lock, fcntl.LOCK_EX)
        telemetry = read_telemetry(root)
        if any(row["status"] == "running" for row in telemetry["experiments"]):
            raise ValueError("wait for the active experiment to complete before promotion")
        if digest_file(root / "program.json") != (root / "program.sha256").read_text():
            raise ValueError("source program changed")
        program = Program.model_validate_json((root / "program.json").read_text())
        row = next(
            (
                row
                for row in reversed(telemetry["experiments"])
                if row["candidate"] == candidate_id
                and row["status"] in ("baseline", "keep", "discard")
                and row["result"]
            ),
            None,
        )
        if row is None:
            raise ValueError("candidate has no completed backtest in this session")
        candidate_path = root / "candidates" / f"{candidate_id}.json"
        candidate = Candidate.model_validate_json(candidate_path.read_text())
        if candidate.digest() != candidate_id:
            raise ValueError("source candidate changed")
        validate_candidate(program.baseline, candidate)
        result = json.loads(row["result"])
        if result.get("return_floor", {}).get("passes") is False:
            raise ValueError("candidate is below the required incumbent net-return floor")
        evidence = result.get("artifact_hashes", {})
        objective_path = f"outputs/runs/{candidate_id}/objective.json"
        if objective_path not in evidence:
            raise ValueError("promotion requires recorded objective evidence")
        for relative, expected in {**program.frozen_files, **evidence}.items():
            path = root / relative
            if not path.resolve().is_relative_to(root) or digest_file(path) != expected:
                raise ValueError("promotion evidence changed: " + relative)
        objective = json.loads((root / objective_path).read_text())
        for key in ("score", "score_version", "failed_gates", "eligible"):
            if objective.get(key) != result.get(key):
                raise ValueError("ledger and objective evidence disagree")
        previous = active_promotion(directory)
        record = {
            "schema_version": "research-promotion/1",
            "status": "operator_selected",
            "label": label,
            "reason": reason,
            "selected_at": dt.datetime.now(dt.UTC).isoformat(),
            "candidate_id": candidate_id,
            "candidate": candidate.model_dump(mode="json"),
            "source_session": str(root),
            "source_program_sha256": program.digest(),
            "source_experiment_id": row["id"],
            "source_candidate_sha256": digest_file(candidate_path),
            "source_objective_sha256": evidence[objective_path],
            "automatic_decision": row["status"],
            "automatic_score": result.get("score"),
            "automatic_failed_gates": result.get("failed_gates", []),
            "automatic_uncertainty": result.get("uncertainty"),
            "automatic_checks_overridden": row["status"] != "keep",
            "previous_candidate_id": previous["candidate_id"] if previous else None,
            "applies_to": "starting baseline for new simple research sessions",
            "execution_authority": "none",
        }
        relative = f"promotions/{uuid.uuid4().hex}.json"
        write_json(directory / relative, record)
        write_json(
            directory / "active-strategy.json",
            {
                "record": relative,
                "sha256": digest_file(directory / relative),
            },
        )
        return record


def select_v13_reference(source: Path, directory: Path) -> dict[str, Any]:
    """User-authorized baseline import from the selected fixed return-target study."""
    from autoresearch.baseline import load_research_config  # noqa: PLC0415
    from autoresearch.best_strategies import preserve_return_reference  # noqa: PLC0415

    reference = json.loads((source / "configs/autoresearch/baseline-reference.json").read_text())
    config = source / reference["strategy_config"]
    study = source / reference["study"]
    for path in (config, study):
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("baseline reference escapes source")
    if digest_file(config) != reference["config_sha256"]:
        raise ValueError("baseline configuration changed")
    for relative, expected in reference["artifacts"].items():
        path = study / relative
        if not path.resolve().is_relative_to(study.resolve()) or digest_file(path) != expected:
            raise ValueError("baseline reference evidence changed")
    candidate = Candidate(config=load_research_config(config))
    archived = preserve_return_reference(study, directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "promotion.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = active_promotion(directory)
        if previous and previous["candidate_id"] == candidate.digest():
            return previous
        record = {
            "schema_version": "research-promotion/2",
            "status": "operator_selected",
            "label": "v13 · growth_rotation4_20_weekly",
            "reason": "User selected the preserved v13 study as the research baseline.",
            "selected_at": dt.datetime.now(dt.UTC).isoformat(),
            "candidate_id": candidate.digest(),
            "candidate": candidate.model_dump(mode="json"),
            "source_study": str(study.resolve()),
            "reference_archive_id": archived["id"],
            "source_config_sha256": reference["config_sha256"],
            "source_artifact_hashes": reference["artifacts"],
            "historical_results": {
                name: data["later"]["annualized_return"]
                for name, data in archived["validation"].items()
            },
            "previous_candidate_id": previous["candidate_id"] if previous else None,
            "applies_to": "starting baseline for new daily research sessions",
            "execution_authority": "none",
            "statistical_approval": "not_inferred_from_selection",
        }
        relative = f"promotions/{uuid.uuid4().hex}.json"
        write_json(directory / relative, record)
        write_json(
            directory / "active-strategy.json",
            {"record": relative, "sha256": digest_file(directory / relative)},
        )
        return record
