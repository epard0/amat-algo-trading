"""Regression coverage for independent jobs and snapshot/event boundaries."""

import json
import time

from fastapi.testclient import TestClient

from server.app import Service, create_app
from server.supervision import dispatch, worker_active


def test_worker_survives_service_restart(tmp_path):
    state = tmp_path / "executor/state"
    state.mkdir(parents=True)
    executable = tmp_path / "executor/.venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nsleep 0.5\nprintf "observed local state\\n"\n')
    executable.chmod(0o700)
    service = Service(tmp_path, storage=tmp_path / "store")
    job = {
        "id": "independent-worker",
        "idempotency_key": "survives-restart",
        "fingerprint": "test",
        "capability": "executor.status",
        "inputs": {},
        "status": "queued",
    }
    service.save_job(job)
    dispatch(service, job)
    assert worker_active(service.storage, job["id"])
    restarted = Service(tmp_path, storage=tmp_path / "store")
    assert restarted.jobs()[0]["status"] in {"queued", "started"}
    for _ in range(200):
        record = restarted.jobs()[0]
        if record["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert record["status"] == "succeeded", record
    assert "observed local state" in record["output"]
    assert any(event["kind"] == "action.succeeded" for event in restarted.events())


def test_latest_event_watermark_beyond_first_ten_thousand(tmp_path):
    service = Service(tmp_path, storage=tmp_path / "store")
    event = {"kind": "test.observation", "observed_at": "2026-10-09T00:00:00Z"}
    service.db.executemany("INSERT INTO events(data) VALUES (?)", [(json.dumps(event),)] * 10005)
    service.db.commit()
    dashboard = service.dashboard()
    assert dashboard["watermark"] == "10005"
    assert len(dashboard["events"]) == 200
    assert dashboard["events"][0]["cursor"] == "9806"


def test_preview_binds_selected_configuration_and_security_headers(tmp_path):
    root = tmp_path
    configdir = root / "amat/configs/strategy"
    configdir.mkdir(parents=True)
    selected = configdir / "custom.toml"
    selected.write_text('name="first"')
    python = root / "amat/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("placeholder executable")
    service = Service(root, storage=root / "store")
    client = TestClient(create_app(service))
    session = client.get("/api/v1/session").json()
    headers = {"X-CSRF-Token": session["csrf_token"]}
    action = {"capability": "amat.config.validate", "inputs": {"config": "custom.toml"}}
    preview = client.post("/api/v1/actions/preview", json=action, headers=headers)
    assert preview.status_code == 200
    selected.write_text('name="changed"')
    result = client.post(
        "/api/v1/actions",
        headers=headers,
        json=dict(
            action,
            preview_id=preview.json()["preview_id"],
            idempotency_key="changed-selected-config",
        ),
    )
    assert result.status_code == 409
    assert "frame-ancestors 'none'" in result.headers["content-security-policy"]
    assert client.get("/api/v1/health/live").status_code == 200


def test_optional_executor_controls_use_existing_boundary(tmp_path, monkeypatch):
    executable = tmp_path / "executor/.venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.write_text("not executed")
    service = Service(tmp_path, storage=tmp_path / "store")
    import pytest
    from fastapi import HTTPException

    from server.app import Action

    with pytest.raises(HTTPException):
        service.command(Action(capability="executor.submit"))
    monkeypatch.setenv("TERMINAL_ENABLE_EXECUTOR_ACTIONS", "1")
    cmd, cwd, _ = service.command(
        Action(capability="executor.submit", inputs={"session": "2026-10-09"})
    )
    assert cmd[-3:] == ["submit", "--session", "2026-10-09"]
    assert cmd[2] == "executor.cli"
    assert cwd == tmp_path / "executor"
    for invalid in [
        {"session": "20261009"},
        {"session": "2026-99-99"},
        {"session": 1},
        {"arbitrary": "argument"},
    ]:
        with pytest.raises(HTTPException):
            service.command(Action(capability="executor.submit", inputs=invalid))
    monkeypatch.setenv("TERMINAL_ENABLE_BROKER_READS", "1")
    cmd, _, _ = service.command(Action(capability="executor.readiness"))
    assert cmd[-1] == "--ready" and cmd[3] == "health"


def test_broker_readmodel_keeps_its_observation_time(tmp_path):
    service = Service(tmp_path, storage=tmp_path / "store")
    value = {
        "observed_at": "2026-10-09T12:00:00+00:00",
        "value": {"account": {"equity": "100.01"}, "positions": []},
    }
    service.db.execute(
        "INSERT INTO observations VALUES (?,?)", ("executor.account", json.dumps(value))
    )
    service.db.commit()
    account = service.dashboard()["account"]
    assert account["observed_at"] == value["observed_at"]
    assert account["account"]["equity"] == "100.01"
    assert account["status"] == "observed"


def test_job_completion_and_activity_event_publish_in_one_transaction(tmp_path, monkeypatch):
    service = Service(tmp_path, storage=tmp_path / "store")
    reader = Service(tmp_path, storage=tmp_path / "store", recover=False)
    job = {
        "id": "atomic",
        "idempotency_key": "atomic-transition",
        "fingerprint": "test",
        "capability": "executor.status",
        "status": "queued",
    }
    service.save_job(job)
    original = service.event

    def before_event(*args, **kwargs):
        assert reader.jobs()[0]["status"] == "queued"
        assert reader.events() == []
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "event", before_event)
    job["status"] = "succeeded"
    service.save_job_event(job, {"capability": job["capability"]})
    assert reader.jobs()[0]["status"] == "succeeded"
    assert reader.events()[0]["kind"] == "action.succeeded"
