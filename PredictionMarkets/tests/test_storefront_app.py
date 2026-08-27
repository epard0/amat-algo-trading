"""Flask storefront HTTP layer.

The stdlib server's logic moved wholesale into Flask, so these tests cover the
seam: routing, raw-body webhook verification, rate limiting, error shape, and
the security headers. The payment and licensing logic itself is covered in
test_storefront.py against the underlying modules.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

flask = pytest.importorskip("flask", reason="storefront extra not installed")

from storefront.app import RateLimiter, create_app  # noqa: E402
from storefront.licenses import LicenseStore  # noqa: E402

SECRET = "whsec_flasktest"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("DOWNLOAD_SIGNING_SECRET", "dl_test_secret_at_least_32_chars_ok")
    monkeypatch.setenv("STOREFRONT_BASE_URL", "https://shop.test")
    app = create_app(db_path=tmp_path / "s.db", artifact_path=tmp_path / "none.tar.gz")
    app.config.update(TESTING=True)
    return app.test_client()


def csrf(client) -> dict[str, str]:
    """Headers a browser would send: the token it read from its own cookie.

    A cross-origin page can cause the request but cannot read the cookie to
    echo it, which is the whole of the protection.
    """
    client.get("/")
    token = next(
        (c.value for c in client._cookies.values() if c.key == "kx15_csrf"), ""
    ) if hasattr(client, "_cookies") else client.get_cookie("kx15_csrf").value
    return {"X-CSRF-Token": token}


def signed(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


PAID = json.dumps({
    "id": "evt_flask_1", "type": "checkout.session.completed", "livemode": False,
    "data": {"object": {
        "id": "cs_flask_1", "payment_status": "paid", "payment_intent": "pi_1",
        "amount_total": 10000, "currency": "usd",
        "customer_details": {"email": "buyer@example.com"},
    }},
}).encode()


# ------------------------------------------------------------------ pages ---

@pytest.mark.parametrize("route", ["/", "/success", "/terms", "/privacy",
                                   "/theme.css", "/bulma.min.css",
                                   "/app.js", "/success.js", "/terms.js"])
def test_pages_render(client, route) -> None:
    assert client.get(route).status_code == 200


def test_no_page_relies_on_an_inline_script_or_style(client) -> None:
    """The CSP is script-src 'self'; an inline block is dropped, not warned about.

    The success page is the one that matters: it exists to show a paying
    customer their licence key, and an inline script there fails silently in
    exactly the case nobody tests by hand.
    """
    for route in ("/", "/success", "/terms"):
        body = client.get(route).get_data(as_text=True)
        assert "<script>" not in body, f"{route} has an inline script"
        assert 'style="' not in body, f"{route} has an inline style attribute"


def test_no_script_writes_an_inline_style() -> None:
    """style-src 'self' blocks inline styles the scripts write, too.

    Chrome treats an `element.style` assignment and a style attribute in
    injected markup as inline style and refuses both, so a layout that depends
    on either works everywhere except behind this app's own CSP. Caught here
    because the failure is a console message nobody reads, not an exception.
    """
    from storefront.app import WEB_DIR

    for name in ("app.js", "success.js", "terms.js"):
        body = (WEB_DIR / name).read_text(encoding="utf-8")
        assert 'style="' not in body, f"{name} injects a style attribute"
        assert ".style." not in body, f"{name} writes an inline style"


def test_every_referenced_asset_is_served(client) -> None:
    """A page that links a stylesheet the app does not route renders unstyled."""
    import re

    for route in ("/", "/success", "/terms"):
        body = client.get(route).get_data(as_text=True)
        for asset in re.findall(r'(?:href|src)="(/[^"#?]+\.(?:css|js))"', body):
            assert client.get(asset).status_code == 200, f"{route} -> {asset}"


def test_terms_and_privacy_serve_the_same_document(client) -> None:
    assert client.get("/terms").data == client.get("/privacy").data


def test_healthz_reports_licence_count(client) -> None:
    body = client.get("/healthz").get_json()
    assert body["status"] == "ok"
    assert body["licenses"] == 0


def test_unknown_route_is_json_not_html(client) -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.get_json()["error"]


def test_security_headers_are_set(client) -> None:
    headers = client.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_static_route_cannot_escape_the_web_directory(client) -> None:
    for attempt in ("/../storefront.db", "/%2e%2e/app.py", "/..%2fapp.py"):
        assert client.get(attempt).status_code in (403, 404, 308)


# ---------------------------------------------------------------- config ---

def test_config_exposes_price_but_never_secrets(client) -> None:
    body = client.get("/api/config").get_json()
    assert body["price_cents"] == 10_000
    assert body["payments_enabled"] is True
    assert body["livemode"] is False
    blob = json.dumps(body)
    assert "sk_test" not in blob and "whsec" not in blob and "dl_test" not in blob


def issued_key(client, session_id: str = "cs_flask_1") -> str:
    """The key a customer would read out of their receipt email.

    /api/license no longer returns it on the normal path — delivery is by
    email, and the endpoint reports on that delivery — so a test that needs the
    plaintext takes the same route the email does: straight out of the store.
    """
    store = client.application.extensions["storefront"]["store"]
    key = store.reveal(session_id)
    assert key, f"no key was revealed for {session_id}"
    return key

# --------------------------------------------------------------- webhook ---

def test_valid_webhook_issues_a_licence(client) -> None:
    response = client.post("/webhook/stripe", data=PAID,
                           headers={"Stripe-Signature": signed(PAID)})
    assert response.status_code == 200
    assert response.get_json()["received"] is True

    issued = client.get("/api/license?session_id=cs_flask_1").get_json()
    assert issued["status"] == "issued"
    # The key travels by email; this endpoint reports on that delivery.
    assert issued["delivery"] in {"sent", "pending", "failed"}
    assert issued_key(client).startswith("KX15-")


def test_replayed_webhook_is_deduplicated(client) -> None:
    header = signed(PAID)
    client.post("/webhook/stripe", data=PAID, headers={"Stripe-Signature": header})
    second = client.post("/webhook/stripe", data=PAID,
                         headers={"Stripe-Signature": signed(PAID)})
    assert second.get_json().get("duplicate") is True


def test_forged_webhook_is_rejected(client) -> None:
    response = client.post("/webhook/stripe", data=PAID,
                           headers={"Stripe-Signature": signed(PAID, "whsec_attacker")})
    assert response.status_code == 400
    assert response.get_json()["error"] == "signature_verification_failed"


def test_tampered_webhook_body_is_rejected(client) -> None:
    """Signature is verified against raw bytes, so any edit breaks it."""
    header = signed(PAID)
    response = client.post("/webhook/stripe", data=PAID.replace(b"10000", b"00001"),
                           headers={"Stripe-Signature": header})
    assert response.status_code == 400


def test_stale_webhook_is_rejected_as_replay(client) -> None:
    response = client.post(
        "/webhook/stripe", data=PAID,
        headers={"Stripe-Signature": signed(PAID, timestamp=int(time.time()) - 4000)},
    )
    assert response.status_code == 400
    assert "replay" in response.get_json()["detail"]


def test_webhook_without_signature_is_rejected(client) -> None:
    assert client.post("/webhook/stripe", data=PAID).status_code == 400


def test_refund_revokes_the_licence(client) -> None:
    client.post("/webhook/stripe", data=PAID, headers={"Stripe-Signature": signed(PAID)})
    key = issued_key(client)
    assert client.get("/api/validate", headers={"X-KX15-License": key}).get_json()["valid"] is True

    refund = json.dumps({
        "id": "evt_flask_2", "type": "charge.refunded", "livemode": False,
        "data": {"object": {"payment_intent": "pi_1"}},
    }).encode()
    client.post("/webhook/stripe", data=refund, headers={"Stripe-Signature": signed(refund)})

    assert client.get("/api/validate", headers={"X-KX15-License": key}).get_json()["valid"] is False


# -------------------------------------------------------------- checkout ---

def test_checkout_rejects_a_bad_email_before_calling_stripe(client) -> None:
    response = client.post("/api/checkout", headers=csrf(client), json={"email": "not-an-email"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_email"


def test_checkout_ignores_any_client_supplied_amount(client) -> None:
    """The request cannot influence the price; it is server-side only."""
    response = client.post("/api/checkout", headers=csrf(client),
                           json={"email": "a@b.co", "amount": 1, "price_cents": 1})
    # Stripe is fake here, so this fails at the API call — never at validation,
    # and never by accepting the injected amount.
    assert response.status_code in (502, 503)
    assert "price" not in json.dumps(response.get_json()).lower()


def test_checkout_is_rate_limited(client) -> None:
    for _ in range(10):
        client.post("/api/checkout", headers=csrf(client), json={"email": "a@b.co"})
    assert client.post("/api/checkout", headers=csrf(client), json={"email": "a@b.co"}).status_code == 429


# ---------------------------------------------------------------- resend ---

def test_resend_does_not_reveal_whether_a_key_exists(client) -> None:
    """Identical response either way, so this cannot enumerate licences."""
    unknown = client.post("/api/resend", headers=csrf(client), json={"license_key": "KX15-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ"})
    empty = client.post("/api/resend", headers=csrf(client), json={})
    assert unknown.status_code == empty.status_code == 200
    assert unknown.get_json() == empty.get_json() == {"status": "ok"}


# -------------------------------------------------------------- download ---

def test_download_rejects_a_forged_token(client) -> None:
    from storefront.downloads import mint_token

    forged = mint_token("KX15-A", "wrong-secret").token
    response = client.get(f"/download?token={forged}")
    assert response.status_code == 403
    assert response.get_json()["error"] == "invalid_link"


def test_download_rejects_a_signed_token_with_no_stored_nonce(client) -> None:
    """The signature proves we minted it; the nonce row is what authorises it."""
    from storefront.downloads import mint_token

    orphan = mint_token("KX15-A", "dl_test_secret_at_least_32_chars_ok").token
    response = client.get(f"/download?token={orphan}")
    assert response.status_code == 410


def test_download_reports_a_missing_artifact_rather_than_500(client, tmp_path) -> None:
    from storefront.downloads import mint_token

    client.post("/webhook/stripe", data=PAID, headers={"Stripe-Signature": signed(PAID)})
    key = issued_key(client)
    app = client.application
    store = app.extensions["storefront"]["store"]
    key_hash = store.hash_of(key)
    token = mint_token(key_hash, "dl_test_secret_at_least_32_chars_ok")
    store.record_token(
        nonce=token.nonce, license_hash=key_hash,
        expires_at=token.expires_at.isoformat(), max_uses=3,
    )

    response = client.get(f"/download?token={token.token}")
    assert response.status_code == 503
    assert response.get_json()["error"] == "artifact_missing"


# ---------------------------------------------------------- rate limiter ---

def test_rate_limiter_allows_then_blocks() -> None:
    limiter = RateLimiter()
    assert all(limiter.allow("k", limit=3, window=60) for _ in range(3))
    assert limiter.allow("k", limit=3, window=60) is False


def test_rate_limiter_is_per_key() -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.allow("a", limit=3, window=60)
    assert limiter.allow("b", limit=3, window=60) is True


def test_rate_limiter_window_expires() -> None:
    limiter = RateLimiter()
    assert limiter.allow("k", limit=1, window=0.05) is True
    assert limiter.allow("k", limit=1, window=0.05) is False
    time.sleep(0.08)
    assert limiter.allow("k", limit=1, window=0.05) is True


# ----------------------------------------------------------- performance ---

def test_performance_endpoint_serves_live_figures(client) -> None:
    """The page must not be able to quote figures the bot has moved past."""
    body = client.get("/api/performance").get_json()
    if body.get("error"):
        pytest.skip("no ledger or snapshot available in this environment")
    assert body["source"] in {"live-ledger", "snapshot-file"}
    assert "live" in body["modes"]


# ------------------------------------------------------ verified record ----

from storefront.verified import VerifiedRecord, build_payload, project  # noqa: E402


def test_verified_record_loads_and_derives_correctly() -> None:
    record = VerifiedRecord.load()
    assert record is not None
    assert record.trades == record.wins + record.losses
    assert record.win_rate == pytest.approx(record.wins / record.trades)
    # Capital moved in and out during the period, so no growth multiple is
    # quoted: the ratio of closing to opening balance would describe when the
    # withdrawal happened rather than how the bot traded.
    assert record.has_flows
    assert record.multiple == 0.0
    assert record.roi_on_stake == pytest.approx(
        float(record.net_pnl) / float(record.staked), abs=1e-6
    )


def test_wilson_interval_tightens_with_the_larger_sample() -> None:
    """74 trades gives a materially narrower band than 20 did."""
    record = VerifiedRecord.load()
    low, high = record.win_rate_interval()
    assert 0.0 <= low < record.win_rate < high <= 1.0
    assert (high - low) < 0.25   # was >0.30 at n=18


def test_the_model_is_consistent_with_the_record_without_being_fitted_to_it() -> None:
    """The published result must be an ordinary draw, not the model's median.

    An earlier version solved the parameters so the median *equalled* $45.98,
    which is a circular way to describe one sample and quietly claimed the
    observed fortnight was typical. Now the parameters are measured and where
    the result lands is an output. It should be unremarkable — somewhere in the
    middle — but not the centre.
    """
    record = VerifiedRecord.load()
    replay = project(record, starting_balance=float(record.starting_balance))

    assert 0.4 < replay["observed_percentile"] < 0.9
    assert replay["median"] < float(record.ending_balance), (
        "the advertised outcome should be better than typical, and be shown as such"
    )


def test_projection_publishes_the_downside() -> None:
    record = VerifiedRecord.load()
    proj = project(record, starting_balance=10.0)
    p = proj["percentiles"]
    assert p["p5"] < p["p50"] < p["p95"]
    assert p["p5"] < 10.0, "the 5th percentile must show a real loss"
    for key in ("probability_of_loss", "probability_of_half_loss", "probability_of_ruin"):
        assert key in proj


def test_the_projection_does_not_scale_with_capital() -> None:
    """Ten times the money must not promise ten times the return.

    A 15-minute crypto book is thin, so a trade is capped at the depth it can
    absorb. Past that point more capital buys nothing, and a projection that
    scaled linearly would be selling a claim the strategy cannot make.
    """
    record = VerifiedRecord.load()
    small = project(record, starting_balance=10.0)
    large = project(record, starting_balance=500.0)

    small_multiple = small["percentiles"]["p50"] / 10.0
    large_multiple = large["percentiles"]["p50"] / 500.0
    assert large_multiple < small_multiple * 1.5
    assert large["percentiles"]["p95"] / 500.0 < small["percentiles"]["p95"] / 10.0


def test_payload_carries_provenance() -> None:
    """A self-reported figure must never be presented as ledger-audited."""
    payload = build_payload()
    assert payload["source"] == "kalshi-account-statement"
    assert "not derived from the local ledger" in payload["provenance"].lower()


def test_performance_endpoint_exposes_verified_and_ledger_separately(client) -> None:
    body = client.get("/api/performance").get_json()
    if body.get("error"):
        pytest.skip("no data in this environment")
    assert "verified" in body        # exchange record, the headline
    assert "modes" in body           # local ledger, the audited subset
    # They must not be merged: the counts genuinely differ.
    assert body["verified"]["trades"] != body["modes"]["live"]["settled"]


# ------------------------------------------------------------- installer ---

def test_install_script_is_plain_text_with_the_real_base_url(client) -> None:
    response = client.get("/install.sh")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"   # readable in a browser first
    body = response.get_data(as_text=True)
    assert "${BASE_URL}" not in body, "template marker left unsubstituted"
    assert "https://shop.test" in body


def test_install_script_verifies_a_checksum(client) -> None:
    body = client.get("/install.sh").get_data(as_text=True)
    assert "/api/artifact-sha256" in body
    assert "checksum mismatch" in body


def test_install_script_refuses_without_a_licence(client) -> None:
    body = client.get("/install.sh").get_data(as_text=True)
    assert "a licence key is required" in body
    assert "api/validate" in body     # licence is checked before download


def test_install_download_requires_a_valid_licence(client) -> None:
    assert client.get("/api/install").status_code == 403
    assert client.get("/api/install", headers={"X-KX15-License": "KX15-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ"}).status_code == 403


def test_install_download_rejects_a_revoked_licence(client) -> None:
    client.post("/webhook/stripe", data=PAID, headers={"Stripe-Signature": signed(PAID)})
    key = issued_key(client)

    refund = json.dumps({
        "id": "evt_rev", "type": "charge.refunded", "livemode": False,
        "data": {"object": {"payment_intent": "pi_1"}},
    }).encode()
    client.post("/webhook/stripe", data=refund, headers={"Stripe-Signature": signed(refund)})

    assert client.get("/api/install", headers={"X-KX15-License": key}).status_code == 403


def test_install_download_is_rate_limited(client) -> None:
    for _ in range(12):
        client.get("/api/install", headers={"X-KX15-License": "KX15-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ"})
    assert client.get("/api/install", headers={"X-KX15-License": "KX15-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ"}).status_code == 429


def test_launcher_exposes_the_dashboard_command(client) -> None:
    """`kx15 dashboard` is the live stats GUI over the local ledger."""
    body = client.get("/install.sh").get_data(as_text=True)
    assert "kx15 dashboard" in body
    assert "monitoring.server" in body
    assert "--ledger" in body


# ------------------------------------------------------------------ theme ---

@pytest.mark.parametrize("asset", ["/bulma.min.css", "/theme.css", "/app.js"])
def test_vendored_assets_are_served_locally(client, asset) -> None:
    """CSP is default-src 'self'; nothing may come from a CDN."""
    assert client.get(asset).status_code == 200


def test_page_references_no_external_origins(client) -> None:
    body = client.get("/").get_data(as_text=True)
    for marker in ("cdn.", "https://fonts.", "googleapis", "unpkg", "jsdelivr"):
        assert marker not in body, f"external reference {marker} would be CSP-blocked"


def test_the_licence_store_survives_concurrent_writers(tmp_path) -> None:
    """One connection is shared by request threads and the fulfilment thread.

    Under Python's implicit transaction handling those threads share a single
    BEGIN, so whichever calls commit() second raises "cannot commit - no
    transaction is active" and a paying customer's download 500s. This failed
    roughly one run in three before the store moved to autocommit.
    """
    import threading

    store = LicenseStore(tmp_path / "concurrent.db")
    errors: list[Exception] = []
    ready = threading.Barrier(8)

    def hammer(index: int) -> None:
        ready.wait()
        try:
            for i in range(25):
                store.record_event(f"evt_{index}_{i}", "checkout.session.completed")
                store.issue(
                    stripe_session_id=f"cs_{index}_{i}", email="a@b.test",
                    payment_intent=f"pi_{index}_{i}", amount_cents=10000,
                    currency="usd", livemode=False,
                )
                store.mark_handled(f"evt_{index}_{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert store.count() == 200
    store.close()


def test_a_download_token_cannot_be_spent_past_its_budget(tmp_path) -> None:
    """The use budget has to hold when eight threads race for the last use."""
    import threading
    from datetime import datetime, timedelta, timezone

    store = LicenseStore(tmp_path / "tokens.db")
    store.record_token(nonce="n1", license_hash="deadbeef" * 8,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        max_uses=3,
    )
    granted: list[bool] = []
    ready = threading.Barrier(8)

    def spend() -> None:
        ready.wait()
        granted.append(bool(store.consume_token("n1")["ok"]))

    threads = [threading.Thread(target=spend) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(granted) == 3, granted
    store.close()


def test_the_favicon_is_the_same_file_everywhere_it_is_served() -> None:
    """assets/ is the source; the two apps serve copies of it.

    A mark that drifts between the storefront and the dashboard is a mark that
    is wrong in one of them, and nobody notices a favicon slowly going stale.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    source = (root / "assets" / "favicon.svg").read_bytes()

    for copy in (root / "storefront" / "web" / "favicon.svg",
                 root / "monitoring" / "webui" / "favicon.svg"):
        assert copy.read_bytes() == source, f"{copy} has drifted from assets/favicon.svg"


