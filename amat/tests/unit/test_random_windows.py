"""Random-window selection and measurement invariants."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np

from amat.backtest.attribution import TradeLedger
from amat.backtest.runner import BacktestResult, SessionRecord
from amat.backtest.sleeves import sleeve_strategy
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.domain.fills import Fill
from amat.domain.ledgers import PeriodAttribution
from amat.domain.types import Side, Symbol
from amat.validation.randomwindows import RandomWindow, measure_window, sample_random_windows


def _record(session: dt.date, equity: str, fee: str = "0") -> SessionRecord:
    return SessionRecord(
        session=session,
        equity=Decimal(equity),
        cash=Decimal(equity),
        gross_exposure=0.0,
        forecast_volatility_pre=0.0,
        forecast_volatility_post=0.0,
        fills=(),
        missed=(),
        attribution=PeriodAttribution(
            market_pnl=Decimal(0),
            distributions=Decimal(0),
            cash_yield=Decimal(0),
            fees=Decimal(fee),
            slippage=Decimal(0),
        ),
        binding_constraints=(),
    )


def test_sampling_is_reproducible_stratified_and_split() -> None:
    sessions = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=index) for index in range(1800))
    returns = 0.0002 + 0.01 * np.sin(np.arange(len(sessions)) / 31.0)
    prices = 100.0 * np.cumprod(1.0 + returns)
    left = sample_random_windows(sessions, prices, samples_per_duration=12, seed=7)
    right = sample_random_windows(sessions, prices, samples_per_duration=12, seed=7)
    assert left == right
    assert len(left) == 36
    for duration in (1, 3, 6):
        subset = [window for window in left if window.duration_months == duration]
        assert {window.volatility_bucket for window in subset} == {
            "quiet",
            "normal",
            "volatile",
        }
        assert sum(window.selection_split == "development" for window in subset) == 6
        assert sum(window.selection_split == "validation" for window in subset) == 6


def test_measurement_uses_the_prior_equity_mark_and_only_in_window_costs() -> None:
    strategy = load_strategy_config("configs/strategy/amat_v1.toml").strategy
    universe = sleeve_strategy(strategy, "SPY").to_universe()
    panel = generate_panel(universe, sessions=500, seed=19, trend_strength=0.0)
    start_index = 420
    end_index = 440
    records = tuple(
        _record(
            session,
            str(Decimal("100000") + Decimal(index * 100)),
            "2" if start_index <= index <= end_index else "50",
        )
        for index, session in enumerate(panel.sessions)
    )
    result = BacktestResult(
        sessions=records,
        initial_equity=Decimal("100000"),
        one_way_bps=2.0,
        is_primary=True,
        universe_symbols=("SPY",),
    )
    window = RandomWindow(
        window_id="fixture",
        duration_months=1,
        start=panel.sessions[start_index],
        end=panel.sessions[end_index],
        sessions=end_index - start_index + 1,
        spy_annualized_volatility=0.2,
        volatility_bucket="normal",
        selection_split="validation",
    )
    measurement = measure_window(result, panel, (), window)
    result_index = panel.sessions.index(window.start)
    expected_start = float(records[result_index - 1].equity)
    expected_costs = 2.0 * measurement.sessions
    assert measurement.start_equity == expected_start
    assert measurement.costs == expected_costs
    assert measurement.completed_round_trips == 0


def test_trade_replay_rescales_an_open_position_for_a_split() -> None:
    ledger = TradeLedger()
    symbol = Symbol("XLE")
    buy = Fill(symbol, dt.date(2024, 1, 2), Side.BUY, Decimal("10"), Decimal("100"), Decimal(0))
    sell = Fill(symbol, dt.date(2024, 1, 4), Side.SELL, Decimal("20"), Decimal("50"), Decimal(0))
    ledger.apply(buy)
    ledger.apply_split(symbol, Decimal("2"))
    closed = ledger.apply(sell)
    assert closed is not None
    assert closed.quantity == Decimal("20")
    assert closed.entry_price == Decimal("50")
    assert ledger.open_symbols == ()
