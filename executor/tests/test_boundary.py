"""The two properties that make this split real rather than aspirational.

1. This package never imports ``amat``. If it did, the executor could reach
   around the plan artifact and call a signal function directly with arguments
   the plan never carried -- and the strategy repository's guarantee that one
   core serves every runner would be quietly false.
2. Paper remains the default; only the explicit live adapter contains the live host.

Both are checked by scanning source, not by importing and hoping, so a lazy
import inside a function is caught too.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "executor"

#: Assembled from pieces so this test file's own source does not match the scan
#: it performs over the package.
LIVE_HOST = "api.alpaca" + ".markets"
PAPER_HOST = "paper-api.alpaca" + ".markets"


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_package_has_source_to_scan() -> None:
    """Guard against a rename silently emptying every scan below."""
    assert len(_python_files()) >= 8


@pytest.mark.parametrize("path", _python_files(), ids=lambda path: path.name)
def test_no_module_imports_the_strategy_package(path: Path) -> None:
    """The dependency arrow points one way: executor -> plan artifact, never
    executor -> amat."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "amat", (
                    f"{path.name} imports {alias.name}; the executor must consume the "
                    f"strategy only through the plan artifact"
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] != "amat", (
                f"{path.name} imports from {node.module}; the executor must consume the "
                f"strategy only through the plan artifact"
            )


@pytest.mark.parametrize("path", _python_files(), ids=lambda path: path.name)
def test_live_trading_host_is_confined_to_live_adapter(path: Path) -> None:
    """Only the explicit live adapter may pin the live endpoint."""
    source = path.read_text(encoding="utf-8")
    # The paper host legitimately contains the live host as a substring is
    # false -- "paper-api.alpaca.markets" does not contain "api.alpaca.markets"
    # as a token boundary -- so the check strips paper mentions first.
    without_paper = source.replace(PAPER_HOST, "")
    assert path.name == "alpaca_live.py" or LIVE_HOST not in without_paper, (
        f"{path.name} mentions the live trading host. Live host belongs only in the live adapter."
    )


def test_paper_host_is_a_constant_not_a_setting() -> None:
    """The host must not be read from the environment."""
    config_source = (SRC / "config.py").read_text(encoding="utf-8")
    assert f'PAPER_TRADING_HOST = "{PAPER_HOST}"' in config_source

    # No *environment lookup* names a host, base URL, or endpoint. The check
    # inspects the arguments of the lookup helpers specifically -- scanning
    # every string constant instead would flag `__all__`, which merely exports
    # the constant's name.
    lookups = {"_require", "_optional", "getenv", "get"}
    tree = ast.parse(config_source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        name = (
            function.attr
            if isinstance(function, ast.Attribute)
            else function.id
            if isinstance(function, ast.Name)
            else ""
        )
        if name not in lookups:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        lowered = first.value.lower()
        names_an_endpoint = lowered.endswith(("_url", "_host", "_endpoint", "_base_url"))
        # SMTP_HOST is a mail relay, not a venue. The property under test is
        # that the *broker* endpoint is not configurable.
        names_the_broker = any(
            token in lowered for token in ("alpaca", "apca", "broker", "trading", "api")
        )
        if names_an_endpoint and names_the_broker:
            pytest.fail(
                f"config.py reads {first.value!r} from the environment; the broker "
                f"host must not be configurable"
            )


def test_order_submission_lives_in_exactly_one_module() -> None:
    """Everything that can change remote state is in one file."""
    submitters = [
        path.relative_to(SRC).as_posix()
        for path in _python_files()
        if "/v2/orders" in path.read_text(encoding="utf-8")
    ]
    assert submitters == ["broker/alpaca_paper.py"], (
        f"order submission appears in {submitters}; it belongs in exactly one module"
    )