def test_the_favicon_is_served(client) -> None:
    response = client.get("/favicon.svg")

    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"


def test_the_privacy_page_documents_the_licence_check(client) -> None:
    """The Software contacts a server now; a policy that says otherwise is a lie.

    This test exists because the previous wording — "the Software does not
    phone home" — was true when written and silently became false the moment
    activation shipped.
    """
    body = client.get("/privacy").get_data(as_text=True)

    assert "does not phone home" not in body
    for disclosed in ("machine fingerprint", "UTC offset", "fails open",
                      "180 days", "--show-report"):
        assert disclosed in body, f"privacy policy does not mention {disclosed}"


def test_the_privacy_page_still_promises_what_is_still_true(client) -> None:
    body = client.get("/privacy").get_data(as_text=True)

    assert "never transmits anything about your trading" in body


def test_an_unconfigured_deployment_says_so_on_the_terms_page(client) -> None:
    """A placeholder in a legal page is exactly the kind of thing that ships.

    Without SELLER_NAME and friends the page cannot name a contracting party,
    a data controller, or a governing law, so it shows a warning instead of
    rendering prose that reads like it did.
    """
    body = client.get("/terms").get_data(as_text=True)

    assert 'id="seller-missing"' in body
    assert client.get("/api/config").get_json()["seller"] == {}


