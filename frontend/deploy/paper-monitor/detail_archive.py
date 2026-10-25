"""Lossless, bounded archives of completed collector detail; summaries stay live.

Only observation-*.json files directly under a dated session or its evidence
folder are eligible. Account state, immutable snapshots and ledgers are outside
this namespace. Archives and per-file manifests are retained without deletion.
"""

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

MAX_DETAIL_BYTES = 2_000_000
RESERVE_BYTES = 128 * 1024 * 1024
NAME = re.compile(r"observation-[A-Za-z0-9_-]+\.json\Z")


def _relative(name):
    path = Path(name)
    if path.is_absolute() or len(path.parts) not in (1, 2):
        raise ValueError("Invalid archived observation path")
    if len(path.parts) == 2 and path.parts[0] != "evidence":
        raise ValueError("Invalid archived observation namespace")
    if not NAME.fullmatch(path.name):
        raise ValueError("Invalid archived observation name")
    return path


def _safe(folder, relative):
    path = Path(folder)
    if path.is_symlink():
        raise ValueError("Symlink session is not allowed")
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlink observation is not allowed")
    return path


def _hash(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            result.update(chunk)
    return result.hexdigest()


def read_detail(folder, name):
    relative = _relative(name)
    source = _safe(folder, relative)
    if source.exists():
        if not source.is_file() or source.stat().st_size > MAX_DETAIL_BYTES:
            raise ValueError("Observation exceeds detail bound")
        return source.read_bytes()
    packed = _safe(folder, Path("archives") / (str(relative) + ".gz"))
    metadata = _safe(folder, Path("archives") / (str(relative) + ".archive.json"))
    if not metadata.is_file() or not packed.is_file() or metadata.stat().st_size > 4096:
        raise ValueError("Invalid archive manifest")
    manifest = json.loads(metadata.read_text())
    if (
        manifest.get("schema") != "collector-detail-archive/1"
        or manifest.get("file") != relative.as_posix()
        or type(manifest.get("bytes")) is not int
        or not 0 <= manifest["bytes"] <= MAX_DETAIL_BYTES
        or packed.stat().st_size > MAX_DETAIL_BYTES + 65536
        or _hash(packed) != manifest.get("archive_sha256")
    ):
        raise ValueError("Invalid observation archive")
    with gzip.open(packed, "rb") as handle:
        value = handle.read(manifest["bytes"] + 1)
    if len(value) != manifest["bytes"] or hashlib.sha256(value).hexdigest() != manifest.get(
        "sha256"
    ):
        raise ValueError("Observation archive checksum mismatch")
    return value


def detail_names(folder, namespace=""):
    relative = Path(namespace) if namespace else Path()
    if namespace not in ("", "evidence"):
        raise ValueError("Invalid observation namespace")
    direct = _safe(folder, relative)
    archived = _safe(folder, Path("archives") / relative)
    names = set()
    if direct.exists():
        names.update(p.name for p in direct.glob("observation-*.json") if NAME.fullmatch(p.name))
    if archived.exists():
        names.update(
            p.name.removesuffix(".archive.json")
            for p in archived.glob("observation-*.json.archive.json")
        )
    return [str(relative / name) for name in sorted(names)]


def resource_headroom(store):
    try:
        memory = {
            line.split(":", 1)[0]: int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.split(":", 1)[0] in ("MemAvailable", "MemTotal", "SwapTotal", "SwapFree")
        }
        available = memory["MemAvailable"]
        swap = memory.get("SwapTotal", 0)
        return (
            available >= max(128 * 1024 * 1024, memory["MemTotal"] * 0.15)
            and (not swap or (swap - memory.get("SwapFree", 0)) / swap < 0.75)
            and os.getloadavg()[0] <= 1.5 * (os.cpu_count() or 1)
            and shutil.disk_usage(store).free >= RESERVE_BYTES + 2 * MAX_DETAIL_BYTES
        )
    except (OSError, ValueError, KeyError, StopIteration):
        return False


def _archive_one(folder, relative):
    source = _safe(folder, relative)
    before = source.stat()
    if not source.is_file() or before.st_size > MAX_DETAIL_BYTES:
        raise ValueError("Observation exceeds archive bound")
    destination = _safe(folder, Path("archives") / (str(relative) + ".gz"))
    metadata = _safe(folder, Path("archives") / (str(relative) + ".archive.json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".archive-", dir=destination.parent)
    temporary = Path(name)
    try:
        checksum = hashlib.sha256()
        copied = 0
        with os.fdopen(descriptor, "wb") as output:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=output, compresslevel=1, mtime=0
            ) as archive:
                with source.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(65536), b""):
                        copied += len(chunk)
                        if copied > MAX_DETAIL_BYTES:
                            raise ValueError("Observation grew beyond archive bound")
                        checksum.update(chunk)
                        archive.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if (source.stat().st_ino, source.stat().st_size, source.stat().st_mtime_ns) != (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            raise ValueError("Observation changed during archival")
        manifest = dict(
            schema="collector-detail-archive/1",
            file=relative.as_posix(),
            bytes=copied,
            sha256=checksum.hexdigest(),
            archive_sha256=_hash(temporary),
        )
        # Existing valid archives are never replaced with different evidence.
        if metadata.exists():
            prior = json.loads(metadata.read_text())
            if prior != manifest:
                raise ValueError("Existing archive differs from observation")
        temporary.replace(destination)
        descriptor, name = tempfile.mkstemp(prefix=".manifest-", dir=metadata.parent)
        manifest_tmp = Path(name)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(manifest, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            manifest_tmp.replace(metadata)
        finally:
            manifest_tmp.unlink(missing_ok=True)
        # Validate the actual compressed bytes before removing generated detail.
        with gzip.open(destination, "rb") as handle:
            restored = handle.read(copied + 1)
        if len(restored) != copied or hashlib.sha256(restored).hexdigest() != manifest["sha256"]:
            raise ValueError("Archive verification failed")
        # Persist newly-created ancestor directory entries as well as the
        # archive/manifest before the source can disappear after a power loss.
        for directory_path in (destination.parent, Path(folder) / "archives", Path(folder)):
            directory = os.open(directory_path, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        if (source.stat().st_ino, source.stat().st_size, source.stat().st_mtime_ns) != (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            raise ValueError("Observation changed before removal")
        source.unlink()
        directory = os.open(source.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return copied
    finally:
        temporary.unlink(missing_ok=True)


def archive_completed(
    store, now, *, active_session=None, max_files=32, max_bytes=8_000_000, max_seconds=2
):
    """At most one short low-compression batch per existing collector cycle."""
    result = dict(files=0, source_bytes=0, skipped="resource_pressure")
    if not resource_headroom(store):
        return result
    result["skipped"] = None
    cutoff = now - dt.timedelta(days=7)
    began = time.monotonic()
    for folder in sorted(Path(store).iterdir()):
        if not folder.is_dir() or folder.is_symlink() or folder.name == active_session:
            continue
        try:
            day = dt.date.fromisoformat(folder.name)
        except ValueError:
            continue
        final = _safe(folder, "final.json")
        if day >= cutoff.date() or not final.is_file() or final.stat().st_size > MAX_DETAIL_BYTES:
            continue
        publication = json.loads(final.read_text())
        if (
            publication.get("status") != "complete"
            or publication.get("target_session", folder.name) != folder.name
        ):
            continue
        for namespace in ("", "evidence"):
            # Readers merge archived/live names; maintenance visits only raw
            # sources so an expanding archive cannot consume every cycle's budget.
            raw = _safe(folder, Path(namespace) if namespace else Path())
            names = sorted(
                path.name for path in raw.glob("observation-*.json") if NAME.fullmatch(path.name)
            )
            for filename in names:
                name = str(Path(namespace) / filename) if namespace else filename
                if (
                    result["files"] >= max_files
                    or result["source_bytes"] >= max_bytes
                    or time.monotonic() - began >= max_seconds
                ):
                    return result
                relative = _relative(name)
                source = _safe(folder, relative)
                if not source.exists() or source.stat().st_mtime >= cutoff.timestamp():
                    continue
                size = source.stat().st_size
                if size > MAX_DETAIL_BYTES or result["source_bytes"] + size > max_bytes:
                    continue
                result["source_bytes"] += _archive_one(folder, relative)
                result["files"] += 1
    return result
