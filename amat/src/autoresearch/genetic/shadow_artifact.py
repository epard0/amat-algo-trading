"""Immutable, dependency-bound pattern artifacts with report-only authority."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.contracts import Candidate, Program
from autoresearch.genetic.adapter import GeneticSymbolicAdapterSpec
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import CandleGenome
from autoresearch.genetic.shadow_inference import calibrate_values
from autoresearch.genetic.study import _runtime_versions
from autoresearch.genetic.symbolic import symbolic_required_history
from autoresearch.genetic.symbolic_study import _source_hashes
from autoresearch.market_scenarios import panel_digest
from autoresearch.signals import build_signal

PROBE_ROWS = 5
MIN_PROGRAM_SESSIONS = 4
SHA256_LENGTH = 64
MONITOR_POLICY = {
    "min_available_fraction": 0.8,
    "max_abs_mean_z": 4.0,
    "max_prevalence_shift": 0.35,
    "max_score_change": 0.5,
}
_PAYLOAD_KEYS = {
    "schema_version",
    "diagnostic_only",
    "execution_authority",
    "promotion_authority",
    "candidate",
    "candidate_sha256",
    "sources",
    "runtime_versions",
    "registry",
    "universe",
    "calibration",
    "required_history",
    "reference_stats",
    "reference_window_sessions",
    "probes",
    "provenance",
    "provenance_sha256",
    "monitor_policy",
}


def _candidate(candidate: Candidate) -> Candidate:
    value = Candidate.model_validate(candidate.model_dump(mode="json"))
    if value.genetic_pattern is None:
        raise ValueError("shadow artifact requires a genetic pattern")
    if value.signal is not None or value.features or value.entry_model is not None:
        raise ValueError(
            "shadow inference requires a fixed native baseline without fitted adapters"
        )
    return value


def _history(candidate: Candidate) -> int:
    if candidate.genetic_pattern is None:
        raise ValueError("shadow artifact requires a genetic pattern")
    pattern = candidate.genetic_pattern.pattern
    return (
        pattern.genome.max_lag
        if isinstance(pattern.genome, CandleGenome)
        else symbolic_required_history(pattern)
    )


def _registry(candidate: Candidate) -> dict[str, str] | None:
    spec = candidate.genetic_pattern
    if isinstance(spec, GeneticSymbolicAdapterSpec):
        return {"version": spec.registry_version, "sha256": spec.registry_sha256}
    return None


def _panel(candidate: Candidate, panel: MarketPanel) -> None:
    if list(map(str, panel.symbols)) != list(map(str, candidate.config.strategy.universe.symbols)):
        raise ValueError("shadow panel requires the exact ordered candidate universe")
    if len(panel) < MIN_PROGRAM_SESSIONS or any(
        a >= b for a, b in zip(panel.sessions, panel.sessions[1:], strict=False)
    ):
        raise ValueError("shadow panel requires at least four ordered unique sessions")


def make_shadow_program(candidate: Candidate, panel: MarketPanel) -> Program:
    """Create a no-fitting program; dates are structural and never select outcomes."""
    candidate = _candidate(candidate)
    _panel(candidate, panel)
    return Program(
        schema_version="strategy-development/9",
        name="pattern-shadow-report",
        baseline=candidate,
        load_start=panel.sessions[0],
        measured_start=panel.sessions[1],
        train_end=panel.sessions[-2],
        validation_end=panel.sessions[-1],
        holdout_after=panel.sessions[-1],
        frozen_files={},
        dependencies={},
    )


def evaluate_shadow(
    candidate: Candidate, panel: MarketPanel
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute numerical diagnostics only, without labels or any execution engine."""
    candidate = _candidate(candidate)
    program = make_shadow_program(candidate, panel)
    plain = Candidate.model_validate({**candidate.model_dump(mode="json"), "genetic_pattern": None})
    baseline = build_signal(panel, plain, program).scores
    trace = build_signal(panel, candidate, program)
    if trace.genetic_scores is None:
        raise ValueError("shadow evaluator returned no genetic values")
    return trace.genetic_scores, baseline, trace.scores


def _matrix(values: np.ndarray) -> list[list[float | None]]:
    return [[float(v) if np.isfinite(v) else None for v in row] for row in values]