def test_a_configured_deployment_publishes_the_controller(client, monkeypatch) -> None:
    monkeypatch.setenv("SELLER_NAME", "Example Trading Ltd")
    monkeypatch.setenv("SELLER_CONTACT", "legal@example.test")
    monkeypatch.setenv("SELLER_JURISDICTION", "England and Wales")

    import importlib

    import storefront.app as app_module

    importlib.reload(app_module)
    fresh = app_module.create_app(
        db_path=client.application.extensions["storefront"]["store"].path,
    ).test_client()
    seller = fresh.get("/api/config").get_json()["seller"]

    assert seller["name"] == "Example Trading Ltd"
    assert seller["contact"] == "legal@example.test"
    assert seller["jurisdiction"] == "England and Wales"

    importlib.reload(app_module)


def test_the_privacy_page_admits_to_server_logs(client) -> None:
    """Every web server logs IPs, and this one logs an email address too.

    An earlier draft said "we do not store it" about IP addresses, which was
    true of the activation record and false of the access log sitting next to
    it.
    """
    body = client.get("/privacy").get_data(as_text=True)

    assert "Server logs" in body
    assert "user-agent" in body
    for stored in ("risk score", "delivery log", "hosting provider"):
        assert stored.lower() in body.lower(), f"privacy policy omits the {stored}"


