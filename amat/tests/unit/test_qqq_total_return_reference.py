"""Explicit investor-reference economics, separate from native execution parity."""

from __future__ import annotations

import datetime as dt
import importlib.util
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.domain.types import Symbol

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "qqq_total_return_reference", ROOT / "scripts/run_qqq_total_return_reference.py"
)
assert SPEC and SPEC.loader
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def panel():
    return MarketPanel(
        sessions=(dt.date(2016, 12, 30), dt.date(2017, 1, 3), dt.date(2026, 10, 12)),
        symbols=(Symbol("QQQ"),),
        adjusted_closes=np.array([[90.0], [100.0], [200.0]]),
        raw_closes=np.array([[90.0], [110.0], [220.0]]),
        window_vwaps=np.array([[90.0], [100.0], [220.0]]),
        window_volumes=np.array([[1000.0], [1000.0], [1000.0]]),
        source="explicit-synthetic-investor-reference",
    )


@pytest.mark.parametrize("cost", [2, 6, 10])
def test_fractional_entry_fee_observed_vwap_and_index_growth(cost):
    rows, entry = driver.investor_rows(panel(), cost)
    factor = 1 + cost / 10000
    assert rows[0]["equity"] == pytest.approx(110000 / factor)
    assert rows[-1]["equity"] == pytest.approx(220000 / factor)
    assert rows[0]["fees"] == pytest.approx(100000 * (cost / 10000) / factor)
    assert rows[-1]["fees"] == 0
    assert rows[0]["fills"] == 1
    assert rows[-1]["fills"] == 0
    assert abs(Decimal(entry["initial_cash_residual"])) < Decimal("0.000000000001")
    assert entry["initial_raw_vwap"] == "100.0"


def test_common_future_adjustment_factor_cancels_from_economic_path():
    p = panel()
    a, _ = driver.investor_rows(p, 6)
    b, _ = driver.investor_rows(replace(p, adjusted_closes=p.adjusted_closes / 4), 6)
    assert a == b


def test_missing_initial_execution_window_fails_closed():
    p = panel()
    prices = p.window_vwaps.copy()
    prices[1] = np.nan
    with pytest.raises(ValueError, match="Observed initial"):
        driver.investor_rows(replace(p, window_vwaps=prices), 6)


def test_adjusted_dividend_growth_is_not_credited_twice():
    p = panel()
    p = replace(
        p,
        raw_closes=np.array([[100.0], [100.0], [99.0]]),
        adjusted_closes=np.array([[100.0], [100.0], [100.0]]),
    )
    rows, _ = driver.investor_rows(p, 2)
    assert rows[-1]["equity"] == rows[0]["equity"]


def test_foundation_verification_binds_loaded_sources_and_partitions(monkeypatch):
    foundation = {
        "schema": "qqq-native-foundation/1",
        "execution_start": "2017-01-03",
        "execution_end": "2026-10-12",
        "initial_cash": "100000",
        "costs_bps": [2, 6, 10],
        "root": "/frozen/root",
        "snapshot": "/frozen/snapshot",
        "source_sha256": {"src/amat/example.py": "a" * 64},
        "partition_sha256": {"daily.parquet": "b" * 64},
    }
    attestations = []
    monkeypatch.setattr(driver, "sealed", lambda _: foundation)
    monkeypatch.setattr(driver, "attest_loaded_sources", lambda *args: attestations.append(args))
    monkeypatch.setattr(
        driver,
        "load_manifest",
        lambda _: SimpleNamespace(partition_sha256=foundation["partition_sha256"]),
    )
    assert driver.verified_foundation(Path("protocol.json")) == foundation
    assert attestations[0][0] == Path("/frozen/root/src")
    assert attestations[0][1] == foundation["source_sha256"]
    monkeypatch.setattr(driver, "load_manifest", lambda _: SimpleNamespace(partition_sha256={}))
    with pytest.raises(ValueError, match="snapshot differs"):
        driver.verified_foundation(Path("protocol.json"))
