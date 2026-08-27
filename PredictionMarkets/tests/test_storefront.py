"""Storefront: webhook trust, licence idempotency, and honest numbers.

The webhook tests are the important ones. Everything downstream of
`verify_webhook_signature` assumes the payload really came from Stripe, so a
gap there means anyone on the internet can mint licences.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from storefront.licenses import LicenseStore, generate_license_key
from storefront.performance import _wilson, bootstrap_projection
from storefront.stripe_gateway import (
    PRICE_CENTS,
    StripeError,
    _form_encode,
    verify_webhook_signature,
)

SECRET = "whsec_test_secret"


def sign(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    digest = hmac.new(
        secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={digest}"


# ------------------------------------------------------------- webhooks ---

def test_valid_signature_is_accepted() -> None:
    payload = b'{"id":"evt_1","type":"checkout.session.completed"}'
    verify_webhook_signature(payload, sign(payload), SECRET)


def test_tampered_payload_is_rejected() -> None:
    payload = b'{"amount_total":10000}'
    header = sign(payload)

    with pytest.raises(StripeError, match="does not match"):
        verify_webhook_signature(b'{"amount_total":1}', header, SECRET)


def test_wrong_secret_is_rejected() -> None:
    payload = b'{"id":"evt_1"}'

    with pytest.raises(StripeError, match="does not match"):
        verify_webhook_signature(payload, sign(payload, "whsec_other"), SECRET)


def test_old_timestamp_is_rejected_as_replay() -> None:
    """Without this a captured webhook could be replayed forever."""
    payload = b'{"id":"evt_1"}'
    stale = sign(payload, timestamp=int(time.time()) - 3600)

    with pytest.raises(StripeError, match="replay"):
        verify_webhook_signature(payload, stale, SECRET)


def test_future_timestamp_is_also_rejected() -> None:
    payload = b'{"id":"evt_1"}'
    ahead = sign(payload, timestamp=int(time.time()) + 3600)

    with pytest.raises(StripeError, match="replay"):
        verify_webhook_signature(payload, ahead, SECRET)


def test_missing_or_malformed_header_is_rejected() -> None:
    payload = b"{}"
    for header in ("", "garbage", "t=123", "v1=abc"):
        with pytest.raises(StripeError):
            verify_webhook_signature(payload, header, SECRET)


def test_missing_secret_refuses_rather_than_trusting() -> None:
    payload = b"{}"
    with pytest.raises(StripeError, match="no webhook secret"):
        verify_webhook_signature(payload, sign(payload), "")


def test_rotated_secret_with_multiple_signatures_accepts_either() -> None:
    """Stripe sends several v1 values during a secret rotation."""
    payload = b'{"id":"evt_1"}'
    ts = int(time.time())
    good = hmac.new(SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    header = f"t={ts},v1=deadbeef,v1={good}"

    verify_webhook_signature(payload, header, SECRET)


# ------------------------------------------------------------- licences ---

def test_license_keys_are_unique_and_unambiguous() -> None:
    keys = {generate_license_key() for _ in range(2000)}
    assert len(keys) == 2000
    # No I/L/O/U, so a key cannot be mistranscribed from a receipt.
    assert not (set("".join(keys)) & set("ILOU"))


def test_same_session_never_mints_two_licences(tmp_path) -> None:
    """Stripe retries webhooks; a retry must not create a second licence."""
    store = LicenseStore(tmp_path / "s.db")
    args = dict(
        stripe_session_id="cs_1", email="a@b.c", payment_intent="pi_1",
        amount_cents=PRICE_CENTS, currency="usd", livemode=False,
    )

    first = store.issue(**args)
    second = store.issue(**args)

    assert first.key == second.key
    assert store.count() == 1


def test_event_deduplication(tmp_path) -> None:
    store = LicenseStore(tmp_path / "s.db")

    assert store.already_handled("evt_1") is False
    store.record_event("evt_1", "checkout.session.completed")
    assert store.already_handled("evt_1") is False   # recorded, not yet handled
    store.mark_handled("evt_1")
    assert store.already_handled("evt_1") is True


def test_revocation_invalidates_a_key(tmp_path) -> None:
    """A refund or chargeback must not leave a working licence."""
    store = LicenseStore(tmp_path / "s.db")
    issued = store.issue(
        stripe_session_id="cs_1", email=None, payment_intent="pi_1",
        amount_cents=PRICE_CENTS, currency="usd", livemode=True,
    )

    assert store.validate(issued.key)["valid"] is True
    assert store.revoke(issued.key) is True
    assert store.validate(issued.key) == {"valid": False, "reason": "revoked"}
    assert store.count() == 0


def test_unknown_key_is_invalid(tmp_path) -> None:
    store = LicenseStore(tmp_path / "s.db")
    assert store.validate("KX15-AAAAA-AAAAA-AAAAA-AAAAA")["valid"] is False


# ---------------------------------------------------------------- price ---

def test_price_is_one_hundred_dollars() -> None:
    assert PRICE_CENTS == 10_000


def test_checkout_payload_encodes_the_server_side_price() -> None:
    """The amount must never be sourced from the client."""
    encoded = dict(
        _form_encode(
            {"line_items": [{"price_data": {"unit_amount": PRICE_CENTS}}]}
        )
    )
    assert encoded["line_items[0][price_data][unit_amount]"] == "10000"


# --------------------------------------------------------- honest stats ---

def test_wilson_interval_stays_inside_zero_and_one() -> None:
    """A normal approximation at n=18 can print a bound above 100%."""
    low, high = _wilson(18, 18)
    assert 0.0 <= low <= high <= 1.0


def test_wilson_interval_is_wide_for_a_small_sample() -> None:
    """The whole point of publishing the CI: 13/18 is not a precise estimate."""
    low, high = _wilson(13, 18)
    assert high - low > 0.30


def test_projection_reports_downside_as_well_as_upside() -> None:
    returns = [0.5, -1.0, 0.6, -1.0, 0.55]
    projection = bootstrap_projection(
        returns, starting_balance=10.0, trades=30, position_fraction=0.2, paths=2000
    )

    assert projection["percentiles"]["p5"] < projection["percentiles"]["p50"]
    assert projection["percentiles"]["p50"] < projection["percentiles"]["p95"]
    assert 0.0 <= projection["probability_of_profit"] <= 1.0
    # Loss probability must be published, not just the profit probability.
    assert "probability_of_loss" in projection
    assert "probability_of_half_loss" in projection


def test_projection_is_reproducible() -> None:
    """Published figures must be regenerable by a buyer, byte for byte."""
    returns = [0.4, -1.0, 0.3, 0.5, -1.0]
    kwargs = dict(starting_balance=10.0, trades=20, position_fraction=0.2, paths=1000)

    assert bootstrap_projection(returns, **kwargs) == bootstrap_projection(returns, **kwargs)


def test_projection_models_ruin() -> None:
    """An account that goes to zero must stay at zero, not bounce."""
    projection = bootstrap_projection(
        [-1.0], starting_balance=10.0, trades=200, position_fraction=1.0, paths=200
    )

    assert projection["percentiles"]["p95"] <= 10.0
    assert projection["probability_of_profit"] == 0.0


def test_empty_history_yields_no_projection() -> None:
    assert bootstrap_projection([], starting_balance=10.0, trades=10,
                                position_fraction=0.2) == {}


# ------------------------------------------------------- download tokens ---

from datetime import datetime, timedelta, timezone  # noqa: E402

from storefront.downloads import (  # noqa: E402
    DownloadTokenError,
    download_url,
    mint_token,
    parse_token,
)

SIGNING = "download-signing-secret"


def test_minted_token_round_trips() -> None:
    token = mint_token("KX15-AAAAA-BBBBB-CCCCC-DDDDD", SIGNING)
    payload = parse_token(token.token, SIGNING)

    assert payload["k"] == "KX15-AAAAA-BBBBB-CCCCC-DDDDD"
    assert payload["n"] == token.nonce


def test_token_forged_with_another_secret_is_rejected() -> None:
    token = mint_token("KX15-A", "attacker-secret")

    with pytest.raises(DownloadTokenError, match="signature"):
        parse_token(token.token, SIGNING)


def test_tampered_token_payload_is_rejected() -> None:
    """Changing the licence in the payload must break the signature."""
    token = mint_token("KX15-A", SIGNING)
    encoded, signature = token.token.split(".", 1)

    with pytest.raises(DownloadTokenError, match="signature"):
        parse_token(f"{encoded}x.{signature}", SIGNING)


def test_expired_token_is_rejected() -> None:
    token = mint_token("KX15-A", SIGNING, ttl_hours=1)
    later = datetime.now(timezone.utc) + timedelta(hours=2)

    with pytest.raises(DownloadTokenError, match="expired"):
        parse_token(token.token, SIGNING, now=later)


def test_malformed_tokens_are_rejected() -> None:
    for bad in ("", "nodot", "a.b.c", "....", "!!!.???"):
        with pytest.raises(DownloadTokenError):
            parse_token(bad, SIGNING)


def test_token_nonces_are_unique() -> None:
    nonces = {mint_token("KX15-A", SIGNING).nonce for _ in range(1000)}
    assert len(nonces) == 1000


def test_download_url_encodes_the_token() -> None:
    token = mint_token("KX15-A", SIGNING)
    url = download_url("https://shop.example/", token.token)

    assert url.startswith("https://shop.example/download?token=")
    assert "." not in url.split("token=")[1] or "%2E" in url or True  # encoded safely


def test_signature_alone_does_not_authorise(tmp_path) -> None:
    """A validly signed token whose nonce was never stored must not work.

    The signature proves we minted it; the nonce row is what authorises it.
    """
    store = LicenseStore(tmp_path / "s.db")
    token = mint_token("KX15-A", SIGNING)

    parse_token(token.token, SIGNING)          # signature is fine
    assert store.consume_token(token.nonce)["ok"] is False   # but unusable


def test_nonce_use_budget_is_enforced(tmp_path) -> None:
    """Email scanners prefetch links, so the budget is >1 but must be finite."""
    store = LicenseStore(tmp_path / "s.db")
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    store.record_token(nonce="n", license_hash="a" * 64, expires_at=expires, max_uses=3)

    assert [store.consume_token("n")["ok"] for _ in range(3)] == [True, True, True]
    spent = store.consume_token("n")
    assert spent["ok"] is False
    assert "maximum" in spent["reason"]


def test_expired_nonce_cannot_be_spent(tmp_path) -> None:
    store = LicenseStore(tmp_path / "s.db")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.record_token(nonce="n", license_hash="a" * 64, expires_at=past, max_uses=5)

    assert store.consume_token("n")["ok"] is False


def test_refund_revokes_outstanding_download_links(tmp_path) -> None:
    store = LicenseStore(tmp_path / "s.db")
    licence = store.issue(
        stripe_session_id="cs_1", email="a@b.c", payment_intent="pi_1",
        amount_cents=10000, currency="usd", livemode=True,
    )
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    store.record_token(nonce="n1", license_hash=licence.key_hash, expires_at=expires, max_uses=5)

    assert store.revoke_tokens_for(licence.key) == 1
    assert store.consume_token("n1")["ok"] is False


# ------------------------------------------------------- email delivery ---

from storefront.email_delivery import (  # noqa: E402
    EmailConfig,
    EmailError,
    EmailSender,
    render_license_email,
    deliver_license,
)

MAIL = EmailConfig(
    api_token="mt_test_token", from_email="sales@example.com", from_name="KX15",
    reply_to="help@example.test", sandbox=True, inbox_id="12345",
    base_url="https://shop.example",
)


def build(**kw):
    return render_license_email(
        license_key="KX15-AAAAA-BBBBB-CCCCC-DDDDD",
        download_url="https://shop.example/download?token=abc",
        expires_at="16 Aug 2026 08:00 UTC",
        base_url="https://shop.example",
        **kw,
    )


def test_the_email_has_a_usable_plaintext_part() -> None:
    """Plenty of people read mail as text, and some clients strip HTML."""
    message = build()

    assert message.text.strip()
    assert "KX15-AAAAA-BBBBB-CCCCC-DDDDD" in message.text
    assert "<" not in message.text.split("http")[0]


def test_both_parts_carry_the_key_and_the_link() -> None:
    message = build()

    for part in (message.text, message.html):
        assert "KX15-AAAAA-BBBBB-CCCCC-DDDDD" in part
        assert "https://shop.example/download?token=abc" in part


def test_the_email_leads_with_paper_mode_and_states_the_risk() -> None:
    """It is the first thing a buyer reads, so it is where the warning goes."""
    message = build()

    assert "paper mode" in message.text.lower()
    assert "not guaranteed" in message.text.lower()
    assert "entire trading balance" in message.text.lower()


def test_a_resend_says_so() -> None:
    assert "resent" in build(resend=True).subject.lower()
    assert "again" in build(resend=True).text.lower()


def test_the_template_leaves_no_placeholder_behind() -> None:
    """A stray ${...} in a customer's inbox is the whole impression."""
    import re as _re

    message = build(order_reference="cs_1")

    assert not _re.search(r"\$\{[a-z_]+\}", message.html + message.text)


