"""Run a preregistered argv-only research cycle with one writer and a 40-minute ceiling.

This controller proposes no experiments, submits no orders and never changes deployment
configuration. A completed command is measurement completion, not strategy acceptance.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

MAX_SECONDS = 40 * 60
SHUTDOWN_AND_RECEIPT_SECONDS = 2.0
PROMOTION_GATES = (
    "independent_review",
    "reproduction",
    "accounting",
    "causality",
    "native_parity",
    "full_cost_validation",
    "incumbent_improvement",
    "policy_compliance",
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def byte_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sealed(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or canonical_sha(value) != path.with_suffix(".sha256").read_text().strip()
    ):
        raise ValueError(f"Invalid JSON seal: {path}")
    return value


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def event(campaign: Path, kind: str, **fields: Any) -> None:
    with (campaign / "events.jsonl").open("a") as stream:
        stream.write(
            json.dumps({"event": kind, "utc": utcnow().isoformat(), **fields}, sort_keys=True)
            + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def absolute(value: Any) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("Paths must be explicit absolute paths")
    return Path(value)


def timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC deadline must have a timezone")
    return parsed.astimezone(dt.UTC)


def process_identity(pid: int) -> dict[str, Any] | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {
            "pid": pid,
            "start_ticks": fields[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def identity_alive(identity: dict[str, Any] | None) -> bool:
    return bool(identity and process_identity(identity["pid"]) == identity)


def run_processes(run_id: str) -> list[dict[str, Any]]:
    """Find live token-preserving descendants, including detached sessions.

    Workloads must preserve the inherited token. This is lifecycle cleanup for
    trusted study code, not containment of hostile or environment-scrubbing code.
    """
    if not run_id:
        return []
    token = ("AMAT_RESEARCH_RUN_ID=" + run_id).encode()
    found = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            identity = process_identity(int(directory.name))
            matches = token in (directory / "environ").read_bytes().split(b"\0")
            if identity and matches and identity_alive(identity):
                found.append(identity)
        except (OSError, PermissionError):
            continue
    return found


def active_processes(active: dict[str, Any]) -> bool:
    return any(identity_alive(active.get(key)) for key in ("writer", "child")) or bool(
        run_processes(active.get("run_id", ""))
    )


def signal_identity(identity: dict[str, Any], sig: signal.Signals) -> None:
    # Never send a signal based only on a stale numeric PID. Linux start ticks
    # and boot identity must still match immediately before signalling.
    if identity_alive(identity):
        with suppress(ProcessLookupError):
            os.kill(identity["pid"], sig)


def terminate_run(
    run_id: str,
    process: subprocess.Popen[bytes] | None,
    child_identity: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Terminate matching descendants regardless of their process group/session."""
    targets = run_processes(run_id)
    if child_identity and identity_alive(child_identity) and child_identity not in targets:
        targets.append(child_identity)
    for identity in targets:
        signal_identity(identity, signal.SIGTERM)
    if targets:
        time.sleep(0.05)
    # Rescan because a TERM handler can create or detach another child. Capture
    # start identity afresh; retain former targets only when still the same PID.
    for _ in range(2):
        current = run_processes(run_id)
        current.extend(
            identity for identity in targets if identity_alive(identity) and identity not in current
        )
        for identity in current:
            signal_identity(identity, signal.SIGKILL)
        targets = current
        if current:
            time.sleep(0.025)
    if process is not None:
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0.1)
    survivors = run_processes(run_id)
    survivors.extend(
        identity for identity in targets if identity_alive(identity) and identity not in survivors
    )
    return survivors


def verify_inputs(inputs: list[dict[str, Any]]) -> None:
    for item in inputs:
        if byte_sha(absolute(item["path"])) != item["sha256"]:
            raise ValueError(f"Frozen input bytes changed: {item['path']}")


def validate_promotion(
    protocol: dict[str, Any], _state: dict[str, Any], _path: Path
) -> dict[str, Any]:
    """Legacy receipt booleans cannot authorize baseline changes, even if enabled."""
    if protocol.get("enable_receipt_baseline_updates") is not True:
        raise ValueError("Receipt baseline updates are disabled by the sealed protocol")
    raise ValueError(
        "Receipt booleans are unsupported; numerical review is read-only and "
        "native profile/default promotion requires a separately verified apply contract"
    )


