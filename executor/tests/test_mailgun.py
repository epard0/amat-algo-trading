"""Provider/alert tests use fake transports only; no real email is sent."""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest
from tests.test_daemon import _settings

from executor.alerts import notify
from executor.errors import ConfigurationError
from executor.mailgun import MailgunSettings, _NoRedirect, send_mailgun
from executor.safety import atomic_json


def _mail(region="US"):
    return MailgunSettings(
        domain="mg.example.test",
        api_key="private-test-key",
        region=region,
        sender="AMAT <alerts@mg.example.test>",
        recipients=("operator@example.test",),
    )


def test_api_secret_is_not_in_representation():
    assert "private-test-key" not in repr(_mail())


def test_unconfigured_mailgun_is_optional(monkeypatch):
    monkeypatch.delenv("MAILGUN_DOMAIN", raising=False)
    monkeypatch.delenv("MAILGUN_API_KEY", raising=False)
    assert MailgunSettings.from_environment() is None


def test_partial_mailgun_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("MAILGUN_DOMAIN", "mg.example.test")
    monkeypatch.delenv("MAILGUN_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="Mailgun requires"):
        MailgunSettings.from_environment()


@pytest.mark.parametrize(
    ("region", "host"), [("US", "api.mailgun.net"), ("EU", "api.eu.mailgun.net")]
)
def test_mailgun_uses_correct_region_and_multipart(monkeypatch, region, host):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"id":"accepted-id"}'
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr("executor.mailgun.urllib.request.build_opener", lambda *_: opener)
    accepted, detail = send_mailgun(_mail(region), "test subject", "report body")
    assert accepted
    assert "inbox delivery not independently verified" in detail
    request = opener.open.call_args.args[0]
    assert request.full_url == f"https://{host}/v3/mg.example.test/messages"
    assert request.method == "POST"
    assert b"report body" in request.data
    assert b'"o:tracking"' in request.data
    assert "private-test-key" not in request.full_url
    opener.open.assert_called_once()


def test_mailgun_timeout_is_not_retried_or_reported_delivered(monkeypatch):
    opener = Mock()
    opener.open.side_effect = TimeoutError("could contain sensitive provider text")
    monkeypatch.setattr("executor.mailgun.urllib.request.build_opener", lambda *_: opener)
    accepted, detail = send_mailgun(_mail(), "subject", "body")
    assert not accepted
    assert "unknown" in detail
    assert "sensitive provider text" not in detail
    opener.open.assert_called_once()


def test_mailgun_http_error_does_not_echo_response_or_secret(monkeypatch):
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError("url", 401, "private-test-key", {}, None)
    monkeypatch.setattr("executor.mailgun.urllib.request.build_opener", lambda *_: opener)
    assert send_mailgun(_mail(), "subject", "body") == (False, "Mailgun HTTP 401; not retried")


def test_redirects_cannot_forward_mailgun_authorization():
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test") is None


def test_alerts_deduplicate_across_restarts(monkeypatch, tmp_path):
    settings = replace(_settings(tmp_path), mailgun=_mail())
    settings.ensure_directories()
    send = Mock(return_value=(True, "accepted"))
    monkeypatch.setattr("executor.alerts.send_mailgun", send)
    assert notify(settings, "test-alert", "failure")
    assert not notify(settings, "test-alert", "failure")
    assert send.call_count == 1
    record = json.loads((settings.state_root / "alerts/test-alert.json").read_text())
    assert record["status"] == "accepted"


def test_alert_failure_is_durable_and_does_not_raise(monkeypatch, tmp_path):
    settings = replace(_settings(tmp_path), mailgun=_mail())
    settings.ensure_directories()
    monkeypatch.setattr("executor.alerts.send_mailgun", lambda *_: (False, "HTTP 503"))
    assert not notify(settings, "test-alert", "failure")
    record = json.loads((settings.state_root / "alerts/test-alert.json").read_text())
    assert record["status"] == "failed"


def test_alert_is_eligible_after_repeat_interval(monkeypatch, tmp_path):
    settings = replace(_settings(tmp_path), mailgun=_mail())
    settings.ensure_directories()
    monkeypatch.setattr("executor.alerts.send_mailgun", lambda *_: (True, "accepted"))
    assert notify(settings, "test-alert", "failure")

    path = settings.state_root / "alerts/test-alert.json"
    record = json.loads(path.read_text())
    record["attempted_at"] = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat()
    atomic_json(path, record)
    assert notify(settings, "test-alert", "failure")


def test_unlimited_caps_do_not_block_readiness_when_other_requirements_pass(tmp_path):
    from executor.health import readiness_status

    original = _settings(tmp_path)
    settings = replace(
        original,
        dry_run=False,
        mailgun=_mail(),
        limits=replace(
            original.limits,
            disabled=False,
            max_session_notional=None,
            max_symbol_notional=None,
            max_orders_per_session=None,
        ),
    )
    settings.ensure_directories()
    now = dt.datetime.now(dt.UTC).isoformat()
    atomic_json(
        settings.state_root / "heartbeat.json", {"status": "running", "last_success_at": now}
    )
    atomic_json(
        settings.state_root / "portfolio-monitor.json", {"status": "reconciled", "observed_at": now}
    )
    assert readiness_status(settings)["ready_for_orders"]
    without_notifications = readiness_status(replace(settings, mailgun=None))
    assert without_notifications["ready_for_orders"]
    assert without_notifications["notification_warnings"]
