"""Remove redundant snapshot storage while preserving referenced identities.

Partition bytes remain immutable; identical files share a hard link. Completed
window checkpoints are reproducible scratch and can be removed once their final
snapshot hashes validate. An unfinished pull is never removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from amat.data.snapshot import load_manifest


def compact(root: Path, *, apply: bool = False) -> dict:
    seen = {}
    changes = []
    for directory in sorted((root / "data/snapshots").iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        manifest = load_manifest(directory)
        for name, expected in manifest.partition_sha256.items():
            path = directory / name
            if path.is_symlink():
                raise ValueError("snapshot partitions must not be symlinks")
            key = (expected, path.stat().st_size)
            existing = seen.setdefault(key, path)
            if existing == path or existing.stat().st_ino == path.stat().st_ino:
                continue
            changes.append(
                {
                    "operation": "deduplicate_partition",
                    "path": str(path),
                    "retained": str(existing),
                    "sha256": expected,
                    "bytes": path.stat().st_size,
                }
            )
            if apply:
                temporary = path.with_name("." + path.name + ".dedup")
                os.link(existing, temporary)
                temporary.replace(path)
        checkpoint = root / "data/interim" / (manifest.snapshot_id + ".windows.json")
        if checkpoint.is_file() and not checkpoint.is_symlink():
            changes.append(
                {
                    "operation": "remove_completed_checkpoint",
                    "path": str(checkpoint),
                    "snapshot": manifest.snapshot_id,
                    "sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                    "bytes": checkpoint.stat().st_size,
                }
            )
            if apply:
                checkpoint.unlink()
        load_manifest(directory)
    return {
        "schema": "snapshot-compaction/1",
        "applied": apply,
        "bytes_reclaimed": sum(c["bytes"] for c in changes),
        "policy": (
            "Keep all snapshot identities and manifests; remove only duplicate partition "
            "storage and completed ingestion scratch. Retain unfinished pulls."
        ),
        "changes": changes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    if args.audit.exists():
        raise ValueError("audit already exists")
    result = compact(args.root.resolve(), apply=args.apply)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "changes": len(result["changes"]),
                "bytes_reclaimed": result["bytes_reclaimed"],
                "applied": args.apply,
            }
        )
    )


if __name__ == "__main__":
    main()
