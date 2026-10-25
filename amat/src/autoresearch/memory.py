"""Frozen, compatible development evidence for cross-session hypothesis retrieval."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from autoresearch.contracts import Candidate, Program, Proposal
from autoresearch.workspace import digest_file

MEMORY_PATH = "runtime/research-memory.json"
MAX_RECORDS = 500
MAX_MATCHES = 4
EVALUATOR_FILES = (
    "worker.py",
    "features.py",
    "registry.py",
    "signals.py",
    "specs.py",
    "labels.py",
    "objective.py",
    "experiments.py",
    "diagnostics.py",
    "routing.py",
    "patterns.py",
    "selection.py",
    "entry_model.py",
    "entry_labels.py",
    "robustness.py",
    "market_scenarios.py",
)


def _verified(root: Path, relative: str, expected: str) -> Path:
    path = root / relative
    if (
        not path.resolve().is_relative_to(root.resolve())
        or path.is_symlink()
        or digest_file(path) != expected
    ):
        raise ValueError("evidence_hash_mismatch")
    return path


def _compatible(source: dict[str, Any], target: Program, runtime: Path) -> bool:
    for name in (
        "schema_version",
        "measured_start",
        "load_start",
        "evaluation",
        "dependencies",
        "baseline",
        "min_improvement",
        "max_drawdown",
        "min_positive_folds",
        "min_exposure",
        "max_experiments",
    ):
        if source.get(name) != target.model_dump(mode="json")[name]:
            return False
    if source["train_end"] > str(target.train_end):
        return False
    if source["baseline"]["config"] != target.baseline.model_dump(mode="json")["config"]:
        return False
    hashes = source["frozen_files"]
    # Comparing evaluator inputs, rather than proposer/report source, lets a new
    # research workflow learn from measurements computed by the same engine.
    for name in EVALUATOR_FILES:
        relative = "runtime/autoresearch/" + name
        if hashes.get(relative) != digest_file(runtime / relative):
            return False
    for path in (runtime / "runtime/amat").rglob("*.py"):
        if hashes.get(str(path.relative_to(runtime))) != digest_file(path):
            return False
    snapshot_hashes = {
        str(p.relative_to(runtime)): digest_file(p)
        for p in (runtime / "snapshot").rglob("*")
        if p.is_file()
    }
    return snapshot_hashes == {k: v for k, v in hashes.items() if k.startswith("snapshot/")}


def _records(
    root: Path, target: Program, runtime: Path, cutoff: dt.datetime
) -> list[dict[str, Any]]:
    from autoresearch.telemetry import analysis_lock  # noqa: PLC0415 - avoid exporter cycle

    with analysis_lock(root):
        _verified(root, "program.json", (root / "program.sha256").read_text())
        source = json.loads((root / "program.json").read_text())
        if not _compatible(source, target, runtime):
            raise ValueError("incompatible_evaluator_data_or_period")
        if (root / "checkpoints").exists() or (root / "checkpoint.json").exists():
            raise ValueError("checkpoint_exposed_session")
        for relative, expected in source["frozen_files"].items():
            _verified(root, relative, expected)
        with sqlite3.connect((root / "research.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            latest = db.execute("SELECT MAX(recorded_at) FROM events").fetchone()[0]
            if latest is None or dt.datetime.fromisoformat(latest) > cutoff:
                raise ValueError("unknown_or_future_research_time")
            if db.execute("SELECT 1 FROM state WHERE key LIKE 'validation:%'").fetchone():
                raise ValueError("checkpoint_exposed_session")
            rows = db.execute(
                "SELECT * FROM experiments WHERE status IN ('keep','discard') ORDER BY id"
            ).fetchall()
        records = []
        for row in rows:
            result = json.loads(row["result"])
            artifacts = result.get("artifact_hashes", {})
            objective_path = f"outputs/runs/{row['candidate']}/objective.json"
            candidate_path = root / "candidates" / f"{row['candidate']}.json"
            if objective_path not in artifacts or not candidate_path.is_file():
                raise ValueError("missing_measurement_provenance")
            for relative, expected in artifacts.items():
                _verified(root, relative, expected)
            objective = json.loads((root / objective_path).read_text())
            if any(
                result.get(k) != objective.get(k)
                for k in ("score", "train", "stress", "failed_gates")
            ):
                raise ValueError("ledger_measurement_mismatch")
            candidate = Candidate.model_validate_json(candidate_path.read_text())
            if candidate.digest() != row["candidate"]:
                raise ValueError("candidate_hash_mismatch")
            proposal = Proposal.model_validate_json(row["proposal"])
            records.append(
                {
                    "id": f"{digest_file(root / 'program.json')}:{row['id']}",
                    "candidate": candidate.digest(),
                    "hypothesis": proposal.hypothesis[:500],
                    "research": proposal.research.model_dump(mode="json")
                    if proposal.research
                    else None,
                    "features": [f.model_dump(mode="json") for f in candidate.features],
                    "patches": [p.model_dump(mode="json") for p in proposal.patches],
                    "signal": candidate.signal.model_dump(mode="json")
                    if candidate.signal
                    else None,
                    "entry_model": candidate.entry_model.model_dump(mode="json")
                    if candidate.entry_model
                    else None,
                    "status": row["status"],
                    "score": objective.get("score"),
                    "score_version": objective.get("score_version"),
                    "failed_gates": result.get("failed_gates", []),
                    "train_end": source["train_end"],
                    "research_known_at": latest,
                    "objective_sha256": artifacts[objective_path],
                    "evidence_use": "adaptive_development_only",
                }
            )
        return records


def freeze_memory(parent: Path, root: Path, program: Program) -> dict[str, Any]:
    cutoff = dt.datetime.now(dt.UTC)
    records, excluded = [], []
    for database in sorted(parent.glob("*/research.sqlite3")):
        source = database.parent.resolve()
        if source == root.resolve():
            continue
        try:
            records.extend(_records(source, program, root, cutoff))
        except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
            # Paths and raw legacy errors never enter a model request.
            known = {
                "incompatible_evaluator_data_or_period",
                "checkpoint_exposed_session",
                "unknown_or_future_research_time",
                "missing_measurement_provenance",
                "ledger_measurement_mismatch",
                "candidate_hash_mismatch",
                "evidence_hash_mismatch",
            }
            excluded.append(
                {
                    "session": source.name,
                    "reason": str(exc) if str(exc) in known else "unavailable_or_invalid_evidence",
                }
            )
    records.sort(key=lambda r: (r["research_known_at"], r["id"]), reverse=True)
    return {
        "schema_version": "research-memory/1",
        "research_cutoff": cutoff.isoformat(),
        "market_cutoff": str(program.train_end),
        "eligible_count": len(records),
        "truncated_count": max(0, len(records) - MAX_RECORDS),
        "excluded_sessions": excluded,
        "records": records[:MAX_RECORDS],
    }


def related_experiments(
    root: Path, program: Program, candidate: Candidate, brief: str = ""
) -> list[dict[str, Any]]:
    expected = program.frozen_files.get(MEMORY_PATH)
    if expected is None:
        return []
    path = _verified(root, MEMORY_PATH, expected)
    payload = json.loads(path.read_text())

    def families(value: Any) -> set[str]:
        if isinstance(value, dict):
            return (
                {str(value["kind"])}
                if "kind" in value
                else set().union(*(families(v) for v in value.values()))
            )
        if isinstance(value, list):
            return set().union(*(families(v) for v in value))
        return set()

    query = families(candidate.model_dump(mode="json").get("signal"))
    query |= {feature.kind for feature in candidate.features}
    if candidate.entry_model:
        query |= {"entry_model", "deferral", "adverse", "opportunity"}
    query |= set(re.findall(r"[a-z_]{3,}", brief.lower()))
    if not query:
        query = {"trend", "momentum"}

    def relevance(row: dict[str, Any]) -> tuple[int, str]:
        words = set(
            re.findall(
                r"[a-z_]{3,}",
                json.dumps(
                    {
                        k: row.get(k)
                        for k in ("features", "signal", "entry_model", "hypothesis", "research")
                    }
                ).lower(),
            )
        )
        return len(query & words), row["id"]

    rows = sorted(payload["records"], key=relevance, reverse=True)
    return [row for row in rows if relevance(row)[0] > 0][:MAX_MATCHES]


def known_candidates(root: Path, program: Program) -> set[str]:
    """Exact repeated recipes on identical measured periods need no automatic retry."""
    expected = program.frozen_files.get(MEMORY_PATH)
    if expected is None:
        return set()
    payload = json.loads(_verified(root, MEMORY_PATH, expected).read_text())
    return {
        row["candidate"] for row in payload["records"] if row["train_end"] == str(program.train_end)
    }
