#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def command(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def nested(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}

    cwd_value = (
        nested(payload, "workspace", "current_dir")
        or payload.get("cwd")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )
    cwd = Path(str(cwd_value)).expanduser()

    model = (
        nested(payload, "model", "display_name")
        or nested(payload, "model", "id")
        or payload.get("model")
        or "Claude"
    )
    if isinstance(model, dict):
        model = model.get("display_name") or model.get("id") or "Claude"

    branch = command(["git", "branch", "--show-current"], cwd) or "no-git"
    status = command(["git", "status", "--porcelain"], cwd)
    changed = len(status.splitlines()) if status else 0

    context = payload.get("context_window")
    context_text = ""
    if isinstance(context, dict):
        used_pct = context.get("used_percentage")
        if isinstance(used_pct, (int, float)):
            context_text = f" | ctx {used_pct:.0f}%"

    print(f"AMAT | {model} | {branch} | Δ{changed}{context_text} | BACKTEST-ONLY")


if __name__ == "__main__":
    main()
