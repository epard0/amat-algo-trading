"""Licence key format, entropy, and what the database is allowed to remember."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter

import pytest

from storefront.licenses import (
    KEY_ALPHABET,
    REVEAL_WINDOW_HOURS,
    LicenseStore,
    generate_license_key,
    hash_license_key,
    normalise_license_key,
)

SHAPE = re.compile(r"^KX15(-[0-9A-HJKMNP-TV-Z]{5}){4}$")
ISSUE = dict(
    stripe_session_id="cs_1", email="a@b.test", payment_intent="pi_1",
    amount_cents=10000, currency="usd", livemode=False,
)


@pytest.fixture
def store(tmp_path) -> LicenseStore:
    return LicenseStore(tmp_path / "s.db", secret="unit-test-secret-of-sufficient-length")


# --------------------------------------------------------------- format ----

def test_the_shape_is_stable() -> None:
    for _ in range(50):
        assert SHAPE.match(generate_license_key())


def test_keys_avoid_every_confusable_glyph() -> None:
    """I, L, O and U never appear, so a key can be read off a receipt."""
    body = "".join(generate_license_key() for _ in range(500))

    assert not set(body) & set("ILOU")


def test_keys_do_not_repeat() -> None:
    assert len({generate_license_key() for _ in range(5000)}) == 5000


def test_the_alphabet_is_used_uniformly() -> None:
    """secrets.choice rejection-samples, so there is no modulo bias.

    A generator that took `randbytes` modulo 32 would skew toward the first
    few symbols; with 32 dividing 256 it happens not to, which is exactly the
    kind of accident worth pinning down rather than relying on.
    """
    counts = Counter("".join(generate_license_key()[5:].replace("-", "")
                             for _ in range(4000)))

    assert set(counts) == set(KEY_ALPHABET)
    expected = sum(counts.values()) / len(KEY_ALPHABET)
    assert max(counts.values()) < expected * 1.25
    assert min(counts.values()) > expected * 0.75


def test_the_random_part_carries_ninety_five_bits() -> None:
    """19 characters over a 32-symbol alphabet. Stated so a change is deliberate."""
    import math

    key = generate_license_key()
    random_characters = len(key.replace("-", "")) - len("KX15") - 1

    assert random_characters * math.log2(len(KEY_ALPHABET)) == pytest.approx(95)


# --------------------------------------------------------- normalisation ----

def test_a_key_round_trips_through_its_own_normaliser() -> None:
    key = generate_license_key()

    assert normalise_license_key(key) == key


@pytest.mark.parametrize("mangle", [
    str.lower,
    lambda k: k.replace("-", ""),
    lambda k: f"  {k}\n",
    lambda k: k.replace("-", " - "),
    lambda k: k.lower().replace("-", ""),
])
def test_realistic_mangling_still_resolves(mangle) -> None:
    """People paste keys out of emails, terminals and phone screens."""
    key = generate_license_key()

    assert normalise_license_key(mangle(key)) == key


def test_confusable_letters_are_read_the_way_a_human_meant_them() -> None:
    """O for 0 and I for 1 are transcription errors, not different keys."""
    key = generate_license_key()

    assert normalise_license_key(key.replace("0", "O")) == key
    assert normalise_license_key(key.replace("1", "I")) == key


def test_a_single_character_typo_is_caught_without_a_lookup() -> None:
    """This is what the check character buys: rejection with no round trip.

    Averaged over many keys rather than measured on one. One check character
    over a 32-symbol alphabet catches 31/32 = 96.875% of single-character
    typos, and a threshold of 0.96 sits close enough to that mean that a single
    randomly generated key crosses it perhaps half the time. Twenty-five keys
    is ~15,000 substitutions, which concentrates tightly enough that this
    asserts the property rather than the draw.
    """
    caught = 0
    attempts = 0
    for _ in range(25):
        key = generate_license_key()
        for position in range(5, len(key)):
            if key[position] == "-":
                continue
            for replacement in KEY_ALPHABET:
                if replacement == key[position]:
                    continue
                attempts += 1
                caught += normalise_license_key(
                    key[:position] + replacement + key[position + 1:]
                ) is None

    assert attempts > 10_000
    assert caught / attempts > 0.96


@pytest.mark.parametrize("junk", ["", "hello", "KX15", "KX15-AAAAA", None,
                                  "KX15-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA",
                                  "XX99-AAAAA-AAAAA-AAAAA-AAAAA"])
def test_junk_is_refused(junk) -> None:
    assert normalise_license_key(junk) is None


def test_a_random_string_essentially_never_validates() -> None:
    import secrets

    hits = sum(
        normalise_license_key(
            "KX15-" + "-".join(
                "".join(secrets.choice(KEY_ALPHABET) for _ in range(5))
                for _ in range(4)
            )
        ) is not None
        for _ in range(2000)
    )

    assert hits < 200      # ~1/32 pass the check character, then face 95 bits


# ------------------------------------------------------------- at rest ----

def test_the_database_never_holds_the_key(store: LicenseStore) -> None:
    """A stolen storefront.db must not be a stack of working licences."""
    issued = store.issue(**ISSUE)

    raw = sqlite3.connect(store.path).execute(
        "SELECT * FROM licenses"
    ).fetchall()

    assert issued.key is not None
    assert not any(issued.key in str(value) for row in raw for value in row)


def test_the_hash_is_keyed_to_the_server_secret(tmp_path) -> None:
    """Without the secret a leaked database cannot be brute-forced offline.

    Same key, two secrets, two hashes: the work factor is the secret, not the
    hash function.
    """
    key = generate_license_key()

    assert hash_license_key(key, "one") != hash_license_key(key, "two")


def test_a_key_still_validates_after_it_is_forgotten(store: LicenseStore) -> None:
    issued = store.issue(**ISSUE)
    store._write("DELETE FROM key_reveals", ())

    assert store.validate(issued.key)["valid"] is True
    assert store.by_session("cs_1").key is None      # gone, as intended


def test_the_reveal_window_survives_a_page_refresh(store: LicenseStore) -> None:
    """The success page polls after a redirect; the key has to still be there."""
    issued = store.issue(**ISSUE)

    assert store.by_session("cs_1").key == issued.key


def test_an_expired_reveal_is_deleted_not_merely_hidden(store: LicenseStore) -> None:
    store.issue(**ISSUE)
    store._write("UPDATE key_reveals SET expires_at = '2000-01-01T00:00:00+00:00'", ())

    assert store.reveal("cs_1") is None
    assert store._one("SELECT COUNT(*) AS n FROM key_reveals")["n"] == 0


def test_the_reveal_window_is_short(store: LicenseStore) -> None:
    assert REVEAL_WINDOW_HOURS <= 24


def test_lookup_accepts_a_mangled_key(store: LicenseStore) -> None:
    issued = store.issue(**ISSUE)

    assert store.by_key(issued.key.lower().replace("-", "")) is not None


def test_validation_rejects_a_malformed_key_without_querying(store: LicenseStore) -> None:
    store.issue(**ISSUE)

    assert store.validate("not-a-key") == {"valid": False, "reason": "unknown key"}
    assert store.hash_of("not-a-key") is None


# ------------------------------------------------------------ migration ----

def test_an_existing_plaintext_database_is_migrated(tmp_path) -> None:
    """Upgrading must not invalidate keys already in customers' hands."""
    path = tmp_path / "legacy.db"
    key = generate_license_key()
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE licenses (
            key TEXT PRIMARY KEY, email TEXT,
            stripe_session_id TEXT NOT NULL UNIQUE, stripe_payment_intent TEXT,
            amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
            livemode INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    legacy.execute(
        "INSERT INTO licenses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, "a@b.test", "cs_old", "pi_old", 10000, "usd", 0, "2026-01-01", 0),
    )
    legacy.commit()
    legacy.close()

    store = LicenseStore(path, secret="unit-test-secret-of-sufficient-length")

    assert store.validate(key)["valid"] is True
    columns = {
        row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(licenses)")
    }
    assert "key" not in columns          # and the plaintext is gone with it
    assert "key_hash" in columns


# -------------------------------------------------------------- secret ----

def test_the_secret_comes_from_the_environment_first(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LICENSE_SECRET", "from-the-environment-and-long-enough!!")
    store = LicenseStore(tmp_path / "s.db")

    assert store.secret == "from-the-environment-and-long-enough!!"
    assert store.secret_source == "LICENSE_SECRET"


def test_a_generated_secret_is_owner_only_and_stable(tmp_path, monkeypatch) -> None:
    """A regenerated secret would invalidate every licence ever sold."""
    monkeypatch.delenv("LICENSE_SECRET", raising=False)
    monkeypatch.delenv("DOWNLOAD_SIGNING_SECRET", raising=False)

    first = LicenseStore(tmp_path / "s.db")
    sidecar = (tmp_path / "s.secret")

    assert sidecar.stat().st_mode & 0o077 == 0
    assert LicenseStore(tmp_path / "s.db").secret == first.secret
