import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

from kalshi_perps.operations import diagnostic, notify, report_alert


def health_module():
    path = Path(__file__).resolve().parents[1] / "deploy/health.py"
    spec = importlib.util.spec_from_file_location("paper_health", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blocked_data_is_responsive_but_not_healthy_and_stalled_is_neither():
    health = health_module()
    report = dict(
        updated_at="2026-10-22T20:00:00-04:00",
        state="data_blocked",
        mode="paper",
        live_enabled=False,
    )
    now = health.dt.datetime.fromisoformat(report["updated_at"]).timestamp()
    with patch.object(health.Path, "read_text", return_value=json.dumps(report)):
        with patch.object(health.time, "time", return_value=now + 30):
            assert health.health()["responsive"] is True
            assert health.health()["healthy"] is False
        with patch.object(health.time, "time", return_value=now + 181):
            assert health.health()["responsive"] is False
    report["state"] = "running"
    with patch.object(health.Path, "read_text", return_value=json.dumps(report)):
        with patch.object(health.time, "time", return_value=now + 30):
            assert health.health()["healthy"] is True


def test_diagnostics_do_not_log_arbitrary_exception_urls():
    result = diagnostic(ValueError("https://private.example/token-secret"), "native-snapshot")
    assert result["error_reason"] == "unclassified_ValueError"
    assert "token-secret" not in json.dumps(result)
    assert (
        diagnostic(ValueError("stale liquidation mark"), "native-snapshot")["error_reason"]
        == "stale_liquidation_mark"
    )


def test_alert_dedup_survives_restart_and_failed_sends_do_not_leak_token(tmp_path):
    calls = []

    def send(request):
        calls.append(request)
        return {"ok": True, "result": {"message_id": 1}}

    with patch.dict(os.environ, TELEGRAM_BOT_TOKEN="123:secret", TELEGRAM_CHAT_ID="42"):
        assert notify(tmp_path, "test", "blocked", now=100, send=send)
        assert not notify(tmp_path, "test", "blocked", now=101, send=send)
        assert notify(tmp_path, "test", "blocked", now=3700, send=send)
        assert len(calls) == 2

        def fail(request):
            raise OSError("https://token-secret")

        assert not notify(tmp_path, "failure", "blocked", now=100, send=fail)
        assert "token-secret" not in (tmp_path / "alerts/failure.json").read_text()


def test_blocked_and_recovery_notifications_preserve_transition(tmp_path):
    with patch("kalshi_perps.operations.notify") as send:
        report_alert(tmp_path, dict(state="data_blocked", updated_at="first"))
        assert send.call_args.args[1] == "data-or-account-blocked"
        report_alert(tmp_path, dict(state="running", updated_at="second"))
        assert send.call_args.args[1] == "recovered"
        count = send.call_count
        report_alert(tmp_path, dict(state="running", updated_at="third"))
        assert send.call_count == count


def test_snapshot_recollects_once_without_weakening_freshness(btc):
    from unittest.mock import Mock

    import pytest

    from kalshi_perps.marketdata import Feed

    feed = Feed(Mock(), {btc.ticker: btc})
    feed._snapshot = Mock(side_effect=[ValueError("stale liquidation mark"), {"fresh": True}])
    assert feed.snapshot(15000) == {"fresh": True}
    assert feed._snapshot.call_count == 2
    feed._snapshot.assert_called_with(15000)
    feed._snapshot = Mock(side_effect=ValueError("stale liquidation mark"))
    with pytest.raises(ValueError, match="stale liquidation mark"):
        feed.snapshot(15000)
    assert feed._snapshot.call_count == 2
    feed._snapshot = Mock(
        side_effect=ValueError("market metadata changed; revalidate registry before resuming")
    )
    with pytest.raises(ValueError, match="market metadata changed"):
        feed.snapshot(15000)
    assert feed._snapshot.call_count == 1


def test_slow_strategy_cannot_execute_expired_forward_quotes():
    import pytest

    from kalshi_perps.adx_forward import require_fresh_execution

    with patch("kalshi_perps.adx_forward.time.time", return_value=100):
        require_fresh_execution({"execution_deadline_ms": 100000})
        with pytest.raises(ValueError, match="stale snapshot before execution"):
            require_fresh_execution({"execution_deadline_ms": 99999})
