"""Enforce the layer-order rule declared in :mod:`amat`.

``src/amat/domain/__init__.py`` has always claimed this module enforces
framework neutrality. Until now it did not exist, so the claim was a phantom
control: adding ``import polars`` to a domain record passed every check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "amat"

#: Import order. A module may import from its own layer or any layer above it.
LAYERS: dict[str, int] = {
    "errors": 0,
    "paths": 0,
    "provenance": 0,
    "domain": 1,
    "config": 2,
    "boundaries": 3,
    # Validates the inert newsdesk artifact and translates it into a strategy
    # universe; like boundaries, it may read config/domain but no data layer.
    "news_assets": 3,
    "data": 4,
    "features": 4,
    "context": 4,
    "regime": 4,
    "risk": 4,
    "portfolio": 4,
    "execution": 4,
    "backtest": 4,
    "validation": 4,
    "metrics": 5,
    "reporting": 5,
    "simulation": 5,
    "planning": 5,
    # The scanner reports what the allocator sees, so it sits above the feature
    # and portfolio layers it reads and below the CLI that renders it. Layer 5
    # rather than 4 because it composes those layers and nothing in them may
    # depend on it -- a feature that imported the scanner could not be used to
    # build one.
    "scanner": 5,
    "cli": 6,
}

#: Third-party packages the framework-neutral domain layer must never import.
FORBIDDEN_IN_DOMAIN = {
    "polars",
    "numpy",
    "pandas",
    "sklearn",
    "scipy",
    "vectorbt",
    "nautilus_trader",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "socket",
    "pydantic",
}


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _module_layer(path: Path) -> tuple[str, int] | None:
    relative = path.relative_to(SRC)
    top = relative.parts[0]
    name = top[:-3] if top.endswith(".py") else top
    if name in LAYERS:
        return name, LAYERS[name]
    return None


def _imported_modules(path: Path) -> list[tuple[str, bool]]:
    """Return ``(module, is_type_checking_only)`` for every import in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_type_checking:
                for child in ast.walk(node):
                    guarded.add(id(child))

    found: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, id(node) in guarded))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, id(node) in guarded))
    return found


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.name))
def test_imports_do_not_point_downward(path: Path) -> None:
    """A module may not import from a layer below its own."""
    origin = _module_layer(path)
    if origin is None:
        pytest.skip(f"{path.name} is not in a declared layer")
    origin_name, origin_layer = origin

    for module, type_checking_only in _imported_modules(path):
        if not module.startswith("amat."):
            continue
        target_top = module.split(".")[1]
        if target_top not in LAYERS:
            continue
        target_layer = LAYERS[target_top]
        if target_layer > origin_layer and not type_checking_only:
            message = (
                f"{path.relative_to(SRC)} (layer {origin_layer}, {origin_name}) imports "
                f"{module} (layer {target_layer}); imports may only point upward"
            )
            raise AssertionError(message)


def test_domain_is_framework_neutral() -> None:
    """``amat.domain`` imports only the standard library and ``amat.errors``."""
    offenders: list[str] = []
    for path in sorted((SRC / "domain").rglob("*.py")):
        for module, type_checking_only in _imported_modules(path):
            if type_checking_only:
                continue
            root = module.split(".")[0]
            if root in FORBIDDEN_IN_DOMAIN:
                offenders.append(f"{path.name} imports {module}")
    assert not offenders, "domain layer must stay framework-neutral: " + "; ".join(offenders)


def test_no_module_imports_a_third_party_http_client() -> None:
    """No third-party HTTP stack may enter the dependency tree.

    Narrower than it looks, and deliberately so: the repository's one network
    client is stdlib-only, which this test cannot see. The control that bounds
    *which* module may reach the network, which host, and with which method
    lives in ``test_read_only_data_access.py``. This test only keeps the
    dependency surface small.
    """
    forbidden = {"requests", "httpx", "aiohttp", "websockets", "urllib3"}
    offenders: list[str] = []
    for path in _python_files():
        for module, type_checking_only in _imported_modules(path):
            if type_checking_only:
                continue
            if module.split(".")[0] in forbidden:
                offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert not offenders, "backtest-only repository: " + "; ".join(offenders)
