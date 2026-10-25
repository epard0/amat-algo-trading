import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[2] / "deploy/paper-monitor"
sys.path.insert(0, str(BASE))
import detail_archive as archive  # noqa: E402


def load_module(name):
    spec = importlib.util.spec_from_file_location("archive_" + name, BASE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completed(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "resource_headroom", lambda _: True)
    now = dt.datetime(2026, 10, 23, 20, tzinfo=dt.UTC)
    folder = tmp_path / "2026-10-10"
    folder.mkdir()
    value = dict(
        schema="paper-session/1",
        execution_authority="none",
        status="complete",
        target_session=folder.name,
        observed_at="2026-10-10T20:00:00+00:00",
    )
    body = json.dumps(value).encode()
    (folder / "final.json").write_bytes(body)
    (folder / "latest.json").write_text(
        json.dumps(
            dict(
                file="observation-old.json",
                session=folder.name,
                sha256=load_module("reader").digest(value),
            )
        )
    )
    (folder / "observation-old.json").write_bytes(body)
    stamp = (now - dt.timedelta(days=8)).timestamp()
    os.utime(folder / "observation-old.json", (stamp, stamp))
    return now, folder, body


def test_lossless_archive_reader_and_findings_keep_original_digest(tmp_path, monkeypatch):
    now, folder, body = completed(tmp_path, monkeypatch)
    reader = load_module("reader")
    monkeypatch.setattr(reader, "STORE", tmp_path)
    pointer = json.loads((folder / "latest.json").read_text())
    (folder / "findings.json").write_text(
        json.dumps(
            dict(source=pointer, through=json.loads(body)["observed_at"], summary="Verified")
        )
    )
    result = archive.archive_completed(tmp_path, now)
    assert result["files"] == 1
    assert not (folder / pointer["file"]).exists()
    assert archive.read_detail(folder, pointer["file"]) == body
    assert reader.read(folder.name)["session_findings"]["summary"] == "Verified"
    assert (folder / "final.json").read_bytes() == body
    assert archive.archive_completed(tmp_path, now)["files"] == 0
    packed = folder / "archives/observation-old.json.gz"
    packed.write_bytes(packed.read_bytes()[:-3])
    with pytest.raises(ValueError, match="archive"):
        reader.read(folder.name)


def test_inactive_completed_age_and_batch_bounds(tmp_path, monkeypatch):
    now, folder, body = completed(tmp_path, monkeypatch)
    assert archive.archive_completed(tmp_path, now, active_session=folder.name)["files"] == 0
    (folder / "final.json").write_text(json.dumps(dict(status="monitoring")))
    assert archive.archive_completed(tmp_path, now)["files"] == 0
    (folder / "final.json").write_bytes(body)
    for index in range(4):
        path = folder / f"observation-more{index}.json"
        path.write_bytes(body)
        os.utime(path, (now.timestamp() - 9 * 86400,) * 2)
    assert archive.archive_completed(tmp_path, now, max_files=2)["files"] == 2
    assert len(list(folder.glob("observation-*.json"))) == 3
    monkeypatch.setattr(archive, "resource_headroom", lambda _: False)
    assert archive.archive_completed(tmp_path, now)["skipped"] == "resource_pressure"
    assert len(list(folder.glob("observation-*.json"))) == 3


def test_archive_write_failure_and_symlinks_preserve_source(tmp_path, monkeypatch):
    now, folder, body = completed(tmp_path, monkeypatch)
    original = archive.gzip.GzipFile
    monkeypatch.setattr(
        archive.gzip, "GzipFile", lambda **_: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        archive.archive_completed(tmp_path, now)
    assert (folder / "observation-old.json").read_bytes() == body
    assert not list(folder.rglob(".archive-*"))
    monkeypatch.setattr(archive.gzip, "GzipFile", original)
    (folder / "observation-old.json").unlink()
    secret = tmp_path / "secret.json"
    secret.write_bytes(body)
    (folder / "observation-old.json").symlink_to(secret)
    with pytest.raises(ValueError, match="Symlink"):
        archive.archive_completed(tmp_path, now)
    for name in ("../secret.json", "/observation-one.json", "state/observation-one.json"):
        with pytest.raises(ValueError):
            archive.read_detail(folder, name)


def test_manifest_publication_failure_preserves_readable_detail(tmp_path, monkeypatch):
    now, folder, body = completed(tmp_path, monkeypatch)
    monkeypatch.setattr(
        archive.json, "dump", lambda *_a, **_k: (_ for _ in ()).throw(OSError("interrupted"))
    )
    with pytest.raises(OSError):
        archive.archive_completed(tmp_path, now)
    assert archive.read_detail(folder, "observation-old.json") == body
    assert not list(folder.rglob(".manifest-*"))


def test_streamed_finalization_preserves_summary_with_archived_and_live_detail(
    tmp_path, monkeypatch
):
    import tracemalloc

    from server import paper_session

    monkeypatch.setitem(sys.modules, "paper_session", paper_session)
    worker = load_module("worker")
    folder = tmp_path / "2026-10-10"
    evidence = folder / "evidence"
    evidence.mkdir(parents=True)
    row = dict(
        amat=dict(
            orders_complete=True, plan=dict(sha256="pinned"), config_sha256="config", orders=[]
        ),
        perps=dict(decision=dict(weights=dict(SOL=0)), fills=0, padding="x" * 10000),
    )
    for index in range(1000):
        (evidence / f"observation-{index:04}.json").write_text(json.dumps(row))
    for index in range(10):
        archive._archive_one(folder, Path(f"evidence/observation-{index:04}.json"))
    pointer = dict(session=folder.name, file="observation-current.json", sha256="publication")
    publication = dict(
        observed_at="2026-10-10T20:00:00+00:00",
        audit=dict(status="verified", planned=0),
        diagnostics=dict(
            amat=dict(required_execution=dict(expected_plans={folder.name: "pinned"}))
        ),
        bots=[],
    )
    tracemalloc.start()
    result = worker.final_findings(folder, pointer, publication)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result["evidence_counts"] == dict(
        observations=1000,
        source_errors=0,
        complete_order_coverage=True,
        unique_broker_orders=0,
        config_hashes=["config"],
        plan_digests=["pinned"],
    )
    assert result["summary"].startswith("Verified hold:")
    assert "1000 decision polls, 0 polls requesting nonzero exposure" in result["perps"]
    assert peak < 2_000_000


def test_legacy_pruned_findings_do_not_break_retained_final_session(tmp_path, monkeypatch):
    _, folder, body = completed(tmp_path, monkeypatch)
    reader = load_module("reader")
    monkeypatch.setattr(reader, "STORE", tmp_path)
    (folder / "observation-old.json").unlink()
    (folder / "findings.json").write_text(
        json.dumps(
            dict(
                source=dict(session=folder.name, file="observation-missing.json", sha256="old"),
                through="old",
            )
        )
    )
    result = reader.read(folder.name)
    assert result["observed_at"] == json.loads(body)["observed_at"]
    assert "session_findings" not in result


def test_archive_ancestors_are_synced_before_source_removal_and_source_parent_after(
    tmp_path, monkeypatch
):
    _, folder, body = completed(tmp_path, monkeypatch)
    evidence = folder / "evidence"
    evidence.mkdir()
    source = evidence / "observation-one.json"
    source.write_bytes(body)
    operations = []
    original_sync = archive.os.fsync
    original_unlink = Path.unlink

    def sync(fd):
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if path.is_dir():
            operations.append(("sync", path))
        return original_sync(fd)

    def unlink(path, *args, **kwargs):
        if path == source:
            operations.append(("unlink", path))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(archive.os, "fsync", sync)
    monkeypatch.setattr(Path, "unlink", unlink)
    archive._archive_one(folder, Path("evidence/observation-one.json"))
    index = operations.index(("unlink", source))
    for directory in (folder / "archives/evidence", folder / "archives", folder):
        assert ("sync", directory) in operations[:index]
    assert ("sync", evidence) in operations[index + 1 :]


def test_archived_history_cannot_starve_new_raw_detail(tmp_path, monkeypatch):
    now, folder, body = completed(tmp_path, monkeypatch)
    (folder / "observation-old.json").unlink()
    packed = folder / "archives"
    packed.mkdir()
    for index in range(1000):
        (packed / f"observation-{index}.json.archive.json").write_text("{}")
    later = tmp_path / "2026-10-11"
    later.mkdir()
    (later / "final.json").write_text(
        json.dumps(dict(status="complete", target_session=later.name))
    )
    path = later / "observation-next.json"
    path.write_bytes(body)
    os.utime(path, (now.timestamp() - 9 * 86400,) * 2)
    monkeypatch.setattr(
        archive,
        "detail_names",
        lambda *_a: (_ for _ in ()).throw(AssertionError("Archiver must scan raw names only")),
    )
    assert archive.archive_completed(tmp_path, now, max_files=1)["files"] == 1
    assert not path.exists()
    assert len(list(packed.iterdir())) == 1000


def test_collector_maintenance_yields_priority_without_changing_schedule_or_limits():
    text = (BASE / "amat-paper-monitor.service").read_text()
    assert "Nice=10" in text and "IOSchedulingClass=idle" in text
    assert "CPUWeight=20" in text and "IOWeight=20" in text
    assert "MemoryMax=512M" in text and "TasksMax=96" in text
    assert "OnCalendar=*-*-* *:*:00" in (BASE / "amat-paper-monitor.timer").read_text()