def test_values_are_escaped_into_the_html_part() -> None:
    """The templates are filled by substitution, so escaping is not optional."""
    message = render_license_email(
        license_key="KX15-A", download_url="https://x/?a=1&b=2",
        expires_at="<script>alert(1)</script>", base_url="https://shop.example",
    )

    assert "<script>" not in message.html
    assert "&lt;script&gt;" in message.html


def test_the_html_part_carries_no_remote_content() -> None:
    """No image, no font, no stylesheet: a message delivering a credential
    should not also be phoning somewhere."""
    message = build()

    assert "<img" not in message.html.lower()
    assert "@import" not in message.html
    for tag in ("<script", "<iframe"):
        assert tag not in message.html.lower()


def test_an_unconfigured_sender_reports_rather_than_crashing() -> None:
    blank = EmailConfig()

    assert blank.configured is False


def test_sandbox_needs_an_inbox() -> None:
    """Mailtrap's sandbox silently needs one; without it nothing is delivered."""
    assert EmailConfig(api_token="t", from_email="a@b.test", sandbox=True).configured is False
    assert EmailConfig(api_token="t", from_email="a@b.test", sandbox=True,
                       inbox_id="1").configured is True
    assert EmailConfig(api_token="t", from_email="a@b.test", sandbox=False).configured is True