def _probes(
    panel: MarketPanel, values: tuple[np.ndarray, np.ndarray, np.ndarray]
) -> dict[str, Any]:
    return {
        "dates": [date.isoformat() for date in panel.sessions[-PROBE_ROWS:]],
        **{
            key: _matrix(value[-PROBE_ROWS:])
            for key, value in zip(
                ("values", "baseline_scores", "modified_scores"), values, strict=True
            )
        },
    }


def freeze_shadow_artifact(
    candidate: Candidate,
    calibration_panel: MarketPanel,
    *,
    provenance: dict[str, Any],
    output: Path,
) -> dict[str, Any]:
    """Freeze an inert diagnostic artifact once; provenance cannot convey authority."""
    if output.exists():
        raise FileExistsError(f"shadow artifact already exists: {output}")
    candidate = _candidate(candidate)
    _panel(candidate, calibration_panel)
    history = _history(candidate)
    if len(calibration_panel) <= max(
        history, candidate.config.strategy.warmup.min_completed_sessions
    ):
        raise ValueError("calibration panel does not cover completed warm-up history")
    provenance = json.loads(json.dumps(provenance, allow_nan=False))
    if not isinstance(provenance, dict):
        raise ValueError("provenance must be a JSON object")
    sources, runtime = _source_hashes(), _runtime_versions()
    initial_digest = panel_digest(calibration_panel)
    values = evaluate_shadow(candidate, calibration_panel)
    start = max(history, len(calibration_panel) - 252)
    candidate_json = candidate.model_dump(mode="json")
    payload = {
        "schema_version": "shadow-pattern/1",
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "candidate": candidate_json,
        "candidate_sha256": digest_json(candidate_json),
        "sources": sources,
        "runtime_versions": runtime,
        "registry": _registry(candidate),
        "universe": list(map(str, calibration_panel.symbols)),
        "calibration": {
            "start": calibration_panel.sessions[0].isoformat(),
            "end": calibration_panel.sessions[-1].isoformat(),
            "sessions": len(calibration_panel),
            "panel_sha256": initial_digest,
        },
        "required_history": history,
        "reference_window_sessions": len(calibration_panel) - start,
        "reference_stats": calibrate_values(values[0][start:], calibration_panel.symbols),
        "probes": _probes(calibration_panel, values),
        "monitor_policy": dict(MONITOR_POLICY),
        "provenance": provenance,
        "provenance_sha256": digest_json(provenance),
    }
    envelope = {"payload": payload, "payload_sha256": digest_json(payload)}
    validate_shadow_artifact(envelope)
    if sources != _source_hashes() or initial_digest != panel_digest(calibration_panel):
        raise ValueError("shadow calibration inputs changed during freezing")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return envelope


def validate_shadow_artifact(envelope: dict[str, Any]) -> dict[str, Any]:
    """Fail closed for changed formulas, dependencies, or asserted authority."""
    try:
        _validate(envelope)
    except (KeyError, TypeError, AssertionError, OverflowError) as exc:
        raise ValueError("invalid shadow artifact structure") from exc
    return envelope


def _validate(envelope: dict[str, Any]) -> None:
    json.dumps(envelope, allow_nan=False)
    if set(envelope) != {"payload", "payload_sha256"}:
        raise ValueError("invalid shadow artifact envelope")
    payload = envelope["payload"]
    if set(payload) != _PAYLOAD_KEYS or digest_json(payload) != envelope["payload_sha256"]:
        raise ValueError("shadow artifact payload checksum or fields mismatch")
    if (
        payload["schema_version"] != "shadow-pattern/1"
        or payload["diagnostic_only"] is not True
        or payload["execution_authority"] != "none"
        or payload["promotion_authority"] != "none"
    ):
        raise ValueError("shadow artifact cannot grant execution or promotion authority")
    candidate = _candidate(Candidate.model_validate(payload["candidate"]))
    if digest_json(payload["candidate"]) != payload["candidate_sha256"]:
        raise ValueError("shadow candidate checksum mismatch")
    if payload["sources"] != _source_hashes() or payload["runtime_versions"] != _runtime_versions():
        raise ValueError("shadow inference source or runtime mismatch")
    if (
        payload["registry"] != _registry(candidate)
        or payload["required_history"] != _history(candidate)
        or payload["universe"] != list(map(str, candidate.config.strategy.universe.symbols))
    ):
        raise ValueError("shadow registry, universe or history mismatch")
    if payload["monitor_policy"] != MONITOR_POLICY:
        raise ValueError("shadow monitoring policy mismatch")
    if (
        not isinstance(payload["provenance"], dict)
        or digest_json(payload["provenance"]) != payload["provenance_sha256"]
    ):
        raise ValueError("shadow provenance checksum mismatch")
    _validate_calibration(payload, candidate)


