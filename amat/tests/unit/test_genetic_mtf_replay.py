"""MTF research alignment and exact authoritative replay parity."""

import datetime as dt
from dataclasses import replace
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pytest

from amat.backtest.exits import ExitRules
from amat.backtest.panel import MarketPanel
from amat.data.synthetic import generate_panel
from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate, Program
from autoresearch.experiments import Simulation
from autoresearch.genetic.mtf_replay import (
    align_closed_values,
    blend_mtf,
    prepare_replay,
    recipe_digest,
    replay_prepared,
)
from autoresearch.genetic.multitimeframe import aggregate_completed
from autoresearch.genetic.replay import exact_replay_equal

ROOT = Path(__file__).parents[2]


def market(start="2024-06-03", end="2024-07-12"):
    dates = tuple(x.date() for x in xcals.get_calendar("XNYS", start=start, end=end).sessions)
    x = np.ones((len(dates), 1)) * 100
    return MarketPanel(
        sessions=dates,
        symbols=("SPY",),
        adjusted_closes=x,
        raw_closes=x,
        window_vwaps=x,
        window_volumes=x,
        adjusted_opens=x,
        adjusted_highs=x,
        adjusted_lows=x,
        session_volumes=x,
        source="scenario(mtf)",
    )


def subset(panel, indices):
    arrays = {
        name: getattr(panel, name)[indices]
        for name in (
            "adjusted_closes",
            "raw_closes",
            "window_vwaps",
            "window_volumes",
            "adjusted_opens",
            "adjusted_highs",
            "adjusted_lows",
            "session_volumes",
        )
    }
    return replace(panel, sessions=tuple(panel.sessions[i] for i in indices), **arrays)


def test_weekly_monthly_only_completed_and_no_stale_after_missing_period():
    data = market()
    for frame in ("1w", "1mo"):
        source = aggregate_completed(data, frame)
        values = np.full(source.adjusted_closes.shape, 0.5)
        aligned = align_closed_values(data, source, values, timeframe=frame)
        assert np.isnan(aligned[0, 0])
        assert aligned[data.sessions.index(source.sessions[0]), 0] == 0.5
    source = aggregate_completed(data, "1w")
    values = np.full(source.adjusted_closes.shape, 0.5)
    values[-1] = np.nan
    assert np.isnan(align_closed_values(data, source, values, timeframe="1w")[-1, 0])
    shorter = subset(source, list(range(len(source.sessions) - 1)))
    assert np.isnan(align_closed_values(data, shorter, values[:-1], timeframe="1w")[-1, 0])


def test_intraday_cutoff_missing_latest_and_no_previous_session_reuse():
    data = market("2024-07-01", "2024-07-03")
    stamps = (
        dt.datetime(2024, 7, 1, 19, 45, tzinfo=dt.UTC),
        dt.datetime(2024, 7, 1, 20, tzinfo=dt.UTC),
        dt.datetime(2024, 7, 3, 17, 15, tzinfo=dt.UTC),
    )
    source = replace(data, sessions=stamps)
    result = align_closed_values(data, source, np.array([[0.5], [np.nan], [1.0]]), timeframe="15m")
    assert np.isnan(result).all()  # Latest missing; absentday; earlyclose futurebar.


def test_daily_no_future_or_missing_row_forwardfill():
    data = market("2024-07-01", "2024-07-03")
    source = subset(data, [0, 2])
    result = align_closed_values(data, source, np.ones((2, 1)))
    assert result[0, 0] == result[2, 0] == 1
    assert np.isnan(result[1, 0])


def test_blend_missing_neutral_fixed_weights_positive_gates():
    base = np.array([[0.5, 0.0, np.nan, 0.99]])
    values = {"15m": np.array([[1.0, 1.0, 1.0, 1.0]]), "1h": np.full((1, 4), np.nan)}
    result = blend_mtf(base, values, {"15m": 0.5, "1h": 0.5}, 0.25)
    np.testing.assert_allclose(result, [[0.625, 0.0, np.nan, 1.0]], equal_nan=True)
    assert base[0, 0] == 0.5


