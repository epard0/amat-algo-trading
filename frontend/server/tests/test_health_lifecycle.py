import time

from fastapi.testclient import TestClient
from server.app import Service, create_app


def test_health_api_uses_session_csrf_and_api_lifespan(tmp_path, monkeypatch):
    service = Service(tmp_path, storage=tmp_path / "store")
    app = create_app(service)
    scheduler = app.state.health_scheduler
    calls = []
    monkeypatch.setattr(scheduler, "start", lambda: calls.append("start"))
    monkeypatch.setattr(scheduler, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(scheduler, "describe", lambda: {"scheduler": {"running": True}})
    monkeypatch.setattr(scheduler, "request_refresh", lambda: {"accepted": True})
    assert calls == []  # Constructing a worker/service must not create a scheduler.
    with TestClient(app) as client:
        assert calls == ["start"]
        assert client.get("/api/v1/remote/health").status_code == 401
        csrf = client.get("/api/v1/session").json()["csrf_token"]
        assert client.get("/api/v1/remote/health").json()["scheduler"]["running"]
        assert client.post("/api/v1/remote/health/refresh").status_code == 403
        assert (
            client.post("/api/v1/remote/health/refresh", headers={"X-CSRF-Token": csrf}).status_code
            == 202
        )
    assert calls == ["start", "stop"]


def test_automatic_preview_state_is_expired_and_bounded_without_evicting_valid_previews(
    tmp_path, monkeypatch
):
    service = Service(tmp_path, storage=tmp_path / "store")
    monkeypatch.setattr(
        service, "command", lambda action: (["python", "-V"], tmp_path, {"effects": []})
    )
    client = TestClient(create_app(service))
    csrf = client.get("/api/v1/session").json()["csrf_token"]
    service.previews["expired"] = {"expires": time.time() - 1}
    service.sessions["expired"] = {"expires": time.time() - 1}
    headers = {"X-CSRF-Token": csrf}
    result = client.post("/api/v1/actions/preview", headers=headers, json={"capability": "test"})
    assert result.status_code == 200
    valid = result.json()["preview_id"]
    assert "expired" not in service.previews and "expired" not in service.sessions
    for index in range(2047):
        service.previews[f"other-{index}"] = {"expires": time.time() + 120}
    assert (
        client.post(
            "/api/v1/actions/preview", headers=headers, json={"capability": "test"}
        ).status_code
        == 429
    )
    assert valid in service.previews
    assert len(service.previews) == 2048


def test_running_job_recovers_durable_output_before_completion_and_isolates_other_jobs(tmp_path):
    service = Service(tmp_path, storage=tmp_path / "store")
    service.save_job(
        dict(
            id="running",
            idempotency_key="key",
            fingerprint="digest",
            capability="amat.config.validate",
            status="started",
        )
    )
    service.event("action.output", {"text": "first\n"}, "running")
    service.event("action.output", {"text": "unrelated\n"}, "another")
    service.event("action.output", {"text": "second\n"}, "running")
    client = TestClient(create_app(service))
    client.get("/api/v1/session")
    result = client.get("/api/v1/jobs/running").json()
    assert result["live_output"] == "first\nsecond\n"
    assert result["live_output_cursor"] > 0
    assert result["live_output_truncated"] is False
    assert result["status"] == "started"
    before = result["live_output_cursor"]
    service.event("action.output", {"text": "third\n"}, "running")
    assert client.get("/api/v1/jobs/running").json()["live_output_cursor"] > before
    service.save_job(
        dict(
            id="running",
            idempotency_key="key",
            fingerprint="digest",
            capability="amat.config.validate",
            status="succeeded",
            output="final\n",
        )
    )
    final = client.get("/api/v1/jobs/running").json()
    assert final["output"] == "final\n" and "live_output" not in final


def test_running_output_capture_is_bounded(tmp_path):
    service = Service(tmp_path, storage=tmp_path / "store")
    for _ in range(3):
        service.event("action.output", {"text": "x" * 64000}, "running")
    result = service.live_job_output("running")
    assert len(result["live_output"]) == 128000
    assert result["live_output_truncated"]
