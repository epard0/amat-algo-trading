"""The `curl | sh` installer, checked as a program rather than as a string.

Two kinds of test live here. The first kind reads the rendered script and
asserts the portability rules that are invisible until someone runs it on a Mac
— a GNU-only flag fails on exactly the machines nobody develops on. The second
kind runs it, against a fake storefront in a temp directory, because an
installer that has never been executed has never been tested.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "storefront" / "web" / "install.sh.template"
BASE = "http://127.0.0.1"


@pytest.fixture(scope="module")
def script() -> str:
    """The template as the server renders it."""
    return TEMPLATE.read_text(encoding="utf-8").replace("${BASE_URL}", "https://kx15.test")


@pytest.fixture(scope="module")
def code(script: str) -> str:
    """The script with comments and help text stripped.

    The portability rules below are about what the shell executes. This file
    explains most of them in prose right next to the code that obeys them, so
    matching the raw text would fail on its own documentation.
    """
    lines = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    usage = "\n".join(lines)
    # Drop the launcher's own help text, which is data, not code.
    return re.sub(r"cat <<'USAGE'.*?^USAGE$", "", usage, flags=re.S | re.M)


# ------------------------------------------------------------ portability ---

def test_the_shebang_is_posix_sh(script: str) -> None:
    """Alpine has no bash and macOS ships 3.2; neither can be assumed away."""
    assert script.splitlines()[0] == "#!/bin/sh"


@pytest.mark.parametrize("bashism", ["[[", "declare ", "local ", "function ",
                                     "${!", "=~", "&>", "<<<"])
def test_no_bashisms(code: str, bashism: str) -> None:
    assert bashism not in code


def test_sed_is_never_used_in_place(code: str) -> None:
    """BSD sed requires an argument to -i and GNU sed refuses one.

    There is no spelling of `sed -i` that works on both, so the substitution is
    a redirect into a second file instead.
    """
    assert not re.search(r"\bsed\s+-i\b", code)


def test_mktemp_is_given_a_template(code: str) -> None:
    """BSD mktemp has no default template; a bare `mktemp -d` fails on macOS."""
    assert re.search(r"mktemp -d \"\$\{TMPDIR:-/tmp\}/kx15\.XXXXXX\"", code)


def test_tar_does_not_rely_on_strip_components(code: str) -> None:
    """busybox tar does not implement it, so the top directory is stripped by hand."""
    assert "--strip-components" not in code


@pytest.mark.parametrize("tool", ["sha256sum", "shasum", "sha256", "cksum", "openssl"])
def test_every_common_sha256_tool_is_tried(script: str, tool: str) -> None:
    """Linux, macOS, FreeBSD, NetBSD and a bare container each ship a different one."""
    assert tool in script


def test_wget_is_a_fallback_for_curl(script: str) -> None:
    assert "wget" in script


def test_windows_gets_an_explanation_rather_than_a_failure(script: str) -> None:
    assert "MINGW*" in script and "WSL2" in script


def test_root_is_not_required(script: str) -> None:
    """Without sudo the install lands under $HOME instead of failing."""
    assert "KX15_USER_INSTALL" in script
    assert "$HOME/.local/kx15" in script


def test_config_follows_the_xdg_variable(script: str) -> None:
    assert "${XDG_CONFIG_HOME:-$HOME/.config}/kx15" in script


def test_it_never_prompts(code: str) -> None:
    """stdin is the script itself under `curl | sh`.

    A `read` would eat the rest of the program, so every choice the *installer*
    makes comes from the environment. The launcher it writes is a different
    matter: that is an ordinary command with an ordinary terminal on stdin, and
    `kx15 uninstall` is required to ask before it deletes anything. So the rule
    is checked against the installer body with the launcher removed.
    """
    installer = re.sub(r"cat > \"\$TMP/kx15\" <<'LAUNCHER'.*?^LAUNCHER$", "",
                       code, flags=re.S | re.M)
    assert not re.search(r"^\s*read\s", installer, re.MULTILINE)


def test_only_the_uninstall_confirmation_reads_from_the_terminal(script: str) -> None:
    """And it reads exactly once, to confirm a deletion."""
    launcher = re.search(r"cat > \"\$TMP/kx15\" <<'LAUNCHER'\n(.*?)\nLAUNCHER$",
                         script, flags=re.S | re.M)
    assert launcher is not None
    reads = re.findall(r"^\s*read\s.*$", launcher.group(1), re.MULTILINE)
    assert len(reads) == 1, reads
    assert "reply" in reads[0]


def test_an_unrendered_template_refuses_to_run() -> None:
    result = subprocess.run(
        ["sh", str(TEMPLATE), "SOME-KEY"],
        capture_output=True, text=True, env={"PATH": os.environ["PATH"]},
    )

    assert result.returncode != 0
    assert "not rendered by the server" in result.stderr


def test_the_rendered_script_parses(script: str, tmp_path: Path) -> None:
    path = tmp_path / "install.sh"
    path.write_text(script, encoding="utf-8")

    assert subprocess.run(["sh", "-n", str(path)]).returncode == 0


# ------------------------------------------------------------ end to end ----

class FakeStore(BaseHTTPRequestHandler):
    """A storefront just real enough to install from."""

    archive: bytes = b""
    digest: str = ""
    valid: bool = True

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        route = self.path.split("?")[0]
        if route == "/api/validate":
            body = json.dumps({"valid": FakeStore.valid}).encode()
        elif route == "/api/artifact-sha256":
            body = FakeStore.digest.encode()
        elif route == "/api/install":
            body = FakeStore.archive
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_archive(tmp_path: Path) -> bytes:
    """A tarball shaped like the real release: one top-level directory."""
    source = tmp_path / "kx15-1.2.3"
    source.mkdir()
    (source / "bot.py").write_text("print('bot')\n", encoding="utf-8")
    (source / "requirements.txt").write_text("", encoding="utf-8")
    (source / ".env.example").write_text("ENABLE_LIVE_TRADING=false\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[project]\nname = "kx15"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    for extra in ("view_paper_trades.py", "view_decisions.py"):
        (source / extra).write_text("print('x')\n", encoding="utf-8")
    tar_path = tmp_path / "kx15.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(source, arcname=source.name)
    return tar_path.read_bytes()


@pytest.fixture
def store(tmp_path_factory):
    FakeStore.archive = build_archive(tmp_path_factory.mktemp("release"))
    FakeStore.digest = hashlib.sha256(FakeStore.archive).hexdigest()
    FakeStore.valid = True
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeStore)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"{BASE}:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def install(script_dir: Path, store_url: str, target: Path, **env) -> subprocess.CompletedProcess:
    rendered = TEMPLATE.read_text(encoding="utf-8").replace("${BASE_URL}", store_url)
    path = script_dir / "install.sh"
    path.write_text(rendered, encoding="utf-8")
    for directory in ("home", "tmp"):
        (target / directory).mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["sh", str(path), "KX15-TESTS-TESTS-TESTS-TESTS"],
        capture_output=True, text=True, timeout=300,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(target / "home"),
            "TMPDIR": str(target / "tmp"),
            "KX15_USER_INSTALL": "1",
            "KX15_PREFIX": str(target / "opt"),
            "KX15_BIN_DIR": str(target / "bin"),
            "KX15_CONFIG": str(target / "config"),
            **env,
        },
    )


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_a_full_install_produces_a_working_command(tmp_path, store) -> None:
    result = install(tmp_path, store, tmp_path / "t")

    assert result.returncode == 0, result.stderr
    launcher = tmp_path / "t" / "bin" / "kx15"
    assert launcher.is_file() and os.access(launcher, os.X_OK)
    assert (tmp_path / "t" / "opt" / "bot.py").is_file()
    assert (tmp_path / "t" / "opt" / ".venv" / "bin" / "python").is_file()


def test_credentials_are_not_world_readable(tmp_path, store) -> None:
    install(tmp_path, store, tmp_path / "t")
    config = tmp_path / "t" / "config"

    assert config.stat().st_mode & 0o077 == 0
    assert (config / ".env").stat().st_mode & 0o077 == 0
    assert (config / "licence").stat().st_mode & 0o077 == 0


def test_reinstalling_keeps_your_configuration(tmp_path, store) -> None:
    """An upgrade must never overwrite the file holding the API key."""
    install(tmp_path, store, tmp_path / "t")
    env_file = tmp_path / "t" / "config" / ".env"
    env_file.write_text("KALSHI_API_KEY=mine\n", encoding="utf-8")

    result = install(tmp_path, store, tmp_path / "t")

    assert result.returncode == 0, result.stderr
    assert env_file.read_text(encoding="utf-8") == "KALSHI_API_KEY=mine\n"


def test_a_tampered_archive_is_refused(tmp_path, store) -> None:
    FakeStore.digest = "0" * 64

    result = install(tmp_path, store, tmp_path / "t")

    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not (tmp_path / "t" / "bin" / "kx15").exists()


def test_a_missing_digest_fails_closed(tmp_path, store) -> None:
    """Silently installing unverified bytes is the wrong side to fail on."""
    FakeStore.digest = ""

    result = install(tmp_path, store, tmp_path / "t")

    assert result.returncode != 0
    assert "did not publish a checksum" in result.stderr


def test_a_rejected_licence_downloads_nothing(tmp_path, store) -> None:
    FakeStore.valid = False

    result = install(tmp_path, store, tmp_path / "t")

    assert result.returncode != 0
    assert "not accepted" in result.stderr
    assert not (tmp_path / "t" / "opt").exists()


def test_the_launcher_reports_the_installed_version(tmp_path, store) -> None:
    install(tmp_path, store, tmp_path / "t")

    result = subprocess.run(
        [str(tmp_path / "t" / "bin" / "kx15"), "version"],
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path / "t" / "home"),
             "KX15_CONFIG": str(tmp_path / "t" / "config")},
    )

    assert result.returncode == 0
    assert "1.2.3" in result.stdout


def test_the_launcher_rejects_an_unknown_subcommand(tmp_path, store) -> None:
    install(tmp_path, store, tmp_path / "t")

    result = subprocess.run(
        [str(tmp_path / "t" / "bin" / "kx15"), "nonsense"],
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path / "t" / "home"),
             "KX15_CONFIG": str(tmp_path / "t" / "config")},
    )

    assert result.returncode == 1
    assert "unknown command" in result.stderr


def test_the_launcher_with_no_arguments_prints_help(tmp_path, store) -> None:
    """`set -u` plus a bare `shift` is a classic way to make this exit 1."""
    install(tmp_path, store, tmp_path / "t")

    result = subprocess.run(
        [str(tmp_path / "t" / "bin" / "kx15")],
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path / "t" / "home"),
             "KX15_CONFIG": str(tmp_path / "t" / "config")},
    )

    assert result.returncode == 0
    assert "kx15 run" in result.stdout


# ------------------------------------------------------- where state lives ---

def test_the_launcher_pins_state_outside_the_install_directory(code: str) -> None:
    """`kx15 update` replaces the install directory and deletes the old one.

    The ledger, decision log and mode control file were written there, so an
    upgrade destroyed the user's entire trading history — and `kx15 where`
    reported the config directory, so they could not even find it first.
    """
    assert "KX15_STATE_DIR" in code
    assert 'KX15_STATE_DIR="$CONFIG_DIR"' in code


def test_the_launcher_exports_its_home(code: str) -> None:
    """Read but never exported, so every installed copy reported its version as
    "unknown" — one of the four signals the licence check has."""
    assert 'KX15_HOME="$INSTALL_DIR"; export KX15_HOME' in code


def test_the_licence_key_never_appears_in_a_url(code: str) -> None:
    """A URL lands in the access log and in every proxy in between."""
    import re

    assert not re.search(r"[?&]key=", code)
    assert "X-KX15-License" in code


def test_state_paths_follow_the_state_directory(tmp_path, monkeypatch) -> None:
    """The setting the launcher relies on, tested against the real config."""
    import config

    monkeypatch.setenv("KX15_STATE_DIR", str(tmp_path))
    for name in ("TRADE_LEDGER_PATH", "DECISION_LOG_PATH",
                 "TRADE_LOG_PATH", "MODE_CONTROL_PATH"):
        monkeypatch.delenv(name, raising=False)

    settings = config.KalshiSettings.from_env()

    for path in (settings.ledger_path, settings.decision_log_path,
                 settings.trade_log_path, settings.mode_control_path):
        assert path.parent == tmp_path.resolve(), path


def test_without_the_variable_a_checkout_is_unchanged(monkeypatch) -> None:
    """A source checkout keeps writing beside the code, as it always has."""
    from pathlib import Path

    import config

    monkeypatch.delenv("KX15_STATE_DIR", raising=False)
    for name in ("TRADE_LEDGER_PATH", "DECISION_LOG_PATH"):
        monkeypatch.delenv(name, raising=False)

    settings = config.KalshiSettings.from_env()

    assert settings.ledger_path.parent == Path(config.__file__).resolve().parent