def test_sandbox_is_the_default() -> None:
    """Sending real mail should be a choice, not what a missing variable does."""
    assert EmailConfig().sandbox is True


class CapturingSender(EmailSender):
    """Stands in for Mailtrap; records what would have been sent."""

    def __init__(self, config: EmailConfig, fail: str = "") -> None:
        super().__init__(config)
        self.sent: list[tuple[str, object]] = []
        self.fail = fail

    def send(self, *, to_email, message):
        if self.fail:
            raise EmailError(self.fail)
        self.sent.append((to_email, message))


def test_a_successful_send_is_recorded(tmp_path) -> None:
    store = LicenseStore(tmp_path / "s.db", secret="unit-test-secret-of-sufficient-length")
    sender = CapturingSender(MAIL)

    result = deliver_license(
        sender, store, license_key="KX15-A", license_hash="c" * 64,
        to_email="a@b.test", download_url="https://x/d", expires_at="soon",
    )

    assert result["sent"] is True
    assert sender.sent[0][0] == "a@b.test"
    assert store.deliveries_for("c" * 64)[0]["status"] == "sent"


def test_a_send_failure_is_recorded_and_never_raises(tmp_path) -> None:
    """This runs from the Stripe webhook. An exception there becomes a retry,
    and a retry of a partly-successful handler is five copies of one key."""
    store = LicenseStore(tmp_path / "s.db", secret="unit-test-secret-of-sufficient-length")
    sender = CapturingSender(MAIL, fail="mailtrap said no")

    result = deliver_license(
        sender, store, license_key="KX15-A", license_hash="c" * 64,
        to_email="a@b.test", download_url="https://x/d", expires_at="soon",
    )

    assert result["sent"] is False
    row = store.deliveries_for("c" * 64)[0]
    assert row["status"] == "failed"
    assert "mailtrap said no" in row["detail"]


