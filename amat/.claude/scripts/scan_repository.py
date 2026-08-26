#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

from hooklib import PROJECT_ROOT, scan_secrets  # noqa: E402

EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "data",
    "outputs",
    "dist",
    "build",
    "__pycache__",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".json", ".toml", ".yaml", ".yml", ".sh", ".txt", ".ini", ".cfg"
}


def iter_candidates(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        if rel.as_posix() == ".claude/settings.local.json":
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="High-confidence secret scan for project text files.")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()

    findings: list[str] = []
    for path in iter_candidates(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        matches = scan_secrets(text)
        if matches:
            findings.append(f"{path.relative_to(root).as_posix()}: {', '.join(matches)}")

    if findings:
        print("Potential secret material detected:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print("Secret scan: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
