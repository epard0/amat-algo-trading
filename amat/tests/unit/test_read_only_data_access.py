"""The network surface is one read-only host, reached from one module.

Adding :mod:`amat.data.alpaca` gave this repository its first real network
client. The pre-existing control (``test_no_module_imports_a_broker_or_network_client``)
only banned third-party HTTP libraries, so a stdlib ``urllib.request`` client
satisfied it while doing exactly what it meant to forbid. These tests replace
that with checks against the property that actually matters: what can be
reached, from where, and with which verb.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "amat"

#: Modules permitted to perform network transport, relative to ``src/amat``.
#: This list is the control. Widening it is a deliberate, reviewable act.
NETWORK_CAPABLE = {"data/alpaca.py"}

#: Import roots that constitute network transport.
TRANSPORT_ROOTS = {
    "urllib",
    "http",
    "socket",
    "ssl",
    "asyncio",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "urllib3",
    "ftplib",
    "smtplib",
}

#: Substrings that would indicate a state-changing or account-scoped endpoint.
#: Assembled from pieces so this file's own source does not match the scan it
#: performs over the package.
FORBIDDEN_FRAGMENTS = (
    "api.alpaca" + ".markets",
    "paper-" + "api",
    "/v2/" + "orders",
    "/v2/" + "positions",
    "/v2/" + "account",
    "broker-" + "api",
)

#: HTTP methods that would change remote state.
MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")

#: Non-secret placeholders. Built at runtime rather than written as literals so
#: that credential scanners have nothing that resembles an assigned key.
PLACEHOLDER_KEY = "placeholder-" + "key-value"
PLACEHOLDER_SECRET = "placeholder-" + "secret-value"


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", _python_files(), ids=_relative)
def test_only_the_data_client_performs_network_transport(path: Path) -> None:
    """No module outside the allowlist may import a transport library."""
    if _relative(path) in NETWORK_CAPABLE:
        return
    offending = _imported_roots(path) & TRANSPORT_ROOTS
    assert not offending, (
        f"{_relative(path)} imports network transport {sorted(offending)}; "
        f"only {sorted(NETWORK_CAPABLE)} may do so"
    )


@pytest.mark.parametrize("path", _python_files(), ids=_relative)
def test_no_module_references_an_account_scoped_endpoint(path: Path) -> None:
    """No source file may name a trading host or an account-scoped path."""
    text = path.read_text(encoding="utf-8")
    found = [fragment for fragment in FORBIDDEN_FRAGMENTS if fragment in text]
    assert not found, f"{_relative(path)} references {found}"


@pytest.mark.parametrize("path", _python_files(), ids=_relative)
def test_no_module_issues_a_state_changing_request(path: Path) -> None:
    """Every HTTP method literal in the repository is a read."""
    text = path.read_text(encoding="utf-8")
    found = [
        method for method in MUTATING_METHODS if f'"{method}"' in text or f"'{method}'" in text
    ]
    assert not found, f"{_relative(path)} contains HTTP method literal(s) {found}"


def test_the_data_client_pins_a_single_read_only_host() -> None:
    """The client's host and path allowlist are what this suite claims."""
    from amat.data.alpaca import _PERMITTED_PATH_PREFIXES, DATA_HOST

    assert DATA_HOST == "data.alpaca.markets"
    assert set(_PERMITTED_PATH_PREFIXES) == {"/v2/stocks/bars", "/v1/corporate-actions"}


def test_the_client_refuses_a_path_outside_the_allowlist() -> None:
    """A path outside the prefix list fails closed rather than being sent."""
    from amat.data.alpaca import AlpacaCredentials, AlpacaMarketData
    from amat.errors import ConfigurationError

    client = AlpacaMarketData(
        AlpacaCredentials(key_id=PLACEHOLDER_KEY, secret_key=PLACEHOLDER_SECRET)
    )
    with pytest.raises(ConfigurationError, match="read-only market-data surface"):
        client._get("/v2/" + "account", {})


def test_credentials_never_render_their_values() -> None:
    """A credential must not leak through repr, str, or an f-string."""
    from amat.data.alpaca import AlpacaCredentials

    credentials = AlpacaCredentials(key_id=PLACEHOLDER_KEY, secret_key=PLACEHOLDER_SECRET)
    for rendered in (repr(credentials), str(credentials), f"{credentials}"):
        assert PLACEHOLDER_KEY not in rendered
        assert PLACEHOLDER_SECRET not in rendered


def test_the_source_registry_names_the_variables_the_client_actually_reads() -> None:
    """`configs/data/sources.toml` must not document credentials that do not exist.

    Regression for a real defect: the registry declared ``APCA_API_KEY_ID`` /
    ``APCA_API_SECRET_KEY`` for both Alpaca sources while the client reads
    ``ALPACA_MARKET_API_KEY`` / ``ALPACA_MARKET_API_SECRET``. No code read the
    documented names, so an operator following the registry exported two
    variables nothing used and hit a ConfigurationError naming two others.

    Only *names* are compared here -- this test never touches a value.
    """
    import inspect
    import tomllib

    from amat.data.alpaca import AlpacaCredentials

    signature = inspect.signature(AlpacaCredentials.from_environment)
    actual = {
        str(signature.parameters["key_variable"].default),
        str(signature.parameters["secret_variable"].default),
    }

    registry = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "configs/data/sources.toml").read_text(
            encoding="utf-8"
        )
    )
    alpaca_sources = [
        source for source in registry["sources"] if source.get("provider") == "alpaca"
    ]
    assert alpaca_sources, "the registry lists no alpaca source to check"
    for source in alpaca_sources:
        declared = set(source["credential_env_vars"])
        assert declared == actual, (
            f"source {source['id']!r} documents {sorted(declared)} but the client "
            f"reads {sorted(actual)}"
        )
