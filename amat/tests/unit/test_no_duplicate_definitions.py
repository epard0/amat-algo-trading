"""No module may define the same top-level name twice.

This exists because ``src/amat/reporting/__init__.py`` has now carried duplicate
copies of its entire HTML-rendering block **twice**, and both times the copies
were not identical: the later, shadowing copy had lost its ``<title>`` tag and
contained an ``_asset_table`` whose ternary binds across the whole f-string
chain, collapsing an entire table row to ``<td>-</td>`` whenever ``average_r`` is
``None``. So the live renderer was the broken one, and reports were being
produced from it.

Neither occurrence was caught by anything. Python is happy to rebind a name at
module scope; the tests exercised whichever copy happened to win; and mypy's
incremental cache masked the redefinition until an unrelated edit invalidated it.
The only reliable detector is a direct one, so this parses every module and
compares each top-level name against the set already seen.

The check is deliberately syntactic and cheap. It cannot tell a deliberate
conditional redefinition from an accidental paste, so ``TYPE_CHECKING`` blocks
and ``try``/``except ImportError`` fallbacks are exempted -- those are the two
legitimate reasons to bind one name twice, and neither appears at the bare module
level this scans.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _top_level_definitions(tree: ast.Module) -> list[str]:
    """Names bound by a def, class, or plain assignment at module scope.

    Only the outermost body is inspected. A name bound inside ``if``, ``try`` or
    a function is out of scope by design -- those are the legitimate cases.
    """
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_top_level_name_is_defined_twice(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    counts = Counter(_top_level_definitions(tree))
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    assert not duplicates, (
        f"{path.relative_to(SRC)} defines {duplicates} more than once at module scope. "
        "A second definition silently shadows the first, and the shadowing copy is the "
        "one that runs. This has happened twice in reporting/__init__.py, both times "
        "with the broken copy winning."
    )


def test_the_reporting_module_has_exactly_one_style_sheet() -> None:
    """A targeted check on the module that has regressed twice.

    ``_HTML_STYLE`` is a plain string constant, so three copies of it is the
    cheapest possible signal that the rendering block was pasted again. The
    parametrized test above would also catch it; this one names the specific
    failure so the diagnosis is immediate rather than inferred.
    """
    text = (SRC / "amat" / "reporting" / "__init__.py").read_text(encoding="utf-8")
    occurrences = text.count("\n_HTML_STYLE = ")
    assert occurrences == 1, (
        f"reporting/__init__.py defines _HTML_STYLE {occurrences} times; "
        "the rendering block has been duplicated again"
    )


def test_the_html_report_still_carries_a_title() -> None:
    """The tag the shadowing copy dropped, both times.

    Asserted on rendered output rather than on the source, because the point is
    that the *live* renderer emits it -- which is exactly what was false while a
    duplicate was winning.
    """
    from amat.reporting import _HTML_STYLE

    assert "viz-root" in _HTML_STYLE
    source = (SRC / "amat" / "reporting" / "__init__.py").read_text(encoding="utf-8")
    assert "<title>AMAT backtest report" in source
