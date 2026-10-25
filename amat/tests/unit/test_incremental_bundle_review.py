"""Independent adversarial tests for the read-only numeric bundle consumer.

Synthetic accounts demonstrate evidence handling only, never strategy efficacy.
"""

import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from autoresearch.improvements.incremental_gates import (
    REQUIRED_CHECKS,
    MeasurementIdentity,
    _derived_periods,
)
from autoresearch.improvements.study import digest, source_hashes

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/review_incremental_bundle.py"
CAMPAIGN_SHA = "c" * 64
NOW = dt.datetime(2026, 10, 23, 22, 40, tzinfo=dt.UTC)
DEADLINE = dt.datetime(2026, 10, 24, 4, 40, 10, tzinfo=dt.UTC)


def byte_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seal(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False))
    path.with_suffix(".sha256").write_text(digest(value))
    return {"path": str(path), "sha256": byte_sha(path)}


def load_module():
    spec = importlib.util.spec_from_file_location("review_incremental_bundle_tested", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def rows_for(arm):
    days = []
    day = dt.date(2016, 12, 30)
    while day <= dt.date(2026, 10, 12):
        if day.weekday() < 5:
            days.append(str(day))
        day += dt.timedelta(days=1)
    ends = {
        "candidate": 140000.0,
        "control": 120000.0,
        "original": 115000.0,
        "qqq_native": 180000.0,
    }
    rows = []
    for index, date in enumerate(days):
        equity = (
            100000.0
            if date < "2017-01-03"
            else (100000.0 + (ends[arm] - 100000.0) * index / (len(days) - 1))
        )
        if arm == "qqq_native" and date == "2020-03-16":
            equity = 80000.0
        rows.append({"session": date, "equity": equity, "fees": 0.0})
    return rows


@pytest.fixture
def bundle(tmp_path):
    config = {"adaptive_rotation": {"switch_cost_bps": 2.0}}
    profiles = {}
    for name in ("parent", "original"):
        path = tmp_path / f"{name}.toml"
        path.write_text(f'name = "synthetic-{name}"\n')
        profiles[name] = {"path": str(path), "sha256": byte_sha(path)}
    calendar = digest([r["session"] for r in rows_for("control")])
    protocol = {
        "source_sha256": {"src/runner.py": "a" * 64},
        "partition_sha256": {"daily.parquet": "b" * 64},
        "session_calendar_sha256": calendar,
        "initial_cash": "100000",
        "execution_start": "2017-01-03",
        "participation_cap": 0.01,
        "whole_share_buys": True,
        "costs_bps": [2, 6, 10],
        "configs": dict.fromkeys(("candidate", "control", "original"), config),
        "benchmark_configs": {"qqq_native": config},
    }
    protocol_ref = seal(tmp_path / "protocol.json", protocol)
    identities = {}
    records = {}
    commands = []
    for arm in ("candidate", "control", "original", "qqq_native"):
        profile_hash = profiles["original" if arm == "original" else "parent"]["sha256"]
        identity = MeasurementIdentity(
            arm=arm,
            profile_sha256=profile_hash,
            protocol_sha256=digest(protocol),
            source_sha256=digest(protocol["source_sha256"]),
            partition_sha256=digest(protocol["partition_sha256"]),
            run_id="synthetic-run",
            study_id="synthetic-study",
            calendar_sha256=calendar,
            role="qqq_benchmark" if arm == "qqq_native" else "strategy",
        )
        identities[arm] = asdict(identity)
        records[arm] = {}
        for cost in (2, 6, 10):
            rows = rows_for(arm)
            record = {
                "finalized": True,
                "arm": arm,
                "cost_bps": cost,
                "profile_sha256": profile_hash,
                "protocol_sha256": digest(protocol),
                "run_id": identity.run_id,
                "study_id": identity.study_id,
                "sessions": rows,
                "sessions_sha256": digest(rows),
                "effective_config_sha256": digest(
                    {"adaptive_rotation": {"switch_cost_bps": float(cost)}}
                ),
                "fractional_buy_count": 0,
                "metrics": {
                    key: {"net_return": value.net_return, "max_drawdown": -value.drawdown}
                    for key, value in _derived_periods(rows).items()
                },
            }
            path = tmp_path / f"{arm}-{cost}bps.json"
            records[arm][str(cost)] = seal(path, record)
            commands.append(
                {
                    "argv": [
                        "/absolute/python",
                        "/absolute/frozen-study.py",
                        "measure",
                        "--study",
                        str(tmp_path),
                        "--arm",
                        arm,
                        "--cost",
                        str(cost),
                        "--output",
                        str(path),
                        "--run-id",
                        identity.run_id,
                    ],
                    "cwd": str(tmp_path),
                }
            )
    manifest = {
        "schema": "incremental-research-cycle/1",
        "cycle_id": "synthetic-measurement",
        "campaign_root": str(tmp_path),
        "protocol_sha256": CAMPAIGN_SHA,
        "inputs": [protocol_ref],
        "commands": commands,
    }
    manifest_ref = seal(tmp_path / "manifest.json", manifest)
    receipt = {
        "schema": "incremental-research-cycle-receipt/1",
        "cycle_id": manifest["cycle_id"],
        "manifest_sha256": digest(manifest),
        "protocol_sha256": CAMPAIGN_SHA,
        "status": "cycle_complete",
        "failure": None,
        "cleanup_survivors": [],
        "execution_authority": "none",
        "commands": [{"command_index": index, "returncode": 0} for index in range(len(commands))],
    }
    receipt_ref = seal(tmp_path / "receipt.json", receipt)
    bundles = {}
    for role, arm in {
        "candidate": "candidate",
        "control": "control",
        "original": "original",
        "qqq": "qqq_native",
        "accepted_parent": "control",
    }.items():
        bundles[role] = {
            "protocol": dict(protocol_ref),
            "identity": dict(identities[arm]),
            "records": {cost: dict(ref) for cost, ref in records[arm].items()},
            "run_manifest": dict(manifest_ref),
            "cycle_receipt": dict(receipt_ref),
        }
    policy = {
        "schema": "incremental-bundle-review-policy/1",
        "campaign_protocol_sha256": CAMPAIGN_SHA,
        "parent_profile": profiles["parent"],
        "bundles": bundles,
    }
    policy_path = tmp_path / "review-policy.json"
    seal(policy_path, policy)
    return {
        "root": tmp_path,
        "policy": policy,
        "policy_path": policy_path,
        "expected_policy_sha256": digest(policy),
        "protocol": protocol,
        "manifest": manifest,
        "receipt": receipt,
        "parent_sha256": profiles["parent"]["sha256"],
    }


def review(bundle, **overrides):
    arguments = {
        "expected_policy_sha256": bundle["expected_policy_sha256"],
        "campaign_protocol_sha256": CAMPAIGN_SHA,
        "current_parent_sha256": bundle["parent_sha256"],
        "deadline_utc": DEADLINE.isoformat(),
        "now": NOW,
    }
    arguments.update(overrides)
    return load_module().review_bundle(bundle["policy_path"], **arguments)


def reseal_policy(bundle):
    seal(bundle["policy_path"], bundle["policy"])
    bundle["expected_policy_sha256"] = digest(bundle["policy"])


def mutate_record(bundle, mutator, *, role="candidate", cost="6"):
    reference = bundle["policy"]["bundles"][role]["records"][cost]
    path = Path(reference["path"])
    value = json.loads(path.read_text())
    mutator(value)
    reference.update(seal(path, value))
    reseal_policy(bundle)


def test_numeric_winner_remains_pending_and_has_no_authority(bundle):
    result = review(bundle)
    assert result["evaluation"]["numeric_passes"] is True
    assert result["status"] == "pending_validation"
    assert result["activation_authority"] is False
    assert result["promotion_enabled"] is False
    assert result["native_profile_verified"] is False
    assert set(result["evaluation"]["missing_or_failed_validation"]) == REQUIRED_CHECKS


def test_review_is_read_only(bundle):
    before = {p: p.read_bytes() for p in bundle["root"].iterdir() if p.is_file()}
    review(bundle)
    after = {p: p.read_bytes() for p in bundle["root"].iterdir() if p.is_file()}
    assert before == after


@pytest.mark.parametrize("role", ["candidate", "control", "original", "qqq", "accepted_parent"])
def test_missing_cost_refused(bundle, role):
    del bundle["policy"]["bundles"][role]["records"]["10"]
    reseal_policy(bundle)
    with pytest.raises(ValueError):
        review(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "forged-run"),
        ("profile_sha256", "e" * 64),
        ("study_id", "unregistered-study"),
        ("protocol_sha256", "e" * 64),
        ("finalized", False),
        ("cost_bps", True),
        ("fractional_buy_count", 1),
    ],
)
def test_resealed_wrong_record_identity_refused(bundle, field, value):
    mutate_record(bundle, lambda record: record.__setitem__(field, value))
    with pytest.raises(ValueError):
        review(bundle)


