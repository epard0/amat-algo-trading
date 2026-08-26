from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from hooklib import (
    PROJECT_ROOT,
    emit_block,
    markdown_validation,
    read_event,
    relative_project_path,
    scan_secrets,
)


def target_path(tool_input: dict[str, Any]) -> Path | None:
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            rel = relative_project_path(value)
            return PROJECT_ROOT / rel if rel is not None else None
    return None


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists() or not path.is_file():
        return errors

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"cannot read edited file as UTF-8: {exc}"]

    secret_findings = scan_secrets(text)
    if secret_findings:
        errors.append("potential secret material: " + ", ".join(secret_findings))

    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            json.loads(text)
        elif suffix == ".toml":
            tomllib.loads(text)
        elif suffix == ".py":
            # Built-in compile validates syntax without creating __pycache__ artifacts.
            compile(text, str(path), "exec")
        elif suffix == ".sh":
            result = subprocess.run(
                ["bash", "-n", str(path)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                errors.append(result.stderr.strip() or "shell syntax validation failed")
        elif suffix == ".md":
            errors.extend(markdown_validation(path, text))
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, SyntaxError, OSError) as exc:
        errors.append(str(exc))
    return errors


def main() -> None:
    event = read_event()
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    path = target_path(tool_input)
    if path is None:
        emit_block("Post-edit validation could not resolve the edited path inside the project.")
        return
    errors = validate(path)
    if errors:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        emit_block(f"Fix post-edit validation errors in {rel}: " + " | ".join(errors[:8]))


if __name__ == "__main__":
    main()
