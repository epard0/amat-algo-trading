"""Adversarial identity and numerical tests for incremental campaign evidence."""

import datetime as dt
import hashlib
import json
from dataclasses import replace

import pytest

from autoresearch.improvements.incremental_gates import (
    COSTS,
    REQUIRED_CHECKS,
    MeasurementIdentity,
    NativeMeasurement,
    PeriodMetrics,
    _derived_periods,
    evaluate_incremental,
    load_native_measurement,
    read_sealed,
    verify_validation_reports,
)
from autoresearch.improvements.study import digest

HASH = "a" * 64


def seal(path, value):
    path.write_text(json.dumps(value, allow_nan=False))
    path.with_suffix(".sha256").write_text(digest(value))


@pytest.fixture
def native(tmp_path):
    day = dt.date(2016, 12, 30)
    rows = []
    while day <= dt.date(2026, 10, 12):
        if day.weekday() < 5:
            rows.append({"session": str(day), "equity": 100000.0, "fees": 0.0})
        day += dt.timedelta(days=1)
    rows[-1]["equity"] = 120000.0
    calendar_hash = digest([row["session"] for row in rows])
    config = {"adaptive_rotation": {"switch_cost_bps": 2.0}}
    protocol = {
        "source_sha256": {"src/runner.py": HASH},
        "partition_sha256": {"daily.parquet": HASH},
        "session_calendar_sha256": calendar_hash,
        "initial_cash": "100000",
        "execution_start": "2017-01-03",
        "participation_cap": 0.01,
        "whole_share_buys": True,
        "costs_bps": [2, 6, 10],
        "configs": {"candidate": config},
        "benchmark_configs": {"qqq_native": config},
    }
    expected = MeasurementIdentity(
        "candidate",
        HASH,
        digest(protocol),
        digest(protocol["source_sha256"]),
        digest(protocol["partition_sha256"]),
        "run-001",
        "study-001",
        calendar_hash,
    )
    record = {
        "finalized": True,
        "arm": "candidate",
        "cost_bps": 2,
        "profile_sha256": HASH,
        "protocol_sha256": digest(protocol),
        "run_id": "run-001",
        "study_id": "study-001",
        "sessions": rows,
        "sessions_sha256": digest(rows),
        "effective_config_sha256": digest(config),
        "fractional_buy_count": 0,
        "metrics": {
            key: {"net_return": value.net_return, "max_drawdown": -value.drawdown}
            for key, value in _derived_periods(rows).items()
        },
    }
    seal(tmp_path / "protocol.json", protocol)
    seal(tmp_path / "candidate-2bps.json", record)
    return tmp_path, protocol, record, expected


def load(fixture):
    path, _, _, expected = fixture
    return load_native_measurement(
        path / "candidate-2bps.json", path / "protocol.json", expected=expected
    )


def test_native_metrics_are_recomputed_and_bound(native):
    measurement = load(native)
    assert measurement.periods["full"].net_return == pytest.approx(0.2)
    assert measurement.periods["2023"].net_return == 0
    assert measurement.periods["full"].drawdown == 0


def test_effective_cost_config_and_qqq_benchmark_are_bound(native):
    path, _, record, expected = native
    record["cost_bps"] = 6
    record["effective_config_sha256"] = digest({"adaptive_rotation": {"switch_cost_bps": 6.0}})
    seal(path / "candidate-2bps.json", record)
    assert load(native).cost_bps == 6
    record["arm"] = "qqq_native"
    seal(path / "qqq-6bps.json", record)
    measurement = load_native_measurement(
        path / "qqq-6bps.json",
        path / "protocol.json",
        expected=replace(expected, arm="qqq_native", role="qqq_benchmark"),
    )
    assert measurement.identity.role == "qqq_benchmark"
    with pytest.raises(ValueError, match="Only the matched"):
        load_native_measurement(
            path / "qqq-6bps.json",
            path / "protocol.json",
            expected=replace(expected, arm="qqq_investor_uncapped", role="qqq_benchmark"),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("finalized", False),
        ("finalized", "true"),
        ("cost_bps", True),
        ("cost_bps", 2.0),
        ("cost_bps", 3),
        ("run_id", "other"),
        ("study_id", "other"),
        ("profile_sha256", "b" * 64),
        ("effective_config_sha256", "b" * 64),
        ("sessions_sha256", "b" * 64),
        ("fractional_buy_count", True),
        ("fractional_buy_count", 1),
    ],
)
def test_resealed_wrong_identity_still_rejected(native, field, value):
    path, _, record, _ = native
    record[field] = value
    seal(path / "candidate-2bps.json", record)
    with pytest.raises(ValueError):
        load(native)


