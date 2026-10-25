import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


def control():
    spec = importlib.util.spec_from_file_location(
        "vps_control", Path(__file__).parents[1] / "scripts/vps-control.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remote_paper_control_rejects_live_labels_and_signing_credentials(monkeypatch):
    module = control()
    run = Mock()
    monkeypatch.setattr(module, "call", run)
    with pytest.raises(ValueError, match="paper image"):
        module.validate("perps", {"Config": {"Labels": {"amat.mode": "live"}, "Env": []}})
    with pytest.raises(ValueError, match="Signing credentials"):
        module.validate(
            "perps",
            {"Config": {"Labels": {"amat.mode": "paper"}, "Env": ["KALSHI_PERPS_KEY_ID=present"]}},
        )
    run.assert_not_called()


def test_remote_executor_control_verifies_installed_paper_host_without_credentials(monkeypatch):
    module = control()
    run = Mock()
    monkeypatch.setattr(module, "call", run)
    module.validate("executor", {"State": {"Running": False}, "Image": "sha256:frozen"})
    argv = run.call_args.args[0]
    assert argv[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert "paper-api.alpaca.markets" in argv[-1]
    assert "--env-file" not in argv and "--volumes-from" not in argv


def test_paper_lifecycle_refuses_live_mode(monkeypatch):
    module = control()
    run = Mock()
    monkeypatch.setattr(module, "call", run)
    with pytest.raises(ValueError, match="cannot operate a live"):
        module.validate("executor", {"Config": {"Env": ["TRADING_MODE=live"]}})
    run.assert_not_called()


def test_missing_live_container_is_explicit_and_read_only(monkeypatch):
    module = control()
    run = Mock()
    monkeypatch.setattr(module, "call", run)
    result = module.read_live_executor(None)
    assert result["deployment"]["status"] == "not_deployed"
    assert result["mode"] == "live"
    run.assert_not_called()
    assert "executor-live" not in module.CONTAINERS


def test_live_observation_rejects_paper_readiness(monkeypatch):
    module = control()
    monkeypatch.setattr(module, "container_view", lambda _: {})
    monkeypatch.setattr(module, "call", Mock(return_value=Mock(stdout='{"trading_mode":"paper"}')))
    with pytest.raises(ValueError, match="establish live mode"):
        module.read_live_executor({"State": {"Running": True}})


def test_health_contract_observes_expected_inventory_without_lifecycle_or_order_effects(
    monkeypatch,
):
    module = control()
    calls = []
    deployed = {
        name: dict(
            State=dict(
                Running=True,
                Status="running",
                Health={"Status": "healthy"},
                StartedAt="2026-10-23T00:00:00Z",
                OOMKilled=False,
            ),
            RestartCount=0,
            Config=dict(Image="paper", Labels={"amat.mode": "paper"}, Env=[]),
            Image="sha256:existing",
            HostConfig=dict(
                Memory=100,
                MemorySwap=200,
                NanoCpus=500000000,
                RestartPolicy={"Name": "unless-stopped"},
            ),
        )
        for name in module.EXPECTED_CONTAINERS
    }

    def call(argv, timeout=50, check=True):
        calls.append(argv)
        if argv[:2] == ["docker", "ps"]:
            output = "\n".join([*module.EXPECTED_CONTAINERS, "kalshi-perps-shadow"])
        elif argv[:2] == ["docker", "inspect"]:
            output = module.json.dumps([deployed[argv[2]]])
        elif argv[:2] == ["docker", "stats"]:
            output = module.json.dumps(
                dict(
                    Name="kalshi-perps",
                    MemUsage="90MiB / 320MiB",
                    MemPerc="28%",
                    CPUPerc="1%",
                    PIDs="3",
                )
            )
        elif argv[:2] == ["ss", "-ltnH"]:
            output = "LISTEN 0 4096 127.0.0.1:8765 0.0.0.0:*\nLISTEN 0 4096 0.0.0.0:2222 0.0.0.0:*"
        elif argv[0] == "systemctl":
            output = "amat-terminal.service loaded active running AMAT terminal"
        elif argv[:2] == ["docker", "exec"]:
            output = '{"healthy":true}'
        else:
            raise AssertionError(argv)
        return Mock(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(module, "call", call)
    monkeypatch.setattr(module, "host_observation", lambda: {"memory_available_bytes": 123})
    monkeypatch.setattr(
        module, "backup_observation", lambda now: ({"state": {"status": "fresh"}}, {"stale": False})
    )
    monkeypatch.setattr(
        module,
        "read_perps",
        lambda info, timeout=15: dict(status="observed", mode="paper", live_enabled=False),
    )
    monkeypatch.setattr(
        module,
        "read_executor",
        lambda info, timeout=50: dict(readiness={"ready_for_orders": False, "healthy": True}),
    )
    result = module.execute("health")
    assert result["schema"] == "remote-health/1" and result["execution_authority"] == "none"
    infra = result["infrastructure"]
    assert infra["unexpected_containers"] == ["kalshi-perps-shadow"]
    assert infra["unexpected_listeners"] == ["127.0.0.1:8765"]
    assert infra["unexpected_services"] == ["amat-terminal.service"]
    assert infra["containers"]["kalshi-perps"]["resources"]["MemPerc"] == "28%"
    assert result["perps"]["health"]["healthy"]
    assert result["executor"]["readiness"]["ready_for_orders"] is False
    assert not any(
        any(x in argv for x in ("start", "stop", "restart", "run", "submit", "report"))
        for argv in calls
    )


def test_stopped_engine_diagnostics_never_start_transient_container(monkeypatch):
    module = control()
    calls = []

    def call(argv, timeout=50, check=True):
        calls.append(argv)
        return Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "call", call)
    monkeypatch.setattr(module, "host_observation", lambda: {})
    monkeypatch.setattr(module, "backup_observation", lambda now: ({}, {}))
    result = module.health_observation()
    assert result["perps"]["status"] == "stopped"
    assert result["executor"]["status"] == "stopped"
    assert not any(argv[:2] in (["docker", "exec"], ["docker", "run"]) for argv in calls)


def test_retired_stopped_shadow_is_preserved_without_unexpected_running_alarm(monkeypatch):
    module = control()

    def call(argv, timeout=50, check=True):
        output = "kalshi-perps-shadow\n" if argv[:3] == ["docker", "ps", "-a"] else ""
        return Mock(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(module, "call", call)
    monkeypatch.setattr(module, "host_observation", lambda: {})
    monkeypatch.setattr(module, "backup_observation", lambda now: ({}, {}))
    monkeypatch.setattr(module, "ui_process_observation", lambda: [])
    result = module.health_observation()["infrastructure"]
    assert result["unexpected_containers"] == []
    assert result["inactive_containers"] == ["kalshi-perps-shadow"]


def test_only_exact_managed_backup_names_are_supporting_containers(monkeypatch):
    module = control()
    support = [
        "amat-backup-state-123",
        "amat-backup-perps-42",
        "amat-backup-state-1-cleanup",
        "amat-backup-perps-9-cleanup",
    ]
    unexpected = [
        "amat-backup-state-malicious",
        "amat-backup-state-12-extra",
        "amat-backup-other-12",
        "frontend",
        "kalshi-perps-shadow",
    ]
    names = [*support, *unexpected]

    def call(argv, timeout=50, check=True):
        output = "\n".join(names) if argv[:2] == ["docker", "ps"] else ""
        return Mock(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(module, "call", call)
    monkeypatch.setattr(module, "host_observation", lambda: {})
    monkeypatch.setattr(module, "backup_observation", lambda now: ({}, {}))
    monkeypatch.setattr(module, "ui_process_observation", lambda: [])
    result = module.health_observation()["infrastructure"]
    assert result["supporting_containers"] == support
    assert result["unexpected_containers"] == unexpected
