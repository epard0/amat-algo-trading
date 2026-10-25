import importlib.util
import sqlite3
import tarfile
from pathlib import Path

import pytest


def test_snapshot_captures_committed_wal_and_bounds_state(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "state_snapshot", Path(__file__).parents[1] / "scripts/backup-state.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "state"
    source.mkdir()
    with sqlite3.connect(source / "registry.sqlite3") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE orders(id TEXT)")
        db.execute("INSERT INTO orders VALUES ('receipt')")
        db.commit()
        (source / "journal.ndjson").write_text('{"kind":"observed"}\n')
        (source / "daemon.lock").touch()
        (source / "external").symlink_to(tmp_path / "secret")
        output = tmp_path / "backup.tar.gz"
        result = module.snapshot(source, output)
        assert result["files"] == 2
        restored = tmp_path / "restored"
        with tarfile.open(output) as archive:
            archive.extractall(restored, filter="data")
        with sqlite3.connect(restored / "state/registry.sqlite3") as restored_db:
            assert restored_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert restored_db.execute("SELECT id FROM orders").fetchone()[0] == "receipt"
        assert not (restored / "state/daemon.lock").exists()
        with pytest.raises(ValueError, match="size"):
            module.snapshot(source, tmp_path / "too-small.tar.gz", max_bytes=1)


def snapshot_module():
    spec = importlib.util.spec_from_file_location(
        "bounded_snapshot", Path(__file__).parents[1] / "scripts/backup-state.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_two_full_journal_generations_fit_new_budget_and_have_verified_hashes(tmp_path):
    import hashlib
    import json

    source = tmp_path / "state"
    source.mkdir()
    # Reproduce the old64MiB collision without allocating the journals in RAM.
    for name in ("journal.ndjson", "journal.1.ndjson"):
        with (source / name).open("wb") as handle:
            for _ in range(32):
                handle.write(b"x" * 1024 * 1024)
    (source / "STOP_SUBMISSIONS").write_text("operator stop")
    module = snapshot_module()
    output = tmp_path / "state.tar.gz"
    result = module.snapshot(source, output)
    assert result["source_bytes"] > 64 * 1024 * 1024
    with tarfile.open(output) as archive:
        manifest = json.load(archive.extractfile("state/snapshot-manifest.json"))
        assert manifest["schema"] == "state-backup/2"
        for name, metadata in manifest["file_evidence"].items():
            checksum = hashlib.sha256()
            size = 0
            with archive.extractfile("state/" + name) as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    checksum.update(chunk)
                    size += len(chunk)
            assert metadata == {"bytes": size, "sha256": checksum.hexdigest()}
        assert archive.extractfile("state/STOP_SUBMISSIONS").read() == b"operator stop"


def test_low_staging_disk_defers_without_publishing_backup(tmp_path, monkeypatch):
    from types import SimpleNamespace

    source = tmp_path / "state"
    source.mkdir()
    (source / "receipt.json").write_text("{}")
    module = snapshot_module()
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=1024))
    output = tmp_path / "backup.tar.gz"
    with pytest.raises(ValueError, match="disk"):
        module.snapshot(source, output)
    assert not output.exists()
    assert (source / "receipt.json").read_text() == "{}"
