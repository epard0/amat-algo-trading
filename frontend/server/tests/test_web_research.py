"""Web research must honor the native workspace and offline budget contracts."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from server.app import Service, create_app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    project = tmp_path / "project"
    python = project / "amat/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    snapshot = project / "amat/data/snapshots/frozen"
    snapshot.mkdir(parents=True)
    (snapshot / "daily.parquet").touch()
    external = tmp_path / "external-research"
    monkeypatch.setenv("TERMINAL_RESEARCH_ROOT", str(external))
    return Service(project, storage=project / "frontend/.runtime"), external


def test_launch_defaults_are_registered_external_and_valid_offline_budget(setup):
    service, external = setup
    cap = next(c for c in service.capabilities() if c["id"] == "amat.research.launch")
    assert cap["supported"]
    assert cap["inputs"]["snapshot"]["default"] == "frozen"
    args, _, _ = service.command(SimpleNamespace(capability="amat.research.launch", inputs={}))
    workspace = Path(args[args.index("--workspace") + 1])
    assert workspace == external / "terminal-research"
    assert not workspace.is_relative_to(service.root)
    assert int(args[args.index("--max-model-tokens-per-day") + 1]) >= 256
    assert "--built-in" in args
    assert not workspace.exists(), "Preview must not create a research workspace"


def test_checkout_workspace_and_symlink_escape_are_rejected(setup):
    service, external = setup
    service.research_root = service.root / "frontend/research"
    with pytest.raises(HTTPException, match="outside"):
        service.command(SimpleNamespace(capability="amat.research.launch", inputs={}))
    service.research_root = external
    external.mkdir()
    (external / "terminal-research").symlink_to(service.root / "escape")
    with pytest.raises(HTTPException, match="escapes"):
        service.command(SimpleNamespace(capability="amat.research.launch", inputs={}))


def test_native_reports_are_registered_and_html_download_is_sandboxed(setup):
    service, external = setup
    run = external / "trial"
    run.mkdir(parents=True)
    (run / "report.json").write_text(json.dumps({"status": "batch_completed"}))
    (run / "report.html").write_text("<html><style>body{color:blue}</style>Comparison</html>")
    client = TestClient(create_app(service))
    client.get("/api/v1/session")
    artifacts = client.get("/api/v1/artifacts").json()["items"]
    html = next(a for a in artifacts if a["name"] == "report.html")
    response = client.get("/api/v1/artifacts/" + html["id"] + "/download")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-security-policy"].startswith("sandbox;")
    assert "Comparison" in response.text
