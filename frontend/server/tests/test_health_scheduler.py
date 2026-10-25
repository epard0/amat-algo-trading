import datetime as dt
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from server import vps_actions
from server.app import Service
from server.health_scheduler import RemoteHealthScheduler, issues_for, optional_work_allowed


def good():
    return dict(
        transport="connected",
        infrastructure=dict(
            host=dict(
                memory_available_bytes=400 * 1024**2,
                memory_total_bytes=1024**3,
                cpu_count=1,
                load_1_5_15=[0.2, 0.1, 0.1],
                disk_total_bytes=1000,
                disk_free_bytes=500,
                inodes_total=100,
                inodes_free=50,
            ),
            containers={"amat-executor": dict(running=True, health="healthy")},
            backups={"state": dict(status="fresh")},
            paper_monitor=dict(stale=False),
        ),
    )


def scheduler(value=None):
    service = SimpleNamespace(event=Mock())
    engines = SimpleNamespace(
        refresh=Mock(return_value=value or good()), describe=Mock(return_value=value or good())
    )
    diagnostics = SimpleNamespace(start=Mock())
    clock = [0.0]
    item = RemoteHealthScheduler(
        service, engines, diagnostics, clock=lambda: clock[0], jitter=lambda: 0
    )
    return item, service, engines, diagnostics, clock


def test_api_owned_scheduler_uses_cadence_and_transition_events_only():
    item, service, engines, diagnostics, clock = scheduler()
    item.tick()
    engines.refresh.assert_called_once()
    diagnostics.start.assert_called_once_with(["status", "performance", "logs"])
    assert item.next_due == 60
    clock[0] = 59
    item.tick()
    assert engines.refresh.call_count == 1
    clock[0] = 60
    item.tick()
    assert engines.refresh.call_count == 2
    assert diagnostics.start.call_count == 1
    assert service.event.call_count == 1
    item.describe()
    assert engines.refresh.call_count == 2
    clock[0] = 300
    item.tick()
    assert diagnostics.start.call_count == 2


def test_outage_backoff_is_capped_and_recovers_without_execution_effects():
    item, service, engines, diagnostics, clock = scheduler({"transport": "unavailable"})
    for expected in (120, 240, 300, 300):
        now = clock[0]
        item.tick()
        assert item.next_due == now + expected
        clock[0] = item.next_due
    assert service.event.call_count == 1
    diagnostics.start.assert_not_called()
    engines.refresh.return_value = good()
    item.tick()
    assert item.failures == 0 and item.next_due == clock[0] + 60
    assert service.event.call_args.args[1]["recovered"] is True
    # Scheduler has no lifecycle/order adapter and only three named read-only checks.
    assert diagnostics.start.call_args.args[0] == ["status", "performance", "logs"]


def test_pressure_missing_measurements_and_intentional_stop_defer_heavy_work():
    observation = good()
    host = observation["infrastructure"]["host"]
    host.update(memory_available_bytes=10 * 1024**2, disk_free_bytes=1, inodes_free=1)
    observation["infrastructure"]["containers"]["amat-executor"].update(
        running=False, status="exited", oom_killed=True
    )
    issues = issues_for(observation)
    assert any("memory headroom" in x for x in issues)
    assert any("operator stop is preserved" in x for x in issues)
    assert any("disk space" in x for x in issues)
    assert any("inode" in x for x in issues)
    assert optional_work_allowed(observation) is False
    assert optional_work_allowed({"transport": "connected"}) is False
    item, _, _, diagnostics, _ = scheduler(observation)
    item.tick()
    diagnostics.start.assert_not_called()
    assert "deferred" in item.heavy_status


def test_single_flight_stop_prevents_new_heavy_work_and_new_checks():
    item, service, engines, diagnostics, _ = scheduler()
    entered, release = threading.Event(), threading.Event()

    def refresh():
        entered.set()
        release.wait(timeout=3)
        return good()

    engines.refresh.side_effect = refresh
    thread = threading.Thread(target=item.tick)
    thread.start()
    assert entered.wait(timeout=1)
    item.tick()
    assert engines.refresh.call_count == 1
    item.stop()
    release.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    diagnostics.start.assert_not_called()
    service.event.assert_not_called()
    item.tick()
    assert engines.refresh.call_count == 1


def test_scheduler_start_stop_is_bound_to_api_not_browser(monkeypatch):
    monkeypatch.setenv("TERMINAL_PERPS_TARGET", "remote")
    item, _, engines, _, _ = scheduler()
    item.start()
    item.start()
    item.stop()
    calls = engines.refresh.call_count
    assert not item.describe()["scheduler"]["running"]
    item.tick()
    assert engines.refresh.call_count == calls


