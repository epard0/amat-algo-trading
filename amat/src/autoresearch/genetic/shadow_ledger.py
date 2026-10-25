"""Hash-chained diagnostic observations; no broker or order-generation capability."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from autoresearch.genetic.data import digest_json

LEDGER_VERSION = "genetic-shadow-ledger/1"
OBSERVATION_VERSION = "genetic-shadow-observation/1"
FORBIDDEN = {"orders", "order_intents", "execution_plan", "broker_request"}


def _inert(value: Any) -> None:
    if isinstance(value, dict):
        if FORBIDDEN.intersection(value):
            raise ValueError("shadow records cannot carry order instructions")
        for item in value.values():
            _inert(item)
    elif isinstance(value, list):
        for item in value:
            _inert(item)


def _identity(report: dict[str, Any]) -> str:
    if report.get("schema_version") != OBSERVATION_VERSION:
        raise ValueError("unsupported shadow observation")
    if report.get("execution_authority") != "none" or report.get("promotion_authority") != "none":
        raise ValueError("shadow observations must have no execution or promotion authority")
    if report.get("diagnostic_only") is not True:
        raise ValueError("shadow observation must remain diagnostic-only")
    for name in ("artifact_sha256", "panel_sha256"):
        if not isinstance(report.get(name), str) or not re.fullmatch(r"[a-f0-9]{64}", report[name]):
            raise ValueError(f"invalid {name}")
    if report.get("recommended_monitor_state") not in {"observing", "suspended"}:
        raise ValueError("invalid monitor state")
    if not isinstance(report.get("alerts"), list):
        raise ValueError("shadow alerts must be a list")
    if not isinstance(report.get("as_of"), str):
        raise ValueError("as_of must be an ISO session date")
    if report["recommended_monitor_state"] == "suspended":
        for asset in report.get("assets", []):
            if (
                asset["reported_score"] != asset["baseline_score"]
                or asset["bounded_contribution"] != 0
            ):
                raise ValueError("suspended shadow contributions must be neutral")
    day = dt.date.fromisoformat(report["as_of"])
    if day.isoformat() != report["as_of"]:
        raise ValueError("as_of must be an ISO session date")
    _inert(report)
    # Timing is operational telemetry, not part of a deterministic observation.
    return digest_json({key: value for key, value in report.items() if key != "latency_ms"})


def read_shadow_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if (
        set(data) != {"schema_version", "artifact_sha256", "records", "record_count", "head_sha256"}
        or data["schema_version"] != LEDGER_VERSION
    ):
        raise ValueError("unsupported or malformed shadow ledger")
    records = data["records"]
    if not isinstance(records, list) or not records:
        raise ValueError("existing shadow ledger must contain records")
    if data["record_count"] != len(records) or data["head_sha256"] != records[-1].get("sha256"):
        raise ValueError("shadow ledger count or head mismatch")
    previous, day, state = None, "", "observing"
    for index, record in enumerate(records):
        if set(record) != {
            "sequence",
            "previous_sha256",
            "observation_sha256",
            "report",
            "effective_monitor_state",
            "sha256",
        }:
            raise ValueError("malformed shadow record")
        report = record["report"]
        identity = _identity(report)
        expected_state = (
            "suspended"
            if state == "suspended" or report["recommended_monitor_state"] == "suspended"
            else "observing"
        )
        if expected_state == "suspended" and any(
            row["reported_score"] != row["baseline_score"] or row["bounded_contribution"] != 0
            for row in report.get("assets", [])
        ):
            raise ValueError("suspended ledger contributions must be neutral")
        if (
            record["sequence"] != index
            or record["previous_sha256"] != previous
            or record["observation_sha256"] != identity
            or report["artifact_sha256"] != data["artifact_sha256"]
            or report["as_of"] <= day
            or record["effective_monitor_state"] != expected_state
            or digest_json({k: v for k, v in record.items() if k != "sha256"}) != record["sha256"]
        ):
            raise ValueError("shadow ledger integrity or chronology violation")
        previous, day, state = record["sha256"], report["as_of"], expected_state
    return list(records)


def _publish(path: Path, data: dict[str, Any]) -> None:
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(data, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def record_shadow_observation(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Append atomically; identical repeats are inert, suspension never self-clears."""
    _identity(report)
    report = json.loads(json.dumps(report))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = read_shadow_ledger(path)
        previous = records[-1] if records else None
        earlier = [row for row in records if row["report"]["as_of"] < report["as_of"]]
        if earlier and earlier[-1]["effective_monitor_state"] == "suspended":
            report["recommended_monitor_state"] = "suspended"
            alert = {"symbol": "*", "code": "suspension_latched"}
            if alert not in report["alerts"]:
                report["alerts"].append(alert)
            for asset in report.get("assets", []):
                asset["reported_score"] = asset["baseline_score"]
                asset["bounded_contribution"] = 0.0
        identity = _identity(report)
        if previous is not None:
            last = previous["report"]
            if report["artifact_sha256"] != last["artifact_sha256"]:
                raise ValueError("a shadow ledger is bound to one immutable artifact")
            if report["as_of"] == last["as_of"]:
                if identity == previous["observation_sha256"]:
                    return previous
                raise ValueError("same-session shadow observation changed")
            if report["as_of"] < last["as_of"]:
                raise ValueError("shadow sessions cannot rewind")
        state = report["recommended_monitor_state"]
        if previous is not None and previous["effective_monitor_state"] == "suspended":
            state = "suspended"
        record = {
            "sequence": len(records),
            "previous_sha256": previous["sha256"] if previous else None,
            "observation_sha256": identity,
            "report": json.loads(json.dumps(report)),
            "effective_monitor_state": state,
        }
        record["sha256"] = digest_json(record)
        _publish(
            path,
            {
                "schema_version": LEDGER_VERSION,
                "artifact_sha256": report["artifact_sha256"],
                "records": [*records, record],
                "record_count": len(records) + 1,
                "head_sha256": record["sha256"],
            },
        )
        return record
