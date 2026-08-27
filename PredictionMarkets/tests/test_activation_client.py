"""The client half of activation: caching, and failing open.

Every test here is really the same test asked a different way — does a
customer keep trading when the licence server does not answer. That is the
property that matters, because the alternative makes the seller's uptime a
dependency of somebody else's money.
"""

from __future__ import annotations

import json
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from licensing import activate as client
from licensing.fingerprint import device_report, machine_hash

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
KEY = "KX15-AAAAA-BBBBB-CCCCC-DDDDD"


class Server(BaseHTTPRequestHandler):
    answer: dict = {"allow": True, "band": "allow", "score": 0, "recheck_after_days": 7}
    status: int = 200
    received: list[dict] = []

    def log_message(self, *_args) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        Server.received.append(json.loads(self.rfile.read(length) or b"{}"))
        body = json.dumps(Server.answer).encode()
        self.send_response(Server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    Server.answer = {"allow": True, "band": "allow", "score": 0, "recheck_after_days": 7}
    Server.status = 200
    Server.received = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def home(tmp_path) -> Path:
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "licence").write_text(f"KX15_LICENSE={KEY}\n", encoding="utf-8")
    return directory


# --------------------------------------------------------- fingerprinting ---

def test_only_a_digest_leaves_the_machine(home) -> None:
    """The inputs are local facts; the output is opaque."""
    import getpass
    import platform

    report = device_report(home, KEY, "2.0.0")

    leaked = {platform.node(), str(Path.home())}
    try:
        leaked.add(getpass.getuser())
    except Exception:  # noqa: BLE001
        pass
    serialised = json.dumps(report)
    assert not any(value and value in serialised for value in leaked)


def test_the_report_carries_only_the_documented_fields(home) -> None:
    """Scope creep here is a privacy regression, so the shape is pinned."""
    assert set(device_report(home, KEY, "2.0.0")) == {
        "machine_hash", "os_family", "arch", "app_version",
        "python_version", "tz_offset_minutes",
    }


def test_the_fingerprint_is_stable_across_runs(home) -> None:
    """An identifier that moves makes every honest customer look like a sharer."""
    assert machine_hash(home, KEY) == machine_hash(home, KEY)


def test_one_machine_is_not_linkable_across_licences(home) -> None:
    assert machine_hash(home, KEY) != machine_hash(home, "KX15-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ")


def test_the_install_id_is_written_once_and_owner_only(home) -> None:
    from licensing.fingerprint import install_id

    first = install_id(home)

    assert install_id(home) == first
    assert (home / "install-id").stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------- happy ---

def test_a_successful_check_is_cached(home, server) -> None:
    result = client.check(directory=home, url=server, now=NOW)

    assert result["allow"] is True
    assert json.loads((home / "activation.json").read_text())["allow"] is True


def test_ensure_uses_the_cache_before_it_asks_again(home, server) -> None:
    client.check(directory=home, url=server, now=NOW)
    Server.received.clear()

    result = client.ensure(directory=home, url=server, now=NOW + timedelta(days=1))

    assert result["cached"] is True
    assert Server.received == []          # no second request


def test_a_stale_cache_is_refreshed(home, server) -> None:
    client.check(directory=home, url=server, now=NOW)
    Server.received.clear()

    client.ensure(directory=home, url=server, now=NOW + timedelta(days=30))

    assert len(Server.received) == 1


def test_the_key_and_the_report_are_what_gets_sent(home, server) -> None:
    client.check(directory=home, url=server, now=NOW)

    sent = Server.received[0]
    assert sent["key"] == KEY
    assert len(sent["device"]["machine_hash"]) == 64


# ----------------------------------------------------------- failing open ---

def test_an_unreachable_server_allows(home) -> None:
    result = client.check(directory=home, url="http://127.0.0.1:9", now=NOW)

    assert result["allow"] is True
    assert result["offline"] is True


def test_an_unreachable_server_reuses_the_last_answer(home, server) -> None:
    client.check(directory=home, url=server, now=NOW)

    result = client.check(directory=home, url="http://127.0.0.1:9", now=NOW)

    assert result["allow"] is True
    assert "cached decision" in result["detail"]


def test_a_server_error_allows(home, server) -> None:
    """A 500 is the seller having a bad day, not a verdict on the customer."""
    Server.status = 500

    result = client.check(directory=home, url=server, now=NOW)

    assert result["allow"] is True
    assert result["offline"] is True


def test_a_missing_licence_key_allows(tmp_path) -> None:
    """Running from a source checkout is not piracy."""
    result = client.check(directory=tmp_path / "empty", url="http://example.invalid")

    assert result["allow"] is True


def test_no_configured_server_allows(home) -> None:
    result = client.check(directory=home, url="", now=NOW)

    assert result["allow"] is True


def test_garbage_from_the_server_allows(home, server, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise json.JSONDecodeError("nonsense", "", 0)

    monkeypatch.setattr(client, "call_server", broken)

    assert client.check(directory=home, url=server, now=NOW)["allow"] is True


# --------------------------------------------------------------- refusal ---

def test_an_explicit_refusal_is_honoured(home, server) -> None:
    """The one case that blocks: the server answered, and the answer was no."""
    Server.status = 403
    Server.answer = {"allow": False, "band": "block", "detail": "too many machines"}

    result = client.check(directory=home, url=server, now=NOW)

    assert result["allow"] is False
    assert result["detail"] == "too many machines"


def test_a_refusal_exits_non_zero(home, server) -> None:
    Server.status = 403
    Server.answer = {"allow": False, "band": "block", "detail": "too many machines"}

    assert client.main(["--ensure", "--config", str(home), "--url", server]) == 2


def test_an_allowed_check_exits_zero(home, server) -> None:
    assert client.main(["--ensure", "--config", str(home), "--url", server]) == 0


def test_show_report_sends_nothing(home, server, capsys) -> None:
    """Somebody should be able to see the payload before it is ever sent."""
    assert client.main(["--show-report", "--config", str(home)]) == 0

    assert Server.received == []
    assert "machine_hash" in capsys.readouterr().out


def test_activate_stores_the_key_owner_only(home, server) -> None:
    client.main(["--activate", KEY, "--config", str(home), "--url", server])

    assert (home / "licence").stat().st_mode & 0o077 == 0
    assert KEY in (home / "licence").read_text()


def test_a_corrupt_cache_is_ignored_rather_than_fatal(home) -> None:
    (home / "activation.json").write_text("{ not json", encoding="utf-8")

    assert client.read_grant(home) == {}
    assert client.ensure(directory=home, url="http://127.0.0.1:9")["allow"] is True


def test_nothing_in_licensing_is_imported_by_the_bot() -> None:
    """A licence check on the trading path is a new way to lose a position."""
    source = (Path(__file__).resolve().parent.parent / "bot.py").read_text()

    assert "licensing" not in source


def test_an_http_error_object_without_a_body_still_allows(home, server, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise urllib.error.HTTPError(server, 502, "bad gateway", {}, None)

    monkeypatch.setattr(client, "call_server", refuse)

    assert client.check(directory=home, url=server, now=NOW)["allow"] is True
