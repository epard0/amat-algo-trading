import json
import time

from fastapi.testclient import TestClient
from server.app import Service, create_app, redact


def client(tmp_path):
    (tmp_path / "executor/state/reports").mkdir(parents=True)
    svc = Service(tmp_path, storage=tmp_path / "store")
    return TestClient(create_app(svc)), svc


def auth(c):
    return {"X-CSRF-Token": c.get("/api/v1/session").json()["csrf_token"]}


def test_sessions_host_origin_and_csrf(tmp_path):
    c, s = client(tmp_path)
    assert c.get("/api/v1/dashboard").status_code == 401
    h = auth(c)
    assert c.get("/api/v1/dashboard").status_code == 200
    assert c.get("/api/v1/dashboard", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.get("/api/v1/session", headers={"Host": "evil.example"}).status_code == 400
    assert (
        c.post("/api/v1/actions/preview", json={"capability": "executor.submit"}).status_code == 403
    )
    assert (
        c.post(
            "/api/v1/actions/preview", headers=h, json={"capability": "executor.submit"}
        ).status_code
        == 422
    )


def test_truthful_missing_evidence_and_redaction(tmp_path):
    c, s = client(tmp_path)
    auth(c)
    d = c.get("/api/v1/dashboard").json()
    assert not d["health"]["healthy"]
    assert d["health"]["ready_for_orders"] is None
    assert d["account"]["account"] is None
    assert redact({"account_number": "PA12345678", "nested": {"token": "abc"}}) == {
        "account_number": "[redacted]",
        "nested": {"token": "[redacted]"},
    }


def test_native_tail_partial_corruption_rotation_dedup(tmp_path):
    c, s = client(tmp_path)
    auth(c)
    path = s.state / "journal.ndjson"
    line = (
        json.dumps(
            {
                "event": "intent.created",
                "at": "2026-10-09T00:00:00Z",
                "secret_key": "hidden",
            }
        )
        + "\n"
    )
    path.write_text(line + "broken\n" + '{"event":')
    s.ingest()
    s.ingest()
    rows = s.events()
    assert len(rows) == 2
    assert rows[0]["payload"]["secret_key"] == "[redacted]"
    with path.open("a") as f:
        f.write('"order.filled"}\n')
    s.ingest()
    assert len(s.events()) == 3
    path.rename(s.state / "journal.1.ndjson")
    path.write_text(line)
    s.ingest()
    assert len(s.events()) == 3


def test_previews_jobs_idempotency_and_staleness(tmp_path):
    c, s = client(tmp_path)
    h = auth(c)
    executable = tmp_path / "executor/.venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nprintf "local observation\\n"\n')
    executable.chmod(0o700)
    body = {"capability": "executor.status", "inputs": {}}
    preview = c.post("/api/v1/actions/preview", json=body, headers=h).json()
    assert "preview_id" in preview
    request = dict(body, preview_id=preview["preview_id"], idempotency_key="unique-key-1")
    first = c.post("/api/v1/actions", json=request, headers=h)
    assert first.status_code == 202
    assert c.post("/api/v1/actions", json=request, headers=h).json() == first.json()
    assert (
        c.post(
            "/api/v1/actions",
            json=dict(request, capability="executor.health"),
            headers=h,
        ).status_code
        == 409
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = c.get(first.json()["status_url"]).json()
        if result["status"] not in {"queued", "started"}:
            break
        # The detached worker imports the API in another process. Allow bounded
        # startup time on a busy development host rather than assuming 1 second.
        time.sleep(0.05)
    assert result["status"] == "succeeded"
    kinds = [e["kind"] for e in s.events()]
    assert (
        "action.requested" in kinds
        and "action.started" in kinds
        and "action.output" in kinds
        and "action.succeeded" in kinds
    )
    other = c.post("/api/v1/actions/preview", json=body, headers=h).json()
    (s.state / "heartbeat.json").write_text("{}")
    assert (
        c.post(
            "/api/v1/actions",
            json=dict(request, preview_id=other["preview_id"], idempotency_key="unique-key-2"),
            headers=h,
        ).status_code
        == 409
    )


def test_restart_does_not_retry_jobs_and_artifact_paths_are_bounded(tmp_path):
    c, s = client(tmp_path)
    auth(c)
    s.save_job(
        {
            "id": "test",
            "idempotency_key": "test-key",
            "fingerprint": "abc",
            "status": "started",
        }
    )
    restarted = Service(tmp_path, storage=tmp_path / "store")
    assert restarted.jobs()[0]["status"] == "outcome_unknown"
    assert c.get("/api/v1/artifacts/../../.env").status_code == 404
    assert c.get("/api/v1/market/bars?symbol=../../etc").status_code == 422


def test_typed_engine_inputs_are_registered_and_bounded(tmp_path):
    c, s = client(tmp_path)
    config = tmp_path / "amat/configs/strategy/amat_v6.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[strategy]\n")
    executable = tmp_path / "amat/.venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.touch()
    h = auth(c)
    endpoint = "/api/v1/actions/preview"
    for inputs in [
        {"config": "../../.env"},
        {"sessions": 999999},
        {"sessions": True},
        {"seed": -1},
        {"unexpected": "value"},
    ]:
        assert (
            c.post(
                endpoint,
                json={"capability": "amat.backtest.synthetic", "inputs": inputs},
                headers=h,
            ).status_code
            == 422
        )
    result = c.post(
        endpoint,
        json={
            "capability": "amat.backtest.synthetic",
            "inputs": {"config": "amat_v6.toml", "sessions": 300, "seed": 7},
        },
        headers=h,
    )
    assert result.status_code == 200
    assert "backtest-synthetic" in result.json()["command"]
    assert (
        c.post(
            endpoint,
            json={"capability": "amat.config.universe", "inputs": {}},
            headers=h,
        ).json()["command"][3]
        == "show-universe"
    )


def test_cancel_only_reaches_read_only_jobs_and_news_overview_is_served(tmp_path):
    c, s = client(tmp_path)
    h = auth(c)
    assert c.post("/api/v1/jobs/missing/cancel", headers=h, json={}).status_code == 404
    for job_id, capability, expected in [
        ("j-research", "amat.walkforward", 202),
        ("j-order", "executor.submit", 422),
    ]:
        s.save_job(
            {
                "id": job_id,
                "capability": capability,
                "inputs": {},
                "fingerprint": job_id,
                "idempotency_key": job_id,
                "status": "started",
            }
        )
        assert c.post(f"/api/v1/jobs/{job_id}/cancel", headers=h, json={}).status_code == expected
    assert (s.storage / "cancel/j-research").exists()
    assert not (s.storage / "cancel/j-order").exists()
    assert c.post("/api/v1/jobs/j-research/cancel", json={}).status_code == 403  # CSRF required
    assert c.get("/api/v1/news").json()["store"] is None


def test_every_capability_is_labelled_and_workflows_use_real_steps(tmp_path):
    from server.catalog import META, WORKFLOWS

    c, s = client(tmp_path)
    auth(c)
    caps = c.get("/api/v1/capabilities").json()["items"]
    assert caps and all(x["category"] != "Other" and x["title"] != x["id"] for x in caps)
    ids = {x["id"] for x in caps}
    assert ids <= set(META)
    for flow in WORKFLOWS:
        assert set(flow["steps"]) <= ids, flow["id"]
    flows = c.get("/api/v1/workflows").json()["items"]
    stop = next(f for f in flows if f["id"] == "emergency-stop")
    assert stop["available"] is False or stop["steps"][0]["supported"]  # needs a state root
    morning = next(f for f in flows if f["id"] == "morning-check")
    assert morning["available"] is False and morning["reason"]  # no runtimes in tmp root
    assert not any("orders" in f["risk_label"].lower() for f in flows)  # no workflow places orders


def test_research_presets_are_registered_validated_and_inert(tmp_path):
    c, svc = client(tmp_path)
    executable = tmp_path / "amat/.venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.touch()
    dataset = tmp_path / "amat/data/snapshots/frozen/daily.parquet"
    dataset.parent.mkdir(parents=True)
    dataset.touch()
    h = auth(c)
    from types import SimpleNamespace

    cap = next(v for v in svc.capabilities() if v["id"] == "amat.research.launch")
    assert cap["inputs"]["program"]["options"] == ["standard", "training-2025"]
    for program in ("standard", "training-2025"):
        for robustness in ("historical", "extended"):
            inputs = dict(name="preset-check", snapshot="frozen", iterations=1,
                          program=program, robustness=robustness)
            args, _, _ = svc.command(SimpleNamespace(capability="amat.research.launch", inputs=inputs))
            assert args[args.index("--program") + 1] == program
            assert args[args.index("--robustness") + 1] == robustness
            assert "--built-in" in args and "--max-model-tokens-per-day" in args
            assert not (svc.storage / "research/preset-check").exists()
    for invalid in ({"program": "arbitrary"}, {"robustness": "unbounded"}, {"iterations": 4}):
        response = c.post("/api/v1/actions/preview", headers=h, json={
            "capability": "amat.research.launch",
            "inputs": {"name": "preset-check", "snapshot": "frozen", **invalid},
        })
        assert response.status_code == 422