@pytest.mark.parametrize(
    "weights,effect",
    [
        ({"1h": 1.1}, 0.25),
        ({"1h": -1}, 0.25),
        ({"2h": 1}, 0.25),
        ({"1h": 1}, 0.26),
        ({"1h": float("nan")}, 0.25),
    ],
)
def test_invalid_blend(weights, effect):
    with pytest.raises(ValueError):
        blend_mtf(np.ones((1, 1)), {"1h": np.ones((1, 1))}, weights, effect)


@pytest.fixture(scope="module")
def prepared():
    candidate = Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    )
    data = replace(
        generate_panel(candidate.config.strategy.to_universe(), sessions=280),
        source="scenario(mtf-replay)",
    )
    program = Program(
        schema_version="strategy-development/9",
        name="mtf-replay-test",
        baseline=candidate,
        load_start=data.sessions[0],
        measured_start=data.sessions[260],
        train_end=data.sessions[-2],
        validation_end=data.sessions[-1],
        frozen_files={},
        dependencies={},
    )
    return prepare_replay(Simulation(data, program, None), candidate)


def test_zero_effect_authoritative_replay_parity(prepared):
    expected = prepared.simulation.run(prepared.candidate)
    actual = replay_prepared(prepared)
    assert exact_replay_equal(expected, actual)


def test_recipe_digest_binds_matrices_contracts_exits(prepared):
    values = {"1d": np.ones_like(prepared.trace.scores)}
    args = {
        "scores_by_tf": values,
        "weights": {"1d": 1},
        "max_effect": 0.1,
        "contracts": {"1d": "strict"},
    }
    first = recipe_digest(prepared, **args)
    assert first == recipe_digest(prepared, **args)
    assert first != recipe_digest(prepared, **{**args, "contracts": {"1d": "native"}})
    assert first != recipe_digest(prepared, **args, exit_rules=ExitRules(stop_loss=0.05))
    values["1d"][0, 0] = -1
    assert first != recipe_digest(prepared, **args)


def test_authoritative_runner_receives_mtf_and_exit_rules(prepared, monkeypatch):
    import autoresearch.genetic.mtf_replay as module

    original = module.run_backtest
    seen = {}

    def capture(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_backtest", capture)
    rules = ExitRules(stop_loss=0.05, take_profit=0.1)
    replay_prepared(
        prepared,
        scores_by_tf={"1d": np.ones_like(prepared.trace.scores)},
        weights={"1d": 1},
        max_effect=0.1,
        exit_rules=rules,
    )
    assert seen["exit_rules"] == rules
    assert seen["signal_override"] is not None
    assert seen["max_entry_window_participation"] == 0.01
    assert seen["actions_by_session"] is prepared.simulation.actions


def test_delayed_scaled_baseline_authoritative_parity(prepared):
    assert exact_replay_equal(
        prepared.simulation.run(prepared.candidate, delay=1, scalar=0.5),
        replay_prepared(prepared, delay=1, scalar=0.5),
    )


@pytest.mark.parametrize("change", ["naive", "symbols", "infinity"])
def test_alignment_invalid_source(change):
    data = market("2024-07-01", "2024-07-03")
    source = data
    values = np.ones_like(data.adjusted_closes)
    if change == "naive":
        source = replace(
            data,
            sessions=tuple(
                dt.datetime.combine(day, dt.time(20)).replace(tzinfo=None) for day in data.sessions
            ),
        )
    elif change == "symbols":
        source = replace(data, symbols=("QQQ",))
    else:
        values[0, 0] = np.inf
    with pytest.raises(ValueError):
        align_closed_values(data, source, values)


def test_future_mutation_leaves_aligned_prefix_unchanged():
    data = market()
    source = aggregate_completed(data, "1w")
    values = np.full(source.adjusted_closes.shape, 0.5)
    first = align_closed_values(data, source, values, timeframe="1w")
    values[-1] = -1
    second = align_closed_values(data, source, values, timeframe="1w")
    boundary = data.sessions.index(source.sessions[-1])
    np.testing.assert_array_equal(first[:boundary], second[:boundary])
