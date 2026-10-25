"""Durable best-result archive, separate from automatic or operator promotion."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


def record_best(
    root: Path, *, program: dict[str, Any], candidate: str, result: dict[str, Any], decision: str
) -> None:
    source = root / "candidates" / f"{candidate}.json"
    if not source.is_file():
        raise ValueError("best strategy requires recorded candidate")
    body = source.read_text()
    raw = json.loads(body)
    canonical = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if canonical != candidate:
        raise ValueError("best strategy candidate digest mismatch")
    annual = result.get("train", {}).get("annualized_return")
    score = result.get("score")
    if (
        annual is None
        or not math.isfinite(annual)
        or (score is not None and not math.isfinite(score))
    ):
        return
    contract = {
        key: program.get(key) for key in ("train_end", "measured_start", "load_start", "evaluation")
    }
    contract["score_version"] = result.get("score_version")
    contract["baseline"] = program.get("baseline")
    contract["snapshot"] = {
        key: value
        for key, value in program.get("frozen_files", {}).items()
        if key.startswith("snapshot/")
    }
    group = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    payload = json.dumps(
        {
            "candidate": raw,
            "result": result,
            "decision": decision,
            "source_session": str(root),
            "contract": contract,
            "execution_authority": "none",
        },
        sort_keys=True,
        allow_nan=False,
    )
    path = root.parent / "best-strategies.sqlite3"
    with sqlite3.connect(path, timeout=30) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS strategies(session TEXT, candidate TEXT, "
            "comparison_group TEXT, annual_return REAL, score REAL, eligible INTEGER, "
            "payload TEXT, PRIMARY KEY(session,candidate))"
        )
        db.execute(
            "INSERT INTO strategies VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(session,candidate) DO NOTHING",
            (
                str(root),
                candidate,
                group,
                annual,
                score,
                int(result.get("eligible", False)),
                payload,
            ),
        )
        rows = db.execute(
            "SELECT candidate,annual_return,score,eligible,session FROM strategies "
            "WHERE comparison_group=? ORDER BY annual_return DESC,candidate",
            (group,),
        ).fetchall()
        eligible = [row for row in rows if row[3] and row[2] is not None]
        best_score = max(eligible, key=lambda row: row[2]) if eligible else None
        summary = {
            "comparison_group": group,
            "best_return": rows[0],
            "best_eligible_score": best_score,
            "recorded_candidates": len(rows),
            "execution_authority": "none",
            "interpretation": "return and eligible-score leaders are distinct; no promotion",
        }
        # Per-session projection; other sessions cannot replace this session's view.
        temporary = root / "best-strategies.json.tmp"
        temporary.write_text(json.dumps(summary, indent=2) + "\n")
        temporary.replace(root / "best-strategies.json")


def preserve_return_reference(study: Path, directory: Path) -> dict[str, Any]:
    """Retain selected fixed-study evidence without claiming it won a different protocol."""
    names = (
        "protocol.json",
        "selection.json",
        "development.json",
        "validation.json",
        "decision.json",
    )
    bodies = {name: (study / name).read_bytes() for name in names}
    documents = {name: json.loads(body) for name, body in bodies.items()}
    selected = documents["selection.json"]["selected"]
    if selected is None:
        raise ValueError("return reference has no selected recipe")
    if (
        documents["selection.json"]["development_sha256"]
        != hashlib.sha256(bodies["development.json"]).hexdigest()
    ):
        raise ValueError("return reference development evidence changed")
    payload = {
        "schema": "return-reference/1",
        "source": str(study.resolve()),
        "selected": selected,
        "protocol": documents["protocol.json"],
        "config": documents["protocol.json"]["configs"][selected],
        "development": documents["development.json"][selected],
        "validation": documents["validation.json"][selected],
        "decision": documents["decision.json"],
        "artifact_hashes": {
            name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()
        },
        "execution_authority": "none",
        "status": "preserved_recorded_reference",
    }
    serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
    identity = hashlib.sha256(serialized.encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(directory / "best-strategies.sqlite3", timeout=30) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS return_references(id TEXT PRIMARY KEY, payload TEXT)"
        )
        db.execute("INSERT OR IGNORE INTO return_references VALUES(?,?)", (identity, serialized))
    return {"id": identity, "selected": selected, "validation": payload["validation"]}