def test_no_stylesheet_or_script_is_loaded_from_a_cdn(client) -> None:
    """CSP is default-src 'self'. A CDN reference is a blank page, not a warning.

    Scoped to elements that FETCH. An <a href> to another site loads nothing and
    is not a CSP concern — the privacy policy has to be able to link to the
    processors it names, which is the whole point of naming them.
    """
    import re

    fetching = re.compile(
        r'<(?:script|img|iframe|source|embed|video|audio)\b[^>]*\bsrc="([^"]+)"'
        r"|"
        r'<link\b[^>]*\bhref="([^"]+)"',
        re.IGNORECASE,
    )
    for route in ("/", "/terms", "/success"):
        body = client.get(route).get_data(as_text=True)
        for match in fetching.finditer(body):
            url = match.group(1) or match.group(2)
            assert not url.startswith(("http://", "https://", "//")), \
                f"{route} loads {url} from another origin"


def test_every_off_site_link_is_a_plain_hyperlink(client) -> None:
    """And carries rel=noopener, since each one is a target=_blank away from
    handing another origin a window handle."""
    import re

    for route in ("/", "/terms", "/success"):
        body = client.get(route).get_data(as_text=True)
        for tag in re.findall(r"<a\b[^>]*>", body, re.IGNORECASE):
            href = re.search(r'href="([^"]+)"', tag)
            if href is None or not href.group(1).startswith(("http://", "https://", "//")):
                continue
            assert "noopener" in tag, f"{route}: {tag}"