def _validate_calibration(payload: dict[str, Any], candidate: Candidate) -> None:
    calibration = payload["calibration"]
    if set(calibration) != {"start", "end", "sessions", "panel_sha256"}:
        raise ValueError("invalid shadow calibration fields")
    if dt.date.fromisoformat(calibration["start"]) >= dt.date.fromisoformat(calibration["end"]):
        raise ValueError("invalid shadow calibration dates")
    sessions = calibration["sessions"]
    if type(sessions) is not int or sessions <= max(
        _history(candidate), candidate.config.strategy.warmup.min_completed_sessions
    ):
        raise ValueError("insufficient shadow calibration history")
    if payload["reference_window_sessions"] != min(252, sessions - _history(candidate)):
        raise ValueError("invalid reference window")
    if len(calibration["panel_sha256"]) != SHA256_LENGTH or any(
        c not in "0123456789abcdef" for c in calibration["panel_sha256"]
    ):
        raise ValueError("invalid calibration digest")
    stats = payload["reference_stats"]
    if set(stats) != set(payload["universe"]):
        raise ValueError("shadow reference universe mismatch")
    for row in stats.values():
        _validate_statistics(row)
    probes = payload["probes"]
    if set(probes) != {"dates", "values", "baseline_scores", "modified_scores"}:
        raise ValueError("invalid calibration probes")
    dates = [dt.date.fromisoformat(date) for date in probes["dates"]]
    if (
        len(dates) != PROBE_ROWS
        or dates != sorted(set(dates))
        or dates[-1].isoformat() != calibration["end"]
        or dates[0].isoformat() < calibration["start"]
    ):
        raise ValueError("invalid calibration probe dates")
    for key in ("values", "baseline_scores", "modified_scores"):
        array = np.array(probes[key], dtype=float)
        if array.shape != (PROBE_ROWS, len(payload["universe"])) or np.isinf(array).any():
            raise ValueError("invalid calibration probe values")


def _validate_statistics(row: dict[str, Any]) -> None:
    numeric = {"min", "max", "mean", "std", "positive_fraction"}
    if set(row) != numeric | {"available_fraction"}:
        raise ValueError("invalid reference statistics")
    if (
        type(row["available_fraction"]) not in (int, float)
        or not 0 <= row["available_fraction"] <= 1
    ):
        raise ValueError("invalid reference availability")
    if row["available_fraction"] == 0:
        if any(row[key] is not None for key in numeric):
            raise ValueError("unavailable calibration must have empty reference statistics")
    else:
        if any(type(row[key]) not in (int, float) for key in numeric):
            raise ValueError("available calibration requires numeric reference statistics")
        if not (-1 <= row["min"] <= row["max"] <= 1) or not (
            row["min"] - 1e-12 <= row["mean"] <= row["max"] + 1e-12
        ):
            raise ValueError("invalid reference value range")
        if row["std"] < 0 or not 0 <= row["positive_fraction"] <= 1:
            raise ValueError("invalid reference deviation or prevalence")


def load_shadow_artifact(path: Path) -> dict[str, Any]:
    """Read and validate against the running inference implementation."""
    return validate_shadow_artifact(json.loads(path.read_text(encoding="utf-8")))


def verify_calibration(artifact: dict[str, Any], panel: MarketPanel) -> None:
    """Require exact frozen-panel and numerical parity before report-only use."""
    payload = validate_shadow_artifact(artifact)["payload"]
    if panel_digest(panel) != payload["calibration"]["panel_sha256"]:
        raise ValueError("shadow calibration panel mismatch")
    candidate = Candidate.model_validate(payload["candidate"])
    values = evaluate_shadow(candidate, panel)
    if _probes(panel, values) != payload["probes"]:
        raise ValueError("shadow calibration numerical parity mismatch")
    start = max(payload["required_history"], len(panel) - 252)
    if calibrate_values(values[0][start:], panel.symbols) != payload["reference_stats"]:
        raise ValueError("shadow calibration reference statistics mismatch")