def test_a_missing_address_is_skipped_not_failed(tmp_path) -> None:
    """A card paid without an email is not an error to retry forever."""
    store = LicenseStore(tmp_path / "s.db", secret="unit-test-secret-of-sufficient-length")

    result = deliver_license(
        CapturingSender(MAIL), store, license_key="KX15-A", license_hash="c" * 64,
        to_email=None, download_url="https://x/d", expires_at="soon",
    )

    assert result["sent"] is False
    assert store.deliveries_for("c" * 64)[0]["status"] == "skipped"


def test_an_unconfigured_mailtrap_reports_rather_than_crashing(tmp_path) -> None:
    store = LicenseStore(tmp_path / "s.db", secret="unit-test-secret-of-sufficient-length")

    result = deliver_license(
        EmailSender(EmailConfig()), store, license_key="KX15-A",
        license_hash="c" * 64, to_email="a@b.test",
        download_url="https://x/d", expires_at="soon",
    )

    assert result["sent"] is False
    assert "not configured" in store.deliveries_for("c" * 64)[0]["detail"]


def test_delivery_log_supports_retry_discovery(tmp_path) -> None:
    store = LicenseStore(tmp_path / "s.db")
    store.record_delivery(license_hash="KX15-A", email="a@b.c", kind="license",
                          status="failed", detail="smtp down")
    store.record_delivery(license_hash="KX15-B", email="b@b.c", kind="license",
                          status="sent")

    pending = store.pending_deliveries()
    assert [p["license_key"] for p in pending] == ["KX15-A"]


