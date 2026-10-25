import json
import urllib.error
from dataclasses import replace
from unittest.mock import MagicMock

from executor.alerts import notify
from executor.telegram import TelegramSettings, send_telegram
from test_daemon import _settings


def test_optional_incomplete_notification_cannot_block_startup(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert TelegramSettings.from_environment() is None
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    settings = TelegramSettings.from_environment()
    assert settings is not None
    assert "secret" not in repr(settings)


def test_plain_text_bounded_message_and_timeout(monkeypatch):
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = (
        b'{"ok":true,"result":{"message_id":1}}'
    )
    monkeypatch.setattr("executor.telegram.urllib.request.build_opener", lambda *_: opener)
    assert send_telegram(TelegramSettings("123:secret", "456"), "test", "🚀" * 5000)[0]
    request = opener.open.call_args.args[0]
    payload = json.loads(request.data)
    assert payload["chat_id"] == "456"
    assert len(payload["text"].encode("utf-16-le")) <= 8192
    assert "parse_mode" not in payload
    assert opener.open.call_args.kwargs["timeout"] == 5


def test_transport_failure_never_leaks_token_or_retries(monkeypatch):
    opener = MagicMock()
    opener.open.side_effect = urllib.error.URLError("https://token-secret-in-url")
    monkeypatch.setattr("executor.telegram.urllib.request.build_opener", lambda *_: opener)
    accepted, detail = send_telegram(TelegramSettings("123:secret", "456"), "test", "body")
    assert not accepted
    assert "secret" not in detail
    assert opener.open.call_count == 1


def test_telegram_alert_failure_is_nonfatal_and_persistently_suppressed(tmp_path, monkeypatch):
    settings = replace(_settings(tmp_path), telegram=TelegramSettings("123:secret", "456"))
    send = MagicMock(return_value=(False, "Telegram HTTP 429; not retried"))
    monkeypatch.setattr("executor.alerts.send_telegram", send)
    assert not notify(settings, "test", "issue")
    assert not notify(settings, "test", "issue")
    assert send.call_count == 1
    assert json.loads((settings.state_root / "alerts/test.json").read_text())["status"] == "failed"
