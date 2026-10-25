"""Independent local workers, with a per-job kernel lease across API restarts."""

from __future__ import annotations

import fcntl
import subprocess
import sys
from pathlib import Path


def lease_path(storage: Path, job_id: str) -> Path:
    directory = storage / "leases"
    directory.mkdir(exist_ok=True)
    return directory / (job_id + ".lock")


def worker_active(storage: Path, job_id: str) -> bool:
    path = lease_path(storage, job_id)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def dispatch(service, job: dict) -> None:
    """Transfer a lease to a worker; no shell or renderer-supplied argv."""
    handle = lease_path(service.storage, job["id"]).open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command = [
            sys.executable,
            "-m",
            "server.worker",
            "--root",
            str(service.root),
            "--storage",
            str(service.storage),
            "--state",
            str(service.state),
            "--job",
            job["id"],
            "--lease-fd",
            str(handle.fileno()),
        ]
        subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(handle.fileno(),),
        )
    finally:
        # Child keeps the same open-file-description lease until its exit.
        handle.close()
