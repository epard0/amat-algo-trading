"""Every tool that builds settings must load `.env` first, the same way.

This is a drift guard for a bug that shipped three times over.

`load_dotenv` on a missing path is a **silent no-op**, so a tool that forgets it,
or looks in the wrong place, does not fail — it comes up on pure defaults and
reports the **demo** exchange while the bot beside it trades production. It is
invisible until you compare two numbers that should agree.

It happened to `bot.py` once (the docstring on `config.config_file` records it),
then to `kalshi_history.py` and `view_paper_trades.py`, which each hardcoded
`PROJECT_ROOT / ".env"` and so read nothing at all on an installed copy. Then to
`research/settlements.py`, which cached 135 **demo** settlements as if they were
production truth and labelled a training set with them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import config

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Directories a settings consumer can live in. `config.py` defines from_env
# and is not a consumer of it; tests build settings deliberately in isolation.
_SEARCH = ("", "scripts", "research", "reporting", "monitoring", "execution")
_EXEMPT = {"config.py"}


def _discover_entry_points() -> tuple[str, ...]:
    """Every module that builds settings from the environment, found by scan.

    Derived rather than listed. The hand-written list this replaces was itself
    an instance of the bug it guards: on 21 Aug 2026
    `scripts/reset_paper_stats.py` called `KalshiSettings.from_env()` without
    loading `.env`, was not on the list, and so re-seeded an isolated
    simulation's paper balance from the DEFAULT rather than its configured
    $100 - silently, because KX15_STATE_DIR still resolved the ledger
    correctly and only the balance was wrong.

    A guard against a stale list must not itself be a stale list.
    """
    found: list[str] = []
    for folder in _SEARCH:
        base = PROJECT_ROOT / folder if folder else PROJECT_ROOT
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.py")):
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            if relative in _EXEMPT:
                continue
            # `.from_env(` catches both call styles: from_env() and
            # from_env(PROJECT_ROOT). Matching only the empty-argument form
            # missed bot.py itself.
            if ".from_env(" in path.read_text(encoding="utf-8"):
                found.append(relative)
    return tuple(found)


ENTRY_POINTS = _discover_entry_points()


def test_the_scan_finds_the_known_entry_points():
    """If this stops finding bot.py, the scan is broken, not the repo clean."""
    assert "bot.py" in ENTRY_POINTS
    assert len(ENTRY_POINTS) >= 5


def _source(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def _calls(text: str, function: str) -> bool:
    """Whether the source actually CALLS ``function``, by AST not by substring.

    A substring test passes on the import line alone, so a file that imports
    ``load_environment`` and never calls it satisfied this guard completely.
    Mutation-checked: deleting the call from a listed entry point used to leave
    every test in this file green.
    """
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = (
            target.id
            if isinstance(target, ast.Name)
            else target.attr
            if isinstance(target, ast.Attribute)
            else None
        )
        if name == function:
            return True
    return False


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_every_settings_consumer_loads_the_environment(name: str) -> None:
    text = _source(name)
    assert "from_env(" in text, f"{name} is listed but no longer builds settings"
    assert _calls(text, "load_environment"), (
        f"{name} builds KalshiSettings without CALLING config.load_environment(). "
        "It will silently run on defaults - reporting the demo exchange, or "
        "writing a default paper balance over a configured one."
    )


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_nobody_resolves_dotenv_by_hand(name: str) -> None:
    """Three call sites, three different answers, was the whole problem.

    `config.config_file` is the one resolver: it honours KX15_STATE_DIR, then
    the working directory, then the project root. A hardcoded
    ``PROJECT_ROOT / ".env"`` names a file that on an installed copy is designed
    never to exist.
    """
    text = _source(name)
    assert not re.search(r'load_dotenv\s*\(', text), (
        f"{name} calls load_dotenv directly; use config.load_environment"
    )
    assert not re.search(r'["\']\.env["\']', text), (
        f"{name} names '.env' itself; let config.config_file resolve it"
    )


def test_the_resolver_prefers_the_state_directory(tmp_path, monkeypatch) -> None:
    """An installed copy keeps its .env outside the install directory, because
    `kx15 update` replaces that directory wholesale.
    """
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text("KALSHI_ENVIRONMENT=production\n")
    monkeypatch.setenv("KX15_STATE_DIR", str(state))
    assert config.config_file() == state / ".env"


def test_it_falls_back_through_cwd_to_the_project_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("KX15_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("KALSHI_ENVIRONMENT=production\n")
    assert config.config_file() == tmp_path / ".env"

    (tmp_path / ".env").unlink()
    assert config.config_file() == config.PROJECT_ROOT / ".env"


def test_loading_never_overrides_an_explicit_variable(tmp_path, monkeypatch) -> None:
    """`SALVAGE_ENABLED=true python bot.py` has to beat the file, or every
    documented inline override in this project is a lie.
    """
    monkeypatch.delenv("KX15_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("KX15_TEST_SENTINEL=from_file\n")
    monkeypatch.setenv("KX15_TEST_SENTINEL", "from_command_line")

    import os

    config.load_environment(tmp_path)
    assert os.environ["KX15_TEST_SENTINEL"] == "from_command_line"


def test_a_missing_env_file_is_not_an_error(tmp_path, monkeypatch) -> None:
    """It must stay a no-op - a source checkout without .env still runs on
    defaults - but that silence is exactly why the guards above exist.
    """
    monkeypatch.delenv("KX15_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.load_environment(tmp_path) == tmp_path / ".env"


def test_settings_are_never_loaded_at_import_time() -> None:
    """`.env` carries Fingerprint and developer-licence secrets. A test suite
    that reads them passes for the seller and fails for a buyer, so loading is
    an explicit call by an entry point and never an import side effect.
    """
    tree = ast.parse((PROJECT_ROOT / "config.py").read_text(encoding="utf-8"))
    for node in tree.body:          # module level only
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            name = getattr(node.value.func, "id", "") or getattr(
                node.value.func, "attr", "")
            assert name not in {"load_environment", "load_dotenv"}, (
                "config.py loads the environment at import time"
            )