def test_the_fixed_navbar_reserves_its_own_height(client) -> None:
    """Bulma's is-fixed-top overlaps the first section without this class."""
    for route in ("/", "/terms", "/success"):
        body = client.get(route).get_data(as_text=True)
        if "is-fixed-top" in body:
            assert "has-navbar-fixed-top" in body, f"{route} navbar will cover its hero"


def test_chart_colours_are_never_built_by_concatenation() -> None:
    """Bulma emits hsl(); appending a hex alpha to one is not a colour.

    `C.green + "55"` worked for as long as the palette was hex and threw a
    SyntaxError from addColorStop the moment it was not.
    """
    import re

    from storefront.app import WEB_DIR

    source = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert not re.search(r'C\.\w+\s*\+\s*"', source)


def test_a_licence_key_is_never_accepted_from_the_query_string(client) -> None:
    """A key in a URL lands in the access log and in every proxy in between.

    Both endpoints used to take ?key=. The header path is the only one now, so
    a key can no longer be leaked by an intermediary that logs request lines.
    """
    client.post("/webhook/stripe", data=PAID, headers={"Stripe-Signature": signed(PAID)})
    key = issued_key(client)

    assert client.get(f"/api/validate?key={key}").get_json()["valid"] is False
    assert client.get(f"/api/install?key={key}").status_code == 403
    # ...and still works when presented properly.
    assert client.get("/api/validate",
                      headers={"X-KX15-License": key}).get_json()["valid"] is True


