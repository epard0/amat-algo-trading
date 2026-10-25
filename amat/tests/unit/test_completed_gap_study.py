"""Causal completed-gap BUY deferral: timing, corporate-action basis and exits."""

import datetime as dt
import importlib.util
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.snapshot import _open_array
from amat.data.synthetic import generate_panel
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.positions import Position
from amat.domain.types import Side
from amat.execution import build_instructions

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "completed_gap_study", ROOT / "scripts/run_completed_gap_study.py"
)
assert SPEC and SPEC.loader
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def setup():
    config = load_strategy_config(
        ROOT / "configs/strategy/amat_group_selection_volatility.toml"
    ).strategy
    panel = generate_panel(config.to_universe(), sessions=360, seed=71)
    return config, replace(panel, adjusted_opens=panel.adjusted_closes.copy())


def test_threshold_uses_exact_prior20_excludes_current_and_is_strict():
    _, panel = setup()
    i = 300
    closes = np.full_like(panel.adjusted_closes, 100.0)
    opens = closes.copy()
    closes[i - 21 : i, 0] = 100 * np.exp(np.arange(21) * 0.01)
    opens[i, 0] = closes[i - 1, 0] * 1.05
    closes[i, 0] = opens[i, 0] * 0.99
    view = replace(panel, adjusted_closes=closes, adjusted_opens=opens)
    f = driver.gap_features(view, i)
    assert f["prior20_sigma"][0] == pytest.approx(
        np.std(np.diff(np.log(closes[i - 21 : i, 0])), ddof=0)
    )
    assert f["large_positive"][0] and f["reversal"][0]
    assert not f["large_positive"][1]  # zero gap equals zero sigma, strict comparison
    changed = closes.copy()
    changed[i, 0] *= 0.5
    np.testing.assert_array_equal(
        driver.gap_features(replace(view, adjusted_closes=changed), i)["prior20_sigma"],
        f["prior20_sigma"],
    )
    changed = closes.copy()
    changed[i - 22, 0] *= 0.5
    np.testing.assert_array_equal(
        driver.gap_features(replace(view, adjusted_closes=changed), i)["prior20_sigma"],
        f["prior20_sigma"],
    )
    changed[i - 21, 0] *= 0.5
    assert (
        driver.gap_features(replace(view, adjusted_closes=changed), i)["prior20_sigma"][0]
        > f["prior20_sigma"][0]
    )
    opens[i, 0] = closes[i, 0]
    assert not driver.gap_features(replace(view, adjusted_opens=opens), i)["reversal"][
        0
    ]  # equal candle is not reversal


@pytest.mark.parametrize("bad", [0.0, -1.0, np.nan, np.inf])
def test_unavailable_inputs_fail_closed_without_fill_price_substitution(bad):
    _, panel = setup()
    opening = panel.adjusted_opens.copy()
    opening[300, 0] = bad
    with pytest.raises(ValueError, match="positive gap inputs"):
        driver.gap_features(replace(panel, adjusted_opens=opening), 300)
    with pytest.raises(ValueError, match="Actual opens"):
        driver.gap_features(replace(panel, adjusted_opens=None), 300)
    with pytest.raises(ValueError, match="prior20"):
        driver.gap_features(panel, 20)


def test_symbolwise_positive_adjustment_scale_preserves_gap_and_reversal():
    _, panel = setup()
    scale = np.linspace(0.2, 3.0, len(panel.symbols))
    before = driver.gap_features(panel, 300)
    after = driver.gap_features(
        replace(
            panel,
            adjusted_closes=panel.adjusted_closes * scale,
            adjusted_opens=panel.adjusted_opens * scale,
        ),
        300,
    )
    for key in ["gap_log", "prior20_sigma"]:
        np.testing.assert_allclose(before[key], after[key], atol=1e-14)
    for key in ["large_positive", "reversal"]:
        np.testing.assert_array_equal(before[key], after[key])


@pytest.mark.parametrize(
    ("old_raw", "new_raw", "adjusted"), [(100.0, 50.0, 50.0), (100.0, 99.0, 99.0)]
)
def test_actual_snapshot_open_adjustment_avoids_mechanical_split_or_dividend_gap(
    old_raw, new_raw, adjusted
):
    _, panel = setup()
    days = [dt.date(2026, 1, 2), dt.date(2026, 1, 5)]
    frame = pl.DataFrame(
        {
            "session_date": days,
            "symbol": ["QQQ", "QQQ"],
            "raw_open": [old_raw, new_raw],
            "raw_close": [old_raw, new_raw],
            "adjusted_close": [adjusted, adjusted],
        }
    )
    opens = _open_array(frame, {d: i for i, d in enumerate(days)}, {"QQQ": 0}, (2, 1))
    assert opens is not None
    assert opens[0, 0] == opens[1, 0] == adjusted
    closes = np.full_like(panel.adjusted_closes, adjusted)
    opening = closes.copy()
    opening[299:301, 0] = opens[:, 0]
    f = driver.gap_features(replace(panel, adjusted_closes=closes, adjusted_opens=opening), 300)
    assert f["gap_log"][0] == 0 and not f["large_positive"][0]


