"""Verified-snapshot, diagnostic-only shadow packaging and manual observations."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.data.snapshot import load_panel
from autoresearch.calendar_audit import audit_sessions
from autoresearch.contracts import Candidate
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.replay import attach_pattern
from autoresearch.genetic.shadow_artifact import (
    freeze_shadow_artifact,
    load_shadow_artifact,
    verify_calibration,
)
from autoresearch.genetic.shadow_inference import infer_shadow
from autoresearch.genetic.shadow_ledger import (
    OBSERVATION_VERSION,
    read_shadow_ledger,
    record_shadow_observation,
)
from autoresearch.genetic.study import write_json
from autoresearch.market_scenarios import panel_digest


def verify_evidence(directory: Path) -> dict[str, Any]:
    """Verify every retained input, refusing path traversal or external symlinks."""
    directory = directory.resolve()
    manifest = json.loads((directory / "evidence-manifest.json").read_text())
    if manifest.get("schema_version") != "p4-evidence-manifest/1":
        raise ValueError("shadow packaging requires a P4 evidence manifest")
    for name, expected in manifest["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError("evidence paths must be existing files inside the study")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"P4 evidence changed: {name}")
    for required in ("protocol.json", "report.json"):
        if required not in manifest["files"]:
            raise ValueError("P4 manifest omits required provenance")
    protocol = json.loads((directory / "protocol.json").read_text())
    report = json.loads((directory / "report.json").read_text())
    if report["status"] != "completed" or report["protocol_sha256"] != digest_json(protocol):
        raise ValueError("P4 report is incomplete or inconsistent")
    return {
        "protocol": protocol,
        "report": report,
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(
            (directory / "evidence-manifest.json").read_bytes()
        ).hexdigest(),
    }


def freeze_from_validation(
    study: Path,
    snapshot: Path,
    *,
    fold: str,
    method: str,
    rank: int,
    output: Path,
    allow_registry_rebind: bool = False,
) -> dict[str, Any]:
    """Package one observed shortlist member for diagnostics, including its rejection."""
    evidence = verify_evidence(study)
    protocol = evidence["protocol"]
    if fold not in protocol["folds"] or method not in {"genetic", "random"}:
        raise ValueError("unknown observed fold or search method")
    if type(rank) is not int or rank < 0:
        raise ValueError("shortlist rank must be a nonnegative integer")
    relative = f"observed/{fold}/discovery/report.json"
    selection_relative = f"observed/{fold}/portfolio/selection.json"
    if any(name not in evidence["manifest"]["files"] for name in (relative, selection_relative)):
        raise ValueError("P4 manifest omits observed selection provenance")
    discovery = json.loads((study / relative).read_text())
    patterns = discovery["contenders"][method]
    if rank >= len(patterns):
        raise ValueError("shortlist rank is unavailable")
    candidate = attach_pattern(
        Candidate.model_validate(protocol["incumbent"]), parse_pattern(patterns[rank])
    )
    selection = json.loads((study / selection_relative).read_text())["selections"][method]
    historical_sha256 = candidate.digest()
    registry_rebound = False
    if allow_registry_rebind and candidate.genetic_pattern is not None:
        historical_candidate = candidate.model_dump(mode="json")
        adapter = historical_candidate["genetic_pattern"]
        if "registry_sha256" in adapter:
            adapter["registry_sha256"] = digest_json(protocol["registry"])
            adapter["registry_version"] = protocol["registry"]["version"]
            historical_sha256 = digest_json(historical_candidate)
            registry_rebound = historical_sha256 != candidate.digest()
    contender = next(
        (row for row in selection["contenders"] if row["candidate_sha256"] == historical_sha256),
        None,
    )
    if contender is None:
        raise ValueError("shortlisted pattern has no matching inner evaluation")
    start = dt.date.fromisoformat(protocol["start"])
    end = dt.date.fromisoformat(protocol["folds"][fold]["fit_end"])
    symbols = tuple(str(symbol) for symbol in candidate.config.strategy.universe.symbols)
    panel, _ = load_panel(snapshot, symbols, start=start, end=end, with_context=True)
    audit_sessions(list(panel.sessions), start=start, end=end)
    if panel_digest(panel) != protocol["folds"][fold]["fit_panel_sha256"]:
        raise ValueError("calibration snapshot differs from frozen P4 fitting data")
    provenance = {
        "source": "P4 observed shortlist; diagnostic packaging does not promote it",
        "validation_protocol_sha256": digest_json(protocol),
        "evidence_manifest_sha256": evidence["manifest_sha256"],
        "fold": fold,
        "method": method,
        "rank": rank,
        "inner_objective": contender["objective"],
        "selected_by_inner": not registry_rebound
        and selection["selected_sha256"] == historical_sha256,
        "method_historical_support": not registry_rebound
        and evidence["report"]["statistics"]["methods"][method]["historical_support"],
        "method_failed_gates": evidence["report"]["statistics"]["methods"][method]["failed_gates"],
        "forward_observation_status": "not yet observed prospectively",
        "historical_candidate_sha256": historical_sha256,
        "registry_rebound": registry_rebound,
        "historical_validation_transferred": not registry_rebound,
        "registry_rebind_scope": "diagnostic expression reuse only; no historical approval",
    }
    return freeze_shadow_artifact(candidate, panel, provenance=provenance, output=output)


def _prefix(panel: MarketPanel, end: dt.date) -> MarketPanel:
    stop = panel.sessions.index(end) + 1
    arrays: dict[str, Any] = {
        field.name: value[:stop].copy()
        for field in fields(panel)
        if isinstance(value := getattr(panel, field.name), np.ndarray)
    }
    return replace(panel, sessions=panel.sessions[:stop], **arrays)


def observe_shadow(
    artifact_path: Path,
    snapshot: Path,
    *,
    as_of: dt.date,
    ledger: Path,
    output: Path,
) -> dict[str, Any]:
    """Load through an explicit completed session and publish a report, never orders."""
    reserved = {
        artifact_path.resolve(),
        ledger.resolve(),
        ledger.with_suffix(ledger.suffix + ".lock").resolve(),
    }
    if (
        output.suffix != ".json"
        or output.resolve() in reserved
        or output.with_suffix(".md").resolve() in reserved
    ):
        raise ValueError("shadow output must be a distinct JSON report path")
    artifact = load_shadow_artifact(artifact_path)
    payload = artifact["payload"]
    start = dt.date.fromisoformat(payload["calibration"]["start"])
    calibration_end = dt.date.fromisoformat(payload["calibration"]["end"])
    if as_of <= calibration_end:
        raise ValueError("shadow observations must follow the calibration interval")
    panel, manifest = load_panel(
        snapshot, tuple(payload["universe"]), start=start, end=as_of, with_context=True
    )
    if panel.sessions[-1] != as_of:
        raise ValueError("as_of must be an available completed snapshot session")
    calendar = audit_sessions(list(panel.sessions), start=start, end=as_of)
    verify_calibration(artifact, _prefix(panel, calibration_end))
    report = infer_shadow(artifact, panel)
    report["schema_version"] = OBSERVATION_VERSION
    report["as_of"] = as_of.isoformat()
    report["panel_sha256"] = panel_digest(panel)
    report["latency_ms"] = report.pop("latency_ns") / 1_000_000
    report["alerts"] = report["monitor"]["alerts"]
    report["snapshot"] = manifest.model_dump(mode="json")
    report["calendar_sha256"] = digest_json(calendar)
    report["evidence_level"] = "historical_snapshot_shadow_diagnostic"
    # Latching is a monitoring decision only. A new immutable artifact is required
    # for a new monitoring campaign; the ledger never auto-clears suspension.
    existing = read_shadow_ledger(ledger)
    prior = [row for row in existing if row["report"]["as_of"] < report["as_of"]]
    if prior and prior[-1]["effective_monitor_state"] == "suspended":
        report["recommended_monitor_state"] = "suspended"
        report["alerts"].append({"symbol": "*", "code": "suspension_latched"})
        for row in report["assets"]:
            row["reported_score"] = row["baseline_score"]
            row["bounded_contribution"] = 0.0
    # Refuse a conflicting output before appending durable ledger state.
    if output.exists():
        saved = json.loads(output.read_text())
        comparable = {k: v for k, v in report.items() if k != "latency_ms"}
        if {k: v for k, v in saved["report"].items() if k != "latency_ms"} != comparable:
            raise ValueError("shadow report output exists with different observation")
    record = record_shadow_observation(ledger, report)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, record)
    markdown = output.with_suffix(".md")
    lines = [
        "# Diagnostic pattern observation",
        "",
        f"Session: {as_of}; monitor state: {record['effective_monitor_state']}.",
        "",
        "Report only. No execution or promotion authority. Historical snapshot evidence.",
        "",
        "| Asset | Pattern value | Baseline score | Reported score |",
        "|---|---:|---:|---:|",
    ]
    for row in record["report"]["assets"]:
        lines.append(
            f"| {row['symbol']} | {row['pattern_value']} | "
            f"{row['baseline_score']} | {row['reported_score']} |"
        )
    lines.extend(["", "Alerts:", *[f"- {alert}" for alert in record["report"]["alerts"]]])
    markdown.write_text("\n".join(lines) + "\n")
    return record
