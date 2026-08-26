from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from hooklib import (
    PROJECT_ROOT,
    changed_files,
    compact_output,
    emit_block,
    iter_text_files,
    read_event,
    run,
    scan_secrets,
)


def validate_changed(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix.lower() == ".py":
                # Validate syntax without writing bytecode into the repository.
                compile(text, str(path), "exec")
            elif path.suffix.lower() == ".json":
                json.loads(text)
            elif path.suffix.lower() == ".toml":
                tomllib.loads(text)
        except Exception as exc:  # Hook boundary: report any parser/compiler failure.
            failures.append(f"{rel}: {exc}")

    for path in iter_text_files(paths):
        try:
            findings = scan_secrets(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if findings:
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            failures.append(f"{rel}: potential secret material ({', '.join(findings)})")
    return failures


def main() -> None:
    event = read_event()
    if event.get("stop_hook_active"):
        return

    failures: list[str] = []

    validator = run(["python3", str(PROJECT_ROOT / ".claude/scripts/validate_bundle.py")], timeout=30)
    if validator.returncode != 0:
        failures.append("Claude bundle validation failed:\n" + compact_output(validator))

    paths = changed_files()
    failures.extend(validate_changed(paths))

    if run(["git", "rev-parse", "--git-dir"], timeout=5).returncode == 0:
        whitespace = run(["git", "diff", "--check"], timeout=15)
        if whitespace.returncode != 0:
            failures.append("git diff --check failed:\n" + compact_output(whitespace))

    if os.environ.get("CLAUDE_ENFORCE_FAST_GATES") == "1" and paths:
        gate = run(
            ["bash", str(PROJECT_ROOT / ".claude/scripts/quality_gate.sh"), "fast"],
            timeout=180,
        )
        if gate.returncode != 0:
            failures.append("Fast quality gate failed:\n" + compact_output(gate))

    if failures:
        emit_block(
            "Verification must be corrected before stopping:\n\n"
            + "\n\n".join(failures[:10])
        )


if __name__ == "__main__":
    main()
