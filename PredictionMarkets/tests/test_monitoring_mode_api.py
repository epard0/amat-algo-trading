"""The dashboard's one mutating route.

Driven through a real socket rather than by calling the handler directly,
because most of what is being asserted lives in the HTTP layer: the header
that defeats cross-origin form posts, the Origin check, the confirmation, and
the status codes the UI branches on.

Nothing here starts a bot or touches a ledger; the "bot" is a control file
written by hand.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from execution.mode_control import STALE_AFTER_SECONDS, ModeControl
from monitoring.server import Handler, MonitoringState, env_live_problems

LIVE_READY_ENV = """\
ENABLE_LIVE_TRADING=true
KALSHI_API_KEY=key-id
KALSHI_PRIVATE_KEY_PATH=private_key.pem
"""


@pytest.fixture
def env_file(tmp_path) -> Path:
    key = tmp_path / "private_key.pem"
    # Content is irrelevant - validate_live checks the file exists and that its
    # mode is not too broad. A literal PEM header here would also make the
    # release build refuse to ship this file.
    key.write_text("test fixture, not key material\n", encoding="utf-8")
    key.chmod(0o600)
    path = tmp_path / ".env"
    path.write_text(LIVE_READY_ENV, encoding="utf-8")
    return path


class Server:
    """A live dashboard on an ephemeral port."""

    def __init__(self, tmp_path: Path, env_file: Path, *, control: bool = True):
        self.control_path = tmp_path / "runtime_mode.json"
        self.control = ModeControl(self.control_path)
        Handler.state = MonitoringState(
            tmp_path / "trade_ledger.db", tmp_path / "decisions.jsonl"
        )
        Handler.default_mode = "paper"
        Handler.control = self.control if control else None
        Handler.env_path = env_file
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.verbose = False  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=5) as res:
            return json.loads(res.read())

    def post(self, payload: dict, *, headers: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + "/api/mode",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-KX15-Control": "1",
                     **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    # A bot that reported in moments ago.
    def bot_says(self, mode: str, *, live_available: bool, note: str = "") -> None:
        self.control.report_active(
            mode, live_available=live_available, note=note,
            now=datetime.now(timezone.utc),
        )


@pytest.fixture
def server(tmp_path, env_file):
    s = Server(tmp_path, env_file)
    yield s
    s.close()


# ------------------------------------------------------------------ reads ---

def test_mode_is_reported_before_any_bot_has_run(server: Server) -> None:
    payload = server.get("/api/mode")

    assert payload["control_enabled"] is True
    assert payload["requested"] == "paper"
    assert payload["bot_seen"] is False
    assert payload["stale"] is True


def test_a_reporting_bot_shows_as_seen(server: Server) -> None:
    server.bot_says("paper", live_available=True)

    payload = server.get("/api/mode")

    assert payload["bot_seen"] is True
    assert payload["stale"] is False
    assert payload["live_available"] is True


def test_an_old_report_is_not_treated_as_available(server: Server) -> None:
    """A stopped bot must not present as ready to go live."""
    stale = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 60)
    server.control.report_active("paper", live_available=True, now=stale)

    payload = server.get("/api/mode")

    assert payload["stale"] is True
    assert payload["live_available"] is False


def test_config_advertises_the_switch(server: Server) -> None:
    assert server.get("/api/config")["control_enabled"] is True


# ----------------------------------------------------------------- writes ---

def test_switching_to_paper_needs_no_confirmation(server: Server) -> None:
    server.control.request("live", by="test")
    server.bot_says("live", live_available=True)

    status, payload = server.post({"mode": "paper"})

    assert status == 200
    assert payload["accepted"] is True
    assert server.control.read().requested == "paper"


def test_switching_to_live_is_accepted_when_everything_is_ready(server: Server) -> None:
    server.bot_says("paper", live_available=True)

    status, payload = server.post({"mode": "live", "confirm": "LIVE"})

    assert status == 200
    assert payload["requested"] == "live"
    assert server.control.read().requested == "live"


def test_the_request_records_who_made_it(server: Server) -> None:
    server.bot_says("paper", live_available=True)
    server.post({"mode": "live", "confirm": "LIVE"})

    assert server.control.read().requested_by.startswith("dashboard@127.0.0.1")


# --------------------------------------------------------------- refusals ---

def test_live_without_the_confirmation_word_is_refused(server: Server) -> None:
    server.bot_says("paper", live_available=True)

    status, payload = server.post({"mode": "live"})

    assert status == 400
    assert payload["error"] == "confirmation_required"
    assert server.control.read().requested == "paper"


def test_live_is_refused_when_env_is_not_configured(server: Server, env_file) -> None:
    """The requirement in the operator's words: configure .env first."""
    env_file.write_text("ENABLE_LIVE_TRADING=false\n", encoding="utf-8")
    server.bot_says("paper", live_available=False)

    status, payload = server.post({"mode": "live", "confirm": "LIVE"})

    assert status == 409
    assert payload["error"] == "live_not_configured"
    assert "ENABLE_LIVE_TRADING" in payload["detail"]
    assert server.control.read().requested == "paper"


