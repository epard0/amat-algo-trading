"""Live boundary tests use fake credentials and mocked transports only."""

import dataclasses
import json
import urllib.error
from unittest.mock import Mock

import pytest
from tests.test_config import amat_root  # noqa: F401
from tests.test_config import base_env as config_env
from tests.test_daemon import _settings
from tests.test_submit import _account

from executor.broker import AlpacaPaperClient, create_client
from executor.broker.alpaca_live import LIVE_TRADING_HOST, AlpacaLiveClient
from executor.broker.alpaca_paper import _NoRedirect
from executor.config import ExecutorSettings
from executor.errors import ConfigurationError, OrderOutcomeUnknownError
from executor.plan_source import _planner_environment
from executor.safety import bind_trading_mode

base_env = config_env


def live_client(enabled=True):
    return AlpacaLiveClient(
        "fake-live", "fake-secret", account_number=_account().account_number, enabled=enabled
    )


def test_mode_requires_live_credentials_without_paper_fallback(base_env):
    base_env.setenv("TRADING_MODE", "live")
    base_env.setenv("ALPACA_LIVE_ACCOUNT_NUMBER", "expected")
    with pytest.raises(ConfigurationError, match="ALPACA_LIVE_API_KEY"):
        ExecutorSettings.from_environment()


def test_live_configuration_defaults_and_finite_caps(base_env):
    for key, value in {
        "TRADING_MODE": "live",
        "ALPACA_LIVE_API_KEY": "fake-live",
        "ALPACA_LIVE_API_SECRET": "fake-secret",
        "ALPACA_LIVE_ACCOUNT_NUMBER": "expected",
    }.items():
        base_env.setenv(key, value)
    settings = ExecutorSettings.from_environment()
    assert settings.trading_mode == "live"
    assert settings.dry_run
    assert not settings.live_trading_enabled
    assert settings.trading_key_id == "fake-live"
    assert "fake-secret" not in repr(settings)
    base_env.setenv("MAX_SESSION_NOTIONAL", "unlimited")
    with pytest.raises(ConfigurationError, match="finite"):
        ExecutorSettings.from_environment()


def test_unknown_mode_fails(base_env):
    base_env.setenv("TRADING_MODE", "liv")
    with pytest.raises(ConfigurationError, match="TRADING_MODE"):
        ExecutorSettings.from_environment()


def test_hosts_and_redirects_are_fixed():
    paper = AlpacaPaperClient("fake-paper", "fake-secret")
    live = live_client()
    assert paper._build_request("GET", "/v2/account").host == "paper-api.alpaca.markets"
    assert live._build_request("GET", "/v2/account").host == LIVE_TRADING_HOST
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com") is None


@pytest.mark.parametrize(
    ("enabled", "dry_run", "disabled"),
    [(False, False, False), (True, True, False), (True, False, True)],
)
def test_factory_blocks_post_for_every_disabling_gate(tmp_path, enabled, dry_run, disabled):
    settings = dataclasses.replace(
        _settings(tmp_path),
        trading_mode="live",
        live_account_number="expected",
        live_trading_enabled=enabled,
        dry_run=dry_run,
    )
    settings = dataclasses.replace(
        settings,
        limits=dataclasses.replace(settings.limits, disabled=disabled),
        state_root=tmp_path / "fresh-live",
    )
    client = create_client(settings)
    client._read = Mock(side_effect=AssertionError("Unexpected network call"))
    with pytest.raises(ConfigurationError, match="LIVE_TRADING_ENABLED"):
        client._post_once("/v2/orders", {})
    client._read.assert_not_called()


def test_wrong_account_prevents_post(monkeypatch):
    client = live_client()
    monkeypatch.setattr(
        AlpacaPaperClient,
        "get_account",
        lambda _self: dataclasses.replace(_account(), account_number="wrong"),
    )
    transport = Mock()
    monkeypatch.setattr(client, "_read", transport)
    with pytest.raises(ConfigurationError, match="does not match"):
        client._post_once("/v2/orders", {})
    transport.assert_not_called()


def test_blocked_account_prevents_post(monkeypatch):
    client = live_client()
    monkeypatch.setattr(
        AlpacaPaperClient,
        "get_account",
        lambda _self: dataclasses.replace(_account(), trading_blocked=True),
    )
    transport = Mock()
    monkeypatch.setattr(client, "_read", transport)
    with pytest.raises(ConfigurationError, match="not tradable"):
        client._post_once("/v2/orders", {})
    transport.assert_not_called()


@pytest.mark.parametrize(
    "outcome", [TimeoutError(), urllib.error.HTTPError("https://invalid", 503, "busy", {}, None)]
)
def test_live_ambiguous_post_is_never_retried(monkeypatch, outcome):
    client = live_client()
    monkeypatch.setattr(AlpacaPaperClient, "get_account", lambda _self: _account())
    transport = Mock(side_effect=outcome)
    monkeypatch.setattr(client, "_read", transport)
    with pytest.raises(OrderOutcomeUnknownError):
        client._post_once("/v2/orders", {"client_order_id": "stable-id"})
    assert transport.call_count == 1
    assert transport.call_args.args[0].host == LIVE_TRADING_HOST


def test_paper_and_live_cannot_share_state(tmp_path):
    root = tmp_path / "state"
    bind_trading_mode(root, "paper")
    with pytest.raises(ConfigurationError, match="another trading mode"):
        bind_trading_mode(root, "live")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "journal.ndjson").write_text("{}\n")
    with pytest.raises(ConfigurationError, match="fresh isolated"):
        bind_trading_mode(legacy, "live")
    fresh = tmp_path / "live"
    bind_trading_mode(fresh, "live")
    bind_trading_mode(fresh, "live")
    assert json.loads((fresh / "trading-mode.json").read_text()) == {"mode": "live"}
    with pytest.raises(ConfigurationError, match="another trading mode"):
        bind_trading_mode(fresh, "paper")


def test_live_credentials_do_not_reach_planner(monkeypatch):
    for key in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_API_SECRET", "ALPACA_LIVE_ACCOUNT_NUMBER"):
        monkeypatch.setenv(key, "secret")
        assert key not in _planner_environment()