def test_the_installer_sends_the_key_in_a_header(client) -> None:
    body = client.get("/install.sh").get_data(as_text=True)

    assert "X-KX15-License" in body
    assert "?key=" not in body


@pytest.mark.parametrize("route", ["/api/fraud", "/api/deliveries"])
def test_operator_views_are_not_public(client, route, monkeypatch) -> None:
    """These expose other customers' data.

    nginx blocks them as well, but nginx is a separate artifact that drifted
    once already: /api/fraud was added long after the rule for
    /api/deliveries and inherited nothing.
    """
    response = client.get(route, environ_overrides={"REMOTE_ADDR": "203.0.113.7"})

    assert response.status_code == 404


@pytest.mark.parametrize("route", ["/api/fraud", "/api/deliveries"])
def test_operator_views_answer_on_loopback(client, route) -> None:
    assert client.get(route).status_code == 200


def test_an_operator_token_replaces_the_loopback_rule(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "s3cret-operator-token-value")
    monkeypatch.setenv("DOWNLOAD_SIGNING_SECRET", "dl_test_secret_at_least_32_chars_ok")
    from storefront.app import create_app

    client = create_app(db_path=tmp_path / "s.db").test_client()

    assert client.get("/api/fraud").status_code == 404
    assert client.get(
        "/api/fraud", headers={"X-KX15-Operator": "s3cret-operator-token-value"}
    ).status_code == 200