@pytest.mark.parametrize("arm", driver.ARMS[1:])
def test_only_flagged_positive_whole_buy_is_deferred_reductions_and_other_buy_survive(arm):
    config, panel = setup()
    i = 300
    prices = np.full_like(panel.adjusted_closes, 100.0)
    opens = prices.copy()
    opens[i, 0] = 110.0
    panel = replace(panel, adjusted_closes=prices, adjusted_opens=opens)
    day = panel.sessions[i]
    marks = panel.marks_at(i)
    buy, sell, other = panel.symbols[:3]
    positions = {
        buy: Position(buy, Decimal("13.123456789"), marks[buy], day),
        sell: Position(sell, Decimal(75), marks[sell], day),
    }
    ledger = LedgerSnapshot(day, Decimal(100000), positions)
    targets = dict.fromkeys(panel.symbols, 0.0)
    targets.update({buy: 0.2, sell: 0.001, other: 0.1})
    policy, observations = driver.make_policy(config, panel, arm, 6)
    result = policy(targets, ledger, day, np.eye(len(panel.symbols)))
    assert result[sell] == targets[sell] and result[other] == targets[other]
    assert result[buy] <= targets[buy]
    assert Decimal(str(result[buy])) * ledger.equity / marks[buy] >= positions[buy].quantity
    orders, _ = build_instructions(
        result,
        positions,
        marks,
        ledger.equity,
        signal_session=day,
        execution_session=panel.sessions[i + 1],
        rebalance=config.rebalance,
        execution=config.execution,
    )
    assert not any(o.symbol == buy for o in orders)
    assert any(o.symbol == sell and o.side == Side.SELL for o in orders)
    assert any(o.symbol == other and o.side == Side.BUY for o in orders)
    assert observations[-1]["deferred_buy_symbols"] == [str(buy)]
    assert Decimal(
        observations[-1]["planned_whole_quantities"][str(buy)]
    ).to_integral_value() == Decimal(observations[-1]["planned_whole_quantities"][str(buy)])
    # An independently requested native full exit survives the same signal.
    targets[buy] = 0.0
    assert policy(targets, ledger, day, np.eye(len(panel.symbols)))[buy] == 0.0
    assert str(buy) not in observations[-1]["deferred_buy_symbols"]


def test_gap_only_ablation_catches_continuation_and_zero_whole_buy_does_not_gate():
    config, panel = setup()
    i = 300
    prices = np.full_like(panel.adjusted_closes, 100.0)
    opens = prices.copy()
    opens[i, 0] = 105.0
    prices[i, 0] = 110.0
    panel = replace(panel, adjusted_closes=prices, adjusted_opens=opens)
    ledger = LedgerSnapshot(panel.sessions[i], Decimal(100000), {})
    targets = dict.fromkeys(panel.symbols, 0.0)
    s = panel.symbols[0]
    targets[s] = 0.2
    reversal, _ = driver.make_policy(config, panel, driver.ARMS[1], 6)
    gap, obs = driver.make_policy(config, panel, driver.ARMS[2], 6)
    assert reversal(targets, ledger, ledger.session_date, np.eye(len(panel.symbols))) == targets
    assert gap(targets, ledger, ledger.session_date, np.eye(len(panel.symbols)))[s] == 0.0
    targets[s] = float(Decimal(".5") * panel.marks_at(i)[s] / ledger.equity)
    assert gap(targets, ledger, ledger.session_date, np.eye(len(panel.symbols))) == targets
    assert obs[-1]["optional_buy_symbols"] == obs[-1]["deferred_buy_symbols"] == []


@pytest.mark.parametrize("arm", driver.ARMS[1:])
def test_native_future_mutation_preserves_prior_instructions_fills_and_accounting(arm):
    config, panel = setup()
    cutoff = 330
    changes = {}
    for field, factor in [
        ("adjusted_closes", 5),
        ("adjusted_opens", 7),
        ("raw_closes", 4),
        ("window_vwaps", 3),
        ("window_volumes", 0.1),
    ]:
        values = getattr(panel, field).copy()
        values[cutoff:] *= factor
        changes[field] = values
    changed = replace(panel, **changes)
    outputs = []
    for visible in [panel, changed]:
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
