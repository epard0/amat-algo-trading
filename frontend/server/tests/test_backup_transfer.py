import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


pull = module("backup_pull", ROOT / "frontend/scripts/pull-remote-backup.py")
snapshot = module("backup_writer", ROOT / "executor/scripts/backup-state.py")


def test_writer_transfer_and_restore_contract_includes_wal(tmp_path):
    import sqlite3

    source = tmp_path / "state"
    source.mkdir()
    with sqlite3.connect(source / "orders.sqlite") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE orders(id TEXT)")
        db.execute("INSERT INTO orders VALUES('accepted')")
        db.commit()
        archive = tmp_path / "backup.tar.gz"
        snapshot.snapshot(source, archive)
        assert pull.verify_archive(archive)["files"] == 1
        with tarfile.open(archive) as packed:
            packed.extractall(tmp_path / "restore", filter="data")
    with sqlite3.connect(tmp_path / "restore/state/orders.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT id FROM orders").fetchone() == ("accepted",)


def test_transfer_rejects_repacked_corruption_and_unsafe_members(tmp_path):
    source = tmp_path / "state"
    source.mkdir()
    (source / "receipt.json").write_text('{"status":"unknown"}')
    original = tmp_path / "original.tar.gz"
    snapshot.snapshot(source, original)
    for attack in ("corrupt", "traversal", "link", "duplicate"):
        path = tmp_path / (attack + ".tar.gz")
        with tarfile.open(original) as src, tarfile.open(path, "w:gz") as dst:
            for member in src:
                if member.isdir():
                    dst.addfile(member)
                    continue
                body = src.extractfile(member).read()
                if attack == "corrupt" and member.name.endswith("receipt.json"):
                    body = b"corrupted"
                member.size = len(body)
                dst.addfile(member, io.BytesIO(body))
                if attack == "duplicate" and member.name.endswith("receipt.json"):
                    dst.addfile(member, io.BytesIO(body))
            if attack in ("traversal", "link"):
                member = tarfile.TarInfo(
                    "state/../escape" if attack == "traversal" else "state/link"
                )
                if attack == "link":
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/secret"
                dst.addfile(member, io.BytesIO(b""))
        with pytest.raises(ValueError):
            pull.verify_archive(path)
    assert pull.MAX_SOURCE_BYTES == snapshot.MAX_SOURCE_BYTES
    assert pull.MAX_ARCHIVE_BYTES == snapshot.MAX_ARCHIVE_BYTES


def test_all_directory_headers_are_validated_and_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(pull, "MAX_TOTAL_MEMBERS", 5)
    for attack in ("traversal", "many"):
        path = tmp_path / (attack + ".tar.gz")
        with tarfile.open(path, "w:gz") as archive:
            for index in range(6):
                member = tarfile.TarInfo(
                    "state/../escape" if attack == "traversal" else f"state/d{index}"
                )
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
        with pytest.raises(ValueError, match="member"):
            pull.verify_archive(path)


def test_exact_file_budget_allows_manifest_last(tmp_path):
    import hashlib

    path = tmp_path / "boundary.tar.gz"
    evidence = {}
    with tarfile.open(path, "w:gz") as archive:
        for index in range(pull.MAX_SOURCE_FILES):
            name = f"file{index}"
            member = tarfile.TarInfo("state/" + name)
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
            evidence[name] = dict(bytes=1, sha256=hashlib.sha256(b"x").hexdigest())
        manifest = json.dumps(
            dict(
                schema="state-backup/2", file_evidence=evidence, source_bytes=pull.MAX_SOURCE_FILES
            )
        ).encode()
        member = tarfile.TarInfo("state/snapshot-manifest.json")
        member.size = len(manifest)
        archive.addfile(member, io.BytesIO(manifest))
    assert pull.verify_archive(path)["files"] == pull.MAX_SOURCE_FILES


def test_decompressed_and_extended_header_budgets_are_enforced(tmp_path, monkeypatch):
    path = tmp_path / "extended.tar.gz"
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo("state/file")
        member.pax_headers = {"comment": "x" * 70000}
        archive.addfile(member, io.BytesIO(b""))
    with pytest.raises(ValueError, match="extended header"):
        pull.verify_archive(path)
    monkeypatch.setattr(pull, "MAX_TAR_BYTES", 1024)
    with pytest.raises(ValueError, match="decompressed"):
        pull.verify_archive(path)
