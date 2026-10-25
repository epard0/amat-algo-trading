#!/usr/bin/env python3
"""Bounded online state snapshot; SQLite databases use the backup API.

JSON/journals are copied at their observed length. This is not a transaction
across every file: restore must still reconcile native order receipts.
"""

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 272 * 1024 * 1024
RESERVE_BYTES = 128 * 1024 * 1024
MAX_FILES = 10000
BACKUP_SECONDS = 20


def _checksum(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _copy_file(path: Path, target: Path, size: int, remaining_budget: int) -> None:
    if path.suffix in {".sqlite", ".sqlite3", ".db"}:
        with (
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as src,
            sqlite3.connect(target) as dst,
        ):
            deadline = time.monotonic() + BACKUP_SECONDS
            page_size = src.execute("PRAGMA page_size").fetchone()[0]

            def progress(_status: int, _remaining: int, total_pages: int) -> None:
                if time.monotonic() > deadline:
                    raise TimeoutError("SQLite backup deadline exceeded")
                if total_pages * page_size > remaining_budget:
                    raise ValueError("SQLite backup exceeds snapshot size bound")

            src.backup(dst, pages=256, progress=progress)
    else:
        with path.open("rb") as src, target.open("wb") as dst:
            remaining = size
            while remaining:
                chunk = src.read(min(65536, remaining))
                if not chunk:
                    raise ValueError("Source changed during bounded snapshot")
                dst.write(chunk)
                remaining -= len(chunk)


def snapshot(
    source: str | Path, destination: str | Path, max_bytes: int = MAX_SOURCE_BYTES
) -> dict[str, str | int]:
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.is_relative_to(source):
        raise ValueError("Backup destination must be outside state")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if max_bytes < 1 or max_bytes > MAX_SOURCE_BYTES:
        raise ValueError("Invalid bounded snapshot size")
    if shutil.disk_usage(destination.parent).free < max_bytes + MAX_ARCHIVE_BYTES + RESERVE_BYTES:
        raise ValueError("Insufficient free staging disk space")
    total = 0
    copied = []
    evidence = {}
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        stage = Path(directory)
        for path in sorted(source.rglob("*")):
            if (
                path.is_symlink()
                or not path.is_file()
                or path.name.endswith((".lock", "-wal", "-shm"))
            ):
                continue
            size = path.stat().st_size
            if len(copied) >= MAX_FILES or total + size > max_bytes:
                raise ValueError("State exceeds bounded snapshot size")
            relative = path.relative_to(source)
            if str(relative) == "snapshot-manifest.json":
                continue  # Prior isolated restore metadata is not authoritative account state.
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(path, target, size, max_bytes - total)
            actual = target.stat().st_size
            total += actual
            if total > max_bytes:
                raise ValueError("State exceeds bounded snapshot size")
            copied.append(str(relative))
            evidence[str(relative)] = {"bytes": actual, "sha256": _checksum(target)}
        (stage / "snapshot-manifest.json").write_text(
            json.dumps(
                {
                    "schema": "state-backup/2",
                    "created_at": dt.datetime.now(dt.UTC).isoformat(),
                    "file_evidence": evidence,
                    "source_limit_bytes": max_bytes,
                    "archive_limit_bytes": MAX_ARCHIVE_BYTES,
                    "files": copied,
                    "source_bytes": total,
                    "scope": "online per-file snapshot; reconcile orders after restore",
                }
            )
        )
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            with tarfile.open(temporary, "w:gz", compresslevel=1) as archive:
                archive.add(stage, arcname="state")
            if temporary.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Compressed archive exceeds size bound")
            temporary.chmod(0o600)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return {"archive": str(destination), "files": len(copied), "source_bytes": total}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    print(json.dumps(snapshot(args.source, args.destination)))