def test_reported_return_not_trusted(bundle):
    mutate_record(bundle, lambda record: record["metrics"]["full"].__setitem__("net_return", 99))
    with pytest.raises(ValueError):
        review(bundle)


def test_changed_calendar_not_accepted_even_resealed(bundle):
    def mutate(record):
        del record["sessions"][500]
        record["sessions_sha256"] = digest(record["sessions"])
        record["metrics"] = {
            key: {"net_return": v.net_return, "max_drawdown": -v.drawdown}
            for key, v in _derived_periods(record["sessions"]).items()
        }

    mutate_record(bundle, mutate)
    with pytest.raises(ValueError):
        review(bundle)


def test_actual_profile_bytes_bound(bundle):
    Path(bundle["policy"]["parent_profile"]["path"]).write_text('name="changed"')
    with pytest.raises(ValueError):
        review(bundle)


def test_wrong_current_parent_rejected(bundle):
    with pytest.raises(ValueError):
        review(bundle, current_parent_sha256="e" * 64)


def test_policy_pin_blocks_resealed_policy_tamper(bundle):
    bundle["policy"]["campaign_protocol_sha256"] = "e" * 64
    seal(bundle["policy_path"], bundle["policy"])
    with pytest.raises(ValueError):
        review(bundle)


@pytest.mark.parametrize("now", [DEADLINE, DEADLINE + dt.timedelta(seconds=1)])
def test_expired_deadline_refused(bundle, now):
    with pytest.raises(ValueError):
        review(bundle, now=now)


