"""Bounded local native-plan/broker-contract validation, never a trading runner."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from autoresearch.improvements.agent_native_adapter import execute_adapter
from autoresearch.improvements.agent_native_plan import export_native_plan, restore_native_plan
from autoresearch.improvements.source_attestation import attest_loaded_sources

ROOT = Path(__file__).resolve().parents[2]
REFUSAL_COUNT = 2  # Outer envelope and extracted native projection.


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def seal(path: Path, value: Any) -> None:
    for target, body in (
        (path, json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"),
        (path.with_suffix(".sha256"), digest(value) + "\n"),
    ):
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_sealed(path: Path) -> Any:
    value = json.loads(path.read_text())
    if digest(value) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("Validation seal differs")
    return value


def verify_sources(manifest: dict[str, Any]) -> dict[str, str]:
    for name, expected in manifest["files"].items():
        path = ROOT / name
        if (
            not path.resolve().is_relative_to(ROOT)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Validation source changed: " + name)
    expected = {
        k.removeprefix("amat/src/"): v
        for k, v in manifest["files"].items()
        if k.startswith("amat/src/")
    }
    return attest_loaded_sources(ROOT / "amat/src", expected, sys.modules)


def validate(  # noqa: PLR0912, PLR0915 -- ordered exact-source/receipt/parity boundaries
    manifest_path: Path, output: Path
) -> dict[str, Any]:
    manifest = read_sealed(manifest_path)
    if manifest["schema"] != "native-plan-broker-validation/1" or manifest["output"] != str(output):
        raise ValueError("Unknown validation contract/output")
    output.mkdir(exist_ok=False)
    started = dt.datetime.now(dt.UTC)
    with (output.parent / "validation-writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        seal(
            output / "identity.json",
            {
                "pid": os.getpid(),
                "started_utc": started.isoformat(),
                "manifest_sha256": digest(manifest),
                "scope": "local synthetic research validation only",
            },
        )
        verify_sources(manifest)
        spec = importlib.util.spec_from_file_location(
            "frozen_adapter_fixture", ROOT / "amat/scripts/run_native_agent_adapter_study.py"
        )
        if spec is None or spec.loader is None:
            raise ValueError("Missing fixed fixture source")
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        if manifest["cases"] != list(fixtures.CASES) or manifest["costs_bps"] != [2, 6, 10]:
            raise ValueError("Fixed validation cases differ")
        packets = []
        rows = []
        for cost in manifest["costs_bps"]:
            for case in manifest["cases"]:
                ctx, proposal, evidence, coverage = fixtures.fixture(cost, case)
                plan = fixtures.compile_adapter(proposal, ctx)
                packet = export_native_plan(plan)
                stem = f"{case}-{cost}bps"
                seal(output / f"{stem}-plan.json", packet)
                restored = restore_native_plan(read_sealed(output / f"{stem}-plan.json"), ctx)
                seal(
                    output / f"{stem}-attempt.json",
                    {
                        "plan_id": restored.plan_id,
                        "account_id": ctx.account_id,
                        "context_sha256": ctx.evidence_sha256,
                        "single_attempt": True,
                    },
                )
                receipt = execute_adapter(restored, evidence, action_coverage=coverage)
                fills, missed, ledger, attr = fixtures.native_control(ctx, evidence, case, cost)
                checks = {
                    "roundtrip_exact": restored == plan,
                    "native_fills_exact": receipt["fills"]
                    == json.loads(json.dumps([asdict(f) for f in fills], default=str)),
                    "native_misses_exact": receipt["missed"]
                    == json.loads(json.dumps([asdict(m) for m in missed], default=str)),
                    "native_ledger_exact": receipt["ledger"] == fixtures.ledger_json(ledger),
                    "native_accounting_exact": receipt["accounting"]
                    == (json.loads(json.dumps(asdict(attr), default=str)) if attr else None),
                    "original_intents_exact": receipt["original_authorized_intents"]
                    == packet["adapter_plan"]["context"]["authorized_intents"],
                    "pending_cash_exact": receipt["original_pending_cash"]
                    == packet["original_pending_cash"],
                    "expiry_exact": packet["native_plan"]["expires_at"] == proposal["expires_at"]
                    and all(a["expires_at"] == proposal["expires_at"] for a in receipt["actions"]),
                    "quantity_conserved": all(
                        fixtures.D(a["original_quantity"])
                        == fixtures.D(a["cumulative_fills"]) + fixtures.D(a["remaining_quantity"])
                        for a in receipt["actions"]
                    ),
                }
                try:
                    execute_adapter(
                        restored, evidence, action_coverage=coverage, previous_receipt=receipt
                    )
                except ValueError:
                    checks["prior_receipt_refused"] = True
                else:
                    checks["prior_receipt_refused"] = False
                if not all(checks.values()):
                    raise ValueError("Native parity failed: " + stem)
                seal(output / f"{stem}-receipt.json", receipt)
                packets.append(packet)
                rows.append(
                    {
                        "case": case,
                        "cost_bps": cost,
                        "checks": checks,
                        "plan_file": stem + "-plan.json",
                        "receipt_file": stem + "-receipt.json",
                    }
                )
        # No credentials or inherited broker configuration cross this process boundary.
        broker = subprocess.run(  # noqa: S603 -- fixed argv and exact pinned local source
            [sys.executable, str(ROOT / "executor/scripts/research_native_contract_probe.py")],
            input=json.dumps(packets),
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
            env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "executor/src")},
        )
        witnesses = json.loads(broker.stdout)
        if len(witnesses) != len(packets):
            raise ValueError("Incomplete broker witnesses")
        for packet, witness in zip(packets, witnesses, strict=True):
            if (
                len(witness["parser_refusals"]) != REFUSAL_COUNT
                or not all(row["refused"] for row in witness["parser_refusals"])
                or witness["network_calls"] != 0
            ):
                raise ValueError("Broker boundary refusal failed")
            if len(witness["wire_payloads"]) != len(packet["native_plan"]["intents"]):
                raise ValueError("Incomplete serialization witnesses")
            for intent, body, condition in zip(
                packet["native_plan"]["intents"],
                witness["wire_payloads"],
                packet["conditions"],
                strict=True,
            ):
                if fixtures.D(intent["quantity"]) != fixtures.D(body["qty"]) or fixtures.D(
                    condition["limit_price"]
                ) != fixtures.D(body["limit_price"]):
                    raise ValueError("Broker decimal serialization differs")
        seal(output / "broker-witnesses.json", witnesses)
        loaded = verify_sources(manifest)
        result = {
            "schema": "native-plan-broker-validation-result/1",
            "started_utc": started.isoformat(),
            "completed_utc": dt.datetime.now(dt.UTC).isoformat(),
            "manifest_sha256": digest(manifest),
            "native_cases_passed": len(rows),
            "cases": rows,
            "loaded_source_files_attested": len(loaded),
            "legacy_parser_refusals": 2 * len(witnesses),
            "wire_quantity_and_limit_checks": sum(len(w["wire_payloads"]) for w in witnesses),
            "research_native_plan_parity": "verified on fixed synthetic cases",
            "broker_execution_parity": "unsupported; legacy parser safely refuses",
            "real_broker_fills": "unavailable",
            "network_calls": 0,
            "paper_execution_changes": False,
            "promotion_authority": False,
            "remaining_gates": witnesses[0]["limitations"]
            + [
                "No real broker queue/cost/fill observation",
                "No global production receipt ownership or corporate-action support",
                "No prospective agent performance or strategy-level promotion evidence",
            ],
        }
        seal(output / "summary.json", result)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.manifest, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))