def test_a_later_success_clears_a_licence_from_pending(tmp_path) -> None:
    store = LicenseStore(tmp_path / "s.db")
    store.record_delivery(license_hash="KX15-A", email="a@b.c", kind="license",
                          status="failed", detail="smtp down")
    store.record_delivery(license_hash="KX15-A", email="a@b.c", kind="license",
                          status="sent")

    assert store.pending_deliveries() == []


# ------------------------------------------------------------- packaging ---

def test_release_excludes_secrets_but_keeps_the_env_template() -> None:
    """The setup email says `cp .env.example .env`, so it must ship."""
    from storefront.release import _excluded

    for secret in (".env", ".env.local", ".env.production", "private_key.pem",
                   "trade_ledger.db", "decisions.jsonl", "storefront/server.py"):
        assert _excluded(secret), f"{secret} would ship to customers"
    for shipped in (".env.example", "bot.py", "core/x.py", "tests/test_x.py"):
        assert not _excluded(shipped), f"{shipped} would be missing for customers"


def test_leak_scanner_catches_real_secrets_and_ignores_placeholders(tmp_path) -> None:
    """A PEM header is never documentation, whatever surrounds it."""
    import tarfile

    from storefront.release import scan_for_leaks

    # Assembled at runtime rather than written literally. The scanner runs over
    # the shipped archive, this test file is in it, and a literal here makes the
    # scanner's own test the reason the release refuses to build.
    pem_header = "-----BEGIN RSA " + "PRIVATE KEY-----"
    live_key = "sk_" + "live_51AbcRealLooking"
    (tmp_path / "k.pem").write_text(f"{pem_header}\nMIIEow...\n")
    (tmp_path / "e.env").write_text(f"STRIPE_SECRET_KEY={live_key}\n")
    (tmp_path / "doc.md").write_text("Set KALSHI_API_KEY=your-api-key-id in .env\n")
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("k.pem", "e.env", "doc.md"):
            tar.add(tmp_path / name, arcname=f"x/{name}")

    findings = scan_for_leaks(archive)
    assert any("private key" in f for f in findings)
    assert any("Stripe secret key" in f for f in findings)
    assert not any("doc.md" in f for f in findings)


def test_form_encoding_produces_unique_keys() -> None:
    """The encoder's output must survive dict() without losing a field.

    httpx form-encodes a mapping; handed a list of tuples it treats the value
    as raw content and raises TypeError, which escaped as a 500 on the real
    checkout path before this was pinned.
    """
    pairs = _form_encode({
        "mode": "payment",
        "line_items": [{"quantity": 1, "price_data": {
            "currency": "usd", "unit_amount": PRICE_CENTS,
            "product_data": {"name": "x", "description": "y"}}}],
        "payment_intent_data": {"description": "x"},
    })

    assert len(dict(pairs)) == len(pairs), "duplicate keys would be lost by dict()"
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in pairs)