@pytest.mark.parametrize(
    "field,value",
    [
        ("equity", True),
        ("equity", "100000"),
        ("equity", 0),
        ("fees", -1),
    ],
)
def test_invalid_accounting_numbers_rejected(native, field, value):
    path, _, record, _ = native
    record["sessions"][100][field] = value
    record["sessions_sha256"] = digest(record["sessions"])
    seal(path / "candidate-2bps.json", record)
    with pytest.raises(ValueError):
        load(native)


def test_fabricated_reported_metric_rejected(native):
    path, _, record, _ = native
    record["metrics"]["full"]["net_return"] = 20
    seal(path / "candidate-2bps.json", record)
    with pytest.raises(ValueError, match="differ from native"):
        load(native)


def test_calendar_hash_blocks_one_deleted_session(native):
    path, _, record, _ = native
    del record["sessions"][100]
    record["sessions_sha256"] = digest(record["sessions"])
    seal(path / "candidate-2bps.json", record)
    with pytest.raises(ValueError, match="calendar"):
        load(native)


def test_flat_account_and_whole_share_override_required(native):
    path, protocol, record, expected = native
    record["sessions"][0]["equity"] = 101000
    record["sessions_sha256"] = digest(record["sessions"])
    seal(path / "candidate-2bps.json", record)
    with pytest.raises(ValueError, match="Warmup carried"):
        load(native)
    protocol["whole_share_buys_by_arm"] = {"candidate": False}
    seal(path / "protocol.json", protocol)
    with pytest.raises(ValueError, match="overrides whole-share"):
        load_native_measurement(
            path / "candidate-2bps.json",
            path / "protocol.json",
            expected=replace(expected, protocol_sha256=digest(protocol)),
        )


def test_data_and_source_identity_are_independent_inputs(native):
    path, protocol, _, expected = native
    with pytest.raises(ValueError, match="source identity"):
        load_native_measurement(
            path / "candidate-2bps.json",
            path / "protocol.json",
            expected=replace(expected, source_sha256="b" * 64),
        )
    protocol["partition_sha256"]["daily.parquet"] = "b" * 64
    seal(path / "protocol.json", protocol)
    with pytest.raises(ValueError, match="data partition"):
        load_native_measurement(
            path / "candidate-2bps.json",
            path / "protocol.json",
            expected=replace(expected, protocol_sha256=digest(protocol)),
        )


def test_seal_and_duplicate_keys_rejected(native):
    path, _, _, _ = native
    record_path = path / "candidate-2bps.json"
    record_path.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError, match="Duplicate"):
        read_sealed(record_path)
    record_path.write_text('{"a":1}')
    with pytest.raises(ValueError, match="seal mismatch"):
        read_sealed(record_path)


def bundle(name, ret, dd, *, source=HASH, role="strategy"):
    identity = MeasurementIdentity(name, HASH, HASH, source, HASH, name, "study", HASH, role)
    return {
        cost: NativeMeasurement(
            identity,
            cost,
            digest([name, cost]),
            digest(["sessions", name, cost]),
            ("2017-01-03", "2026-10-12"),
            {key: PeriodMetrics(ret, dd, 100) for key in ("full", "walk_forward", "2023")},
        )
        for cost in COSTS
    }


def inputs():
    control = bundle("control", 0.1, 0.15)
    return {
        "candidate": bundle("candidate", 0.2, 0.1),
        "control": control,
        "original": bundle("original", 0.1, 0.2),
        "qqq": bundle("qqq_native", 0.4, 0.3, role="qqq_benchmark"),
        "parent_sessions_sha256": {cost: value.sessions_sha256 for cost, value in control.items()},
        "validation_reports": [],
        "trusted_verifier_source_sha256": frozenset({HASH}),
    }


