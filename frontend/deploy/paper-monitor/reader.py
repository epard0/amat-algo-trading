#!/usr/bin/env python3
"""Bounded read of remotely collected evidence; never polls a broker or runs a bot."""

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detail_archive import read_detail

STORE = Path(
    os.environ.get("AMAT_MONITOR_STORE", str(Path.home() / ".local/share/amat/paper-monitor"))
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def load(folder, pointer):
    name = pointer["file"]
    if Path(name).name != name:
        raise ValueError("Invalid observation path")
    path = folder / name
    # Final summaries survive detail retention; never silently substitute a different digest.
    archive_manifest = folder / "archives" / (name + ".archive.json")
    if not path.exists() and not archive_manifest.exists() and (folder / "final.json").is_file():
        final = folder / "final.json"
        if final.is_symlink() or final.stat().st_size > 2_000_000:
            raise ValueError("Invalid retained final observation")
        value = json.loads(final.read_text())
    else:
        value = json.loads(read_detail(folder, name))
    if digest(value) != pointer["sha256"]:
        raise ValueError("Observation checksum mismatch")
    if value.get("schema") != "paper-session/1" or value.get("execution_authority") != "none":
        raise ValueError("Non-monitor publication")
    return value


def read(session=None):
    if session is not None and dt.date.fromisoformat(session).isoformat() != session:
        raise ValueError("Invalid session")
    pointer_path = STORE / session / "latest.json" if session else STORE / "latest.json"
    if pointer_path.is_symlink() or pointer_path.stat().st_size > 4096:
        raise ValueError("Invalid pointer")
    pointer = json.loads(pointer_path.read_text())
    date = dt.date.fromisoformat(pointer["session"]).isoformat()
    if session and date != session:
        raise ValueError("Session mismatch")
    value = load(STORE / date, pointer)
    operations = STORE / "operational-review.json"
    if operations.is_file() and not operations.is_symlink():
        value = {**value, "operational_review": json.loads(operations.read_text())}
    preflight = STORE / "plan-preflight.json"
    if preflight.is_file() and not preflight.is_symlink():
        value = {**value, "plan_preflight": json.loads(preflight.read_text())}
    watch = STORE / "session-watch.json"
    if watch.is_file() and not watch.is_symlink():
        value = {**value, "session_watch": json.loads(watch.read_text())}
    review_path = STORE / date / "review.json"
    if review_path.is_file() and not review_path.is_symlink():
        review = json.loads(review_path.read_text())
        if review.get("source_sha256") == pointer["sha256"]:
            value = {
                **value,
                "original_audit": value["audit"],
                "audit": review["audit"],
                "session_review": review,
            }
            if review["audit"].get("precision_adjusted") and not review["audit"].get("issues"):
                message = "Unfilled or partial intents remain after the opening window"
                resolved = [
                    a
                    for a in value.get("alerts", [])
                    if a.get("session") == date and a.get("message") == message
                ]
                value["alerts"] = [a for a in value.get("alerts", []) if a not in resolved]
                value["alert_events"] = [
                    *value.get("alert_events", []),
                    *[dict(a, state="resolved", at=review["reviewed_at"]) for a in resolved],
                ]
    findings_path = STORE / date / "findings.json"
    if findings_path.is_file() and not findings_path.is_symlink():
        findings = json.loads(findings_path.read_text())
        source = findings.get("source", {})
        if (
            source.get("session") == date
            and Path(source.get("file", "")).name == source.get("file")
            and (
                (STORE / date / source["file"]).exists()
                or (STORE / date / "archives" / (source["file"] + ".archive.json")).exists()
            )
        ):
            evidence = load(STORE / date, source)
            if evidence.get("observed_at") == findings.get("through"):
                value = {**value, "session_findings": findings}
    sessions = []
    for folder in sorted(STORE.iterdir(), reverse=True):
        if not folder.is_dir() or folder.is_symlink():
            continue
        try:
            dt.date.fromisoformat(folder.name)
        except ValueError:
            continue
        if (folder / "latest.json").is_file():
            sessions.append(folder.name)
        if len(sessions) == 30:
            break
    now = dt.datetime.now(dt.UTC)
    age = (now - dt.datetime.fromisoformat(value["observed_at"])).total_seconds()
    health = (
        json.loads((STORE / "health.json").read_text()) if (STORE / "health.json").exists() else {}
    )
    current_alerts = (
        json.loads((STORE / "alerts.json").read_text()) if (STORE / "alerts.json").exists() else {}
    )
    if not session:
        value = {
            **value,
            "alerts": [
                dict(
                    id=k,
                    message=v,
                    session=current_alerts.get("details", {}).get(k, {}).get("session"),
                )
                for k, v in current_alerts.get("active", {}).items()
            ],
            "alert_events": current_alerts.get("recent", []),
        }
    return dict(
        **value,
        remote_sessions=sessions,
        remote_health=health,
        stale=(not session and (age > 180 or age < -60 or health.get("status") != "running")),
        transport="connected",
        refreshed_at=now.isoformat(),
    )


if __name__ == "__main__":
    try:
        if len(sys.argv) > 2:
            raise ValueError("At most one registered session")
        print(json.dumps(read(sys.argv[1] if len(sys.argv) == 2 else None)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "execution_authority": "none",
                    "message": "Remote monitor evidence unavailable",
                    "error_type": type(exc).__name__,
                }
            )
        )
        raise SystemExit(1)