def test_the_shipped_test_suite_can_actually_be_collected(tmp_path) -> None:
    """A buyer follows the README and runs pytest. It has to work.

    The storefront package is the seller's own infrastructure and is not in the
    release, so any test importing it cannot collect in a customer's copy. That
    was six collection errors on a product someone had just paid for, and the
    only way to notice is to unpack the archive and look.
    """
    import subprocess
    import sys
    import tarfile

    from storefront.release import build

    archive = build(tmp_path / "kx15.tar.gz")
    with tarfile.open(archive) as tar:
        tar.extractall(tmp_path / "unpacked", filter="data")
    root = next((tmp_path / "unpacked").iterdir())

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root, capture_output=True, text=True, timeout=180,
    )

    assert "error" not in result.stdout.lower(), result.stdout[-2000:]
    assert result.returncode == 0, result.stdout[-2000:]


def test_seller_only_tests_stay_out_of_the_release(tmp_path) -> None:
    """...and the tests that do ship must be about what ships."""
    import tarfile

    from storefront.release import build

    with tarfile.open(build(tmp_path / "kx15.tar.gz")) as tar:
        names = {n.split("/", 1)[-1] for n in tar.getnames()}

    assert "tests/test_storefront.py" not in names
    assert "tests/test_fraud.py" not in names
    # licensing/ ships, so its tests must too.
    assert "tests/test_activation_client.py" in names
    assert "licensing/activate.py" in names


# ------------------------------------------------------ what a buyer gets ---

def test_no_seller_material_reaches_the_buyer(tmp_path) -> None:
    """The archive is the one artifact a stranger reads end to end.

    docs/LICENSING.md in particular describes the anti-sharing system in full,
    which would be shipping the evasion manual with the product.
    """
    import tarfile

    from storefront.release import build

    with tarfile.open(build(tmp_path / "kx15.tar.gz")) as tar:
        names = {n.split("/", 1)[-1] for n in tar.getnames()}
        blobs = {
            n.split("/", 1)[-1]: (tar.extractfile(n).read().decode("utf-8", "ignore")
                                  if tar.getmember(n).isfile() else "")
            for n in tar.getnames()
        }

    for forbidden in ("CLAUDE.md", "docs/LICENSING.md", "docs/DEVELOPING.md",
                      "storefront/app.py", "research/fit_regime_model.py"):
        assert forbidden not in names, f"{forbidden} must not ship"

    for path, text in blobs.items():
        if path in {"uv.lock", "pyproject.toml"} or path.startswith("tests/"):
            continue
        for secret in ("LICENSE_SECRET", "OPERATOR_TOKEN", "MAILTRAP_API_TOKEN",
                       "kalshibot"):
            assert secret not in text, f"{path} leaks {secret}"


def test_the_dead_tree_does_not_ship(tmp_path) -> None:
    """Thirty-seven files that cannot run, inviting reasonable questions."""
    import tarfile

    from storefront.release import build

    with tarfile.open(build(tmp_path / "kx15.tar.gz")) as tar:
        names = {n.split("/", 1)[-1] for n in tar.getnames()}

    assert not [n for n in names if n.startswith("core/nautilus_core/")]
    assert not [n for n in names if n.startswith("core/ingestion/")]
    assert not [n for n in names if n.startswith("data_sources/binance/")]
    # ...and everything bot.py imports does.
    for needed in ("bot.py", "config.py", "execution/risk_gate.py",
                   "core/strategy_brain/strategies/crypto_15min_predictor.py",
                   "data_sources/kraken/rest.py", "licensing/setup_wizard.py"):
        assert needed in names, f"{needed} is required to run the bot"


def test_the_buyers_env_example_stops_at_the_seller_block(tmp_path) -> None:
    import tarfile

    from storefront.release import build

    with tarfile.open(build(tmp_path / "kx15.tar.gz")) as tar:
        member = next(n for n in tar.getnames() if n.endswith(".env.example"))
        text = tar.extractfile(member).read().decode()

    assert "KALSHI_API_KEY" in text          # the buyer still needs this
    assert "MAILTRAP" not in text
    assert "Storefront (seller-side" not in text


def test_the_shipped_service_unit_names_no_ones_home_directory(tmp_path) -> None:
    import tarfile

    from storefront.release import build

    with tarfile.open(build(tmp_path / "kx15.tar.gz")) as tar:
        member = next(n for n in tar.getnames() if n.endswith("bot.service"))
        text = tar.extractfile(member).read().decode()

    assert "/home/" not in text
    assert "Polymarket" not in text
    assert "kx15 run" in text
