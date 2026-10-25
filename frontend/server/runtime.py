"""Measured local gateway resources; never implies VPS/container telemetry."""

from __future__ import annotations

import os
import resource
import shutil
import time
from pathlib import Path

STARTED_AT = time.monotonic()


def observe(storage: Path, jobs: list[dict]) -> dict:
    disk = shutil.disk_usage(storage)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    try:
        memory = next(
            line
            for line in Path("/proc/self/status").read_text().splitlines()
            if line.startswith("VmRSS:")
        )
        resident = int(memory.split()[1]) * 1024
    except (OSError, StopIteration, ValueError):
        resident = None
    try:
        loads = list(os.getloadavg())
    except OSError:
        loads = None
    database = storage / "terminal.sqlite3"
    return {
        "scope": "Local gateway process/host and terminal storage; not remote VPS telemetry",
        "uptime_seconds": round(time.monotonic() - STARTED_AT, 1),
        "process_cpu_seconds": round(usage.ru_utime + usage.ru_stime, 3),
        "process_resident_bytes": resident,
        "host_load_1_5_15_minutes": loads,
        "storage_total_bytes": disk.total,
        "storage_free_bytes": disk.free,
        "storage_pressure": "low_space" if disk.free < 64 * 1024 * 1024 else "normal",
        "database_bytes": database.stat().st_size if database.exists() else None,
        "active_jobs": sum(job["status"] in {"queued", "started"} for job in jobs),
        "outcome_unknown_jobs": sum(job["status"] == "outcome_unknown" for job in jobs),
        "limits": {"heavy_jobs": 1, "total_jobs": 4},
    }
