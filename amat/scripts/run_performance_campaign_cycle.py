"""Single-writer, deadline-gated frozen measurement and publication for a campaign cycle."""

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Script is run with PYTHONPATH=amat/src (or installed AMAT).
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.study import digest, write


def save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    write(temporary, value)
    temporary.replace(path)


def main() -> None:  # noqa: PLR0912, PLR0915 - one explicit locked cycle lifecycle
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    root = source.parent
    proposal = sealed(args.proposal.resolve())
    campaign = Path(proposal["campaign_root"])
    contract = sealed(campaign / "protocol.json")
    with (campaign / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = dt.datetime.now(dt.UTC)
        remaining = (dt.datetime.fromisoformat(contract["deadline_utc"]) - now).total_seconds()
        if remaining < 20 * 60:
            raise ValueError(
                "Final campaign cycle: checkpoint and report, do not start new research"
            )
        state = json.loads((campaign / "state.json").read_text())
        active = state.get("active_run")
        if active:
            for key in ["pid", "child_pid"]:
                pid = active.get(key)
                if pid and Path(f"/proc/{pid}").exists():
                    raise ValueError(
                        "Existing campaign writer/measurement must be resumed, not duplicated"
                    )
        if not re.fullmatch(r"[0-9]{3}-[a-z0-9-]{1,64}", proposal["cycle_id"]):
            raise ValueError("Invalid cycle identifier")
        baseline_protocol = sealed(Path(contract["baseline_study"]) / "protocol.json")
        snapshot = Path(contract["snapshot"])
        for key, expected in baseline_protocol["partition_sha256"].items():
            partition = next(
                p for p in [snapshot / key, snapshot / (key + ".parquet")] if p.is_file()
            )
            if hashlib.sha256(partition.read_bytes()).hexdigest() != expected:
                raise ValueError("Snapshot partition bytes changed")
        output = campaign / "cycles" / proposal["cycle_id"]
        if output.exists():
            raise ValueError("Immutable cycle exists; review/resume rather than overwrite")
        frozen = output / "frozen"
        frozen.mkdir(parents=True)
        for folder in ["src", "configs"]:
            shutil.copytree(
                source / folder,
                frozen / folder,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        state.update(
            status="measuring",
            first_research_run_started=True,
            active_run={
                "cycle_id": proposal["cycle_id"],
                "pid": os.getpid(),
                "child_pid": None,
                "output": str(output),
                "started_utc": now.isoformat(),
            },
        )
        save(campaign / "state.json", state)
        with (campaign / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"event": "measurement_started", **state["active_run"]}) + "\n")
        code = (
            "from pathlib import Path; import sys; "
            "from autoresearch.improvements.campaign_cycle_study import register,run; "
            "register(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]));"
            "run(Path(sys.argv[3]))"
        )
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter/frozen module, no shell
            [
                sys.executable,
                "-c",
                code,
                str(frozen),
                contract["snapshot"],
                str(output / "study"),
                str(args.proposal.resolve()),
            ],
            env={
                **os.environ,
                "PYTHONPATH": str(frozen / "src"),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        state["active_run"]["child_pid"] = process.pid
        save(campaign / "state.json", state)
        try:
            status = process.wait(timeout=min(remaining, 15 * 60))
            if status:
                raise ValueError(f"Frozen measurement failed with exit {status}")
            study = output / "study"
            for path in study.glob("*.json"):
                sealed(path)
            protocol = sealed(study / "protocol.json")
            decisions = sealed(study / "decisions.json")["decisions"]
            publication = {
                "schema": "campaign-publication/1",
                "cycle_id": proposal["cycle_id"],
                "current_default": proposal["labels"]["core_daily"].removeprefix("Current best · "),
                "study_relative_path": str(study.relative_to(root)),
                "protocol_sha256": digest(protocol),
                "hypothesis": proposal["hypothesis"],
                "completed_utc": dt.datetime.now(dt.UTC).isoformat(),
                "coverage_complete": protocol["coverage_complete"],
                "history_count": protocol["source_verified_history_count"],
                "pending_replay_count": protocol["pending_replay_count"],
                "legacy_ledger_count": protocol["legacy_ledger_count"],
                "frontier": protocol["frontier"],
                "statuses": {a: d["status"] for a, d in decisions.items()},
                "message": (
                    "Historical comparison coverage is incomplete; promotion is blocked. "
                    "Rejected candidates remain visible for audit."
                    if not protocol["coverage_complete"]
                    else "Historical coverage verified. See each candidate decision; versioned "
                    "profile verification is required before any default change."
                ),
            }
            audit = sealed(Path(proposal["audit_path"]))
            replay = audit.get("replay_coverage", {})
            publication["verified_historical_replays"] = len(replay.get("verified_identities", []))
            prior_path = campaign / "latest-study.json"
            if prior_path.exists():
                prior = sealed(prior_path).get("benchmark_replay")
                receipts = {r["receipt"] for r in replay.get("verified_identities", [])}
                valid_receipts = {digest(sealed(Path(path))) for path in receipts}
                required_receipts = (
                    set(prior.get("receipt_sha256s", [prior.get("receipt_sha256")]))
                    if prior
                    else set()
                )
                if required_receipts and required_receipts <= valid_receipts:
                    publication["benchmark_replay"] = prior
            archive = campaign / "publications" / f"{proposal['cycle_id']}.json"
            archive.parent.mkdir(exist_ok=True)
            if archive.exists():
                raise ValueError("Immutable cycle publication already exists")
            write(archive, publication)
            archive.with_suffix(".sha256").write_text(digest(publication))
            save(campaign / "latest-study.json", publication)
            (campaign / "latest-study.sha256").write_text(digest(publication))
            state.update(
                status="cycle_complete",
                active_run=None,
                completed_proposals=state["completed_proposals"] + 1,
                last_completed_run=str(study),
                next_action=(
                    "Analyze this cycle; resolve older benchmark contract/replay coverage "
                    "incrementally; preregister a distinct next hypothesis."
                ),
            )
            save(campaign / "state.json", state)
            with (campaign / "events.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": "measurement_completed",
                            "cycle_id": proposal["cycle_id"],
                            "publication_sha256": digest(publication),
                            "statuses": publication["statuses"],
                            "at_utc": publication["completed_utc"],
                        }
                    )
                    + "\n"
                )
            print(json.dumps(publication), flush=True)
        except BaseException as exc:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            state.update(
                status="validation_needs_attention",
                active_run=None,
                last_failed_run=str(output),
                failure=str(exc),
            )
            save(campaign / "state.json", state)
            raise


if __name__ == "__main__":
    main()
