"""Read-only all-cost numerical review; never authorizes a baseline or profile change.

The caller pins a coordinator-owned evidence policy. Seals and process receipts bind
identities but are not proof of independent scientific validation. No externally
asserted validation booleans/reports are admitted by this first-stage bridge.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

from autoresearch.improvements.incremental_gates import (
    COSTS,
    MeasurementIdentity,
    evaluate_incremental,
    load_native_measurement,
    read_sealed,
)
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, source_hashes

SHA256_LENGTH = 64
BUNDLES = frozenset({"candidate", "control", "original", "qqq", "accepted_parent"})
POLICY_KEYS = frozenset({"schema", "campaign_protocol_sha256", "parent_profile", "bundles"})
DESCRIPTOR_KEYS = frozenset({"protocol", "identity", "records", "run_manifest", "cycle_receipt"})
IDENTITY_KEYS = frozenset(f.name for f in fields(MeasurementIdentity))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_path(reference: Any) -> Path:
    """Resolve only an explicit regular absolute file pinned by byte hash."""
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise ValueError("Evidence reference requires exact path/hash fields")
    raw, expected = reference["path"], reference["sha256"]
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise ValueError("Evidence paths must be absolute")
    path = Path(raw)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Evidence must be an existing regular file, not symlink")
    if (
        not isinstance(expected, str)
        or len(expected) != SHA256_LENGTH
        or sha256_file(path) != expected
    ):
        raise ValueError("Evidence byte identity mismatch")
    return path


def _option(argv: list[str], option: str) -> str:
    if argv.count(option) != 1:
        raise ValueError("Measurement command has missing/duplicate identity option")
    index = argv.index(option)
    if index + 1 == len(argv):
        raise ValueError("Measurement command has missing option value")
    return argv[index + 1]


def _bundle(descriptor: Any, name: str, campaign_sha: str) -> dict:
    if not isinstance(descriptor, dict) or set(descriptor) != DESCRIPTOR_KEYS:
        raise ValueError("Bundle fields differ from the frozen contract")
    identity_fields = descriptor["identity"]
    if not isinstance(identity_fields, dict) or set(identity_fields) != IDENTITY_KEYS:
        raise ValueError("Expected identity must contain every typed identity field")
    identity = MeasurementIdentity(**identity_fields)
    expected_role = "qqq_benchmark" if name == "qqq" else "strategy"
    if identity.role != expected_role:
        raise ValueError("Bundle role differs from expected strategy/QQQ purpose")
    protocol_path = pinned_path(descriptor["protocol"])
    manifest_path = pinned_path(descriptor["run_manifest"])
    receipt_path = pinned_path(descriptor["cycle_receipt"])
    manifest, receipt = read_sealed(manifest_path), read_sealed(receipt_path)
    commands = manifest.get("commands")
    outcomes = receipt.get("commands")
    if (
        manifest.get("schema") != "incremental-research-cycle/1"
        or manifest.get("protocol_sha256") != campaign_sha
        or receipt.get("schema") != "incremental-research-cycle-receipt/1"
        or receipt.get("protocol_sha256") != campaign_sha
        or receipt.get("manifest_sha256") != digest(manifest)
        or receipt.get("cycle_id") != manifest.get("cycle_id")
        or receipt.get("status") != "cycle_complete"
        or receipt.get("failure") is not None
        or receipt.get("cleanup_survivors") != []
        or receipt.get("execution_authority") != "none"
        or not isinstance(commands, list)
        or not commands
        or not isinstance(outcomes, list)
        or len(outcomes) != len(commands)
        or any(
            type(item.get("command_index")) is not int
            or item.get("command_index") != i
            or type(item.get("returncode")) is not int
            or item["returncode"] != 0
            for i, item in enumerate(outcomes)
        )
    ):
        raise ValueError("Measurement run receipt/manifest is incomplete or incompatible")
    if not any(
        item.get("path") == str(protocol_path)
        and item.get("sha256") == descriptor["protocol"]["sha256"]
        for item in manifest.get("inputs", [])
    ):
        raise ValueError("Study protocol was not pinned in the measurement manifest")
    records = descriptor["records"]
    if not isinstance(records, dict) or set(records) != set(map(str, COSTS)):
        raise ValueError("Exactly three cost records required")
    result = {}
    for cost in COSTS:
        path = pinned_path(records[str(cost)])
        matches = []
        for command in commands:
            argv = command.get("argv")
            if not isinstance(argv, list) or any(not isinstance(a, str) for a in argv):
                raise ValueError("Measurement manifest must contain explicit argv lists")
            if "--output" in argv and _option(argv, "--output") == str(path):
                matches.append(argv)
        if len(matches) != 1:
            raise ValueError("Native record has no unique frozen measurement command")
        argv = matches[0]
        expected_options = {
            "--study": str(protocol_path.parent),
            "--arm": identity.arm,
            "--cost": str(cost),
            "--run-id": identity.run_id,
        }
        if any(_option(argv, key) != value for key, value in expected_options.items()):
            raise ValueError("Measurement command differs from independently expected identity")
        result[cost] = load_native_measurement(path, protocol_path, expected=identity)
    return result


def review_bundle(  # noqa: PLR0912 -- explicit fail-closed review boundary
    policy_path: Path,
    *,
    expected_policy_sha256: str,
    campaign_protocol_sha256: str,
    current_parent_sha256: str,
    deadline_utc: str,
    now: dt.datetime,
) -> dict[str, Any]:
    """Recompute numerical gates under caller-owned context, without mutation.

    All nonnumeric checks remain unknown here. A numeric pass means pending
    validation, never native-profile eligibility or authority to change defaults.
    """
    if now.tzinfo is None:
        raise ValueError("Actual review time must have a timezone")
    deadline = dt.datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
    if deadline.tzinfo is None or now >= deadline:
        raise ValueError("Campaign review deadline expired or lacks timezone")
    if not policy_path.is_absolute() or policy_path.is_symlink():
        raise ValueError("Policy must be an explicit absolute file")
    policy = read_sealed(policy_path)
    if digest(policy) != expected_policy_sha256:
        raise ValueError("Caller-pinned policy identity differs")
    if set(policy) != POLICY_KEYS or policy.get("schema") != "incremental-bundle-review-policy/1":
        raise ValueError("Unsupported numerical review policy fields")
    if policy["campaign_protocol_sha256"] != campaign_protocol_sha256:
        raise ValueError("Campaign identity differs")
    parent_path = pinned_path(policy["parent_profile"])
    if policy["parent_profile"]["sha256"] != current_parent_sha256:
        raise ValueError("Stale or different current parent profile")
    descriptors = policy["bundles"]
    if not isinstance(descriptors, dict) or set(descriptors) != BUNDLES:
        raise ValueError("Candidate/control/original/QQQ/accepted-parent bundles required")
    loaded = {key: _bundle(descriptors[key], key, campaign_protocol_sha256) for key in BUNDLES}
    for key in ("candidate", "control", "accepted_parent"):
        if loaded[key][COSTS[0]].identity.profile_sha256 != current_parent_sha256:
            raise ValueError("Research measurement does not use the current baseline identity")
    output = evaluate_incremental(
        candidate=loaded["candidate"],
        control=loaded["control"],
        original=loaded["original"],
        qqq=loaded["qqq"],
        parent_sessions_sha256={
            cost: row.sessions_sha256 for cost, row in loaded["accepted_parent"].items()
        },
        validation_reports=[],
        trusted_verifier_source_sha256=frozenset(),
    )
    if output["status"] not in {"rejected", "pending_validation"} or output["validation_passes"]:
        raise ValueError("Read-only bridge cannot grant eligibility")
    # Recheck mutable filesystem identities after all reads. The controller owns
    # the writer lock; neither this function nor its outputs advance the parent.
    if read_sealed(policy_path) != policy or sha256_file(parent_path) != current_parent_sha256:
        raise ValueError("Policy or parent changed during numerical review")
    for descriptor in descriptors.values():
        for key in ("protocol", "run_manifest", "cycle_receipt"):
            pinned_path(descriptor[key])
        for reference in descriptor["records"].values():
            pinned_path(reference)
    return {
        "schema": "incremental-read-only-bundle-review/1",
        "reviewed_utc": now.astimezone(dt.UTC).isoformat(),
        "policy_sha256": expected_policy_sha256,
        "campaign_protocol_sha256": campaign_protocol_sha256,
        "parent_profile_sha256": current_parent_sha256,
        "mode": "numerical_review_only",
        "status": output["status"],
        "evaluation": output,
        "promotion_enabled": False,
        "activation_authority": False,
        "native_profile_verified": False,
        "limitations": [
            "No nonnumeric reports are admitted; native/profile/execution/robustness "
            "validation remains unavailable.",
            "A parent profile hash in a callback study is not a deployable candidate profile.",
            "Pinned run receipts establish provenance; independent scientific correctness "
            "and permission to update defaults remain separate.",
        ],
        "verifier_source_sha256": sha256_file(Path(__file__)),
    }


def publish_review(path: Path, result: dict[str, Any]) -> None:
    """Exclusively create an immutable report then its seal, without following links."""
    seal = path.with_suffix(".sha256")
    if any(p.exists() or p.is_symlink() for p in (path, seal)):
        raise ValueError("Immutable report/seal already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for target, contents in (
        (path, json.dumps(result, sort_keys=True, allow_nan=False) + "\n"),
        (seal, digest(result) + "\n"),
    ):
        with os.fdopen(os.open(target, flags, 0o600), "w") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def main() -> None:  # noqa: PLR0912, PLR0915 -- bounded evidence/context checks
    """Run only as a bounded child of the existing argv-only controller."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    if not args.campaign.is_absolute() or not args.policy.is_absolute():
        raise ValueError("Explicit absolute campaign and policy paths required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", args.label):
        raise ValueError("Safe report label required")
    # Importing the controller grants no authority: its parent owns writer.lock
    # for this entire bounded command and performs its own before/after checks.
    protocol = read_sealed(args.campaign / "protocol.json")
    state_path = args.campaign / "state.json"
    state = json.loads(state_path.read_text())
    active = state.get("active_run")
    token = os.environ.get("AMAT_RESEARCH_RUN_ID")
    if not active or not token or active.get("run_id") != token:
        raise ValueError("Read-only review requires the active bounded controller run")
    manifest_path = Path(active["manifest_path"])
    manifest = read_sealed(manifest_path)
    if (
        digest(manifest) != active.get("manifest_sha256")
        or digest(protocol) != active.get("protocol_sha256")
        or manifest.get("campaign_root") != str(args.campaign)
    ):
        raise ValueError("Active controller context identity differs")
    evaluator = (
        Path(__file__).resolve().parents[1] / "src/autoresearch/improvements/incremental_gates.py"
    )
    for required in (args.policy, Path(__file__).resolve(), evaluator):
        if not any(
            item.get("path") == str(required) and item.get("sha256") == sha256_file(required)
            for item in manifest["inputs"]
        ):
            raise ValueError("Review policy/bridge/evaluator must be frozen controller inputs")
    library_root = Path(__file__).resolve().parents[1]
    expected_sources = {
        str(Path(item["path"]).relative_to(library_root)): item["sha256"]
        for item in manifest["inputs"]
        if Path(item["path"]).is_relative_to(library_root / "src")
    }
    if source_hashes(library_root) != expected_sources:
        raise ValueError("Verifier library source set differs from frozen controller inputs")
    attest_loaded_sources(library_root / "src", expected_sources, dict(sys.modules))
    output = Path(active["output"])
    expected_output = args.campaign / "cycles" / active["cycle_id"]
    if output != expected_output or output.resolve() != expected_output:
        raise ValueError("Review output must be the active controller cycle")
    path = output / ("numeric-review-" + args.label + ".json")
    if (
        path.exists()
        or path.is_symlink()
        or path.with_suffix(".sha256").exists()
        or path.with_suffix(".sha256").is_symlink()
    ):
        raise ValueError("Immutable numeric review already exists")
    result = review_bundle(
        args.policy,
        expected_policy_sha256=args.policy_sha256,
        campaign_protocol_sha256=digest(protocol),
        current_parent_sha256=state["baseline"]["profile_sha256"],
        deadline_utc=protocol["deadline_utc"],
        now=dt.datetime.now(dt.UTC),
    )
    if source_hashes(library_root) != expected_sources:
        raise ValueError("Verifier library sources changed during review")
    attest_loaded_sources(library_root / "src", expected_sources, dict(sys.modules))
    current = json.loads(state_path.read_text())
    if current.get("active_run") != active or current.get("baseline") != state.get("baseline"):
        raise ValueError("Active review parent/context changed")
    if (
        read_sealed(manifest_path) != manifest
        or read_sealed(args.campaign / "protocol.json") != protocol
    ):
        raise ValueError("Controller manifest/protocol changed")
    if dt.datetime.now(dt.UTC) >= dt.datetime.fromisoformat(
        protocol["deadline_utc"].replace("Z", "+00:00")
    ):
        raise ValueError("Campaign deadline expired before review publication")
    if output.resolve() != expected_output or not output.is_dir():
        raise ValueError("Active output directory changed before publication")
    publish_review(path, result)
    print(f"{args.label}: {result['status']}; numerical review only; default unchanged", flush=True)


if __name__ == "__main__":
    main()
