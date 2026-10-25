"""Execution timing and future-data isolation for the isolated proxy study."""
import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location(
    "prop_sequence", Path(__file__).resolve().parents[2] / "scripts/prop_strategy_sequence.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_signal_cannot_capture_pre_entry_move():
    bars = np.ones((390, 5)) * 100
    bars[11:, :4] = 110
    target = np.zeros(390)
    target[10:20] = 1
    pnl, turnover, fills = module.replay(bars, target, 0)
    assert pnl == 0  # jump to next open happens before entry
    assert turnover == 2.2
    assert fills == 2


def test_round_trip_cost_and_forced_flat():
    bars = np.ones((390, 5)) * 100
    pnl, turnover, fills = module.replay(bars, np.ones(390), 1)
    assert np.isclose(pnl, -0.0002)
    assert turnover == 2
    assert fills == 2


def test_future_bars_do_not_change_earlier_signals():
    rng = np.random.default_rng(10)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0005, 390)))
    bars = np.column_stack([close, close + 0.1, close - 0.1, close, np.ones(390) * 1000])
    history = np.tile(bars, (14, 1, 1))
    changed = bars.copy()
    changed[200:, :4] *= 1.2
    for family in module.FAMILIES:
        original = module.targets(bars, history, 100, family, np.zeros((5, 3)))
        altered = module.targets(changed, history, 100, family, np.zeros((5, 3)))
        np.testing.assert_array_equal(original[:200], altered[:200])