def test_naive_clock_refused(bundle):
    with pytest.raises(ValueError):
        review(bundle, now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    "key,value",
    [
        ("validation_reports", ["/fake/all-true.json"]),
        ("trusted_verifier_source_sha256", ["e" * 64]),
        ("status", "eligible_for_profile_verification"),
        ("numeric_passes", True),
        ("cached_result", {"status": "eligible_for_profile_verification", "numeric_passes": True}),
        ("gates", dict.fromkeys(REQUIRED_CHECKS, True)),
    ],
)
def test_forged_all_true_and_cached_results_rejected(bundle, key, value):
    bundle["policy"][key] = value
    reseal_policy(bundle)
    with pytest.raises(ValueError):
        review(bundle)


@pytest.mark.parametrize("field", ["source_sha256", "partition_sha256", "calendar_sha256"])
def test_independent_identity_descriptors_bind_source_data_calendar(bundle, field):
    bundle["policy"]["bundles"]["candidate"]["identity"][field] = "e" * 64
    reseal_policy(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def repin_run(bundle):
    manifest_ref = seal(bundle["root"] / "manifest.json", bundle["manifest"])
    bundle["receipt"]["manifest_sha256"] = digest(bundle["manifest"])
    receipt_ref = seal(bundle["root"] / "receipt.json", bundle["receipt"])
    for descriptor in bundle["policy"]["bundles"].values():
        descriptor["run_manifest"] = dict(manifest_ref)
        descriptor["cycle_receipt"] = dict(receipt_ref)
    reseal_policy(bundle)


@pytest.mark.parametrize(
    "option,bad_value",
    [
        ("--run-id", "different-run"),
        ("--study", "/different-study"),
        ("--arm", "wrong-candidate"),
        ("--cost", "10"),
    ],
)
def test_actual_measurement_command_must_match_expected_identity(bundle, option, bad_value):
    command = bundle["manifest"]["commands"][1]["argv"]  # candidate, 6 bps
    command[command.index(option) + 1] = bad_value
    repin_run(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def test_duplicate_output_command_rejected(bundle):
    bundle["manifest"]["commands"].append(bundle["manifest"]["commands"][0])
    bundle["receipt"]["commands"].append({"command_index": 12, "returncode": 0})
    repin_run(bundle)
    with pytest.raises(ValueError):
        review(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "failed"),
        ("failure", "measurement interrupted"),
        ("cleanup_survivors", [{"pid": 123}]),
        ("execution_authority", "paper"),
        ("cycle_id", "different-cycle"),
    ],
)
def test_noncompleted_or_unrelated_receipt_rejected(bundle, field, value):
    bundle["receipt"][field] = value
    repin_run(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def test_incomplete_receipt_commands_rejected(bundle):
    bundle["receipt"]["commands"].pop()
    repin_run(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def test_boolean_success_returncode_rejected(bundle):
    bundle["receipt"]["commands"][0]["returncode"] = False
    repin_run(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def test_study_protocol_must_be_frozen_measurement_input(bundle):
    bundle["manifest"]["inputs"] = []
    repin_run(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def test_records_require_byte_identity_as_well_as_canonical_seal(bundle):
    reference = bundle["policy"]["bundles"]["candidate"]["records"]["2"]
    path = Path(reference["path"])
    value = json.loads(path.read_text())
    path.write_text(json.dumps(value, indent=8))
    assert digest(value) == path.with_suffix(".sha256").read_text()
    with pytest.raises(ValueError):
        review(bundle)


def test_cached_record_pass_does_not_supply_validation(bundle):
    def add_forgery(record):
        record["numeric_passes"] = True
        record["validation_passes"] = True
        record["status"] = "eligible_for_profile_verification"
        record["gates"] = dict.fromkeys(REQUIRED_CHECKS, True)

    mutate_record(bundle, add_forgery)
    result = review(bundle)
    assert result["status"] == "pending_validation"
    assert result["evaluation"]["validation_passes"] is False
    assert set(result["evaluation"]["missing_or_failed_validation"]) == REQUIRED_CHECKS


def test_numerical_loser_cannot_use_cached_pass(bundle):
    def make_loser(record):
        record["sessions"][-1]["equity"] = 110000.0
        record["sessions_sha256"] = digest(record["sessions"])
        record["metrics"] = {
            key: {"net_return": v.net_return, "max_drawdown": -v.drawdown}
            for key, v in _derived_periods(record["sessions"]).items()
        }
        record["numeric_passes"] = True
        record["status"] = "eligible_for_profile_verification"

    for cost in ("2", "6", "10"):
        mutate_record(bundle, make_loser, cost=cost)
    result = review(bundle)
    assert result["status"] == "rejected"
    assert result["evaluation"]["numeric_passes"] is False
    assert result["promotion_enabled"] is False


def test_symlink_evidence_refused(bundle):
    descriptor = bundle["policy"]["bundles"]["candidate"]
    original = Path(descriptor["records"]["2"]["path"])
    alias = bundle["root"] / "candidate-alias.json"
    alias.symlink_to(original)
    descriptor["records"]["2"]["path"] = str(alias)
    reseal_policy(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def independent_parent(bundle):
    """Bind an earlier accepted account separately from the new control files."""
    accepted = bundle["policy"]["bundles"]["accepted_parent"]
    accepted["identity"]["run_id"] = "earlier-accepted-run"
    for cost in ("2", "6", "10"):
        value = json.loads(Path(accepted["records"][cost]["path"]).read_text())
        value["run_id"] = "earlier-accepted-run"
        path = bundle["root"] / f"accepted-parent-{cost}bps.json"
        accepted["records"][cost] = seal(path, value)
        bundle["manifest"]["commands"].append(
            {
                "argv": [
                    "/absolute/python",
                    "/absolute/frozen-study.py",
                    "measure",
                    "--study",
                    str(bundle["root"]),
                    "--arm",
                    "control",
                    "--cost",
                    cost,
                    "--output",
                    str(path),
                    "--run-id",
                    "earlier-accepted-run",
                ],
                "cwd": str(bundle["root"]),
            }
        )
    bundle["receipt"]["commands"] = [
        {"command_index": index, "returncode": 0}
        for index in range(len(bundle["manifest"]["commands"]))
    ]
    repin_run(bundle)


def test_independently_accepted_parent_reproduces_without_supplied_scalar_hashes(bundle):
    independent_parent(bundle)
    assert review(bundle)["status"] == "pending_validation"


def test_control_must_reproduce_independently_accepted_history(bundle):
    independent_parent(bundle)

    def change_control(record):
        record["sessions"][-1]["equity"] = 119999.0
        record["sessions_sha256"] = digest(record["sessions"])
        record["metrics"] = {
            key: {"net_return": v.net_return, "max_drawdown": -v.drawdown}
            for key, v in _derived_periods(record["sessions"]).items()
        }

    mutate_record(bundle, change_control, role="control")
    with pytest.raises(ValueError, match="control failed exact"):
        review(bundle)


def test_no_caller_supplied_parent_session_scalar_override(bundle):
    bundle["policy"]["parent_sessions_sha256"] = dict.fromkeys(("2", "6", "10"), "e" * 64)
    reseal_policy(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def test_boolean_receipt_command_index_rejected(bundle):
    bundle["receipt"]["commands"][0]["command_index"] = False
    repin_run(bundle)
    with pytest.raises(ValueError):
        review(bundle)


def cli_case(bundle, *, omit=None, label="synthetic"):
    """Prepare an actual argv-controller cycle around the synthetic bundle."""
    campaign = bundle["root"] / "controller-campaign"
    campaign.mkdir()
    protocol = {
        "deadline_utc": (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat(),
        "max_cycle_seconds": 30,
        "min_start_remaining_seconds": 0,
        "promotion_policy_id": "test-numeric-review-only",
    }
    seal(campaign / "protocol.json", protocol)
    campaign_hash = digest(protocol)
    bundle["manifest"]["protocol_sha256"] = campaign_hash
    bundle["receipt"]["protocol_sha256"] = campaign_hash
    bundle["policy"]["campaign_protocol_sha256"] = campaign_hash
    repin_run(bundle)
    parent = {
        "profile_path": bundle["policy"]["parent_profile"]["path"],
        "profile_sha256": bundle["parent_sha256"],
    }
    (campaign / "state.json").write_text(
        json.dumps({"status": "ready", "active_run": None, "baseline": parent})
    )
    library = SCRIPT.parents[1]
    inputs = [
        {"path": str(library / name), "sha256": value}
        for name, value in source_hashes(library).items()
    ]
    inputs.extend(
        {"path": str(path), "sha256": byte_sha(path)}
        for path in (
            SCRIPT,
            bundle["policy_path"],
            SCRIPT.with_name("run_incremental_research_cycle.py"),
        )
    )
    if omit is not None:
        inputs = [item for item in inputs if not omit(item)]
    argv = [
        sys.executable,
        str(SCRIPT),
        "--campaign",
        str(campaign),
        "--policy",
        str(bundle["policy_path"]),
        "--policy-sha256",
        bundle["expected_policy_sha256"],
        "--label",
        label,
    ]
    manifest = {
        "schema": "incremental-research-cycle/1",
        "campaign_root": str(campaign),
        "cycle_id": "synthetic-review",
        "protocol_sha256": campaign_hash,
        "inputs": inputs,
        "commands": [
            {
                "argv": argv,
                "cwd": str(campaign),
                "env": {
                    "PYTHONPATH": str(library / "src"),
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                },
            }
        ],
    }
    manifest_path = campaign / "controller-manifest.json"
    seal(manifest_path, manifest)
    return campaign, manifest_path, parent, argv


def run_cli_controller(manifest_path):
    return subprocess.run(  # noqa: S603 - fixed local controller and synthetic sealed argv
        [
            sys.executable,
            str(SCRIPT.with_name("run_incremental_research_cycle.py")),
            "--manifest",
            str(manifest_path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_actual_controller_cli_preserves_baseline_and_publishes_only_pending(bundle):
    campaign, manifest, parent, _ = cli_case(bundle)
    result = run_cli_controller(manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads((campaign / "state.json").read_text())
    assert state["baseline"] == parent
    assert state["active_run"] is None
    output = campaign / "cycles/synthetic-review/numeric-review-synthetic.json"
    value = json.loads(output.read_text())
    assert digest(value) == output.with_suffix(".sha256").read_text().strip()
    assert value["status"] == "pending_validation"
    assert value["evaluation"]["numeric_passes"] is True
    assert value["promotion_enabled"] is False
    assert value["activation_authority"] is False
    assert set(value["evaluation"]["missing_or_failed_validation"]) == REQUIRED_CHECKS
    assert len(list(output.parent.glob("numeric-review-*.json"))) == 1


@pytest.mark.parametrize("omitted", ["policy", "library-source", "bridge"])
def test_actual_controller_cli_rejects_missing_frozen_inputs(bundle, omitted):
    def omit(item):
        if omitted == "policy":
            return item["path"] == str(bundle["policy_path"])
        if omitted == "bridge":
            return item["path"] == str(SCRIPT)
        return item["path"].endswith("/src/amat/__init__.py")

    campaign, manifest, parent, _ = cli_case(bundle, omit=omit)
    result = run_cli_controller(manifest)
    assert result.returncode != 0
    state = json.loads((campaign / "state.json").read_text())
    assert state["baseline"] == parent
    assert state["status"] == "failed"
    assert not list((campaign / "cycles").glob("**/numeric-review-*.json"))


def test_cli_outside_controller_rejected_without_writes(bundle):
    campaign, _, parent, argv = cli_case(bundle)
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.parents[1] / "src")}
    env.pop("AMAT_RESEARCH_RUN_ID", None)
    result = subprocess.run(  # noqa: S603 - fixed local reviewer and synthetic arguments
        argv, env=env, capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode != 0
    assert "active bounded controller" in result.stderr
    assert not (campaign / "cycles").exists()
    assert json.loads((campaign / "state.json").read_text())["baseline"] == parent


def test_cli_label_cannot_escape_active_output(bundle):
    campaign, manifest, parent, _ = cli_case(bundle, label="../../outside")
    result = run_cli_controller(manifest)
    assert result.returncode != 0
    assert not (bundle["root"] / "outside.json").exists()
    assert json.loads((campaign / "state.json").read_text())["baseline"] == parent


@pytest.mark.parametrize("target", ["report", "seal"])
def test_publish_refuses_dangling_symlinks_without_outside_write(tmp_path, target):
    path = tmp_path / "numeric-review.json"
    outside = tmp_path / "must-not-create.json"
    link = path if target == "report" else path.with_suffix(".sha256")
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        load_module().publish_review(path, {"status": "pending_validation"})
    assert link.is_symlink()
    assert not outside.exists()
    other = path.with_suffix(".sha256") if target == "report" else path
    assert not other.exists()


@pytest.mark.parametrize("target", ["report", "seal"])
def test_publish_preserves_existing_partial_evidence(tmp_path, target):
    path = tmp_path / "numeric-review.json"
    existing = path if target == "report" else path.with_suffix(".sha256")
    existing.write_text("preserved partial evidence")
    with pytest.raises(ValueError):
        load_module().publish_review(path, {"status": "pending_validation"})
    assert existing.read_text() == "preserved partial evidence"
    other = path.with_suffix(".sha256") if target == "report" else path
    assert not other.exists()


def test_publish_creates_exact_canonical_seal_and_private_files(tmp_path):
    path = tmp_path / "numeric-review.json"
    value = {"status": "pending_validation", "activation_authority": False}
    load_module().publish_review(path, value)
    assert json.loads(path.read_text()) == value
    assert path.with_suffix(".sha256").read_text().strip() == digest(value)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.with_suffix(".sha256").stat().st_mode & 0o777 == 0o600