def test_live_is_refused_when_no_bot_is_running(server: Server) -> None:
    """Otherwise a request against a stopped bot looks like a successful switch."""
    status, payload = server.post({"mode": "live", "confirm": "LIVE"})

    assert status == 409
    assert payload["error"] == "no_bot_running"


def test_live_is_refused_when_the_bot_reports_it_cannot(server: Server) -> None:
    """.env can be fixed after the bot started; the bot only reads it once."""
    server.bot_says("paper", live_available=False, note="ENABLE_LIVE_TRADING was false")

    status, payload = server.post({"mode": "live", "confirm": "LIVE"})

    assert status == 409
    assert payload["error"] == "live_not_available"
    assert "restart" in payload["hint"].lower()


def test_a_stale_bot_is_refused(server: Server) -> None:
    stale = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 60)
    server.control.report_active("paper", live_available=True, now=stale)

    status, payload = server.post({"mode": "live", "confirm": "LIVE"})

    assert status == 409
    assert payload["error"] == "bot_not_responding"


def test_an_unknown_mode_is_refused(server: Server) -> None:
    status, payload = server.post({"mode": "demo"})

    assert status == 400
    assert payload["error"] == "invalid_mode"


def test_a_non_object_body_is_refused(server: Server) -> None:
    request = urllib.request.Request(
        server.base + "/api/mode", data=b'"live"', method="POST",
        headers={"Content-Type": "application/json", "X-KX15-Control": "1"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)

    assert exc.value.code == 400


# ------------------------------------------------------- cross-site safety ---

def test_a_post_without_the_control_header_is_refused(server: Server) -> None:
    """A form or an <img> on any page can reach loopback; neither can set this."""
    server.bot_says("paper", live_available=True)

    request = urllib.request.Request(
        server.base + "/api/mode",
        data=json.dumps({"mode": "live", "confirm": "LIVE"}).encode(),
        method="POST", headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)

    assert exc.value.code == 403
    assert server.control.read().requested == "paper"


def test_a_foreign_origin_is_refused(server: Server) -> None:
    server.bot_says("paper", live_available=True)

    status, payload = server.post(
        {"mode": "live", "confirm": "LIVE"},
        headers={"Origin": "https://evil.example"},
    )

    assert status == 403
    assert payload["error"] == "cross_origin"


def test_our_own_origin_is_allowed(server: Server) -> None:
    server.bot_says("paper", live_available=True)
    host = server.base.split("//", 1)[1]

    status, _ = server.post(
        {"mode": "paper"}, headers={"Origin": f"http://{host}"}
    )

    assert status == 200


def test_no_control_makes_the_dashboard_read_only(tmp_path, env_file) -> None:
    server = Server(tmp_path, env_file, control=False)
    try:
        assert server.get("/api/mode")["control_enabled"] is False
        status, payload = server.post({"mode": "paper"})
        assert status == 403
        assert payload["error"] == "control_disabled"
        assert not server.control_path.exists()
    finally:
        server.close()


# ---------------------------------------------------------- env preflight ---

def test_env_preflight_passes_a_configured_file(env_file) -> None:
    assert env_live_problems(env_file) == []


def test_env_preflight_reports_a_missing_file(tmp_path) -> None:
    problems = env_live_problems(tmp_path / "absent.env")

    assert problems and "no .env" in problems[0]


def test_env_preflight_catches_a_world_readable_key(env_file) -> None:
    """The same rule the bot enforces, not a second copy of it."""
    (env_file.parent / "private_key.pem").chmod(0o644)

    problems = env_live_problems(env_file)

    assert problems and "chmod 600" in problems[0]


def test_env_preflight_catches_a_missing_key_file(env_file) -> None:
    (env_file.parent / "private_key.pem").unlink()

    problems = env_live_problems(env_file)

    assert problems and "not found" in problems[0]