def report(tmp_path, candidate, statuses=None):
    evidence = tmp_path / "verifier-output.txt"
    evidence.write_text("Pinned verifier ran recorded checks against all candidate records.\n")
    identity = candidate[2].identity
    value = {
        "schema": "incremental-validation/1",
        "run_id": identity.run_id,
        "subject_records_sha256": {
            str(cost): item.record_sha256 for cost, item in candidate.items()
        },
        "source_sha256": identity.source_sha256,
        "protocol_sha256": identity.protocol_sha256,
        "verifier_source_sha256": HASH,
        "artifacts": [
            {"path": evidence.name, "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
        ],
        "checks": statuses or dict.fromkeys(REQUIRED_CHECKS, "pass"),
    }
    path = tmp_path / "validation.json"
    seal(path, value)
    return path


def test_incremental_pass_is_not_authority_and_need_not_beat_qqq(tmp_path):
    values = inputs()
    values["validation_reports"] = [report(tmp_path, values["candidate"])]
    result = evaluate_incremental(**values)
    assert result["status"] == "eligible_for_profile_verification"
    assert result["activation_authority"] is False
    assert result["qqq_return_objective_met"] is False


def test_missing_unknown_or_failed_independent_validation_blocks(tmp_path):
    values = inputs()
    assert evaluate_incremental(**values)["status"] == "pending_validation"
    for status in ("unknown", "fail"):
        values["validation_reports"] = [report(tmp_path, values["candidate"], {"stress": status})]
        assert evaluate_incremental(**values)["status"] == "pending_validation"


@pytest.mark.parametrize("change", ["return", "drawdown", "2023", "no_strict_gain"])
def test_each_numeric_guard_rejects(change):
    values = inputs()
    candidate = values["candidate"]
    if change == "no_strict_gain":
        candidate = bundle("candidate", 0.1, 0.1)
    else:
        item = candidate[10]
        periods = dict(item.periods)
        target = "2023" if change == "2023" else "walk_forward"
        periods[target] = replace(
            periods[target],
            **({"drawdown": 0.16} if change == "drawdown" else {"net_return": 0.09}),
        )
        candidate[10] = replace(item, periods=periods)
    values["candidate"] = candidate
    assert evaluate_incremental(**values)["status"] == "rejected"


def test_control_reproduction_and_engine_replays_required():
    values = inputs()
    values["parent_sessions_sha256"][6] = "b" * 64
    with pytest.raises(ValueError, match="exact native session"):
        evaluate_incremental(**values)
    values = inputs()
    values["candidate"] = bundle("candidate", 0.2, 0.1, source="b" * 64)
    with pytest.raises(ValueError, match="same engine"):
        evaluate_incremental(**values)


def test_unrelated_or_changed_validation_evidence_rejected(tmp_path):
    values = inputs()
    path = report(tmp_path, values["candidate"])
    report_body = read_sealed(path)
    report_body["subject_records_sha256"]["6"] = "b" * 64
    seal(path, report_body)
    with pytest.raises(ValueError, match="Untrusted or unrelated"):
        verify_validation_reports(
            [path], candidate=values["candidate"], trusted_verifier_source_sha256=frozenset({HASH})
        )
    path = report(tmp_path, values["candidate"])
    (tmp_path / "verifier-output.txt").write_text("changed")
    with pytest.raises(ValueError, match="artifact changed"):
        verify_validation_reports(
            [path], candidate=values["candidate"], trusted_verifier_source_sha256=frozenset({HASH})
        )


def test_boolean_local_gates_are_not_validation(tmp_path):
    values = inputs()
    path = report(tmp_path, values["candidate"], {"accounting": True})
    values["validation_reports"] = [path]
    with pytest.raises(ValueError, match="check name/status"):
        evaluate_incremental(**values)


def test_all_costs_and_exact_calendars_required():
    values = inputs()
    del values["candidate"][10]
    with pytest.raises(ValueError, match="three integer costs"):
        evaluate_incremental(**values)
    values = inputs()
    values["candidate"][2] = replace(values["candidate"][2], calendar=("2020-01-03", "2026-10-12"))
    with pytest.raises(ValueError, match="Unmatched execution calendars"):
        evaluate_incremental(**values)
