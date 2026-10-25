"""Shared test fixtures.

Tests are offline and deterministic. Nothing here reads a network service, a
credential, or anything under ``data/`` or ``outputs/``.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

# Matplotlib (the PDF report backend) warns -- and `filterwarnings = error`
# escalates -- when its default config directory is not writable, which is true
# in sandboxed runs. Point it somewhere writable before any test imports it.
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="mpl-"))

from amat.domain.bars import DailyBar
from amat.domain.types import parse_symbol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CANONICAL_STRATEGY_CONFIG = PROJECT_ROOT / "configs" / "strategy" / "amat_v1.toml"


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Absolute repository root."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Directory holding hand-built fixtures."""
    return FIXTURES


@pytest.fixture(scope="session")
def canonical_config_path() -> Path:
    """Path to the frozen v1 strategy configuration."""
    return CANONICAL_STRATEGY_CONFIG


def read_bar_rows(path: Path) -> list[dict[str, str]]:
    """Read a hand-built bar fixture as raw string rows."""
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_bar(row: dict[str, str]) -> DailyBar:
    """Construct a :class:`DailyBar` from a fixture row without coercion.

    Prices become ``Decimal`` from their exact text, never via ``float``, so the
    fixture value and the record value are identical.
    """
    return DailyBar(
        symbol=parse_symbol(row["symbol"]),
        session_date=dt.date.fromisoformat(row["session_date"]),
        raw_open=Decimal(row["raw_open"]),
        raw_high=Decimal(row["raw_high"]),
        raw_low=Decimal(row["raw_low"]),
        raw_close=Decimal(row["raw_close"]),
        adjusted_close=Decimal(row["adjusted_close"]),
        volume=Decimal(row["volume"]),
        source=row["source"],
        adjustment_mode=row["adjustment_mode"],
    )


@pytest.fixture(scope="session")
def valid_bar_rows() -> list[dict[str, str]]:
    """Rows of the hand-built valid-bar fixture."""
    return read_bar_rows(FIXTURES / "valid_daily_bars.csv")


@pytest.fixture(scope="session")
def invalid_bar_rows() -> list[dict[str, str]]:
    """Rows of the hand-built invalid-bar fixture, each naming its violation."""
    return read_bar_rows(FIXTURES / "invalid_daily_bars.csv")


@pytest.fixture(autouse=True)
def isolated_cost_ledger(tmp_path, monkeypatch):
    """Fixture provider attempts must not pollute application accounting."""
    monkeypatch.setenv("AMAT_COST_LEDGER", str(tmp_path / "model-costs.sqlite3"))
