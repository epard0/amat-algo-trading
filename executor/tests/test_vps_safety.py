"""Fault injection for unattended operation. No live broker or credentials used."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import urllib.error
from decimal import Decimal
from unittest.mock import Mock

import pytest
from tests.test_daemon import _at, _daemon, _FakeClient, _session, _settings
from tests.test_plan_source import _plan_payload
from tests.test_submit import _account, _limits, _now, _RecordingClient

from executor.broker import AlpacaPaperClient
from executor.daemon import Daemon, _Phase, run_daemon
from executor.errors import BrokerError, ConfigurationError, OrderOutcomeUnknownError
from executor.health import health_status
from executor.journal import Journal
from executor.plan_source import _planner_environment, parse_plan
from executor.safety import atomic_json, bind_account, submission_interlock
from executor.submit import _preflight, submit_plan


def _submit(root, client, payload=None, **kwargs):
    return submit_plan(
        parse_plan(payload or _plan_payload()),
        client,
        account=_account(),
        session=dt.date(2026, 8, 11),
        now=_now(),
        journal=Journal(root / "journal.ndjson", root / "equity.ndjson"),
        limits=_limits(),
        **kwargs,
    )


def test_planner_receives_only_runtime_and_market_credentials(monkeypatch):
    for name in (
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_API_SECRET",
        "SMTP_PASSWORD",
        "OPENAI_API_KEY",
        "PYTHONPATH",
        "UV_ENV_FILE",
        "ALPACA_MARKET_API_KEY",
        "ALPACA_MARKET_API_SECRET",
    ):
        monkeypatch.setenv(name, "sensitive-test-value")
    env = _planner_environment()
    assert env["ALPACA_MARKET_API_KEY"] == "sensitive-test-value"
    assert env["ALPACA_MARKET_API_SECRET"] == "sensitive-test-value"
    assert not set(env) & {
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_API_SECRET",
        "SMTP_PASSWORD",
        "OPENAI_API_KEY",
        "PYTHONPATH",
        "UV_ENV_FILE",
    }


def test_runtime_stop_blocks_orders(tmp_path):
    (tmp_path / "STOP_SUBMISSIONS").touch()
    client = _RecordingClient()
    assert "STOP_SUBMISSIONS" in _submit(tmp_path, client).refusal_reason
    assert not client.submitted


def test_disk_pressure_blocks_before_post(tmp_path, monkeypatch):
    monkeypatch.setattr("executor.safety.shutil.disk_usage", lambda _: Mock(free=1024))
    client = _RecordingClient()
    assert "disk space" in _submit(tmp_path, client).refusal_reason
    assert not client.submitted


def test_successful_order_is_not_posted_again_after_restart(tmp_path):
    first = _RecordingClient()
    assert _submit(tmp_path, first).submitted
    second = _RecordingClient()
    result = _submit(tmp_path, second)
    assert not second.submitted
    assert result.outcomes[0].status == "already_submitted"


def test_timeout_stops_remaining_orders_and_future_sessions(tmp_path):
    client = _RecordingClient()
    client.submit_market_order = Mock(side_effect=OrderOutcomeUnknownError("timed out"))
    payload = _plan_payload()
    payload["intents"].append({**payload["intents"][0], "symbol": "QQQ"})
    result = _submit(tmp_path, client, payload)
    assert client.submit_market_order.call_count == 1
    assert result.outcomes[0].status == "outcome_unknown"
    replacement = _RecordingClient()
    assert "unresolved submission" in _submit(tmp_path, replacement).refusal_reason
    assert not replacement.submitted


def test_crash_between_post_and_receipt_acknowledgment_blocks_replay(tmp_path):
    client = _RecordingClient()
    client.submit_market_order = Mock(side_effect=SystemExit("power failure"))
    with pytest.raises(SystemExit):
        _submit(tmp_path, client)
    receipt = next((tmp_path / "submissions").glob("*.json"))
    assert json.loads(receipt.read_text())["status"] == "pending"
    assert "unresolved" in _submit(tmp_path, _RecordingClient()).refusal_reason


def test_stop_file_is_rechecked_between_orders(tmp_path):
    client = _RecordingClient()
    original = client.submit_market_order

    def stop_after_first(**kwargs):
        result = original(**kwargs)
        (tmp_path / "STOP_SUBMISSIONS").touch()
        return result

    client.submit_market_order = stop_after_first
    payload = _plan_payload()
    payload["intents"].append({**payload["intents"][0], "symbol": "QQQ"})
    assert "STOP_SUBMISSIONS" in _submit(tmp_path, client, payload).refusal_reason
    assert len(client.submitted) == 1


def test_rejected_sell_prevents_following_buy(tmp_path):
    client = _RecordingClient()
    client.submit_market_order = Mock(side_effect=BrokerError("insufficient quantity"))
    payload = _plan_payload()
    payload["intents"] = [
        {**payload["intents"][0], "side": "sell"},
        {**payload["intents"][0], "symbol": "QQQ"},
    ]
    assert "rejected" in _submit(tmp_path, client, payload).refusal_reason
    assert client.submit_market_order.call_count == 1


def test_sell_uses_fresh_holdings_not_archived_plan(tmp_path):
    payload = _plan_payload()
    payload["intents"][0]["side"] = "sell"
    client = _RecordingClient()
    _submit(tmp_path, client, payload, current_positions={"SPY": Decimal("2")})
    assert client.submitted[0]["quantity"] == Decimal("2")


def test_expiry_is_rechecked_after_preparation(tmp_path):
    client = _RecordingClient()
    result = _submit(
        tmp_path,
        client,
        submission_time=lambda: dt.datetime(2026, 8, 15, tzinfo=dt.UTC),
    )
    assert "expired" in result.refusal_reason
    assert not client.submitted


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0"])
def test_invalid_intent_size_fails_closed(value):
    payload = _plan_payload()
    payload["intents"][0]["quantity"] = value
    plan = parse_plan(payload)
    assert "invalid intent" in _preflight(
        plan, _account(), plan.earliest_execution_session, _now(), limits=_limits()
    )


def test_understated_notional_cannot_bypass_cap():
    payload = _plan_payload()
    payload["intents"][0].update(quantity="100000", notional="1")
    plan = parse_plan(payload)
    assert "MAX_SESSION_NOTIONAL" in _preflight(
        plan, _account(), plan.earliest_execution_session, _now(), limits=_limits()
    )


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), ValueError("invalid JSON")])
def test_post_transport_failure_is_unknown_and_never_retried(monkeypatch, failure):
    client = AlpacaPaperClient("test-key", "test-secret")
    read = Mock(side_effect=failure)
    monkeypatch.setattr(client, "_read", read)
    with pytest.raises(OrderOutcomeUnknownError):
        client.submit_market_order(
            symbol="SPY", side="buy", quantity=Decimal("1"), client_order_id="x"
        )
    assert read.call_count == 1


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_post_server_error_is_unknown(monkeypatch, status):
    client = AlpacaPaperClient("test-key", "test-secret")
    monkeypatch.setattr(
        client,
        "_read",
        Mock(
            side_effect=urllib.error.HTTPError(
                "https://paper-api.alpaca.markets",
                status,
                "failure",
                {},
                None,
            )
        ),
    )
    with pytest.raises(OrderOutcomeUnknownError):
        client.submit_market_order(
            symbol="SPY", side="buy", quantity=Decimal("1"), client_order_id="x"
        )


def test_malformed_acknowledgment_is_unknown(monkeypatch):
    client = AlpacaPaperClient("test-key", "test-secret")
    monkeypatch.setattr(client, "_post_once", lambda *_args: {})
    with pytest.raises(OrderOutcomeUnknownError):
        client.submit_market_order(
            symbol="SPY", side="buy", quantity=Decimal("1"), client_order_id="x"
        )


def test_saturated_order_query_fails_closed(monkeypatch):
    client = AlpacaPaperClient("test-key", "test-secret")
    monkeypatch.setattr(client, "_get", lambda *_args: [{}])
    with pytest.raises(BrokerError, match="completeness"):
        client.list_orders(limit=1)


def test_corrupt_receipt_is_never_treated_as_missing(tmp_path):
    path = tmp_path / "submissions" / "x.json"
    path.parent.mkdir()
    path.write_text("{")
    with pytest.raises(ConfigurationError, match="unreadable"):
        submission_interlock(tmp_path)


def test_account_binding_survives_move_but_rejects_account_change(tmp_path):
    bind_account(tmp_path, "PA-original")
    bind_account(tmp_path, "PA-original")
    with pytest.raises(ConfigurationError, match="different paper account"):
        bind_account(tmp_path, "PA-other")


@pytest.mark.parametrize("age", [-60, 1801])
def test_health_rejects_future_or_stale_heartbeat(tmp_path, age):
    now = dt.datetime.now(dt.UTC)
    atomic_json(
        tmp_path / "heartbeat.json",
        {
            "status": "running",
            "last_success_at": (now - dt.timedelta(seconds=age)).isoformat(),
        },
    )
    assert not health_status(tmp_path, now=now)["healthy"]


def test_health_requires_successful_progress_and_reports_stop(tmp_path):
    assert not health_status(tmp_path)["healthy"]
    now = dt.datetime.now(dt.UTC)
    atomic_json(
        tmp_path / "heartbeat.json", {"status": "running", "last_success_at": now.isoformat()}
    )
    (tmp_path / "STOP_SUBMISSIONS").touch()
    result = health_status(tmp_path, now=now)
    assert result["healthy"]
    assert "STOP_SUBMISSIONS" in result["submission_interlock"]


def test_second_daemon_cannot_acquire_same_state_root(tmp_path):
    settings = _settings(tmp_path)
    settings.ensure_directories()
    with (settings.state_root / "daemon.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ConfigurationError, match="another daemon"):
            run_daemon(settings)


def test_submission_refuses_clock_skew(tmp_path):
    daemon = _daemon(tmp_path, _FakeClient((), _at("2026-08-11", 9, 30)))
    with pytest.raises(ConfigurationError, match="clock skew"):
        daemon._submission_now(dt.date(2026, 8, 11))


def test_one_shot_submit_enforces_calendar_window(tmp_path, monkeypatch):
    now = dt.datetime.now(dt.UTC)
    daemon = _daemon(tmp_path, _FakeClient((), now))
    monkeypatch.setattr(
        daemon,
        "_phases",
        lambda *_: [
            _Phase("submit", now.date(), now - dt.timedelta(hours=2), now - dt.timedelta(hours=1)),
        ],
    )
    with pytest.raises(ConfigurationError, match="outside"):
        daemon._submission_now(now.date())


def test_retry_count_and_backoff_survive_restart(tmp_path, monkeypatch):
    now = _at("2026-08-20", 10, 0)
    client = _FakeClient((_session("2026-08-20"),), now)
    daemon = _daemon(tmp_path, client)
    monkeypatch.setattr(daemon, "run_submit", Mock(side_effect=RuntimeError("broker down")))
    daemon.tick()
    restarted = Daemon(daemon._settings)
    restarted._load_ledger()
    assert restarted._attempts == {"submit:2026-08-20": 1}
    assert restarted._retry_not_before["submit:2026-08-20"] > now


@pytest.mark.parametrize("found", [False, True])
def test_reconciliation_never_clears_an_absent_order(tmp_path, found):
    from executor.safety import reconcile_submissions

    client = _RecordingClient()
    order = client.submit_market_order(
        symbol="SPY",
        side="buy",
        quantity=Decimal("1"),
        client_order_id="test-order",
    )
    path = tmp_path / "submissions/test-order.json"
    atomic_json(
        path,
        {
            "status": "unknown",
            "client_order_id": "test-order",
            "symbol": "SPY",
            "side": "buy",
            "quantity": "1",
        },
    )
    broker = Mock()
    broker.get_account.return_value = _account()
    broker.get_order_by_client_id.return_value = order if found else None
    result = reconcile_submissions(tmp_path, broker)
    assert result["resolved" if found else "unresolved"] == ["test-order"]
    assert json.loads(path.read_text())["status"] == ("submitted" if found else "unknown")
    broker.submit_market_order.assert_not_called()


def test_watchdog_exits_stalled_process_without_broker_actions(monkeypatch):
    from executor.daemon import _watch_progress

    stop = Mock()
    stop.wait.side_effect = [False, True]
    exit_process = Mock()
    monkeypatch.setattr("executor.daemon.os._exit", exit_process)
    monkeypatch.setattr("executor.daemon.time.monotonic", lambda: 1801)
    _watch_progress([0], stop)
    exit_process.assert_called_once_with(1)


@pytest.mark.parametrize(
    ("status", "age"), [("discrepancy", 0), ("reconciled", 721), ("reconciled", -1)]
)
def test_unreconciled_or_stale_portfolio_cannot_authorize_orders(tmp_path, status, age):
    from executor.safety import require_reconciled_portfolio

    now = dt.datetime.now(dt.UTC)
    atomic_json(
        tmp_path / "portfolio-monitor.json",
        {
            "status": status,
            "observed_at": (now - dt.timedelta(seconds=age)).isoformat(),
        },
    )
    with pytest.raises(ConfigurationError, match="reconciled portfolio"):
        require_reconciled_portfolio(tmp_path, now)


def test_healthy_loop_records_progress_then_stopped_state(tmp_path, monkeypatch):
    daemon = _daemon(tmp_path, _FakeClient((), dt.datetime.now(dt.UTC)))

    def tick():
        daemon._stop.set()
        return 0

    monkeypatch.setattr(daemon, "tick", tick)
    assert daemon.run_forever() == 0
    payload = json.loads((daemon._settings.state_root / "heartbeat.json").read_text())
    assert payload["status"] == "stopped"
    assert payload["last_success_at"]


def test_shutdown_signal_prevents_any_new_submission(tmp_path):
    daemon = _daemon(tmp_path, _FakeClient((), dt.datetime.now(dt.UTC)))
    daemon._stop.set()
    with pytest.raises(ConfigurationError, match="shutdown requested"):
        daemon._submission_now(dt.datetime.now(dt.UTC).date())


def test_concurrent_report_writer_is_refused(tmp_path):
    daemon = _daemon(tmp_path, _FakeClient((), dt.datetime.now(dt.UTC)))
    with (daemon._settings.state_root / "report.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ConfigurationError, match="another report"):
            daemon.run_report(dt.date(2026, 8, 11))


def test_readiness_does_not_confuse_healthy_disabled_monitor_with_trading(tmp_path):
    from dataclasses import replace

    from executor.health import readiness_status

    settings = _settings(tmp_path)
    settings.ensure_directories()
    atomic_json(
        settings.state_root / "heartbeat.json",
        {
            "status": "running",
            "last_success_at": dt.datetime.now(dt.UTC).isoformat(),
        },
    )
    settings = replace(
        settings, limits=replace(settings.limits, disabled=True, max_session_notional=None)
    )
    result = readiness_status(settings)
    assert result["healthy"]
    assert not result["ready_for_orders"]
    assert result["execution_caps"]["session_notional"] == "unlimited"
    assert not any("unlimited" in issue for issue in result["order_readiness_issues"])
