"""Licence-sharing detection: the signals, the bands, and the exemptions.

The tests that matter most here are the ones about not blocking. A false
positive reaches somebody who paid, possibly while they hold a position, so
every rule that keeps a legitimate customer running is pinned down.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from storefront.fraud import (
    RETENTION_DAYS,
    DeviceReport,
    FraudInputError,
    RiskEngine,
    RiskPolicy,
    ip_prefix_hash,
)
from storefront.licenses import LicenseStore

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
ISSUE = dict(
    stripe_session_id="cs_1", email="a@b.test", payment_intent="pi_1",
    amount_cents=10000, currency="usd", livemode=True,
)


@pytest.fixture
def store(tmp_path) -> LicenseStore:
    return LicenseStore(tmp_path / "s.db", secret="unit-test-secret-of-sufficient-length")


@pytest.fixture
def engine(store: LicenseStore) -> RiskEngine:
    return RiskEngine(store, RiskPolicy())


@pytest.fixture
def licence(store: LicenseStore) -> str:
    return store.issue(**ISSUE).key_hash


def device(machine: str, **overrides) -> DeviceReport:
    return DeviceReport(
        machine_hash=machine.ljust(64, "0")[:64],
        os_family=overrides.get("os_family", "linux"),
        arch=overrides.get("arch", "x86_64"),
        app_version=overrides.get("app_version", "2.0.0"),
        tz_offset_minutes=overrides.get("tz", 0),
    )


def seen(engine: RiskEngine, licence: str, report: DeviceReport, *, ip="1.2.3.4", at=NOW):
    decision = engine.assess(licence, report, ip=ip, now=at)
    engine.record(licence, report, decision, ip=ip, now=at)
    return decision


# ------------------------------------------------------------- the report ---

def test_a_report_must_carry_a_real_digest() -> None:
    for bad in [{}, {"machine_hash": "short"}, {"machine_hash": "zz" * 32}, None, []]:
        with pytest.raises(FraudInputError):
            DeviceReport.from_payload(bad)


def test_unknown_fields_are_clamped_not_trusted() -> None:
    """The client is the adversary; nothing it sends is taken at face value."""
    report = DeviceReport.from_payload({
        "machine_hash": "a" * 64,
        "os_family": "PlanNine",
        "arch": "x" * 500,
        "app_version": "'; DROP TABLE licenses;--",
        "tz_offset_minutes": 10 ** 9,
    })

    assert report.os_family == "other"
    assert len(report.arch) <= 32
    assert ";" not in report.app_version
    assert report.tz_offset_minutes == 840      # +14:00, the real maximum


def test_the_ip_is_reduced_to_a_network() -> None:
    """Same house, different lease; and never the address itself."""
    a = ip_prefix_hash("203.0.113.7", "s")
    b = ip_prefix_hash("203.0.113.200", "s")
    c = ip_prefix_hash("198.51.100.7", "s")

    assert a == b
    assert a != c
    assert "203.0.113" not in a


def test_the_network_hash_is_keyed() -> None:
    assert ip_prefix_hash("203.0.113.7", "one") != ip_prefix_hash("203.0.113.7", "two")


def test_ipv6_is_reduced_to_a_prefix() -> None:
    a = ip_prefix_hash("2001:db8:abcd:1::1", "s")
    b = ip_prefix_hash("2001:db8:abcd:9999::5", "s")

    assert a == b


def test_a_garbled_address_does_not_raise() -> None:
    assert ip_prefix_hash("not-an-ip", "s")


# ------------------------------------------------------- ordinary customers --

def test_the_first_activation_is_always_allowed(engine, licence) -> None:
    """There is nothing to compare a first activation against."""
    decision = seen(engine, licence, device("aa"))

    assert decision.allow is True
    assert decision.band == "allow"
    assert decision.score == 0


def test_a_laptop_and_a_desktop_are_not_suspicious(engine, licence) -> None:
    seen(engine, licence, device("aa"))
    decision = seen(engine, licence, device("bb"), at=NOW + timedelta(days=1))

    assert decision.band == "allow"


def test_a_laptop_a_desktop_and_a_vps_are_not_suspicious(engine, licence) -> None:
    """Running the bot on a cheap VPS is the documented deployment."""
    seen(engine, licence, device("aa"))
    seen(engine, licence, device("bb"), at=NOW + timedelta(days=2))
    decision = seen(engine, licence, device("cc"), ip="198.51.100.9",
                    at=NOW + timedelta(days=4))

    assert decision.allow is True
    assert decision.band == "allow"


def test_the_same_machine_checking_in_repeatedly_stays_quiet(engine, licence) -> None:
    for day in range(20):
        decision = seen(engine, licence, device("aa"), at=NOW + timedelta(days=day))

    assert decision.band == "allow"
    assert len(engine.store.devices_for(licence)) == 1


def test_travelling_with_one_laptop_is_not_flagged_as_sharing(engine, licence) -> None:
    """One machine, four countries, a month apart. That is a holiday."""
    for index, offset in enumerate((0, 60, 480, -300)):
        decision = seen(engine, licence, device("aa", tz=offset),
                        ip=f"198.51.{index}.4", at=NOW + timedelta(days=index * 3))

    assert decision.allow is True


# ---------------------------------------------------------------- sharing ---

def test_a_key_on_many_machines_is_blocked(engine, licence) -> None:
    for index in range(6):
        decision = seen(engine, licence, device(f"m{index}", tz=index * 60),
                        ip=f"203.0.{index}.5", at=NOW + timedelta(hours=index * 30))

    assert decision.allow is False
    assert decision.band == "block"
    assert any(s.name == "device_count" for s in decision.signals)


def test_a_burst_of_new_machines_scores(engine, licence) -> None:
    """Four installs in an afternoon is a link being passed around."""
    for index in range(4):
        decision = seen(engine, licence, device(f"m{index}"),
                        at=NOW + timedelta(minutes=index * 45))

    assert any(s.name == "new_device_burst" for s in decision.signals)


def test_two_machines_at_once_is_flagged(engine, licence) -> None:
    seen(engine, licence, device("aa"))
    decision = seen(engine, licence, device("bb"), at=NOW + timedelta(minutes=2))

    assert any(s.name == "concurrent_use" for s in decision.signals)


def test_a_timezone_jump_too_fast_to_fly_is_flagged(engine, licence) -> None:
    seen(engine, licence, device("aa", tz=0))
    decision = seen(engine, licence, device("bb", tz=480),
                    at=NOW + timedelta(hours=1))

    assert any(s.name == "impossible_travel" for s in decision.signals)


def test_three_operating_systems_on_one_key_is_flagged(engine, licence) -> None:
    seen(engine, licence, device("aa", os_family="linux"))
    seen(engine, licence, device("bb", os_family="darwin"),
         at=NOW + timedelta(days=1))
    decision = seen(engine, licence, device("cc", os_family="windows"),
                    at=NOW + timedelta(days=2))

    assert any(s.name == "os_spread" for s in decision.signals)


def test_the_reasons_travel_with_the_decision(engine, licence) -> None:
    """A wrongly-flagged customer deserves better than 'computer says no'."""
    for index in range(6):
        decision = seen(engine, licence, device(f"m{index}", tz=index * 60),
                        ip=f"203.0.{index}.5", at=NOW + timedelta(hours=index * 30))

    payload = decision.to_dict()
    assert payload["signals"]
    assert all(s["detail"] for s in payload["signals"])
    assert "receipt" in payload["detail"]


# ------------------------------------------------------------- exemptions ---

def test_a_machine_that_worked_yesterday_is_never_cut_off(engine, licence) -> None:
    """Blocking a running install is a support incident and maybe an open trade.

    By the time a licence looks shared, the sharing has already happened;
    revoking it is a decision for a person, not a side effect of a heartbeat.
    """
    first = device("aa")
    seen(engine, licence, first)
    for index in range(8):
        seen(engine, licence, device(f"m{index}", tz=index * 60),
             ip=f"203.0.{index}.5", at=NOW + timedelta(hours=index * 20))

    decision = seen(engine, licence, first, at=NOW + timedelta(days=9))

    assert decision.allow is True
    assert decision.band == "review"
    assert "known machine" in decision.detail


def test_a_machine_the_seller_blocked_stays_blocked(engine, licence, store) -> None:
    report = device("aa")
    seen(engine, licence, report)
    store.block_device(licence, report.machine_hash)

    decision = engine.assess(licence, report, ip="1.2.3.4", now=NOW)

    assert decision.allow is False
    assert "seller" in decision.detail


# ------------------------------------------------------------- retention ---

def test_history_is_deleted_once_it_stops_being_useful(engine, licence, store) -> None:
    seen(engine, licence, device("aa"), at=NOW - timedelta(days=400))
    seen(engine, licence, device("bb"), at=NOW)

    events = store.activation_events(licence, since="1970-01-01T00:00:00+00:00")

    assert all(e["seen_at"] > (NOW - timedelta(days=RETENTION_DAYS)).isoformat()
               for e in events)


def test_retention_is_bounded() -> None:
    assert RETENTION_DAYS <= 365


# ------------------------------------------------------------- operators ---

def test_the_operator_view_surfaces_shared_licences(engine, licence, store) -> None:
    for index in range(5):
        seen(engine, licence, device(f"m{index}"), ip=f"203.0.{index}.5",
             at=NOW + timedelta(hours=index * 20))

    rows = store.risky_licenses(minimum_devices=3)

    assert rows and rows[0]["devices"] == 5


def test_a_quiet_licence_is_not_in_the_operator_view(engine, licence, store) -> None:
    seen(engine, licence, device("aa"))

    assert store.risky_licenses(minimum_devices=3) == []


# --------------------------------------------------------- the HTTP layer ---

@pytest.fixture()
def client(tmp_path, monkeypatch):
    pytest.importorskip("flask", reason="storefront extra not installed")
    from storefront.app import create_app

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_flasktest")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("DOWNLOAD_SIGNING_SECRET", "dl_test_secret_at_least_32_chars_ok")
    monkeypatch.setenv("STOREFRONT_BASE_URL", "https://shop.test")
    app = create_app(db_path=tmp_path / "s.db", artifact_path=tmp_path / "none.tar.gz")
    app.config.update(TESTING=True)
    return app.test_client()


def activate(client, key: str, machine: str = "a", **overrides):
    return client.post("/api/activate", json={
        "key": key,
        "device": {
            "machine_hash": machine * 64 if len(machine) == 1 else machine.ljust(64, "0"),
            "os_family": overrides.get("os_family", "linux"),
            "arch": "x86_64",
            "app_version": "2.0.0",
            "tz_offset_minutes": overrides.get("tz", 0),
        },
    })


def issued_key(client):
    import json as _json

    paid = _json.dumps({
        "id": "evt_act", "type": "checkout.session.completed", "livemode": False,
        "data": {"object": {
            "id": "cs_act", "payment_status": "paid", "payment_intent": "pi_act",
            "amount_total": 10000, "currency": "usd",
            "customer_details": {"email": "buyer@example.com"},
        }},
    }).encode()
    from tests.test_storefront_app import signed

    client.post("/webhook/stripe", data=paid, headers={"Stripe-Signature": signed(paid)})
    store = client.application.extensions["storefront"]["store"]
    key = store.reveal("cs_act")
    assert key
    return key


def test_activation_allows_a_first_machine(client) -> None:
    key = issued_key(client)

    response = activate(client, key)

    assert response.status_code == 200
    assert response.get_json()["allow"] is True


def test_activation_refuses_an_unknown_key(client) -> None:
    """@private answers before the handler runs, so there is no score to report.

    One shape for absent, malformed, unknown and revoked: the differences are
    useful only to somebody testing keys. The client reads a 4xx as a
    considered "no" and defaults `allow` to false.
    """
    response = activate(client, "KX15-AAAAA-AAAAA-AAAAA-AAAAA")

    assert response.status_code == 403
    assert response.get_json()["error"] == "invalid_license"


def test_activation_refuses_a_revoked_key(client) -> None:
    """A refund has to reach the machines, not just the database."""
    key = issued_key(client)
    client.application.extensions["storefront"]["store"].revoke(key)

    response = activate(client, key)

    assert response.status_code == 403
    assert response.get_json()["error"] == "invalid_license"


def test_a_refused_activation_is_read_as_a_no_by_the_client(tmp_path) -> None:
    """The 403 body has no `allow` field; the client must not read that as yes."""
    import json
    import urllib.error

    from licensing import activate as client_module

    def refuse(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "u", 403, "forbidden", {},
            __import__("io").BytesIO(json.dumps({"error": "invalid_license"}).encode()),
        )

    directory = tmp_path / "cfg"
    directory.mkdir()
    (directory / "licence").write_text("KX15_LICENSE=KX15-A\n", encoding="utf-8")
    original = client_module.call_server
    client_module.call_server = refuse
    try:
        result = client_module.check(directory=directory, url="http://x")
    finally:
        client_module.call_server = original

    assert result["allow"] is False


def test_a_malformed_report_is_a_400_not_a_500(client) -> None:
    key = issued_key(client)

    response = client.post("/api/activate", json={"key": key, "device": {"machine_hash": "x"}})

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_device_report"


def test_activation_is_rate_limited(client) -> None:
    key = issued_key(client)
    codes = {activate(client, key, machine=f"{i:02d}").status_code for i in range(40)}

    assert 429 in codes


def test_the_operator_view_lists_nothing_for_a_clean_licence(client) -> None:
    key = issued_key(client)
    activate(client, key)

    assert client.get("/api/fraud").get_json()["licenses"] == []


# ------------------------------------------------------------ hard limits ---

def test_a_seller_block_outranks_every_heuristic(engine, licence, store) -> None:
    """It did not. The block check sat after the known-machine exemption, and
    that exemption fires only in the block band — so a banned machine was let
    through precisely when the licence looked most shared."""
    target = device("aa")
    seen(engine, licence, target)
    store.block_device(licence, target.machine_hash)

    for index in range(9):
        seen(engine, licence, device(f"m{index}", tz=index * 120,
                                     os_family=["linux", "darwin", "windows"][index % 3]),
             ip=f"203.0.{index}.5", at=NOW + timedelta(hours=index * 20))

    decision = engine.assess(licence, target, ip="1.2.3.4", now=NOW + timedelta(days=9))

    assert decision.score >= 70, "precondition: the licence must look shared"
    assert decision.allow is False
    assert "seller" in decision.detail


def test_one_key_cannot_grow_the_database_without_bound(engine, licence, store) -> None:
    """A valid key plus attacker-chosen fingerprints was 600 rows for 600 calls."""
    from storefront.fraud import MAX_DEVICES_PER_LICENSE

    for index in range(MAX_DEVICES_PER_LICENSE + 40):
        seen(engine, licence, device(f"{index:04d}"),
             at=NOW + timedelta(seconds=index))

    assert len(store.devices_for(licence)) == MAX_DEVICES_PER_LICENSE


def test_the_audit_trail_is_capped_too(engine, licence, store) -> None:
    for index in range(460):
        seen(engine, licence, device(f"{index:04d}"), at=NOW + timedelta(seconds=index))

    events = store.activation_events(licence, since="1970-01-01T00:00:00+00:00",
                                     limit=10_000)
    assert len(events) <= 400


def test_scoring_stays_cheap_with_a_full_history(engine, licence) -> None:
    """The pairwise scans are quadratic in the history they read."""
    import time

    for index in range(300):
        seen(engine, licence, device(f"{index:04d}"), at=NOW + timedelta(seconds=index))

    started = time.perf_counter()
    engine.assess(licence, device("zz"), ip="1.2.3.4", now=NOW)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05, f"scoring took {elapsed * 1000:.0f} ms"


def test_the_network_hash_uses_its_own_subkey() -> None:
    """Two purposes, one master secret, and no shared HMAC key between them."""
    from storefront.fraud import ip_prefix_hash
    from storefront.licenses import hash_license_key

    secret = "a-master-secret-of-sufficient-length"
    naive = __import__("hmac").new(
        secret.encode(), b"1.2.3.0/24", __import__("hashlib").sha256
    ).hexdigest()

    assert ip_prefix_hash("1.2.3.4", secret) != naive
    assert ip_prefix_hash("1.2.3.4", secret) != hash_license_key("1.2.3.0/24", secret)


# ------------------------------------------------------- developer tokens ---

DEV_SECRET = "a-developer-secret-of-sufficient-length"


def test_a_developer_token_verifies_and_expires(monkeypatch) -> None:
    import time

    from storefront import developer

    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)
    token = developer.mint(hours=1)

    assert developer.verify(token) > time.time()
    with pytest.raises(developer.DeveloperTokenError, match="expired"):
        developer.verify(developer.mint(hours=1, now=int(time.time()) - 7200))


def test_the_signature_covers_the_expiry(monkeypatch) -> None:
    """Otherwise a token is extended by editing one field."""
    from storefront import developer

    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)
    parts = developer.mint(hours=1).split(".")
    parts[2] = str(int(parts[2]) + 10_000_000)

    with pytest.raises(developer.DeveloperTokenError, match="signature"):
        developer.verify(".".join(parts))


def test_a_token_from_another_secret_is_refused(monkeypatch) -> None:
    from storefront import developer

    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)
    token = developer.mint(hours=1)

    with pytest.raises(developer.DeveloperTokenError, match="signature"):
        developer.verify(token, key="a-different-secret-of-sufficient-length")


def test_without_a_secret_the_whole_path_is_dead(monkeypatch) -> None:
    """Which is the state of every deployment that has not opted in."""
    from storefront import developer

    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)
    token = developer.mint(hours=1)
    monkeypatch.delenv("DEV_LICENSE_SECRET")

    assert developer.enabled() is False
    assert developer.resolve(token) is None


def test_a_real_licence_key_is_never_treated_as_a_token(monkeypatch) -> None:
    from storefront import developer

    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)

    assert developer.resolve("KX15-AAAAA-AAAAA-AAAAA-AAAAA") is None
    assert developer.looks_like_token("KX15-AAAAA-AAAAA-AAAAA-AAAAA") is False


def test_an_optional_address_list_narrows_it(monkeypatch) -> None:
    from storefront import developer

    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)
    token = developer.mint(hours=1)

    monkeypatch.setenv("DEV_LICENSE_IPS", "198.51.100.0/24")
    assert developer.resolve(token, "198.51.100.7") is not None
    assert developer.resolve(token, "203.0.113.7") is None
    monkeypatch.delenv("DEV_LICENSE_IPS")
    assert developer.resolve(token, "203.0.113.7") is not None


def test_the_token_fits_the_header_budget(monkeypatch) -> None:
    """clean_token truncates at 64 characters; a longer token would be mangled
    into an invalid one and refused for the wrong reason."""
    from storefront import developer

    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)

    assert len(developer.mint(hours=24 * 14)) <= 64


def test_a_developer_token_authenticates_every_licensed_endpoint(
    client, monkeypatch
) -> None:
    """/api/validate is @public and used to resolve keys itself, so a token
    authenticated the download but failed the check the installer runs first."""
    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)
    from storefront import developer

    token = developer.mint(hours=1)
    headers = {"X-KX15-License": token}

    assert client.get("/api/validate", headers=headers).get_json()["valid"] is True
    assert client.get("/api/install", headers=headers).status_code in (200, 503)
    assert activate(client, token).status_code == 200


def test_sharing_detection_does_not_apply_to_a_developer_token(
    client, monkeypatch
) -> None:
    """It is installed repeatedly from throwaway machines by one person. Scored
    as a licence it blocks itself on the third run — which it did."""
    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)
    from storefront import developer

    token = developer.mint(hours=1)
    for index in range(12):
        response = activate(client, token, machine=f"{index:02d}", tz=index * 60)

    assert response.status_code == 200
    assert response.get_json()["allow"] is True
    assert "developer token" in response.get_json()["detail"]


def test_a_developer_token_never_reaches_the_fraud_report(client, monkeypatch) -> None:
    monkeypatch.setenv("DEV_LICENSE_SECRET", DEV_SECRET)
    from storefront import developer

    token = developer.mint(hours=1)
    for index in range(6):
        activate(client, token, machine=f"{index:02d}")

    assert client.get("/api/fraud").get_json()["licenses"] == []


# --------------------------------------------------------------------------
# The pinned developer licence key
# --------------------------------------------------------------------------


@pytest.fixture()
def pinned(tmp_path, monkeypatch):
    """A developer secret, a fresh key, and a pinned address — all disposable."""
    from storefront import developer

    monkeypatch.setenv("DEV_LICENSE_SECRET", "dev-secret-of-quite-sufficient-length-42")
    monkeypatch.delenv("DEV_LICENSE_IPS", raising=False)
    pins = tmp_path / "pins.json"
    monkeypatch.setattr(developer, "PINS_PATH", pins)
    monkeypatch.delenv("DEV_LICENSE_PINS", raising=False)
    key = developer.generate_license_key()
    developer.write_pins(key=key, addresses=["203.0.113.7"], path=pins)
    monkeypatch.setenv("DEV_LICENSE_KEY", key)
    return developer, key, pins


def test_the_pins_file_contains_neither_the_key_nor_the_address(pinned) -> None:
    """That is the whole reason it is safe to commit."""
    _, key, pins = pinned
    body = pins.read_text()
    assert key not in body
    assert "203.0.113.7" not in body
    assert "203.0.113" not in body


def test_the_pinned_key_resolves_from_the_pinned_address(pinned) -> None:
    developer, key, _ = pinned
    licence = developer.resolve(key, "203.0.113.7")
    assert licence is not None
    assert licence.key_hash == developer.DEV_LICENSE_HASH


def test_the_pinned_key_is_refused_from_anywhere_else(pinned) -> None:
    developer, key, _ = pinned
    assert developer.resolve(key, "198.51.100.9") is None


def test_loopback_is_always_allowed(pinned) -> None:
    """The packet never left the machine, so there is no header to forge."""
    developer, key, _ = pinned
    assert developer.resolve(key, "127.0.0.1") is not None
    assert developer.resolve(key, "::1") is not None


def test_another_well_formed_key_is_not_the_developer_key(pinned) -> None:
    developer, _, _ = pinned
    other = developer.generate_license_key()
    assert developer.resolve(other, "127.0.0.1") is None


def test_a_key_in_the_environment_that_is_not_pinned_is_refused(pinned, monkeypatch) -> None:
    """The pin is the authority; the environment is only storage."""
    developer, _, _ = pinned
    monkeypatch.setenv("DEV_LICENSE_KEY", developer.generate_license_key())
    assert developer.configured_key() is None


def test_rotating_the_secret_invalidates_both_pins(pinned, monkeypatch) -> None:
    developer, key, _ = pinned
    monkeypatch.setenv("DEV_LICENSE_SECRET", "a-completely-different-secret-value-here")
    assert developer.key_matches_pin(key) is False
    assert developer.address_pinned("203.0.113.7") is False


def test_without_a_secret_the_pinned_key_is_dead_too(pinned, monkeypatch) -> None:
    """The token half is covered above; this is the same rule for the key."""
    developer, key, _ = pinned
    monkeypatch.setenv("DEV_LICENSE_SECRET", "")
    assert developer.enabled() is False
    assert developer.resolve(key, "127.0.0.1") is None


def test_key_and_address_digests_cannot_be_swapped(pinned) -> None:
    """Different domain-separation labels, so one is never the other."""
    developer, key, _ = pinned
    # An address that happens to be presented as a key, and vice versa, must
    # not collide across the two pins.
    assert developer.key_digest(key) != developer.address_digest("203.0.113.7")


# --------------------------------------------------------------------------
# Purchase-time signals, carried forward to activation
# --------------------------------------------------------------------------


def _context(store, session: str, licence_hash: str, **fp):
    """One purchase, optionally with a Fingerprint verdict attached."""
    class _Verdict:
        event_id = fp.pop("event_id", "evt")
        visitor_id = fp.pop("visitor_id", "visitor-abc")
        verified = fp.pop("verified", True)
        reason = "ok"
        confidence = 0.99
        bot = fp.pop("bot", "not_detected")
        vpn = fp.pop("vpn", False)
        proxy = fp.pop("proxy", False)
        incognito = False
        tampering = fp.pop("tampering", False)
        virtual_machine = False
        suspect_score = 0
        country = "US"
        events_24h = 1

    store.record_purchase_context(
        stripe_session_id=session,
        context=fp.pop("context", {"timezone": "UTC", "screen": "1920x1080"}),
        ip_prefix_hash="prefix", user_agent="UA", accept_language="en",
        fingerprint=_Verdict(),
    )
    store.link_purchase_context(session, licence_hash)


def test_a_clean_purchase_adds_no_points(engine, store) -> None:
    _context(store, "cs_1", "lic_clean")
    assert engine._purchase_signals("lic_clean") == []


def test_a_licence_with_no_purchase_record_adds_no_points(engine) -> None:
    """Absence of evidence, scored as absence of evidence."""
    assert engine._purchase_signals("lic_never_seen") == []


def test_a_bot_at_checkout_is_scored(engine, store) -> None:
    _context(store, "cs_2", "lic_bot", bot="bad")
    names = {s.name for s in engine._purchase_signals("lic_bot")}
    assert "purchase_bot" in names


def test_tampering_at_checkout_is_scored(engine, store) -> None:
    _context(store, "cs_3", "lic_tamper", tampering=True)
    names = {s.name for s in engine._purchase_signals("lic_tamper")}
    assert "purchase_tampering" in names


def test_an_unverified_verdict_is_never_read_as_clean_or_dirty(engine, store) -> None:
    """A blocked agent must not become a way to buy without being seen — but it
    must not convict anybody either. It simply contributes nothing."""
    _context(store, "cs_4", "lic_unverified", verified=False, bot="bad", tampering=True)
    assert engine._purchase_signals("lic_unverified") == []


def test_many_licences_from_one_browser_look_like_resale(engine, store) -> None:
    for n in range(5):
        _context(store, f"cs_resale_{n}", f"lic_resale_{n}")
    signals = engine._purchase_signals("lic_resale_0")
    resale = [s for s in signals if s.name == "resale_pattern"]
    assert resale, signals
    assert resale[0].points > 0


def test_a_vpn_at_checkout_nudges_but_cannot_block(engine, store) -> None:
    """Privacy-conscious is the common case; it must not be the deciding one."""
    _context(store, "cs_5", "lic_vpn", vpn=True)
    signals = engine._purchase_signals("lic_vpn")
    assert sum(s.points for s in signals) < engine.policy.block_at
    assert sum(s.points for s in signals) < engine.policy.review_at
