"""Unit-consistent discretionary-buy costs, held-share rounding and causality."""

import importlib.util
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.positions import Position
from amat.domain.types import Side
from amat.execution import build_instructions

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "portfolio_marginal_cost_study", ROOT / "scripts/run_portfolio_marginal_cost_study.py"
)
assert SPEC and SPEC.loader
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def setup():
    config = load_strategy_config(
        ROOT / "configs/strategy/amat_group_selection_volatility.toml"
    ).strategy
    panel = generate_panel(config.to_universe(), sessions=360, seed=71)
    return config, panel


def test_cost_units_whole_floor_and_preserved_reductions():
    config, panel = setup()
    index = 300
    day = panel.sessions[index]
    marks = panel.marks_at(index)
    symbol, sell = panel.symbols[:2]
    positions = {
        symbol: Position(symbol, Decimal("13.123456789"), marks[symbol], day),
        sell: Position(sell, Decimal("75"), marks[sell], day),
    }
    ledger = LedgerSnapshot(day, Decimal(100000), positions)
    targets = dict.fromkeys(panel.symbols, 0.0)
    targets[symbol] = 0.20
    targets[sell] = 0.001
    # A negative completed trailing mean makes either proxy refuse the optional buy.
    prices = panel.adjusted_closes.copy()
    prices[: index + 1] = np.exp(-np.arange(index + 1)[:, None] * 0.01) * np.ones(
        (1, len(panel.symbols))
    )
    negative = replace(panel, adjusted_closes=prices)
    for arm in driver.ARMS[1:]:
        policy, observations = driver.make_policy(config, negative, arm, 6)
        result = policy(targets, ledger, day, np.eye(len(panel.symbols)) * 0.04)
        assert result[sell] == targets[sell]
        assert result[symbol] <= targets[symbol]
        assert (
            Decimal(str(result[symbol])) * ledger.equity / marks[symbol]
            >= positions[symbol].quantity
        )
        orders, _ = build_instructions(
            result,
            positions,
            marks,
            ledger.equity,
            signal_session=day,
            execution_session=panel.sessions[index + 1],
            rebalance=config.rebalance,
            execution=config.execution,
        )
        assert not any(o.symbol == symbol for o in orders)
        assert any(o.symbol == sell and o.side == Side.SELL for o in orders)
        forced, _ = build_instructions(
            {symbol: result[symbol]},
            {symbol: positions[symbol]},
            marks,
            ledger.equity,
            signal_session=day,
            execution_session=panel.sessions[index + 1],
            rebalance=config.rebalance.model_copy(
                update={"min_weight_delta": 0.0, "min_trade_notional": Decimal(0)}
            ),
            execution=config.execution,
        )
        # The runner keeps only SELL instructions from this forced branch.
        assert not any(o.side == Side.SELL for o in forced)
        obs = observations[-1]
        q = Decimal(obs["planned_whole_quantities"][str(symbol)])
        assert q == q.to_integral_value()
        expected = float(driver.one_way_cost(q * marks[symbol], 6) / ledger.equity)
        assert obs["estimated_immediate_buy_cost_nav"] == expected
        assert obs["optional_buys_permitted"] is False


def test_zero_whole_requested_buy_does_not_create_cost_gate():
    config, panel = setup()
    i = 300
    ledger = LedgerSnapshot(panel.sessions[i], Decimal(100000), {})
    symbol = panel.symbols[0]
    targets = dict.fromkeys(panel.symbols, 0.0)
    targets[symbol] = float(Decimal("0.5") * panel.marks_at(i)[symbol] / ledger.equity)
    policy, observations = driver.make_policy(config, panel, driver.ARMS[1], 6)
    result = policy(targets, ledger, panel.sessions[i], np.eye(len(panel.symbols)) * 0.04)
    assert result == targets
    assert observations[-1]["optional_buy_symbols"] == []
    assert observations[-1]["estimated_immediate_buy_cost_nav"] == 0
    assert observations[-1]["optional_buys_permitted"] is True


def test_unsafe_covariance_and_delayed_execution_fail_closed():
    config, panel = setup()
    delayed = config.model_copy(
        update={
            "execution": config.execution.model_copy(update={"signal_to_execution_lag_sessions": 2})
        }
    )
    with pytest.raises(ValueError, match="lag1"):
        driver.make_policy(delayed, panel, driver.ARMS[1], 6)
    policy, _ = driver.make_policy(config, panel, driver.ARMS[1], 6)
    day = panel.sessions[300]
    ledger = LedgerSnapshot(day, Decimal(100000), {})
    with pytest.raises(ValueError, match="semidefinite"):
        policy(dict.fromkeys(panel.symbols, 0.0), ledger, day, -np.eye(len(panel.symbols)))
    assert driver.safe_held_weight(ledger, panel.symbols[0], 0.3) == 0


def test_return_only_retains_same_mean_and_omits_only_quadratic_penalty():
    config, panel = setup()
    i = 300
    ledger = LedgerSnapshot(panel.sessions[i], Decimal(100000), {})
    targets = dict.fromkeys(panel.symbols, 0.0)
    targets[panel.symbols[0]] = 0.2
    covariance = np.eye(len(panel.symbols)) * 0.04
    observations = []
    for arm in driver.ARMS[1:]:
        policy, obs = driver.make_policy(config, panel, arm, 6)
        policy(targets, ledger, panel.sessions[i], covariance)
        observations.append(obs[-1])
    a, b = observations
    assert a["estimated_immediate_buy_cost_nav"] == b["estimated_immediate_buy_cost_nav"]
    assert b["predicted_five_session_benefit_nav"] - a[
        "predicted_five_session_benefit_nav"
    ] == pytest.approx(5 / 252 * 0.5 * a["annual_variance_difference"])


@pytest.mark.parametrize("arm", driver.ARMS[1:])
def test_native_future_mutation_preserves_account_instructions_and_fills(arm):
    config, panel = setup()
    cutoff = 330
    prices = panel.adjusted_closes.copy()
    prices[cutoff:] *= 5
    raw = panel.raw_closes.copy()
    raw[cutoff:] *= 4
    windows = panel.window_vwaps.copy()
    windows[cutoff:] *= 3
    changed = replace(panel, adjusted_closes=prices, raw_closes=raw, window_vwaps=windows)
    outputs = []
    for visible in (panel, changed):
        policy, _ = driver.make_policy(config, visible, arm, 6)
        result = run_backtest(
            config,
            visible,
            config.to_universe(),
            one_way_bps=6,
            max_entry_window_participation=0.01,
            whole_share_buys=True,
            decision_policy=policy,
        )
        outputs.append(
            [
                (r.session, r.equity, r.cash, r.fills, r.missed, r.instructions)
                for r in result.sessions
                if r.session < panel.sessions[cutoff]
            ]
        )
    assert outputs[0] == outputs[1]