def run(manifest_path: Path) -> int:  # noqa: PLR0912, PLR0915 - explicit bounded lifecycle
    entered = time.monotonic()
    manifest = sealed(manifest_path)
    if manifest.get("schema") != "incremental-research-cycle/1":
        raise ValueError("Unknown cycle manifest schema")
    campaign = absolute(manifest["campaign_root"])
    protocol_path = campaign / "protocol.json"
    protocol = sealed(protocol_path)
    if canonical_sha(protocol) != manifest["protocol_sha256"]:
        raise ValueError("Cycle protocol identity mismatch")
    deadline = timestamp(protocol["deadline_utc"])
    maximum = float(protocol.get("max_cycle_seconds", MAX_SECONDS))
    if not math.isfinite(maximum) or not 0 < maximum <= MAX_SECONDS:
        raise ValueError("Cycle duration must be positive and no more than 40 minutes")
    minimum = float(protocol.get("min_start_remaining_seconds", 1200))
    if not math.isfinite(minimum) or minimum < 0:
        raise ValueError("Minimum remaining time must be finite and nonnegative")
    cycle = manifest["cycle_id"]
    if not isinstance(cycle, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", cycle):
        raise ValueError("Invalid cycle identity")
    commands = manifest.get("commands", [])
    if not isinstance(commands, list) or not commands:
        raise ValueError("Manifest requires explicit frozen commands")
    for command in commands:
        argv = command.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(a, str) or "\0" in a for a in argv)
        ):
            raise ValueError("Command must be a nonempty string argv list")
        absolute(argv[0])
        absolute(command["cwd"])
        timeout = float(command.get("timeout_seconds", maximum))
        if not math.isfinite(timeout) or not 0 < timeout <= MAX_SECONDS:
            raise ValueError("Command timeout exceeds 40 minutes or is invalid")
        env = command.get("env", {})
        if not isinstance(env, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or "=" in k or "\0" in k + v
            for k, v in env.items()
        ):
            raise ValueError("Invalid explicit command environment")
    inputs = manifest.get("inputs", [])
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("Manifest must pin frozen input bytes")
    campaign.mkdir(parents=True, exist_ok=True)
    with (campaign / "writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75
        state_path = campaign / "state.json"
        state = json.loads(state_path.read_text())
        active = state.get("active_run")
        if active:
            if active_processes(active):
                event(
                    campaign, "overlap_refused", cycle_id=cycle, active_run_id=active.get("run_id")
                )
                return 75
            # Stale work requires explicit human/Codex review. Never label an
            # unobserved child successful or silently start another experiment.
            state["status"] = "interrupted_review_required"
            save(state_path, state)
            event(campaign, "stale_run_requires_review", cycle_id=cycle, active_run=active)
            return 76
        remaining = (deadline - utcnow()).total_seconds()
        if (
            remaining <= 0
            or remaining < minimum
            or state.get("status") in ("closed", "finalization_required")
        ):
            state.update(
                status="finalization_required",
                active_run=None,
                finalization_required_utc=utcnow().isoformat(),
            )
            save(state_path, state)
            event(campaign, "finalization_required", remaining_seconds=remaining)
            return 0
        verify_inputs(inputs)
        output = campaign / "cycles" / cycle
        output.mkdir(parents=True, exist_ok=False)
        run_id = str(uuid.uuid4())
        active = {
            "run_id": run_id,
            "cycle_id": cycle,
            "writer": process_identity(os.getpid()),
            "child": None,
            "manifest_path": str(manifest_path),
            "manifest_sha256": canonical_sha(manifest),
            "protocol_sha256": canonical_sha(protocol),
            "started_utc": utcnow().isoformat(),
            "maximum_seconds": maximum,
            "output": str(output),
        }
        state.update(status="running", active_run=active)
        save(state_path, state)
        event(campaign, "cycle_started", **active)
        outcomes: list[dict[str, Any]] = []
        process = None
        failure = None
        timed_out = False
        try:
            for index, command in enumerate(commands):
                # Both clocks are checked before *every* command. Later clock
                # adjustments cannot defeat the monotonic total-run ceiling.
                budget = min(
                    maximum - (time.monotonic() - entered),
                    (deadline - utcnow()).total_seconds(),
                    float(command.get("timeout_seconds", maximum)),
                )
                if budget <= SHUTDOWN_AND_RECEIPT_SECONDS:
                    raise subprocess.TimeoutExpired(command["argv"], max(0, budget))
                if sealed(manifest_path) != manifest or sealed(protocol_path) != protocol:
                    raise ValueError("Sealed command/protocol changed during cycle")
                verify_inputs(inputs)
                with (output / f"command-{index:02d}.log").open("wb") as log:
                    process = subprocess.Popen(  # noqa: S603 - sealed human/Codex argv, never shell text
                        command["argv"],
                        cwd=command["cwd"],
                        env={
                            **os.environ,
                            **command.get("env", {}),
                            "AMAT_RESEARCH_RUN_ID": run_id,
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        shell=False,
                        start_new_session=True,
                        pass_fds=(lock.fileno(),),
                    )
                    active.update(child=process_identity(process.pid), command_index=index)
                    save(state_path, state)
                    returncode = process.wait(timeout=budget - SHUTDOWN_AND_RECEIPT_SECONDS)
                # Disallow surviving background workers even if their leader
                # exited successfully. A cycle is a bounded measurement tree.
                if run_processes(run_id):
                    raise ValueError(
                        "Command left background descendants (including detached sessions)"
                    )
                outcomes.append(
                    {
                        "command_index": index,
                        "returncode": returncode,
                        "completed_utc": utcnow().isoformat(),
                    }
                )
                process = None
                active["child"] = None
                save(state_path, state)
                if returncode:
                    raise ValueError(f"Command {index} failed with exit {returncode}")
                verify_inputs(inputs)
            if sealed(manifest_path) != manifest or sealed(protocol_path) != protocol:
                raise ValueError("Sealed command/protocol changed during cycle")
            if manifest.get("promotion_receipt"):
                if utcnow() >= deadline:
                    raise ValueError("Promotion deadline expired")
                validate_promotion(protocol, state, absolute(manifest["promotion_receipt"]))
            state["status"] = "cycle_complete"
        except subprocess.TimeoutExpired:
            timed_out = True
            failure = "Cycle or command time budget exhausted; matching descendants terminated"
            state["status"] = "timed_out"
        except BaseException as exc:
            failure = f"{type(exc).__name__}: {exc}"
            state["status"] = "failed"
        finally:
            survivors = terminate_run(run_id, process, active.get("child"))
            if survivors:
                failure = "Cleanup incomplete: live matching descendants require review"
                state["status"] = "cleanup_review_required"
            receipt = {
                "schema": "incremental-research-cycle-receipt/1",
                "run_id": run_id,
                "cycle_id": cycle,
                "manifest_sha256": canonical_sha(manifest),
                "protocol_sha256": canonical_sha(protocol),
                "status": state["status"],
                "commands": outcomes,
                "failure": failure,
                "completed_utc": utcnow().isoformat(),
                "elapsed_seconds": time.monotonic() - entered,
                "execution_authority": "none",
                "process_scope": "trusted descendants preserving AMAT_RESEARCH_RUN_ID",
                "cleanup_survivors": survivors,
            }
            save(output / "receipt.json", receipt)
            (output / "receipt.sha256").write_text(canonical_sha(receipt) + "\n")
            state.update(
                active_run=active if survivors else None,
                last_cycle_receipt=str(output / "receipt.json"),
                last_cycle_id=cycle,
                updated_utc=utcnow().isoformat(),
            )
            save(state_path, state)
            event(
                campaign,
                "cycle_finished",
                cycle_id=cycle,
                status=state["status"],
                receipt_sha256=canonical_sha(receipt),
                failure=failure,
            )
        return 124 if timed_out else (1 if failure else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()

    def interrupted(signum: int, _frame: Any) -> None:
        raise InterruptedError(f"Controller received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        return run(args.manifest.resolve())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"Research cycle refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