def test_remote_cache_read_does_not_connect_and_rejects_bad_or_nonpaper_contract(
    monkeypatch, tmp_path
):
    service = Service(root=tmp_path, storage=tmp_path / "store")
    cache = vps_actions.RemoteEngines(service)
    runner = Mock()
    monkeypatch.setattr(vps_actions, "Runner", lambda _: runner)
    monkeypatch.setattr(vps_actions, "load_config", lambda _: {})
    assert cache.describe()["stale"]
    runner.connect.assert_not_called()
    observation = {**good(), "schema": "remote-health/1", "execution_authority": "none"}
    runner.execute.return_value = dict(exit_code=0, truncated=False, data=observation)
    result = cache.refresh()
    assert result["transport"] == "connected" and not result["stale"]
    assert runner.execute.call_args.args[1] == "health_summary"
    previous = result["refreshed_at"]
    runner.execute.return_value = dict(
        exit_code=0, truncated=False, data={**observation, "execution_authority": "submit"}
    )
    result = cache.refresh()
    assert result["transport"] == "unavailable" and result["stale"]
    assert result["refreshed_at"] == previous
    assert result["infrastructure"] == observation["infrastructure"]
    runner.execute.return_value = dict(
        exit_code=0,
        truncated=False,
        data={**observation, "perps": dict(status="observed", mode="live", live_enabled=True)},
    )
    assert cache.refresh()["transport"] == "unavailable"


def test_cached_freshness_does_not_turn_old_success_into_current_health(tmp_path):
    service = Service(root=tmp_path, storage=tmp_path / "store")
    cache = vps_actions.RemoteEngines(service)
    cache.cached = {
        **good(),
        "refreshed_at": (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=4)).isoformat(),
    }
    assert cache.describe()["stale"]
    cache.cached["refreshed_at"] = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30)).isoformat()
    assert cache.describe()["stale"]


def test_inventory_backup_native_health_and_resource_issues_are_distinct():
    value = good()
    value["executor"] = dict(health=dict(healthy=True), readiness=dict(ready_for_orders=False))
    value["perps"] = dict(health=dict(healthy=False, responsive=True, state="data_blocked"))
    infra = value["infrastructure"]
    infra.update(
        unexpected_containers=["retired-shadow"],
        unexpected_listeners=["127.0.0.1:8765"],
        backups={"perps": dict(status="stale")},
    )
    infra["containers"]["amat-executor"]["resources"] = dict(MemPerc="92.3%")
    issues = issues_for(value)
    assert any("readiness" in x for x in issues)
    assert any("perps native" in x for x in issues)
    assert any("85%" in x for x in issues)
    assert any("backup is stale" in x for x in issues)
    assert any("Unexpected listeners" in x for x in issues)


def test_refresh_while_scheduler_paused_is_explicitly_rejected():
    item, _, engines, diagnostics, _ = scheduler()
    result = item.request_refresh()
    assert result["accepted"] is False
    assert "TERMINAL_PERPS_TARGET=remote" in result["reason"]
    engines.refresh.assert_not_called()
    diagnostics.start.assert_not_called()


def test_stopped_engine_preserves_dated_metrics_without_claiming_native_health(
    monkeypatch, tmp_path
):
    service = Service(root=tmp_path, storage=tmp_path / "store")
    cache = vps_actions.RemoteEngines(service)
    cache.cached = dict(
        perps=dict(
            status="observed",
            mode="paper",
            live_enabled=False,
            observed_at="2026-10-23T12:00:00+00:00",
            accounts=[{"equity": "10000"}],
            positions={"DOGE": "1"},
            health={"healthy": True},
        )
    )
    runner = Mock()
    monkeypatch.setattr(vps_actions, "Runner", lambda _: runner)
    monkeypatch.setattr(vps_actions, "load_config", lambda _: {})
    runner.execute.return_value = dict(
        exit_code=0,
        truncated=False,
        data={
            **good(),
            "schema": "remote-health/1",
            "execution_authority": "none",
            "perps": dict(status="stopped", deployment={"running": False, "status": "exited"}),
        },
    )
    result = cache.refresh()
    assert result["transport"] == "connected"
    assert result["perps"]["stale"]
    assert result["perps"]["status"] == "stopped"
    assert result["perps"]["observed_at"] == "2026-10-23T12:00:00+00:00"
    assert result["perps"]["accounts"] == [{"equity": "10000"}]
    assert result["perps"]["positions"] == {"DOGE": "1"}
    assert "health" not in result["perps"]
