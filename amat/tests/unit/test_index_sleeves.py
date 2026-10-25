"""Signed research arithmetic, causal signal rules and conservative execution."""

import datetime as dt
from decimal import Decimal

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.types import Symbol
from autoresearch.improvements.index_sleeves_study import signal_weights, signed_sleeve


def panel(closes=(100.0, 100.0, 99.0, 99.0), volumes=1000000.0):
    prices = np.array(closes)[:, None]
    return MarketPanel(
        sessions=tuple(dt.date(2017, 1, 2) + dt.timedelta(days=i) for i in range(len(prices))),
        symbols=(Symbol("QQQ"),),
        adjusted_closes=prices,
        raw_closes=prices,
        window_vwaps=prices,
        window_volumes=np.full_like(prices, volumes),
        source="synthetic signed-account arithmetic",
    )


def test_short_dividend_is_liability_and_split_does_not_create_profit():
    p = panel()
    weights = np.array([[-0.5], [0.0], [0.0], [0.0]])
    actions = {
        p.sessions[2]: [
            CorporateAction(
                Symbol("QQQ"), p.sessions[2], ActionKind.CASH_DIVIDEND, cash_per_share=Decimal(1)
            )
        ]
    }
    rows = signed_sleeve(p, weights, actions, cost_bps=0, borrow_rate=0)
    assert rows[1]["short_liability"] == 10000
    assert rows[2]["distributions"] == -100
    assert rows[-1]["equity"] == pytest.approx(20000)
    for r in rows[1:]:
        assert r["equity"] == pytest.approx(r["cash"] + r["net_position_value"])
    p = panel((100.0, 100.0, 50.0, 50.0))
    actions = {
        p.sessions[2]: [
            CorporateAction(Symbol("QQQ"), p.sessions[2], ActionKind.SPLIT, ratio=Decimal(2))
        ]
    }
    rows = signed_sleeve(p, weights, actions, cost_bps=0, borrow_rate=0)
    assert rows[-1]["equity"] == pytest.approx(20000)
    assert rows[2]["traded_notional"] == 10000


def test_borrow_is_calendar_accrued_and_unavailable_shorts_fail_closed():
    p = panel((100.0, 100.0, 100.0, 100.0))
    weights = np.full_like(p.raw_closes, -0.5)
    rows = signed_sleeve(p, weights, {}, cost_bps=0, borrow_rate=0.03)
    assert rows[2]["borrow_cost"] == pytest.approx(10000 * 0.03 / 365)
    assert rows[-1]["equity"] == pytest.approx(20000 - sum(r["borrow_cost"] for r in rows))
    unavailable = signed_sleeve(p, weights, {}, cost_bps=6, short_available=False)
    assert unavailable[-1]["equity"] == 20000
    assert all(r["fills"] == 0 for r in unavailable)
    assert rows[1]["collateral_free_cash"] == 10000


def test_volume_costs_and_no_future_signal_execution():
    p = panel((100.0, 100.0, 100.0, 100.0), volumes=100.0)
    weights = np.full_like(p.raw_closes, 0.98)
    rows = signed_sleeve(p, weights, {}, cost_bps=10)
    assert rows[1]["traded_notional"] == 100
    assert rows[1]["fees"] == pytest.approx(0.1)
    assert rows[1]["missed"] > 0
    altered = weights.copy()
    altered[2:] *= -1
    assert signed_sleeve(p, altered, {}, cost_bps=10)[:3] == rows[:3]
    with pytest.raises(ValueError):
        signed_sleeve(p, weights * 2, {}, cost_bps=2)


def test_signal_prefix_and_future_mutation_and_gross_limits():
    rng = np.random.default_rng(19)
    prices = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, (500, 3)), axis=0))
    symbols = ["QQQ", "SPY", "IWM"]
    full = signal_weights(prices, symbols)
    prefix = signal_weights(prices[:380], symbols)
    changed = prices.copy()
    changed[380:] *= 3
    mutated = signal_weights(changed, symbols)
    for arm in full:
        np.testing.assert_array_equal(full[arm][:380], prefix[arm])
        np.testing.assert_array_equal(full[arm][:380], mutated[arm][:380])
        assert np.max(np.sum(np.abs(full[arm]), axis=1)) <= 0.98000001
    assert not np.any(full["qqq_long_only"] < 0)
    assert not np.any(full["basket_long_only"] < 0)
    assert np.any(full["relative_pair"] < 0)


def test_pair_is_not_opened_unhedged_when_borrowing_unavailable():
    p = panel((100.0, 100.0, 100.0, 100.0))
    from dataclasses import replace

    p = replace(
        p,
        symbols=(Symbol("QQQ"), Symbol("SPY")),
        adjusted_closes=np.repeat(p.adjusted_closes, 2, axis=1),
        raw_closes=np.repeat(p.raw_closes, 2, axis=1),
        window_vwaps=np.repeat(p.window_vwaps, 2, axis=1),
        window_volumes=np.repeat(p.window_volumes, 2, axis=1),
    )
    targets = np.tile([0.49, -0.49], (4, 1))
    rows = signed_sleeve(p, targets, {}, cost_bps=6, short_available=False, paired=True)
    assert all(r["fills"] == 0 for r in rows)
    assert rows[-1]["equity"] == 20000


def test_signed_ledger_is_serializable_and_collateral_is_not_spending_power():
    import json

    p = panel((100.0, 100.0, 100.0, 100.0))
    rows = signed_sleeve(p, np.full_like(p.raw_closes, -0.98), {}, cost_bps=10)
    json.dumps(rows, allow_nan=False)
    for r in rows[1:]:
        assert r["exposure"] <= 0.98 + 1e-8
        assert r["collateral_free_cash"] >= 0
        assert r["cash"] > r["equity"]
