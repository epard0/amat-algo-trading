"""Pending exits must not become unauthorized replacement buys."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from amat.backtest.runner import DecisionOverlay, run_backtest
from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from amat.domain.types import Side


@pytest.mark.parametrize("lag", [2, 3])
@pytest.mark.parametrize("partial_entry", [False, True])
def test_entry_block_accounts_for_orders_awaiting_execution(lag: int, partial_entry: bool) -> None:
    strategy = load_v6_config("configs/strategy/amat_v6.toml").config.strategy
    strategy = strategy.model_copy(
        update={
            "execution": strategy.execution.model_copy(
                update={"signal_to_execution_lag_sessions": lag}
            )
        }
    )
    universe = strategy.to_universe()
    panel = generate_panel(universe, sessions=285, seed=61, trend_strength=0.4)
    if partial_entry:
        panel = replace(panel, window_volumes=np.full_like(panel.window_volumes, 100.0))
    first_decision = strategy.warmup.min_completed_sessions + 5
    scores = np.zeros(panel.adjusted_closes.shape)
    scores[first_decision:, 0] = 1.0
    # Queue an entry, then queue its exit before the entry has settled.
    scores[first_decision + 1, 0] = 0.0
    allowed = np.ones(scores.shape, dtype=bool)
    allowed[first_decision + lag :] = False
    result = run_backtest(
        strategy,
        panel,
        universe,
        signal_override=scores,
        decision_overlay=DecisionOverlay(np.ones(scores.shape), allowed),
        max_entry_window_participation=0.01 if partial_entry else None,
    )
    dates = {day: i for i, day in enumerate(panel.sessions)}
    columns = {symbol: i for i, symbol in enumerate(panel.symbols)}
    buys = [fill for row in result.sessions for fill in row.fills if fill.side == Side.BUY]
    sells = [fill for row in result.sessions for fill in row.fills if fill.side == Side.SELL]
    assert buys and sells, "The regression must exercise a filled entry and pending exit."
    for fill in buys:
        decision = dates[fill.execution_session] - lag
        assert allowed[decision, columns[fill.symbol]], (
            "A pending exit was counteracted by an unauthorized replacement buy."
        )
    assert all(row.cash >= 0 for row in result.sessions)
