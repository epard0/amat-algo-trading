"""``amat`` and ``newsdesk`` must not import each other, in either direction.

The decision this enforces: news feeds the strategy through a *database*, not
through a function call. A strategy version reads point-in-time features out of
the store; it never reaches an API while forming a signal.

Both directions matter, for different reasons.

``amat`` importing ``newsdesk``
    would put an HTTP client, a scheduler, and a SQLite writer inside the
    backtest core, and would make a signal computation capable of a network
    call. ``tests/unit/test_read_only_data_access.py`` bounds that surface to
    one read-only host today; this keeps the other package from routing around
    it.

``newsdesk`` importing ``amat``
    would couple the collector to the strategy's release cycle and let a news
    bug reach the ledger. It also makes the "separate process" claim false --
    a separate process that imports the trading core is not separate in any way
    that matters.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
AMAT = _ROOT / "src" / "amat"
NEWSDESK = _ROOT / "src" / "newsdesk"

#: Transport libraries. ``newsdesk`` is permitted these in its client modules
#: only; ``amat`` is bounded separately by test_read_only_data_access.py.
TRANSPORT_ROOTS = {"urllib", "http", "socket", "ssl", "requests", "httpx", "aiohttp"}

#: The one module family in newsdesk allowed to perform network transport.
#: Widening this list is a deliberate, reviewable act.
NETWORK_CAPABLE = {
    "clients/marketaux.py",
    "clients/adanos.py",
    "clients/alpaca.py",
    "clients/base.py",
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
    return found


def _files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py")) if root.is_dir() else []


@pytest.mark.parametrize("path", _files(AMAT), ids=lambda p: p.name)
def test_amat_never_imports_newsdesk(path: Path) -> None:
    offenders = [name for name in _imports(path) if name.split(".")[0] == "newsdesk"]
    assert not offenders, (
        f"{path.relative_to(AMAT)} imports {offenders}; news reaches the strategy "
        "through the database, never through a call from the backtest core"
    )


@pytest.mark.parametrize("path", _files(NEWSDESK), ids=lambda p: p.name)
def test_newsdesk_never_imports_amat(path: Path) -> None:
    offenders = [name for name in _imports(path) if name.split(".")[0] == "amat"]
    assert not offenders, (
        f"{path.relative_to(NEWSDESK)} imports {offenders}; a collector that imports "
        "the trading core is not a separate process"
    )


@pytest.mark.parametrize("path", _files(NEWSDESK), ids=lambda p: p.name)
def test_only_declared_client_modules_reach_the_network(path: Path) -> None:
    relative = path.relative_to(NEWSDESK).as_posix()
    if relative in NETWORK_CAPABLE:
        return
    # `urllib.parse` is exempted by exact name: it is a string library with no
    # socket capability, and the store legitimately splits URLs to build dedup
    # keys. `urllib.request`/`urllib.error` -- the transport half -- remain
    # confined to the client modules.
    offending = {
        name.split(".")[0] for name in _imports(path) if name != "urllib.parse"
    } & TRANSPORT_ROOTS
    assert not offending, (
        f"{relative} imports network transport {sorted(offending)}; "
        f"only {sorted(NETWORK_CAPABLE)} may do so"
    )


def test_the_store_is_stdlib_only() -> None:
    """The store carries no third-party dependency.

    ``amat`` today has zero third-party HTTP dependencies and enforces it. The
    news package would have been the easy place to lose that property: the
    obvious PyPI candidate for this job, ``financial-news-scraper``, declares 18
    direct dependencies and would have pulled three separate HTTP stacks in
    behind it. It was rejected on supply-chain grounds -- its declared
    repository and its GitHub user both return 404, so its source cannot be
    audited against anything.
    """
    third_party = {"requests", "httpx", "aiohttp", "pandas", "sqlalchemy", "pydantic"}
    store = NEWSDESK / "store.py"
    offending = {name.split(".")[0] for name in _imports(store)} & third_party
    assert not offending, f"store.py imports {sorted(offending)}"


#: HTTP verbs that change remote state. Assembled from parts so this file's own
#: source does not match the scan it performs.
_MUTATING = ("PO" + "ST", "PU" + "T", "PA" + "TCH", "DEL" + "ETE")


@pytest.mark.parametrize("path", _files(NEWSDESK), ids=lambda p: p.name)
def test_no_mutating_http_verb_literal_exists_anywhere(path: Path) -> None:
    """The clients are GET-only structurally: "GET" is a literal in base.py and
    no mutating verb literal exists in the package, so a write path cannot be
    added without failing this test. Adanos documents one POST endpoint
    (sentiment analyze); this package must never be able to express it."""
    text = path.read_text(encoding="utf-8")
    found = [verb for verb in _MUTATING if f'"{verb}"' in text or f"'{verb}'" in text]
    assert not found, f"{path.name} contains mutating HTTP verb literal(s) {found}"


@pytest.mark.parametrize("path", _files(NEWSDESK), ids=lambda p: p.name)
def test_transport_calls_are_concentrated_in_base(path: Path) -> None:
    """`urlopen` appears in exactly one module. The vendor clients build
    requests as data and inherit transport, so auditing the socket layer means
    reading one function, not four."""
    if path.relative_to(NEWSDESK).as_posix() == "clients/base.py":
        return
    assert "urlopen" not in path.read_text(encoding="utf-8"), (
        f"{path.name} performs its own transport; all socket work belongs to clients/base.py"
    )


@pytest.mark.parametrize("path", _files(NEWSDESK), ids=lambda p: p.name)
def test_the_whole_package_is_stdlib_only(path: Path) -> None:
    """Zero third-party dependencies, matching amat's own network posture. The
    obvious PyPI candidate (financial-news-scraper) was rejected on supply-chain
    grounds: its declared repository and its GitHub user both 404."""
    third_party = {
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "pandas",
        "polars",
        "numpy",
        "pydantic",
        "sqlalchemy",
        "feedparser",
        "bs4",
        "lxml",
    }
    offending = {name.split(".")[0] for name in _imports(path)} & third_party
    assert not offending, f"{path.relative_to(NEWSDESK)} imports {sorted(offending)}"
