#!/usr/bin/env python3
"""Inspect Git publication candidates without displaying matched secret values.

Default: tracked and nonignored untracked working files. --staged: exact index
blobs. --history: additionally inspect all reachable Git blobs for token patterns.
Only Python's standard library and Git are required. This is a heuristic check.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

MAX_BYTES = 20 * 1024 * 1024
PATTERNS = {
    "private key": re.compile(
        rb"-----BEGIN (?:RSA |OPENSSH |EC |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "provider token": re.compile(
        rb"(?<![A-Za-z0-9_-])(?:sk-(?:proj-|or-v1-)?[A-Za-z0-9_-]{24,}|"
        rb"AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|"
        rb"[0-9]{8,12}:[A-Za-z0-9_-]{30,})"
    ),
}
GENERATED_ROOTS = (
    "amat/data/",
    "amat/outputs/",
    "executor/state/",
    "executor/backups/",
    "executor/reports/",
    "executor/plans/",
    "kalshi-perps/runtime/",
    "kalshi-perps/outputs/",
    "frontend/.runtime/",
    "operations/",
    ".playwright-mcp/",
)


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def path_findings(name: str) -> list[str]:
    path = PurePosixPath(name)
    parts = {part.lower() for part in path.parts}
    base = path.name.lower()
    findings = []
    template = base.endswith(".example")
    if (
        parts & {".keys", ".ssh", "secrets", "credentials"}
        or (
            not template
            and (base == ".env" or base.startswith(".env.") or base.endswith(".env"))
        )
        or base
        in {
            "id_ed25519",
            "id_rsa",
            "id_ecdsa",
            "id_dsa",
            ".mcp.json",
            "remote.toml",
            "ssh.toml",
            "known_hosts",
            "authorized_keys",
        }
        or base.startswith("known_hosts.")
        or base.endswith((".local.toml", ".pub", ".pem", ".key", ".p12", ".pfx"))
        or name == "frontend/config/remote.toml"
    ):
        findings.append("private configuration/key path")
    if (
        name.startswith(GENERATED_ROOTS)
        or parts & {".venv", "node_modules", "__pycache__"}
        or re.search(r"\.(?:sqlite3?|db)(?:-wal|-shm)?$", base)
        or base.endswith((".tar", ".tar.gz", ".tar.xz", ".tgz", ".zip"))
    ):
        findings.append("runtime/generated/archive candidate")
    return findings


def content_findings(data: bytes) -> list[str]:
    findings = [label for label, pattern in PATTERNS.items() if pattern.search(data)]
    for match in re.finditer(rb"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", data):
        try:
            address = ipaddress.ip_address(match[0].decode())
        except ValueError:
            continue
        if (
            not address.is_loopback
            and not address.is_unspecified
            and not address.is_multicast
        ):
            findings.append(
                "embedded non-loopback IPv4 address; use private configuration"
            )
            break
    return findings


def check(
    root: Path, *, staged: bool, history: bool
) -> tuple[list[tuple[str, str]], int]:
    findings: list[tuple[str, str]] = []
    if staged:
        entries = []
        for raw in git(root, "ls-files", "--stage", "-z").split(b"\0"):
            if raw:
                meta, name = raw.split(b"\t", 1)
                mode, oid, stage = meta.decode().split()
                if stage != "0":
                    findings.append((os.fsdecode(name), "unmerged index entry"))
                    continue
                entries.append((os.fsdecode(name), mode, oid))
    else:
        names = set(
            filter(
                None,
                git(
                    root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
                ).split(b"\0"),
            )
        )
        entries = [(os.fsdecode(name), "", "") for name in sorted(names)]
    checked = 0
    for name, mode, oid in entries:
        findings.extend((name, label) for label in path_findings(name))
        path = root / name
        if mode == "160000":
            findings.append((name, "submodule requires separate publication review"))
            continue
        if staged:
            size = int(git(root, "cat-file", "-s", oid))
        elif not path.exists() and not path.is_symlink():
            continue  # A tracked deletion has no working content to publish.
        else:
            size = path.lstat().st_size
        if size > MAX_BYTES:
            findings.append((name, "file exceeds 20 MiB publication review limit"))
            continue
        if staged:
            data = git(root, "cat-file", "blob", oid)
        elif path.is_symlink():
            data = os.fsencode(os.readlink(path))
        elif path.is_file():
            data = path.read_bytes()
        else:
            findings.append((name, "unsupported file type"))
            continue
        checked += 1
        if mode == "120000" or (not staged and path.is_symlink()):
            target = Path(os.fsdecode(data))
            if target.is_absolute() or not (
                path.parent / target
            ).resolve().is_relative_to(root):
                findings.append((name, "symlink escapes repository"))
        findings.extend((name, label) for label in content_findings(data))
    if history:
        # Batch metadata first so oversized historical blobs need not be loaded.
        objects = git(root, "rev-list", "--objects", "--all").splitlines()
        oids = b"\n".join(row.split(b" ", 1)[0] for row in objects)
        metadata = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "cat-file",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            ],
            input=oids,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        for row in metadata:
            oid, kind, size = row.decode().split()
            if kind != "blob":
                continue
            label = f"history:{oid}"
            if int(size) > MAX_BYTES:
                findings.append(
                    (label, "historical blob exceeds scan limit; inspect privately")
                )
                continue
            data = git(root, "cat-file", "blob", oid)
            checked += 1
            findings.extend((label, issue) for issue in content_findings(data))
    return sorted(set(findings)), checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged", action="store_true", help="Inspect exact Git index blobs"
    )
    parser.add_argument(
        "--history", action="store_true", help="Also scan all reachable Git blobs"
    )
    parser.add_argument(
        "--export",
        type=Path,
        metavar="DIRECTORY",
        help="Copy passing working sources to a new external directory, without .git",
    )
    args = parser.parse_args()
    if args.export and args.staged:
        parser.error(
            "--export uses working sources and cannot be combined with --staged"
        )
    root = Path(
        os.fsdecode(
            subprocess.check_output(["git", "rev-parse", "--show-toplevel"])
        ).strip()
    ).resolve()
    findings, count = check(root, staged=args.staged, history=args.history)
    for name, issue in findings:
        print(f"REVIEW {name!r}: {issue}")
    print(
        f"Checked {count} file/blob contents; {len(findings)} finding(s). Matched values suppressed."
    )
    print(
        "Heuristic scan only: review personal/account data, binaries and custom credentials privately."
    )
    if args.export and not findings:
        destination = args.export.expanduser().resolve()
        if destination.exists() or destination.is_relative_to(root):
            parser.error("export must be a new directory outside the source repository")
        names = set(
            filter(
                None,
                git(
                    root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
                ).split(b"\0"),
            )
        )
        destination.mkdir(parents=True)
        copied = 0
        for raw in sorted(names):
            name = os.fsdecode(raw)
            source, target = root / name, destination / name
            if not source.exists() and not source.is_symlink():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
            copied += 1
        print(
            f"Exported {copied} source files to {destination}; no Git history copied."
        )
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
