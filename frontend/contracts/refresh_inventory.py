"""Refresh CLI schema inventory without running any command or contacting providers."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    source = ROOT / "docs/hedge-fund-workbench/inventory.py"
    spec = importlib.util.spec_from_file_location("planning_inventory", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    os.chdir(ROOT / "amat")
    commands = []
    for name, (module_name, builder) in module.BUILDERS.items():
        parser = getattr(__import__(module_name, fromlist=[builder]), builder)()
        commands.extend(module.collect(parser, name))
    hashes = {}
    for directory in ("amat/src", "executor/src"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    document = {
        "schema_version": "terminal-cli-inventory/1",
        "basis": "Current working tree; parser construction only; no command execution.",
        "commands": commands,
        "source_sha256": hashes,
        "coverage_note": "Discovery does not imply an executable UI adapter. API capabilities report implemented actions separately.",
    }
    target = Path(__file__).with_name("cli-inventory.json")
    target.write_text(json.dumps(document, indent=2) + "\n")
    print(f"Recorded {len(commands)} CLI endpoints and {len(hashes)} source hashes in {target}")


if __name__ == "__main__":
    main()
