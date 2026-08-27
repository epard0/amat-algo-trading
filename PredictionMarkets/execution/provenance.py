"""Stable, secret-free fingerprints attached to every collection run."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import fields, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SENSITIVE_PARTS = ("key", "secret", "token", "password", "license")


def _digest(parts: list[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _source_digest() -> str:
    paths: list[Path] = []
    for name in ("bot.py", "config.py", "core", "execution", "research"):
        root = PROJECT_ROOT / name
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths.extend(root.rglob("*.py"))
    parts: list[bytes] = []
    for path in sorted(paths):
        parts.extend((str(path.relative_to(PROJECT_ROOT)).encode(), path.read_bytes()))
    return _digest(parts)


def source_revision() -> str:
    """Return a Git revision, or a content hash for an uncommitted project."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        return f"git:{revision}{'-dirty' if dirty else ''}"
    except (OSError, subprocess.SubprocessError):
        return f"unversioned:{_source_digest()}"


def _safe(value: Any) -> Any:
    if isinstance(value, (Decimal, Path)):
        return str(value)
    if isinstance(value, tuple):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in sorted(value.items())}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def canonical_config(settings: Any) -> dict[str, Any]:
    """Return the complete, stable, secret-free runtime configuration."""
    if not is_dataclass(settings):
        return {"settings_type": repr(type(settings))}
    return {
        field.name: _safe(getattr(settings, field.name))
        for field in fields(settings)
        if not any(part in field.name.lower() for part in SENSITIVE_PARTS)
    }


def canonical_config_json(settings: Any) -> str:
    return json.dumps(canonical_config(settings), sort_keys=True, separators=(",", ":"))


def config_fingerprint(settings: Any) -> str:
    public_json = canonical_config_json(settings)
    return _digest([public_json.encode()])


def model_fingerprint(settings: Any) -> str:
    predictor = (
        PROJECT_ROOT / "core/strategy_brain/strategies/crypto_15min_predictor.py"
    )
    parts: list[bytes] = [b"builtin_predictor", predictor.read_bytes()]
    artifact_found = False
    for attribute in ("regime_model_path", "commodity_model_path"):
        configured = getattr(settings, attribute, None)
        if not configured:
            continue
        root = Path(configured)
        if root.is_file():
            parts.extend((attribute.encode(), root.read_bytes()))
            artifact_found = True
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    parts.extend(
                        (
                            f"{attribute}/{path.relative_to(root)}".encode(),
                            path.read_bytes(),
                        )
                    )
                    artifact_found = True
    return f"bundle:{_digest(parts)}" if artifact_found else f"builtin:{_digest(parts)}"


def run_provenance(settings: Any) -> dict[str, str]:
    return {
        "source_revision": source_revision(),
        "config_hash": config_fingerprint(settings),
        "config_json": canonical_config_json(settings),
        "model_hash": model_fingerprint(settings),
        "fee_model": "kalshi_quadratic_0.07_round_up_v1",
        "fill_model": "rest_depth_latency_event_v4",
    }
