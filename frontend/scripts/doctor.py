#!/usr/bin/env python3
"""Check terminal runtime dependencies without reading credentials or engine state."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    failures = []
    print(f"AMAT Terminal runtime: {ROOT}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 11):
        failures.append("Python 3.11 or newer is required")
    for module in ("fastapi", "uvicorn"):
        available = importlib.util.find_spec(module) is not None
        print(f"{module}: {'available' if available else 'missing'}")
        if not available:
            failures.append(f"Install frontend Python dependencies for {module}")
    for command in ("node", "npm"):
        executable = shutil.which(command)
        if executable:
            result = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, timeout=10
            )
            print(f"{command}: {result.stdout.strip()}")
            if result.returncode:
                failures.append(f"{command} failed its version check")
        else:
            failures.append(f"{command} is missing")
    installed = (ROOT / "web/node_modules").is_dir()
    print(
        f"Web dependencies: {'installed' if installed else 'missing; run npm ci in frontend/web'}"
    )
    if not installed:
        failures.append("Web dependencies are not installed")
    for failure in failures:
        print(f"CHECK: {failure}")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
