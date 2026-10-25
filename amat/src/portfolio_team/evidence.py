"""Read the legacy study envelope without treating aggregate results as validation."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import tomllib
from pathlib import Path
from typing import Any

from amat.provenance import RunManifest
from portfolio_team.contracts import Bundle, Catalog, Check, Fact, RunEvidence, RunSpec

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MIN_SESSIONS = 2
UNITS = {
    "total_return": "fraction",
    "annualized_return": "fraction",
    "annualized_volatility": "fraction",
    "sharpe": "ratio",
    "max_drawdown": "fraction",
    "hit_rate": "fraction",
    "average_gross_exposure": "fraction",
    "total_fills": "count",
    "total_missed_fills": "count",
    "total_costs": "USD",
    "initial_equity": "USD",
    "final_equity": "USD",
    "one_way_bps": "bps",
}


class EvidenceError(ValueError):
    """An input is not suitable for this evidence adapter."""


class BudgetDeferredError(EvidenceError):
    """No call was dispatched; retain the budget snapshot for scheduling."""

    def __init__(self, budget: dict[str, Any]) -> None:
        self.budget = budget
        super().__init__("deferred_budget:" + ",".join(budget["blocked_dimensions"]))


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise EvidenceError("nonfinite artifact value")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def parse_json(raw: bytes | str) -> Any:
    value = json.loads(raw, object_pairs_hook=_pairs)
    _finite(value)
    return value


def read_source(root: Path, relative: str) -> bytes:
    allowed = root.resolve()
    path = (allowed / relative).resolve()
    if not path.is_relative_to(allowed):
        raise EvidenceError("source path escapes its configured root")
    before = path.stat()
    if not path.is_file() or before.st_size > MAX_SOURCE_BYTES:
        raise EvidenceError("source must be a bounded regular file")
    with path.open("rb") as source:
        raw = source.read(MAX_SOURCE_BYTES + 1)
    after = path.stat()
    if len(raw) > MAX_SOURCE_BYTES or (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise EvidenceError("source changed during import; retry after writer completes")
    return raw


def load_catalog(path: Path) -> Catalog:
    catalog = Catalog.model_validate(tomllib.loads(path.read_text()))
    ids = [run.id for run in catalog.runs]
    comparisons = [item.id for item in catalog.comparisons]
    if len(set(ids)) != len(ids) or len(set(comparisons)) != len(comparisons):
        raise EvidenceError("catalog aliases must be unique")
    for item in catalog.comparisons:
        if item.candidate not in ids or item.control not in ids:
            raise EvidenceError(f"unknown run alias in {item.id}")
        if item.candidate == item.control:
            raise EvidenceError("a comparison needs two different run aliases")
    return catalog


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvidenceError("metric must be a JSON number, not a string or boolean")
    if not math.isfinite(value):
        raise EvidenceError("metric must be finite")
    return float(value)


def _config_scalar(value: Any) -> str:
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    raise EvidenceError("unsupported TOML configuration scalar")


def _same(left: Any, right: Any) -> bool:
    return math.isclose(_number(left), _number(right), rel_tol=1e-9, abs_tol=1e-9)


def _validate_metrics(combined: dict[str, Any]) -> None:
    performance = combined["performance"]
    for name in UNITS:
        _number(performance[name])
    for name in ("first_session", "last_session", "sessions"):
        if performance[name] != combined["sample"][name]:
            raise EvidenceError(f"contradictory sample field: {name}")
    for left, right in (
        ("total_return", "total_return"),
        ("max_drawdown", "max_drawdown"),
        ("total_costs", "total_costs"),
        ("total_fills", "total_fills"),
        ("total_missed", "total_missed_fills"),
        ("one_way_bps", "one_way_bps"),
        ("initial_equity", "initial_equity"),
        ("final_equity", "final_equity"),
    ):
        if not _same(combined[left], performance[right]):
            raise EvidenceError(f"contradictory duplicated metric: {left}")
    initial, final = performance["initial_equity"], performance["final_equity"]
    if initial <= 0 or final <= 0 or not _same(final / initial - 1, performance["total_return"]):
        raise EvidenceError("equity and total return are inconsistent")
    if not -1 <= performance["max_drawdown"] <= 0 or not 0 <= performance["hit_rate"] <= 1:
        raise EvidenceError("drawdown/hit-rate units are invalid")
    for name in ("total_fills", "total_missed_fills", "sessions"):
        value = _number(performance[name])
        if value < 0 or not value.is_integer():
            raise EvidenceError(f"invalid count: {name}")
    for name in ("total_costs", "one_way_bps", "annualized_volatility", "average_gross_exposure"):
        if performance[name] < 0:
            raise EvidenceError(f"negative metric: {name}")
    first = dt.date.fromisoformat(performance["first_session"])
    last = dt.date.fromisoformat(performance["last_session"])
    if first > last or performance["sessions"] < MIN_SESSIONS:
        raise EvidenceError("invalid sample coverage")


def import_run(root: Path, spec: RunSpec) -> tuple[RunEvidence, dict[str, bytes]]:
    """Return normalized evidence and exact source bytes, keyed by content digest."""
    try:
        return _import_run(root, spec)
    except (KeyError, IndexError, TypeError) as exc:
        raise EvidenceError("unsupported or incomplete AMAT study envelope") from exc


def _import_run(root: Path, spec: RunSpec) -> tuple[RunEvidence, dict[str, bytes]]:
    manifest_raw = read_source(root, f"{spec.path}/manifest.json")
    results_raw = read_source(root, f"{spec.path}/results.json")
    manifest_data = parse_json(manifest_raw)
    manifest = RunManifest.model_validate(manifest_data)
    payload = parse_json(results_raw)
    # Match the original JSON representation, without adding new model defaults.
    canonical = json.dumps(manifest_data, sort_keys=True, separators=(",", ":")).encode()
    if payload["manifest_sha256"] != sha256(canonical) or payload["run_id"] != manifest.run_id:
        raise EvidenceError("run ID or canonical manifest digest mismatch")
    study = payload["results"]["studies"][spec.study_index]
    combined = study["combined"]
    if study["engine"] != spec.engine or combined["engine"] != spec.engine:
        raise EvidenceError("selected engine mismatch")
    if combined["config_sha256"] != manifest.config_sha256:
        raise EvidenceError("result/manifest configuration mismatch")
    _validate_metrics(combined)
    blobs = {sha256(manifest_raw): manifest_raw, sha256(results_raw): results_raw}
    config_digest = None
    config_json = None
    config_status = "missing"
    try:
        config_raw = read_source(root, spec.config_path)
    except FileNotFoundError:
        pass
    else:
        config_digest = sha256(config_raw)
        blobs[config_digest] = config_raw
        config_status = "verified" if config_digest == manifest.config_sha256 else "mismatch"
        if config_status == "verified":
            parsed = tomllib.loads(config_raw.decode())
            # Identity/syntax only: historical strategy schemas can differ from
            # today's executable config model. Capability checks stay missing.
            _finite(parsed)
            config_json = json.dumps(parsed, sort_keys=True, default=_config_scalar)
    performance = combined["performance"]
    prefix = f"/results/studies/{spec.study_index}/combined/performance"
    facts = tuple(
        Fact.model_validate(
            {
                "id": f"{spec.id}.{name}",
                "metric": name,
                "value": performance[name],
                "unit": unit,
                "artifact_sha256": sha256(results_raw),
                "pointer": f"{prefix}/{name}",
            }
        )
        for name, unit in UNITS.items()
    )
    evidence = RunEvidence.model_validate(
        {
            "alias": spec.id,
            "manifest": manifest,
            "manifest_file_sha256": sha256(manifest_raw),
            "results_file_sha256": sha256(results_raw),
            "config_file_sha256": config_digest,
            "config_status": config_status,
            "config_json": config_json,
            "first_session": performance["first_session"],
            "last_session": performance["last_session"],
            "sessions": performance["sessions"],
            "facts": facts,
            "selected_study_json": json.dumps(study, sort_keys=True),
        }
    )
    return evidence, blobs


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(_flatten(item, name))
        else:
            result[name] = item
    return result


def make_bundle(
    catalog: Catalog, comparison_id: str, root: Path
) -> tuple[Bundle, dict[str, bytes]]:
    comparison = next(item for item in catalog.comparisons if item.id == comparison_id)
    specs = {run.id: run for run in catalog.runs}
    candidate, blobs = import_run(root, specs[comparison.candidate])
    control, other = import_run(root, specs[comparison.control])
    blobs.update(other)
    differences: tuple[str, ...] = ()
    if candidate.config_json and control.config_json:
        left, right = (
            _flatten(parse_json(candidate.config_json)),
            _flatten(parse_json(control.config_json)),
        )
        differences = tuple(
            sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key))
        )
    checks = _checks(candidate, control, differences)
    return Bundle(
        comparison=comparison,
        candidate=candidate,
        control=control,
        evaluation_use=catalog.default_evaluation_use,
        settings_differences=differences,
        checks=checks,
    ), blobs


def _checks(
    candidate: RunEvidence, control: RunEvidence, differences: tuple[str, ...]
) -> tuple[Check, ...]:
    aligned = (candidate.first_session, candidate.last_session, candidate.sessions) == (
        control.first_session,
        control.last_session,
        control.sessions,
    )
    checks = [
        Check(
            id="sample_alignment",
            status="pass" if aligned else "fail",
            scope="comparison",
            message="Reported sample dates/counts match."
            if aligned
            else "A persisted matched-sample control is required.",
        )
    ]
    for run in (candidate, control):
        checks.extend(
            (
                Check(
                    id=f"{run.alias}.config",
                    status="pass" if run.config_status == "verified" else "missing",
                    scope="readiness",
                    message=f"Historical configuration status: {run.config_status}.",
                ),
                Check(
                    id=f"{run.alias}.revision",
                    status="pass" if run.manifest.code_revision.is_promotable else "fail",
                    scope="readiness",
                    message="Release requires detected clean code provenance.",
                ),
            )
        )
    checks.append(
        Check(
            id="configuration_differences",
            status="missing"
            if any(run.config_status != "verified" for run in (candidate, control))
            else "pass",
            scope="comparison",
            message=(
                "Settings compared; differences are not isolated causal effects: "
                + ", ".join(differences)
            ),
        )
    )
    checks.extend(
        Check.model_validate({"id": key, "status": "missing", "scope": scope, "message": message})
        for key, scope, message in (
            (
                "untouched_evaluation",
                "research",
                "Aggregate historical studies do not establish untouched evaluation evidence.",
            ),
            (
                "daily_series",
                "research",
                "Daily series missing; diversification and uncertainty need more artifacts.",
            ),
            (
                "accounting_parity",
                "readiness",
                "No accounting/parity test evidence is attached to this study adapter.",
            ),
            (
                "capability_validation",
                "readiness",
                "No execution-capability validation evidence is attached.",
            ),
            (
                "dataset_partitions",
                "readiness",
                "Manifest links checked; source market-data partitions have not been verified.",
            ),
            (
                "registered_protocol",
                "research",
                "Hypothesis, lineage, cost stress and validation protocol are not attached.",
            ),
        )
    )
    return tuple(checks)
