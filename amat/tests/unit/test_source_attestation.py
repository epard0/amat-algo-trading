"""Identical bytes at an accidental import root must not masquerade as frozen source."""

import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.improvements.source_attestation import attest_loaded_sources


@pytest.mark.parametrize("mutation", ["none", "wrong_root", "changed", "unregistered", "no_file"])
def test_refit_source_path_and_bytes_are_both_required(tmp_path: Path, mutation: str) -> None:
    root = tmp_path / "registered"
    path = root / "amat/data.py"
    path.parent.mkdir(parents=True)
    path.write_text("value = 1\n")
    expected = {"src/amat/data.py": hashlib.sha256(path.read_bytes()).hexdigest()}
    if mutation == "wrong_root":
        other = tmp_path / "editable/data.py"
        other.parent.mkdir()
        other.write_bytes(path.read_bytes())
        path = other
    elif mutation == "changed":
        path.write_text("value = 2\n")
    elif mutation == "unregistered":
        expected = {}
    module = SimpleNamespace(__file__=str(path)) if mutation != "no_file" else SimpleNamespace()
    modules = {"amat.data": module, "json": SimpleNamespace()}
    if mutation == "none":
        result = attest_loaded_sources(root, expected, modules)
        assert set(result) == {"amat/data.py"}
    else:
        with pytest.raises(ValueError):
            attest_loaded_sources(root, expected, modules)


def test_empty_source_attestation_cannot_pass(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No registered"):
        attest_loaded_sources(tmp_path, {}, {"json": SimpleNamespace()})


def test_risk_package_and_implementation_have_registered_source_files() -> None:
    root = Path(__file__).resolve().parents[2] / "src"
    names = ("amat.risk", "amat.risk.active")
    modules = {name: importlib.import_module(name) for name in names}
    expected = {
        "src/amat/risk/__init__.py": hashlib.sha256(
            (root / "amat/risk/__init__.py").read_bytes()
        ).hexdigest(),
        "src/amat/risk/active.py": hashlib.sha256(
            (root / "amat/risk/active.py").read_bytes()
        ).hexdigest(),
    }
    assert attest_loaded_sources(root, expected, modules) == {
        key.removeprefix("src/"): value for key, value in expected.items()
    }
