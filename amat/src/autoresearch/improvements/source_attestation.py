"""Fail before a refit if imported research libraries escape the registered source tree."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def attest_loaded_sources(
    source: Path, expected: Mapping[str, str], modules: Mapping[str, Any]
) -> dict[str, str]:
    """Bind actual loaded module files to both an explicit root and registered bytes."""
    root = source.resolve()
    manifest = {key.removeprefix("src/"): value for key, value in expected.items()}
    verified = {}
    for name, module in modules.items():
        if name.split(".")[0] not in {"amat", "autoresearch", "llmrouter"}:
            continue
        filename = getattr(module, "__file__", None)
        if not filename:
            raise ValueError(f"Loaded research module has no source file: {name}")
        path = Path(filename).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Loaded research module escaped registered root: {name}")
        key = str(path.relative_to(root))
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if manifest.get(key) != actual:
            raise ValueError(f"Loaded research module source is unregistered or changed: {name}")
        verified[key] = actual
    if not verified:
        raise ValueError("No registered research libraries were loaded")
    return verified
