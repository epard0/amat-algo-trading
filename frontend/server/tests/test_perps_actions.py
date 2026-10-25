from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from fastapi import HTTPException
from server.perps_actions import command, observation


def workspace(tmp_path):
    root = tmp_path
    project = root / "kalshi-perps"
    (project / ".venv/bin").mkdir(parents=True)
    (project / ".venv/bin/python").touch()
    (project / "configs/strategies").mkdir(parents=True)
    (project / "configs/strategies/test.toml").touch()
    (project / "configs/paper.toml").write_text('mode = "paper"')
    return SimpleNamespace(root=root)


def test_only_registered_paper_arguments_reach_the_native_engine(tmp_path):
    svc = workspace(tmp_path)
    action = SimpleNamespace(capability="perps.observe", inputs={"strategy": "test"})
    argv, cwd, _ = command(svc, action)
    assert argv[-3:] == ["--strategy", "test", "--once"]
    assert "--config" in argv and cwd == tmp_path / "kalshi-perps"
    for inputs in (
        {"strategy": "../../evil"},
        {"config": "/tmp/live.toml"},
        {"strategy": "test; touch x"},
    ):
        action.inputs = inputs
        with pytest.raises(HTTPException):
            command(svc, action)
    action.inputs = {}
    (cwd / "configs/paper.toml").write_text('mode = "live"')
    with pytest.raises(HTTPException, match="paper-only"):
        command(svc, action)


def test_stop_requires_boolean_and_uses_native_reduce_only_flag(tmp_path):
    svc = workspace(tmp_path)
    action = SimpleNamespace(capability="perps.stop", inputs={"flatten": "false"})
    with pytest.raises(HTTPException):
        command(svc, action)
    action.inputs = {"flatten": True}
    assert command(svc, action)[0][-2:] == ["--all", "--flatten"]


def test_report_only_returns_allowlisted_metrics_and_rejects_path_escape(tmp_path):
    runtime = tmp_path / "kalshi-perps/runtime"
    run = runtime / "comparisons/current"
    run.mkdir(parents=True)
    pointer = runtime / "comparison-current.json"
    pointer.write_text(json.dumps({"directory": str(run)}))
    (run / "comparison.json").write_text(
        json.dumps(
            {
                "mode": "paper",
                "accounts": {
                    "strategy": {
                        "state": "running",
                        "metrics": {"equity": "10001", "token": "secret"},
                        "database": "private",
                    }
                },
            }
        )
    )
    data = observation(tmp_path)
    assert data["accounts"][0]["equity"] == "10001"
    assert "secret" not in json.dumps(data) and "private" not in json.dumps(data)
    pointer.write_text(json.dumps({"directory": str(tmp_path)}))
    assert observation(tmp_path)["status"] == "unavailable"
