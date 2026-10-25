"""Crash-boundary checks for immutable dashboard generations, without broker I/O."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "adaptive_append_pointer", ROOT / "scripts/append_adaptive_experiment.py"
)
assert SPEC and SPEC.loader
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def publication(rows):
    return {
        "schema": "incremental-research-dashboard/1",
        "protocol_sha256": "a" * 64,
        "results": rows,
    }


def generation(campaign, value):
    archive = campaign / "publications"
    archive.mkdir(exist_ok=True)
    path = archive / f"{publisher.digest(value)}.json"
    publisher.save(path, value)
    return path


def test_pointer_keeps_previous_generation_through_legacy_pair_interruption(tmp_path):
    old = publication([{"id": "old"}])
    new = publication([{"id": "new"}])
    generation(tmp_path, old)
    publisher.save(tmp_path / "dashboard-results.json", old)
    publisher.publication_pointer(tmp_path, old)
    generation(tmp_path, new)
    # Model a crash between compatibility JSON and its sidecar publication.
    (tmp_path / "dashboard-results.json").write_text(json.dumps(new))
    assert publisher.current_publication(tmp_path, "a" * 64) == old
    publisher.publication_pointer(tmp_path, new)
    assert publisher.current_publication(tmp_path, "a" * 64) == new


def test_interrupted_pointer_replace_does_not_destroy_committed_generation(tmp_path, monkeypatch):
    old = publication([])
    new = publication([{"id": "new"}])
    generation(tmp_path, old)
    generation(tmp_path, new)
    publisher.publication_pointer(tmp_path, old)
    replace = Path.replace

    def interrupt(path, destination):
        if Path(destination).name == "publication-pointer.json":
            raise OSError("injected interruption before pointer commit")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", interrupt)
    with pytest.raises(OSError, match="injected"):
        publisher.publication_pointer(tmp_path, new)
    assert publisher.current_publication(tmp_path, "a" * 64) == old


@pytest.mark.parametrize("bad", ["../outside", "f" * 63, "G" * 64])
def test_invalid_pointer_cannot_fall_back_to_a_valid_legacy_pair(tmp_path, bad):
    old = publication([])
    publisher.save(tmp_path / "dashboard-results.json", old)
    (tmp_path / "publication-pointer.json").write_text(
        json.dumps(
            {
                "schema": "incremental-publication-pointer/1",
                "protocol_sha256": "a" * 64,
                "publication_sha256": bad,
            }
        )
    )
    with pytest.raises(ValueError, match="pointer identity"):
        publisher.current_publication(tmp_path, "a" * 64)


def test_generation_tampering_and_dangling_pointer_fail_closed(tmp_path):
    old = publication([])
    path = generation(tmp_path, old)
    publisher.save(tmp_path / "dashboard-results.json", old)
    publisher.publication_pointer(tmp_path, old)
    path.write_text("{}")
    with pytest.raises(ValueError):
        publisher.current_publication(tmp_path, "a" * 64)
    pointer = tmp_path / "publication-pointer.json"
    pointer.unlink()
    pointer.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        publisher.current_publication(tmp_path, "a" * 64)


def test_sealed_generation_with_wrong_schema_is_not_admitted(tmp_path):
    wrong = publication([])
    wrong["schema"] = "other-protocol/1"
    generation(tmp_path, wrong)
    publisher.publication_pointer(tmp_path, wrong)
    with pytest.raises(ValueError, match="Invalid existing"):
        publisher.current_publication(tmp_path, "a" * 64)
