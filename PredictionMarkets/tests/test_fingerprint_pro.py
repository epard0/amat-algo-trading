"""Fingerprint Pro: the rejection rules, the key hygiene, and the CSP.

Every test here is offline. The Server API is never called; `fetch_event` takes
an opener so the HTTP layer can be replaced with a function.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error

import pytest

from storefront.fingerprint_pro import (
    MAX_EVENT_AGE_SECONDS,
    FingerprintConfig,
    fetch_event,
    parse_event,
)
from storefront.security import CSP, extend_csp


def event(**overrides) -> dict:
    body = {
        "event_id": "1784375313929.aVJwoG",
        "timestamp": int(time.time() * 1000),
        "replayed": False,
        "identification": {
            "visitor_id": "9vlLQmi383ZgqexiEq0E",
            "confidence": {"score": 0.99},
            "visitor_found": True,
        },
        "bot": "not_detected",
        "vpn": False,
        "proxy": False,
        "incognito": False,
        "tampering": False,
        "virtual_machine": False,
        "suspect_score": 0,
        "ip_address": "203.0.113.7",
        "ip_info": {"v4": {"geolocation": {"country_code": "US"}}},
        "velocity": {"events": {"24_hours": 3}},
    }
    body.update(overrides)
    return body


# ----------------------------------------------------------------- parsing ---

def test_a_clean_event_verifies() -> None:
    verdict = parse_event(event())
    assert verdict.verified is True
    assert verdict.visitor_id == "9vlLQmi383ZgqexiEq0E"
    assert verdict.confidence == pytest.approx(0.99)
    assert verdict.is_bot is False
    assert verdict.country == "US"
    assert verdict.events_24h == 3


def test_a_stale_event_is_refused() -> None:
    """An old event id is a clean identification from some other session."""
    old = event(timestamp=int((time.time() - MAX_EVENT_AGE_SECONDS - 60) * 1000))
    verdict = parse_event(old)
    assert verdict.verified is False
    assert verdict.reason.startswith("stale_event")


def test_an_event_just_inside_the_window_is_accepted() -> None:
    fresh = event(timestamp=int((time.time() - MAX_EVENT_AGE_SECONDS + 30) * 1000))
    assert parse_event(fresh).verified is True


def test_a_replayed_event_is_refused() -> None:
    verdict = parse_event(event(replayed=True))
    assert verdict.verified is False
    assert verdict.reason == "replayed"


def test_an_event_without_a_visitor_id_proves_nothing() -> None:
    assert parse_event({"event_id": "x"}).reason == "no_visitor_id"


def test_a_detected_bot_is_a_bot_whichever_kind() -> None:
    for label in ("good", "bad"):
        assert parse_event(event(bot=label)).is_bot is True
    assert parse_event(event(bot="not_detected")).is_bot is False


def test_signals_survive_the_round_trip() -> None:
    verdict = parse_event(event(vpn=True, proxy=True, incognito=True,
                                tampering=True, virtual_machine=True,
                                suspect_score=71))
    assert (verdict.vpn, verdict.proxy, verdict.incognito) == (True, True, True)
    assert verdict.tampering is True
    assert verdict.virtual_machine is True
    assert verdict.suspect_score == 71


def test_a_garbled_body_does_not_raise() -> None:
    assert parse_event({}).verified is False
    assert parse_event({"identification": "nonsense"}).verified is False
    assert parse_event({"identification": {"visitor_id": "v"},
                        "timestamp": "not a number"}).verified is True


# ------------------------------------------------------------------ config ---

def test_without_a_public_key_nothing_is_configured(monkeypatch) -> None:
    for name in ("FINGERPRINT_PUBLIC_API_KEY", "FINGERPRINT_API_KEY",
                 "FINGERPRINT_SECRET_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config = FingerprintConfig.from_env()
    assert config.configured is False
    assert config.csp_sources() == {}


def test_the_bare_alias_is_accepted_as_the_public_key(monkeypatch) -> None:
    monkeypatch.delenv("FINGERPRINT_PUBLIC_API_KEY", raising=False)
    monkeypatch.setenv("FINGERPRINT_API_KEY", "pk_from_the_quickstart")
    assert FingerprintConfig.from_env().public_key == "pk_from_the_quickstart"


def test_the_public_key_wins_over_the_alias(monkeypatch) -> None:
    monkeypatch.setenv("FINGERPRINT_PUBLIC_API_KEY", "explicit")
    monkeypatch.setenv("FINGERPRINT_API_KEY", "alias")
    assert FingerprintConfig.from_env().public_key == "explicit"


def test_the_same_value_in_both_keys_switches_the_feature_off(monkeypatch) -> None:
    """One key in both slots: nobody knows which kind it is, and one of the two
    possibilities publishes a secret key in a public page. So it is used in
    neither position — and the shop still starts, because this is an optional
    signal and taking the storefront down over it is a self-inflicted outage."""
    monkeypatch.setenv("FINGERPRINT_PUBLIC_API_KEY", "same")
    monkeypatch.setenv("FINGERPRINT_SECRET_API_KEY", "same")
    config = FingerprintConfig.from_env()
    assert config.configured is False
    assert config.public_key == ""
    assert config.secret_key == ""
    assert config.csp_sources() == {}
    assert "dashboard" in config.warning


def test_an_ambiguous_key_never_reaches_the_page(monkeypatch) -> None:
    """The end-to-end version of the rule above."""
    pytest.importorskip("flask")
    from storefront.app import create_app
    import tempfile
    from pathlib import Path as _Path

    monkeypatch.setenv("FINGERPRINT_PUBLIC_API_KEY", "ambiguous-key-value")
    monkeypatch.setenv("FINGERPRINT_SECRET_API_KEY", "ambiguous-key-value")
    tmp = _Path(tempfile.mkdtemp())
    app = create_app(db_path=tmp / "s.db", artifact_path=tmp / "none.tar.gz")
    client = app.test_client()
    assert client.get("/api/config").get_json()["fingerprint"]["enabled"] is False
    for route in ("/", "/api/config"):
        assert b"ambiguous-key-value" not in client.get(route).get_data()


def test_an_unknown_region_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("FINGERPRINT_PUBLIC_API_KEY", "pk")
    monkeypatch.setenv("FINGERPRINT_REGION", "antarctica")
    with pytest.raises(ValueError, match="FINGERPRINT_REGION"):
        FingerprintConfig.from_env()


def test_each_region_has_its_own_api_origin() -> None:
    origins = {
        FingerprintConfig(public_key="pk", region=r).api_origin
        for r in ("us", "eu", "ap")
    }
    assert len(origins) == 3


# --------------------------------------------------------------------- CSP ---

def test_the_policy_is_untouched_while_fingerprint_is_off() -> None:
    """Byte for byte: an unused integration must cost no policy strength."""
    assert extend_csp(CSP, FingerprintConfig().csp_sources()) == CSP


def test_the_policy_gains_exactly_what_the_agent_needs() -> None:
    policy = extend_csp(CSP, FingerprintConfig(public_key="pk").csp_sources())
    assert "script-src 'self' https://fpjscdn.net" in policy
    assert "https://api.fpjs.io" in policy
    assert "https://*.api.fpjs.io" in policy
    # worker-src is absent from the base policy, so it has to be appended: a
    # missing directive falls back to default-src and blocks the Blob worker.
    assert "worker-src blob:" in policy
    # And nothing else moved.
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "style-src 'self';" in policy


def test_extending_is_idempotent() -> None:
    sources = FingerprintConfig(public_key="pk").csp_sources()
    once = extend_csp(CSP, sources)
    assert extend_csp(once, sources) == once


# --------------------------------------------------------------- fetching ---

def _opener(body: dict | None = None, error: Exception | None = None):
    def open_url(request, timeout=None):  # noqa: ARG001
        if error is not None:
            raise error

        class _Response:
            def read(self):
                return json.dumps(body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()
    return open_url


CONFIGURED = FingerprintConfig(public_key="pk", secret_key="sk", region="us")


def test_fetch_returns_a_verdict_from_the_server_api() -> None:
    verdict = fetch_event("e1", CONFIGURED, opener=_opener(event()))
    assert verdict.verified is True


def test_fetch_without_a_secret_key_says_so_rather_than_guessing() -> None:
    config = FingerprintConfig(public_key="pk")
    assert fetch_event("e1", config).reason == "no_secret_key"


def test_a_403_names_the_likely_cause() -> None:
    error = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))
    verdict = fetch_event("e1", CONFIGURED, opener=_opener(error=error))
    assert verdict.verified is False
    assert "secret key" in verdict.reason and "FINGERPRINT_REGION" in verdict.reason


def test_an_unreachable_api_fails_open() -> None:
    error = urllib.error.URLError("connection refused")
    verdict = fetch_event("e1", CONFIGURED, opener=_opener(error=error))
    assert verdict.verified is False
    assert verdict.reason.startswith("unreachable")


def test_a_timeout_fails_open() -> None:
    verdict = fetch_event("e1", CONFIGURED, opener=_opener(error=TimeoutError()))
    assert verdict.verified is False


def test_an_absurd_event_id_is_rejected_before_the_network() -> None:
    called = []

    def open_url(request, timeout=None):  # noqa: ARG001
        called.append(request)
        raise AssertionError("should not reach the network")

    assert fetch_event("x" * 500, CONFIGURED, opener=open_url).reason == "bad_event_id"
    assert fetch_event("", CONFIGURED, opener=open_url).reason == "bad_event_id"
    assert not called


def test_nothing_the_browser_claims_is_read() -> None:
    """The client sends an event id; every fact comes from the Server API.

    A page that posts a flattering visitor id is describing a value nobody
    reads, and this is the test that keeps it that way.
    """
    lying = event(bot="not_detected")
    truth = event(bot="bad", tampering=True)
    # Same event id, different server answers: only the server answer matters.
    assert fetch_event("e1", CONFIGURED, opener=_opener(lying)).is_bot is False
    assert fetch_event("e1", CONFIGURED, opener=_opener(truth)).is_bot is True