def test_the_key_reveal_endpoint_is_rate_limited(client) -> None:
    codes = {client.get(f"/api/license?session_id=cs_{i}").status_code for i in range(40)}

    assert 429 in codes


# --------------------------------------------------------------------------
# The developer key endpoint, and the purchase-time fingerprint
# --------------------------------------------------------------------------


def test_the_dev_key_endpoint_does_not_exist_without_a_secret(client) -> None:
    """A switched-off endpoint that 403s tells a scanner to come back."""
    assert client.get("/api/dev/license").status_code == 404


@pytest.fixture()
def dev_client(tmp_path, monkeypatch):
    from storefront import developer

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("DOWNLOAD_SIGNING_SECRET", "dl_test_secret_at_least_32_chars_ok")
    monkeypatch.setenv("STOREFRONT_BASE_URL", "https://shop.test")
    monkeypatch.setenv("DEV_LICENSE_SECRET", "dev-secret-of-quite-sufficient-length-42")
    monkeypatch.setattr(developer, "PINS_PATH", tmp_path / "pins.json")
    monkeypatch.delenv("DEV_LICENSE_PINS", raising=False)
    key = developer.generate_license_key()
    developer.write_pins(key=key, addresses=["203.0.113.7"], path=tmp_path / "pins.json")
    monkeypatch.setenv("DEV_LICENSE_KEY", key)
    app = create_app(db_path=tmp_path / "s.db", artifact_path=tmp_path / "none.tar.gz")
    app.config.update(TESTING=True)
    return app.test_client(), key


