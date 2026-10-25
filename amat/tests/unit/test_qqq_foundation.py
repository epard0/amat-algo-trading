"""Economic/action fixtures for the executable benchmark, independent of live data."""

from __future__ import annotations

import datetime as dt
import importlib.util
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.config.loading import load_strategy_config
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.fills import MissedFillReason
from amat.domain.types import Symbol

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "qqq_foundation", ROOT / "scripts/run_qqq_foundation.py"
)
assert SPEC and SPEC.loader
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


@pytest.fixture
def strategy():
    return load_strategy_config(
        ROOT / "configs/strategy/amat_group_selection_volatility.toml"
    ).strategy


def panel(raw, *, windows=None, volumes=None):
    raw = np.array(raw, dtype=float).reshape(-1, 1)
    return MarketPanel(
        sessions=(
            dt.date(2016, 12, 30),
            dt.date(2017, 1, 3),
            dt.date(2017, 1, 4),
            dt.date(2017, 1, 5),
        )[: len(raw)],
        symbols=(Symbol("QQQ"),),
        adjusted_closes=np.full_like(raw, 100),
        raw_closes=raw,
        window_vwaps=np.array(windows, dtype=float).reshape(-1, 1)
        if windows is not None
        else raw.copy(),
        window_volumes=np.array(volumes, dtype=float).reshape(-1, 1)
        if volumes is not None
        else np.full_like(raw, 1_000_000),
        source="synthetic-explicit-action-fixture",
    )


def test_dividend_split_cash_and_asset_attribution(strategy):
    p = panel([100, 100, 99, 49.5])
    actions = {
        p.sessions[2]: [
            CorporateAction(
                "QQQ", p.sessions[2], ActionKind.CASH_DIVIDEND, cash_per_share=Decimal(1)
            )
        ],
        p.sessions[3]: [CorporateAction("QQQ", p.sessions[3], ActionKind.SPLIT, ratio=Decimal(2))],
    }
    result = driver.qqq_reference(strategy, p, actions, cost=0, participation=0.01)
    rows, audit = driver.explain(result, p, actions)
    assert result.final_equity == Decimal(100000)
    assert result.total_fills == 1
    assert rows[-1]["assets"]["QQQ"]["quantity"] == "2000"
    assert Decimal(rows[-1]["cash"]) == 1000
    assert Decimal(rows[1]["assets"]["QQQ"]["distributions"]) == 1000
    assert Decimal(rows[1]["assets"]["QQQ"]["market_pnl"]) == -1000
    assert Decimal(audit["maximum_asset_pnl_residual"]) == 0


def test_whole_share_fees_and_cash_gap_miss_match_native(strategy):
    p = panel([100, 100, 99])
    result = driver.qqq_reference(strategy, p, {}, cost=2, participation=0.01)
    # First order sized at 100 close asks for 1000 shares; fees make it unfunded.
    assert result.sessions[0].fills == ()
    assert result.sessions[0].missed[0].reason == MissedFillReason.INSUFFICIENT_CASH
    # Next observed VWAP is 99, so the original sizing now fits with costs.
    fill = result.sessions[1].fills[0]
    assert fill.quantity == 1000
    assert fill.price == 99
    assert fill.cost_cash == Decimal("19.800000")
    assert result.final_equity == Decimal("99980.200000")
    driver.explain(result, p, {})


def test_native_cap_uses_observed_opening_volume(strategy):
    p = panel([100, 100, 100], volumes=[1000, 1000, 2000])
    capped = driver.qqq_reference(strategy, p, {}, cost=0, participation=0.01)
    uncapped = driver.qqq_reference(strategy, p, {}, cost=0, participation=None)
    assert [r.fills[0].quantity for r in capped.sessions] == [10, 20]
    assert capped.sessions[0].missed[0].reason == MissedFillReason.PARTICIPATION_CAP
    assert uncapped.sessions[0].fills[0].quantity == 1000
    assert uncapped.total_fills == 1


def test_missing_window_is_missed_and_never_imputed(strategy):
    p = panel([100, 100, 100], windows=[100, np.nan, 100])
    result = driver.qqq_reference(strategy, p, {}, cost=0, participation=0.01)
    assert result.sessions[0].fills == ()
    assert result.sessions[0].missed[0].reason == MissedFillReason.NO_EXECUTION_BAR
    assert result.sessions[1].fills[0].quantity == 1000


def test_reconstructed_nav_rejects_tampered_result(strategy):
    p = panel([100, 100, 99])
    result = driver.qqq_reference(strategy, p, {}, cost=0, participation=0.01)
    bad = replace(
        result, sessions=(replace(result.sessions[0], equity=Decimal(12)), *result.sessions[1:])
    )
    with pytest.raises(ValueError, match="reconstruction differs"):
        driver.explain(bad, p, {})


def test_future_window_cannot_change_earlier_orders_or_equity(strategy):
    p = panel([100, 100, 100, 100], volumes=[1000, 1000, 1000, 1000])
    a = driver.qqq_reference(strategy, p, {}, cost=0, participation=0.01)
    windows = p.window_vwaps.copy()
    windows[-1] *= 5
    b = driver.qqq_reference(
        strategy, replace(p, window_vwaps=windows), {}, cost=0, participation=0.01
    )
    assert a.sessions[:-1] == b.sessions[:-1]
    assert a.sessions[-1].instructions == b.sessions[-1].instructions
