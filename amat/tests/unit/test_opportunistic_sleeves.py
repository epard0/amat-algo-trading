"""Residual-cash budgeting, netting and committed-core execution priority."""

from dataclasses import replace

import numpy as np
from test_index_sleeves import panel

from amat.domain.types import Symbol
from autoresearch.improvements.index_sleeves_study import signed_sleeve
from autoresearch.improvements.opportunistic_sleeves_study import net_cash_target


def test_only_residual_cash_is_budgeted_without_changing_core():
    core = np.array([0.4, 0.3, 0.2])
    signal = np.array([1.0, 0, 0])
    cov = np.eye(3) * 0.01
    net, budget = net_cash_target(core, signal, cov, index_columns=[0, 1])
    assert 0 < budget <= 0.08 + 1e-8
    assert net[0] <= 0.45 + 1e-8
    np.testing.assert_array_equal(net[1:], core[1:])
    full = np.array([0.4, 0.3, 0.3])
    net, budget = net_cash_target(full, signal, cov, index_columns=[0, 1])
    np.testing.assert_array_equal(net, full)
    assert budget == 0


def test_netting_avoids_unnecessary_borrow_and_pair_scales_together():
    core = np.array([0.3, 0.2])
    signal = np.array([-0.49, 0.49])
    cov = np.eye(2) * 0.01
    available, b = net_cash_target(core, signal, cov, index_columns=[0, 1])
    unavailable, c = net_cash_target(core, signal, cov, index_columns=[0, 1], short_available=False)
    np.testing.assert_allclose(available, unavailable)
    assert b == c
    assert np.all(unavailable >= 0)
    np.testing.assert_allclose((unavailable - core)[0], -(unavailable - core)[1])
    core = np.array([0.3, 0.0])
    signal = np.array([0.49, -0.49])
    net, budget = net_cash_target(core, signal, cov, index_columns=[0, 1], short_available=False)
    np.testing.assert_allclose(net, core, atol=1e-9)
    assert budget < 1e-8


def test_joint_risk_limit_prevents_correlated_sleeve_from_exceeding_risk():
    core = np.array([0.4, 0.4])
    signal = np.array([0.5, 0.5])
    cov = np.full((2, 2), 0.0625)
    net, budget = net_cash_target(core, signal, cov, index_columns=[0, 1])
    assert net @ cov @ net <= 0.04 + 1e-8
    assert budget < 1e-8


def test_core_gap_commitment_blocks_sleeve_before_it_uses_cash():
    p = panel((100.0, 100.0, 100.0, 100.0))
    prices = np.repeat(p.raw_closes, 2, axis=1)
    prices[1:, 1] = 200
    p = replace(
        p,
        symbols=(Symbol("QQQ"), Symbol("SPY")),
        raw_closes=prices,
        adjusted_closes=prices,
        window_vwaps=prices,
        window_volumes=np.full_like(prices, 1_000_000),
    )
    net = np.tile([0.18, 0.8], (4, 1))
    core = np.tile([0.0, 0.8], (4, 1))
    rows = signed_sleeve(p, net, {}, cost_bps=0, initial_cash=100000, core_targets=core)
    first = rows[1]
    assert first["quantities"][0] == 0
    assert first["quantities"][1] > 0
    assert first["cash"] >= 0