def test_the_dev_key_is_served_over_loopback(dev_client) -> None:
    client, key = dev_client
    response = client.get("/api/dev/license")
    assert response.status_code == 200
    assert response.get_json()["license_key"] == key
    assert response.headers["Cache-Control"] == "no-store"


def test_the_dev_key_is_not_served_to_a_stranger(dev_client) -> None:
    client, _ = dev_client
    response = client.get("/api/dev/license", environ_overrides={"REMOTE_ADDR": "198.51.100.9"})
    assert response.status_code == 404


def test_the_dev_key_authenticates_like_a_customer_key(dev_client) -> None:
    """The point of the key over the token: it exercises the same paths."""
    client, key = dev_client
    body = client.get("/api/validate", headers={"X-KX15-License": key}).get_json()
    assert body["valid"] is True


def test_checkout_records_the_browser_context(client, monkeypatch) -> None:
    """A checkout that reaches Stripe writes one context row for that session."""
    import storefront.app as app_module

    store = client.application.extensions["storefront"]["store"]
    gateway = client.application.extensions["storefront"]["gateway"]

    class _Session:
        id = "cs_ctx_1"
        url = "https://checkout.test/pay"

    monkeypatch.setattr(gateway, "create_checkout_session", lambda **_: _Session())
    monkeypatch.setattr(type(gateway.config), "configured", property(lambda self: True))

    response = client.post(
        "/api/checkout", headers=csrf(client),
        json={"email": "buyer@example.com", "client": {
            "timezone": "America/Detroit", "tz_offset_minutes": -240,
            "languages": "en-US,en", "platform": "Linux x86_64",
            "screen": "2560x1440", "hardware_concurrency": 16,
            "device_memory": 32, "touch_points": 0, "colour_depth": 24,
        }},
    )
    assert response.status_code == 200
    row = store._one(
        "SELECT * FROM purchase_context WHERE stripe_session_id = ?", ("cs_ctx_1",))
    assert row is not None
    assert row["timezone"] == "America/Detroit"
    assert row["hardware_concurrency"] == 16
    assert row["browser_hash"]
    assert row["license_hash"] is None
    assert app_module  # imported for the module-level allow-list below


def test_the_client_context_allow_list_drops_everything_else() -> None:
    from storefront.app import _clean_client_context

    cleaned = _clean_client_context({
        "timezone": "UTC",
        "canvas_hash": "a" * 64,          # not on the list
        "webgl_renderer": "NVIDIA",       # not on the list
        "hardware_concurrency": "9999999",
        "tz_offset_minutes": -10_000,
    })
    assert set(cleaned) == {"timezone", "hardware_concurrency", "tz_offset_minutes"}
    assert cleaned["hardware_concurrency"] == 4096      # clamped
    assert cleaned["tz_offset_minutes"] == -720         # clamped


def test_a_non_dict_client_context_is_dropped_not_raised() -> None:
    from storefront.app import _clean_client_context

    assert _clean_client_context("not a dict") == {}
    assert _clean_client_context(None) == {}


def test_the_licence_endpoint_reports_delivery_rather_than_the_key(client) -> None:
    client.post("/webhook/stripe", data=PAID, headers={"Stripe-Signature": signed(PAID)})
    body = client.get("/api/license?session_id=cs_flask_1").get_json()
    assert body["status"] == "issued"
    assert body["delivery"] in {"sent", "pending", "failed"}
    assert body["delivered_to"] == "b***@example.com"
    if body["delivery"] in {"sent", "pending"}:
        assert "license_key" not in body
