"""Fail-closed numerical checks for incremental research (never an activation API).

Seals detect accidental/tampered evidence; they do not make a caller trustworthy.
Expected identities and verifier hashes must come from preregistered campaign
inputs, rather than being copied from the measurement being evaluated.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoresearch.improvements.study import digest

COSTS = (2, 6, 10)
PERIODS = ("full", "walk_forward", "2023")
# These are preregistered measurement constants, not fitted thresholds.
# ruff: noqa: PLR2004
REQUIRED_CHECKS = frozenset(
    {
        "causality",
        "accounting",
        "stress",
        "walk_forward",
        "native_planning",
        "native_execution",
        "uncertainty",
        "independent_review",
    }
)


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number, not a boolean/string")
    return float(value)


def _hash(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA256")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_sealed(path: Path) -> dict[str, Any]:
    """Read canonical study seals, refusing duplicate keys and nonfinite JSON."""
    value = json.loads(path.read_text(), object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise ValueError("Sealed evidence must be an object")
    if digest(value) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError(f"Evidence seal mismatch: {path.name}")
    return value


@dataclass(frozen=True)
class MeasurementIdentity:
    """Expected identities obtained before results; profile is the file-byte hash."""

    arm: str
    profile_sha256: str
    protocol_sha256: str
    source_sha256: str
    partition_sha256: str
    run_id: str
    study_id: str
    calendar_sha256: str
    role: str = "strategy"


@dataclass(frozen=True)
class PeriodMetrics:
    net_return: float
    drawdown: float  # positive magnitude
    observations: int


@dataclass(frozen=True)
class NativeMeasurement:
    identity: MeasurementIdentity
    cost_bps: int
    record_sha256: str
    sessions_sha256: str
    calendar: tuple[str, ...]
    periods: Mapping[str, PeriodMetrics]


def _derived_periods(rows: list[dict[str, Any]]) -> dict[str, PeriodMetrics]:
    output: dict[str, PeriodMetrics] = {}
    for key, start, end in (
        ("full", "2017-01-03", "2026-10-12"),
        ("walk_forward", "2020-01-01", "2026-10-12"),
        ("2023", "2023-01-01", "2023-12-31"),
    ):
        selected = [i for i, row in enumerate(rows) if start <= row["session"] <= end]
        if len(selected) < 2:
            raise ValueError(f"Missing {key} history")
        first = selected[0]
        opening = rows[first - 1]["equity"] if first else 100000.0
        peak, worst = opening, 0.0
        for index in selected:
            equity = rows[index]["equity"]
            peak = max(peak, equity)
            worst = max(worst, 1 - equity / peak)
        output[key] = PeriodMetrics(
            rows[selected[-1]]["equity"] / opening - 1, worst, len(selected)
        )
    return output


def load_native_measurement(  # noqa: PLR0912, PLR0915 - fail-closed evidence boundary
    record_path: Path, protocol_path: Path, *, expected: MeasurementIdentity
) -> NativeMeasurement:
    """Verify finalized native output against independently expected identities.

    New drivers add finalized/run_id/study_id/profile_sha256/sessions_sha256 to
    their native records. Legacy records can be references, but cannot silently
    become finalized campaign measurements merely by adding a gate receipt.
    """
    protocol, record = read_sealed(protocol_path), read_sealed(record_path)
    for field in (
        "profile_sha256",
        "protocol_sha256",
        "source_sha256",
        "partition_sha256",
        "calendar_sha256",
    ):
        _hash(getattr(expected, field), field)
    if not expected.arm or not expected.run_id or not expected.study_id:
        raise ValueError("Arm/run/study identity is required")
    if digest(protocol) != expected.protocol_sha256:
        raise ValueError("Unexpected protocol identity")
    if digest(protocol.get("source_sha256")) != expected.source_sha256:
        raise ValueError("Unexpected source identity")
    if digest(protocol.get("partition_sha256")) != expected.partition_sha256:
        raise ValueError("Unexpected data partition")
    if not isinstance(protocol.get("source_sha256"), dict) or not protocol["source_sha256"]:
        raise ValueError("Missing native source manifest")
    for value in protocol["source_sha256"].values():
        _hash(value, "source file hash")
    if protocol.get("costs_bps") != list(COSTS) or any(
        type(cost) is not int for cost in protocol["costs_bps"]
    ):
        raise ValueError("Study must preregister exactly integer 2/6/10bps costs")
    if (
        protocol.get("execution_start") != "2017-01-03"
        or protocol.get("initial_cash") not in (100000, "100000", "100000.0")
        or isinstance(protocol.get("initial_cash"), bool)
        or _number(protocol.get("participation_cap"), "volume cap") != 0.01
        or protocol.get("whole_share_buys") is not True
    ):
        raise ValueError("Native execution contract differs")
    if expected.role not in {"strategy", "qqq_benchmark"}:
        raise ValueError("Unknown measurement role")
    if expected.role == "qqq_benchmark" and expected.arm != "qqq_native":
        raise ValueError("Only the matched native entry-cap QQQ benchmark is comparable")
    if protocol.get("whole_share_buys_by_arm", {}).get(expected.arm, True) is not True:
        raise ValueError("Arm overrides whole-share contract")
    if record.get("finalized") is not True:
        raise ValueError("Measurement is not finalized")
    for field in ("arm", "profile_sha256", "protocol_sha256", "run_id", "study_id"):
        if record.get(field) != getattr(expected, field):
            raise ValueError(f"Measurement {field} mismatch")
    cost = record.get("cost_bps")
    if type(cost) is not int or cost not in COSTS:
        raise ValueError("Measurement cost must be integer 2/6/10bps")
    configs = (
        protocol.get("configs", {})
        if expected.role == "strategy"
        else protocol.get("benchmark_configs", {})
    )
    config = json.loads(json.dumps(configs[expected.arm]))
    rotation = config.get("adaptive_rotation", {})
    if rotation.get("switch_cost_bps", 0) > 0:
        rotation["switch_cost_bps"] = float(cost)
    if record.get("effective_config_sha256") != digest(config):
        raise ValueError("Effective strategy config mismatch")
    if record.get("fractional_buy_count") != 0 or type(record["fractional_buy_count"]) is not int:
        raise ValueError("Whole-share buy evidence is missing or failed")
    rows = record.get("sessions")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Missing native sessions")
    if record.get("sessions_sha256") != digest(rows):
        raise ValueError("Session seal differs")
    calendar: list[str] = []
    for row in rows:
        date = row["session"]
        if not isinstance(date, str) or str(dt.date.fromisoformat(date)) != date:
            raise ValueError("Invalid session date")
        if calendar and date <= calendar[-1]:
            raise ValueError("Sessions are duplicated or unordered")
        calendar.append(date)
        if _number(row["equity"], "equity") <= 0:
            raise ValueError("Native equity must stay positive")
        if _number(row["fees"], "fees") < 0:
            raise ValueError("Negative execution costs")
    execution_dates = [day for day in calendar if day >= "2017-01-03"]
    if (
        digest(calendar) != expected.calendar_sha256
        or protocol.get("session_calendar_sha256") != expected.calendar_sha256
    ):
        raise ValueError("Session calendar differs from preregistered data calendar")
    if (
        not execution_dates
        or execution_dates[0] != "2017-01-03"
        or calendar[-1] != "2026-10-12"
        or len(execution_dates) < 2400
    ):
        raise ValueError("Incomplete long-history execution calendar")
    if calendar[0] < "2017-01-03" and any(
        row["equity"] != 100000 or row["fees"] != 0 for row in rows if row["session"] < "2017-01-03"
    ):
        raise ValueError("Warmup carried capital or fees into flat account")
    periods = _derived_periods(rows)
    for key, derived in periods.items():
        reported = record["metrics"][key]
        ret = _number(reported.get("net_return"), f"{key} return")
        dd = _number(reported.get("max_drawdown"), f"{key} drawdown")
        if dd > 0 or dd < -1:
            raise ValueError("Native drawdown must be signed in [-1,0]")
        if not math.isclose(
            ret, derived.net_return, abs_tol=1e-11, rel_tol=1e-11
        ) or not math.isclose(-dd, derived.drawdown, abs_tol=1e-11, rel_tol=1e-11):
            raise ValueError(f"Reported {key} metrics differ from native equity")
    return NativeMeasurement(expected, cost, digest(record), digest(rows), tuple(calendar), periods)


def _bundle(value: Mapping[int, NativeMeasurement]) -> None:
    if set(value) != set(COSTS) or any(type(key) is not int for key in value):
        raise ValueError("All three integer costs are required")
    reference = value[2].identity
    for cost, item in value.items():
        if item.cost_bps != cost or item.identity != reference:
            raise ValueError("Mixed measurement identity or cost")
        for key in PERIODS:
            period = item.periods[key]
            ret = _number(period.net_return, "derived return")
            dd = _number(period.drawdown, "derived drawdown")
            if (
                ret <= -1
                or not 0 <= dd < 1
                or type(period.observations) is not int
                or period.observations < 2
            ):
                raise ValueError("Invalid derived period metrics")


def verify_validation_reports(
    paths: list[Path],
    *,
    candidate: Mapping[int, NativeMeasurement],
    trusted_verifier_source_sha256: frozenset[str],
) -> dict[str, str]:
    """Accept named external checks only with seals, subjects and real artifacts.

    These checks remain verifier assertions; they never replace numerical
    calculation here. Trusted verifier identities must be preregistered.
    """
    _bundle(candidate)
    checks: dict[str, str] = {}
    subjects = {str(cost): item.record_sha256 for cost, item in candidate.items()}
    identity = candidate[2].identity
    for path in paths:
        report = read_sealed(path)
        if (
            report.get("schema") != "incremental-validation/1"
            or report.get("subject_records_sha256") != subjects
            or report.get("source_sha256") != identity.source_sha256
            or report.get("protocol_sha256") != identity.protocol_sha256
            or report.get("run_id") != identity.run_id
            or report.get("verifier_source_sha256") not in trusted_verifier_source_sha256
        ):
            raise ValueError("Untrusted or unrelated validation report")
        artifacts = report.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError("Validation requires concrete artifact evidence")
        for artifact in artifacts:
            target = Path(artifact["path"])
            if not target.is_absolute():
                target = path.parent / target
            expected = _hash(artifact["sha256"], "artifact hash")
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("Validation artifact changed")
        outcomes = report.get("checks")
        if not isinstance(outcomes, dict) or not outcomes:
            raise ValueError("Missing named validation checks")
        for name, status in outcomes.items():
            if name not in REQUIRED_CHECKS or status not in {"pass", "fail", "unknown"}:
                raise ValueError("Invalid check name/status")
            if name in checks:
                raise ValueError("Ambiguous duplicate validation check")
            checks[name] = status
    return checks


def evaluate_incremental(
    *,
    candidate: Mapping[int, NativeMeasurement],
    control: Mapping[int, NativeMeasurement],
    original: Mapping[int, NativeMeasurement],
    qqq: Mapping[int, NativeMeasurement],
    parent_sessions_sha256: Mapping[int, str],
    validation_reports: list[Path],
    trusted_verifier_source_sha256: frozenset[str],
) -> dict[str, Any]:
    """Derive incremental-v2 numbers; historical bests are benchmarks, not vetoes.

    A returned pass means eligible for versioned profile verification only. It
    does not grant deployment, unsupported short execution, or live authority.
    """
    for bundle in (candidate, control, original, qqq):
        _bundle(bundle)
    if (
        any(bundle[2].identity.role != "strategy" for bundle in (candidate, control, original))
        or qqq[2].identity.role != "qqq_benchmark"
    ):
        raise ValueError("QQQ benchmarks cannot be strategy promotion candidates")
    if candidate[2].identity.source_sha256 != control[2].identity.source_sha256:
        raise ValueError("Candidate and control require the same engine/source replay")
    validation_checks = verify_validation_reports(
        validation_reports,
        candidate=candidate,
        trusted_verifier_source_sha256=trusted_verifier_source_sha256,
    )
    if set(parent_sessions_sha256) != set(COSTS):
        raise ValueError("Missing parent control references")
    for cost in COSTS:
        if control[cost].sessions_sha256 != _hash(parent_sessions_sha256[cost], "parent session"):
            raise ValueError("Current control failed exact native session reproduction")
        for bundle in (control, original, qqq):
            if candidate[cost].calendar != bundle[cost].calendar:
                raise ValueError("Unmatched execution calendars")
            if candidate[cost].identity.partition_sha256 != bundle[cost].identity.partition_sha256:
                raise ValueError("Unmatched source data partitions")
    checks: list[dict[str, Any]] = []
    gains = []
    for cost in COSTS:
        for period in ("full", "walk_forward"):
            actual = candidate[cost].periods[period]
            current = control[cost].periods[period]
            anchor = original[cost].periods[period]
            benchmark = qqq[cost].periods[period]
            difference = actual.net_return - current.net_return
            gains.append(difference)
            checks.extend(
                [
                    {
                        "name": "incremental_return",
                        "cost_bps": cost,
                        "period": period,
                        "difference": difference,
                        "passes": difference >= 0,
                    },
                    {
                        "name": "drawdown",
                        "cost_bps": cost,
                        "period": period,
                        "candidate": actual.drawdown,
                        "current": current.drawdown,
                        "original": anchor.drawdown,
                        "qqq": benchmark.drawdown,
                        "passes": actual.drawdown <= min(current.drawdown, anchor.drawdown)
                        and actual.drawdown < benchmark.drawdown,
                    },
                ]
            )
        difference = (
            candidate[cost].periods["2023"].net_return - control[cost].periods["2023"].net_return
        )
        checks.append(
            {
                "name": "preserve_2023",
                "cost_bps": cost,
                "difference": difference,
                "passes": difference >= 0,
            }
        )
    numeric = all(check["passes"] for check in checks) and any(gain > 0 for gain in gains)
    validation = all(validation_checks.get(name) == "pass" for name in REQUIRED_CHECKS)
    return {
        "schema": "incremental-numeric-gates/1",
        "policy": "incremental-qqq-drawdown-v2-20261023",
        "numeric_passes": numeric,
        "strict_return_gain": any(gain > 0 for gain in gains),
        "validation_passes": validation,
        "status": "eligible_for_profile_verification"
        if numeric and validation
        else "rejected"
        if not numeric
        else "pending_validation",
        "activation_authority": False,
        "checks": checks,
        "missing_or_failed_validation": sorted(
            name for name in REQUIRED_CHECKS if validation_checks.get(name) != "pass"
        ),
        "qqq_return_objective_met": all(
            candidate[cost].periods[period].net_return >= qqq[cost].periods[period].net_return
            for cost in COSTS
            for period in ("full", "walk_forward")
        ),
        "candidate_records_sha256": {
            str(cost): item.record_sha256 for cost, item in candidate.items()
        },
        "control_records_sha256": {str(cost): item.record_sha256 for cost, item in control.items()},
        "historical_frontier_policy": "Retain visible benchmarks; no dominance veto",
    }
