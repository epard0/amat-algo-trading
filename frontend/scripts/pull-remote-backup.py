#!/usr/bin/env python3
"""Fetch the latest bounded state archive over pinned key-only SSH; retain 28 copies."""

import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.remote import Runner, config_path, load_config

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 272 * 1024 * 1024


MAX_SOURCE_FILES = 10_000
MAX_TOTAL_MEMBERS = 2 * MAX_SOURCE_FILES + 2
MAX_TAR_BYTES = MAX_SOURCE_BYTES + 32 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
VERIFY_SECONDS = 30


class _BudgetReader:
    def __init__(self, source):
        self.source = source
        self.remaining = MAX_TAR_BYTES
        self.deadline = time.monotonic() + VERIFY_SECONDS

    def read(self, size=-1):
        if time.monotonic() > self.deadline:
            raise ValueError("Backup verification deadline exceeded")
        if size < 0 or size > self.remaining:
            raise ValueError("Backup decompressed stream exceeds bounds")
        value = self.source.read(size)
        self.remaining -= len(value)
        return value


class _BoundedTarInfo(tarfile.TarInfo):
    # TarInfo parses extended headers before yielding a member. Bound those
    # metadata payloads too, so they cannot bypass the visible member budget.
    def _metadata(self, archive):
        archive._metadata_count = getattr(archive, "_metadata_count", 0) + 1
        if self.size > MAX_HEADER_BYTES or archive._metadata_count > MAX_TOTAL_MEMBERS:
            raise ValueError("Backup extended header exceeds bounds")

    def _proc_pax(self, archive):
        self._metadata(archive)
        if self.type == tarfile.XGLTYPE:
            raise ValueError("Global backup extended headers are not supported")
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        self._metadata(archive)
        return super()._proc_gnulong(archive)


def verify_archive(path):
    """Check every staged byte against its v2 manifest without extraction."""
    observed = {}
    seen = set()
    manifest = None
    total = 0
    with gzip.open(path, "rb") as compressed:
        with tarfile.open(
            fileobj=_BudgetReader(compressed), mode="r|", tarinfo=_BoundedTarInfo
        ) as archive:
            for member in archive:
                # Streaming modes can still cache TarInfo objects on older Python.
                archive.members.clear()
                name = Path(member.name)
                if (
                    len(seen) >= MAX_TOTAL_MEMBERS
                    or len(member.name) > 1024
                    or name.is_absolute()
                    or ".." in name.parts
                    or not name.parts
                    or name.parts[0] != "state"
                    or name.as_posix() in seen
                ):
                    raise ValueError("Unsafe or excessive backup member")
                seen.add(name.as_posix())
                if member.isdir():
                    if member.size != 0:
                        raise ValueError("Invalid backup directory")
                    continue
                if not member.isfile() or member.size < 0 or member.size > MAX_SOURCE_BYTES:
                    raise ValueError("Backup member exceeds bounds")
                relative = name.relative_to("state").as_posix()
                handle = archive.extractfile(member)
                if relative == "snapshot-manifest.json":
                    if manifest is not None or member.size > 4 * 1024 * 1024:
                        raise ValueError("Backup manifest exceeds bounds")
                    manifest = json.loads(handle.read())
                    continue
                if len(observed) >= MAX_SOURCE_FILES:
                    raise ValueError("Backup file count exceeds bounds")
                total += member.size
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("Restored backup exceeds source bound")
                checksum = hashlib.sha256()
                count = 0
                for chunk in iter(lambda: handle.read(65536), b""):
                    count += len(chunk)
                    checksum.update(chunk)
                if count != member.size:
                    raise ValueError("Incomplete backup member")
                observed[relative] = dict(bytes=count, sha256=checksum.hexdigest())
    if (
        not manifest
        or manifest.get("schema") != "state-backup/2"
        or manifest.get("file_evidence") != observed
        or manifest.get("source_bytes") != total
    ):
        raise ValueError("Backup checksum manifest mismatch")
    return dict(files=len(observed), source_bytes=total)


def main():
    storage = Path(os.getenv("TERMINAL_STORAGE", Path(__file__).resolve().parents[1] / ".runtime"))
    config = load_config(config_path(storage))
    client = Runner(config).connect()
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(20)
            directory = sftp.normalize(".") + "/.local/share/amat/backups"
            target = storage / "backups"
            target.mkdir(exist_ok=True, parents=True, mode=0o700)
            available = sftp.listdir(directory)
            for kind in ("state", "perps"):
                names = sorted(
                    n
                    for n in available
                    if re.fullmatch(r"backup-" + kind + r"-\d{8}T\d{6}Z\.tar\.gz", n)
                )
                if not names:
                    raise ValueError(f"No remote {kind} archive available")
                name = names[-1]
                size = sftp.stat(directory + "/" + name).st_size
                if not 0 < size <= MAX_ARCHIVE_BYTES:
                    raise ValueError("Remote archive exceeds download bounds")
                if shutil.disk_usage(target).free < size + 128 * 1024 * 1024:
                    raise ValueError("Insufficient free download disk space")
                temporary = target / (name + ".partial")
                final = target / name
                if not final.exists():
                    try:
                        with (
                            sftp.open(directory + "/" + name, "rb") as src,
                            temporary.open("wb") as dst,
                        ):
                            temporary.chmod(0o600)
                            remaining = size
                            while remaining:
                                chunk = src.read(min(65536, remaining))
                                if not chunk:
                                    raise ValueError("Incomplete remote archive")
                                dst.write(chunk)
                                remaining -= len(chunk)
                        if sftp.stat(directory + "/" + name).st_size != size:
                            raise ValueError("Remote archive changed during download")
                        verify_archive(temporary)
                        temporary.replace(final)
                    finally:
                        temporary.unlink(missing_ok=True)
                else:
                    verify_archive(final)
                for old in sorted(target.glob(f"backup-{kind}-*.tar.gz"), reverse=True)[28:]:
                    old.unlink()
                print(f"Latest {kind} archive: {name} ({size} bytes)")

    finally:
        client.close()


if __name__ == "__main__":
    main()
