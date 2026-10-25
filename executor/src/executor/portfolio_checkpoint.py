"""Verified accounting checkpoints retaining the complete activity audit window.

Checkpoints reduce repeated effect replay, not broker-history coverage. The query
start is deliberately unchanged: late postings and corrections must remain visible.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

_CHECKPOINT_DAYS = 30
_CHECKPOINT_ACTIVITIES = 1000


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def verify_checkpoint(root: Path, baseline: dict[str, Any]) -> None:
    """Verify the whole retained chain and the current anchor; never repair in place."""
    if not isinstance(baseline, dict):
        raise ValueError("invalid_checkpoint_baseline")
    current = baseline
    seen: set[str] = set()
    while reference := current.get("checkpoint_sha256"):
        if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{64}", reference):
            raise ValueError("invalid_checkpoint_reference")
        if reference in seen:
            raise ValueError("checkpoint_cycle")
        seen.add(reference)
        proof = json.loads((root / "portfolio-checkpoints" / f"{reference}.json").read_text())
        if _digest(proof) != reference or proof.get("schema") != "portfolio-checkpoint/1":
            raise ValueError("checkpoint_evidence_changed")
        anchor = {key: value for key, value in current.items() if key != "checkpoint_sha256"}
        if proof["next_baseline"] != anchor:
            raise ValueError("checkpoint_anchor_changed")
        current = proof["previous_baseline"]
        if not isinstance(current, dict):
            raise ValueError("invalid_checkpoint_baseline")


def checkpoint_activities(root: Path, baseline: dict[str, Any]) -> dict[str, str]:
    if baseline["schema"] == "portfolio-baseline/1":
        return dict(baseline["activities"])
    proof = json.loads(
        (root / "portfolio-checkpoints" / f"{baseline['checkpoint_sha256']}.json").read_text()
    )
    activities = proof["activities"]
    if (
        len(activities) != baseline["activity_count"]
        or _digest(activities) != baseline["activities_digest"]
    ):
        raise ValueError("checkpoint activity evidence changed")
    return {key: _digest(row) for key, row in activities.items()}


def checkpoint(
    root: Path,
    baseline: dict[str, Any],
    observed: dict[str, Any],
    activities: dict[str, dict[str, Any]],
    reconciliation: dict[str, Any],
    *,
    now: dt.datetime,
) -> dict[str, Any] | None:
    """Produce an immutable proof before returning a replacement baseline."""
    if reconciliation["status"] != "reconciled":
        return None
    previous_time = dt.datetime.fromisoformat(baseline["observed_at"])
    age = (now - previous_time).total_seconds()
    if age < 0:
        raise ValueError("checkpoint_clock_moved_backwards")
    count = reconciliation["activity_count_since_baseline"]
    if not count or (count < _CHECKPOINT_ACTIVITIES and age < _CHECKPOINT_DAYS * 86400):
        return None
    # Exact equality prevents repeated tolerance-sized drift being erased by rebasing.
    if reconciliation.get("exact_match") is not True:
        return None
    next_baseline = {
        "schema": "portfolio-baseline/2",
        "observed_at": now.isoformat(),
        "origin_at": baseline.get("origin_at", baseline["observed_at"]),
        "activities_after": baseline["activities_after"],
        "snapshot": observed,
        "activity_count": len(activities),
        "activities_digest": _digest(activities),
    }
    proof = {
        "schema": "portfolio-checkpoint/1",
        "previous_baseline": baseline,
        "next_baseline": next_baseline,
        "activities": activities,
        "reconciliation": reconciliation,
    }
    digest = _digest(proof)
    directory = root / "portfolio-checkpoints"
    directory.mkdir(exist_ok=True)
    path = directory / f"{digest}.json"
    if path.exists():
        if json.loads(path.read_text()) != proof:
            raise ValueError("checkpoint_evidence_changed")
    else:
        temporary = directory / f"{digest}.tmp"
        with temporary.open("w") as handle:
            handle.write(json.dumps(proof, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {**next_baseline, "checkpoint_sha256": digest}
