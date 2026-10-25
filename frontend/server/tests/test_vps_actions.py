import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from server import vps_actions
from server.app import Service


def test_remote_controls_are_fixed_and_cannot_fall_back_to_local(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMINAL_PERPS_TARGET", "remote")
    monkeypatch.setattr(vps_actions, "load_config", lambda _: {})
    service = SimpleNamespace(root=tmp_path, storage=tmp_path)
    action = SimpleNamespace(capability="perps.stop", inputs={})
    argv, cwd, cap = vps_actions.command(service, action)
    assert argv[-2:] == ["--operation", "perps.stop"]
    assert "server.vps_actions" in argv and "systemctl" not in argv
    assert "configured VPS" in cap["effects"][0]
    assert cap["target"] == "remote"
    for inputs in ({"strategy": "all"}, {"container": "other"}, {"flatten": True}):
        action.inputs = inputs
        with pytest.raises(HTTPException):
            vps_actions.command(service, action)
    assert (
        vps_actions.command(service, SimpleNamespace(capability="perps.observe", inputs={})) is None
    )


def test_remote_command_refuses_unknown_operations_and_nonpaper_observations(monkeypatch, tmp_path):
    runner = Mock()
    monkeypatch.setattr(vps_actions, "Runner", lambda config: runner)
    monkeypatch.setattr(vps_actions, "load_config", lambda path: {"username": "operator"})
    monkeypatch.setattr(vps_actions, "safe_output", lambda value, config: value)
    with pytest.raises(ValueError, match="Unregistered"):
        vps_actions.invoke(tmp_path / "remote.toml", "perps.start; touch /tmp/evil")
    runner.run.assert_not_called()
    runner.run.return_value = {
        "exit_code": 0,
        "truncated": False,
        "stdout": json.dumps({"status": "observed", "mode": "live", "live_enabled": True}),
    }
    with pytest.raises(ValueError, match="paper mode"):
        vps_actions.invoke(tmp_path / "remote.toml", "perps.status")
    assert runner.run.call_args.args[0] == "/home/operator/.local/bin/amat-vps-control perps.status"


def test_ssh_failure_keeps_old_remote_data_explicitly_stale(monkeypatch, tmp_path):
    service = Service(root=tmp_path, state=tmp_path / "state", storage=tmp_path / "storage")
    old = {
        "perps": {
            "status": "observed",
            "mode": "paper",
            "live_enabled": False,
            "accounts": [{"equity": "10000"}],
        }
    }
    with service.lock, service.db:
        service.db.execute(
            "INSERT INTO observations(name,data) VALUES (?,?)", ("remote.engines", json.dumps(old))
        )
    reader = vps_actions.RemoteEngines(service)
    runner = Mock()
    runner.connect.side_effect = RuntimeError("SSH unavailable")
    monkeypatch.setattr(vps_actions, "load_config", lambda _: {})
    monkeypatch.setattr(vps_actions, "Runner", lambda _: runner)
    assert reader.describe()["stale"] is True
    runner.connect.assert_not_called()
    result = reader.refresh()
    assert result["executor_live"]["stale"] is True
    assert result["executor_live"]["status"] == "unavailable"
    assert result["transport"] == "unavailable"
    assert result["perps"]["stale"] is True
    assert result["perps"]["status"] == "unavailable"
    assert result["perps"]["accounts"][0]["equity"] == "10000"
    assert "remote VPS" in result["perps"]["source"]
    reader.describe()
    assert runner.connect.call_count == 1  # cache reads do not retry SSH


def test_live_lifecycle_is_not_available_from_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMINAL_PERPS_TARGET", "remote")
    service = SimpleNamespace(root=tmp_path, storage=tmp_path)
    for verb in ("start", "restart", "stop", "deploy"):
        action = SimpleNamespace(capability=f"vps.executor-live.{verb}", inputs={})
        assert vps_actions.command(service, action) is None
    assert all("live" not in c["id"] for c in vps_actions.capabilities(tmp_path))
