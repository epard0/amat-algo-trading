"""The stress battery: engine hooks, restatement arithmetic, full-battery shape.

The hooks (missed-fill injection, signal override) are tested against the real
runner on synthetic panels; restatement arithmetic is pinned on hand-built
curves where the answer is computable by inspection.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.domain.fills import MissedFillReason
from amat.errors import CausalityError, DataIntegrityError
from amat.validation.stress import (
    _close_fill_panel,
    _year_removals,
    run_stress_battery,
)


@pytest.fixture(scope="module")
def strategy():
    return load_strategy_config("configs/strategy/amat_v1.toml").strategy


@pytest.fixture(scope="module")
def panel(strategy):
    return generate_panel(strategy.to_universe(), sessions=330, seed=21, trend_strength=0.5)


class TestMissedFillInjection:
    def test_every_tenth_fill_is_withheld_and_recorded(self, strategy, panel) -> None:
        baseline = run_backtest(strategy, panel, strategy.to_universe())
        stressed = run_backtest(strategy, panel, strategy.to_universe(), drop_every_nth_fill=10)
        dropped = [
            miss
            for record in stressed.sessions
            for miss in record.missed
            if miss.reason is MissedFillReason.STRESS_DROPPED
        ]
        assert dropped, "the injection produced no recorded drops"
        assert stressed.total_fills < baseline.total_fills
        # The drop is recorded, never silent: every withheld fill is a miss.
        assert stressed.total_missed >= baseline.total_missed + len(dropped)

    def test_the_baseline_never_emits_the_stress_reason(self, strategy, panel) -> None:
        baseline = run_backtest(strategy, panel, strategy.to_universe())
        reasons = {miss.reason for record in baseline.sessions for miss in record.missed}
        assert MissedFillReason.STRESS_DROPPED not in reasons

    def test_a_cadence_below_two_is_refused(self, strategy, panel) -> None:
        with pytest.raises(CausalityError, match="at least 2"):
            run_backtest(strategy, panel, strategy.to_universe(), drop_every_nth_fill=1)


class TestSignalOverride:
    def test_all_ones_control_differs_from_the_signal(self, strategy, panel) -> None:
        ones = np.ones_like(panel.adjusted_closes)
        control = run_backtest(strategy, panel, strategy.to_universe(), signal_override=ones)
        baseline = run_backtest(strategy, panel, strategy.to_universe())
        assert control.equity_curve != baseline.equity_curve

    def test_availability_is_preserved(self, strategy, panel) -> None:
        # The override is masked by the real signal's NaN pattern, so the
        # control cannot trade before warm-up completes plus the lag.
        ones = np.ones_like(panel.adjusted_closes)
        control = run_backtest(strategy, panel, strategy.to_universe(), signal_override=ones)
        first_fill = next(index for index, record in enumerate(control.sessions) if record.fills)
        assert first_fill >= strategy.execution.signal_to_execution_lag_sessions

    @pytest.mark.parametrize(
        ("doctor", "match"),
        [
            (lambda a: a[:-1], "shape"),
            (lambda a: a * np.nan, "non-finite"),
            (lambda a: a * 2.0, "long-or-cash"),
        ],
    )
    def test_bad_overrides_fail_closed(self, strategy, panel, doctor, match) -> None:
        ones = np.ones_like(panel.adjusted_closes)
        with pytest.raises(DataIntegrityError, match=match):
            run_backtest(strategy, panel, strategy.to_universe(), signal_override=doctor(ones))


class TestCloseFillPanel:
    def test_prices_move_volumes_do_not(self, panel) -> None:
        doctored = _close_fill_panel(panel)
        assert np.array_equal(doctored.window_vwaps, panel.raw_closes)
        assert doctored.window_volumes is panel.window_volumes
        assert doctored.adjusted_closes is panel.adjusted_closes


@dataclass(frozen=True)
class _CurveStub:
    equity_curve: tuple[float, ...]
    session_dates: tuple[dt.date, ...]


class TestYearRemoval:
    def test_hand_computed_recompounding(self) -> None:
        # Two full years: +50% then +10%. Removing the best year leaves +10%.
        dates = (
            dt.date(2020, 1, 2),
            dt.date(2020, 12, 30),
            dt.date(2021, 6, 30),
            dt.date(2021, 12, 30),
        )
        curve = (100.0, 150.0, 160.0, 165.0)
        (entry,) = _year_removals(_CurveStub(curve, dates))  # type: ignore[arg-type]
        assert "2020" in entry.removed
        assert entry.restated_total_return == pytest.approx(0.10)


class TestFullBattery:
    def test_every_family_is_present_and_labelled(self, strategy, panel) -> None:
        from amat.reporting.insights import _coalesce_round_trips, replay_trades

        study = run_stress_battery(
            strategy,
            panel,
            round_trip_loader=lambda result: _coalesce_round_trips(replay_trades(result, panel)),
        )
        assert len(study.cost_ladder) == len(
            [b for b in strategy.costs.stress_one_way_bps if b != 2.0]
        )
        assert [run.name for run in study.delays] == [
            "execution delayed to lag 2",
            "execution delayed to lag 3",
        ]
        assert len(study.missed_fill_injections) == 2
        assert len(study.leave_one_out) == len(strategy.universe.symbols)
        assert {run.name for run in study.leave_one_out} == {
            f"without {symbol}" for symbol in strategy.to_universe().symbols
        }
        assert study.trade_removals and study.year_removals
        assert set(study.correlations) == {"SPY", "60/40 SPY/IEF"}
        assert all(-1.0 <= value <= 1.0 for value in study.correlations.values())
        # Higher costs can never help: the ladder is monotone in total costs.
        ladder_costs = [run.total_costs for run in study.cost_ladder if run.total_costs > 0]
        assert ladder_costs == sorted(ladder_costs)
