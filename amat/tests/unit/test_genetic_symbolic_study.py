"""Common-calendar numerical screens and frozen resumable study evidence."""

import datetime as dt
import json
from dataclasses import replace

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.domain.types import Symbol
from autoresearch.genetic.data import CandleDataset
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.symbolic_search import SymbolicSearchProtocol, SymbolicTerminal
from autoresearch.genetic.symbolic_study import (
    SymbolicScreen,
    SymbolicScreenConfig,
    run_symbolic_study,
)


@pytest.fixture
def market(monkeypatch):
    monkeypatch.setattr(
        "autoresearch.genetic.symbolic_study._source_hashes", lambda: {"test": "fixed"}
    )
    t = np.arange(180)[:, None]
    closes = 100 * np.exp(0.0005 * t + 0.03 * np.sin(t / np.array([[5, 8, 11]])))
    opens = closes * (1 + 0.005 * np.cos(t / np.array([[2, 3, 4]])))
    fields = {
        "open": opens,
        "high": np.maximum(opens, closes) * 1.01,
        "low": np.minimum(opens, closes) * 0.99,
        "close": closes,
    }
    dates = tuple(dt.date(2018, 1, 1) + dt.timedelta(days=i) for i in range(len(t)))
    symbols = ("SPY", "AAA", "BBB")
    panel = MarketPanel(
        sessions=dates,
        symbols=tuple(Symbol(s) for s in symbols),
        adjusted_closes=closes,
        raw_closes=closes,
        window_vwaps=opens,
        window_volumes=np.full_like(closes, 1e6),
        source="scenario(symbolic-study-fixture)",
        adjusted_opens=opens,
        adjusted_highs=fields["high"],
        adjusted_lows=fields["low"],
        session_volumes=np.full_like(closes, 1e7),
    )
    data = CandleDataset(
        tuple(day.isoformat() for day in dates),
        symbols,
        fields,
        opens,
        "1d",
        "corporate_action_adjusted",
        {"training_start": str(dates[0]), "training_end": str(dates[-1]), "symbols": list(symbols)},
    )
    protocol = SymbolicSearchProtocol(
        population_size=6,
        generations=1,
        seed=11,
        max_depth=2,
        max_nodes=7,
        feature_terminals=(
            SymbolicTerminal("momentum", lookback=5),
            SymbolicTerminal("body_atr", lookback=5),
        ),
    )
    return data, panel, protocol


def pattern(root=None):
    if root is None:
        root = {
            "node": "feature",
            "name": "momentum",
            "lookback": 5,
            "lag": 0,
            "source": "self",
            "transform": "time_series_rank",
        }
    return parse_pattern(
        {
            "schemaVersion": "patternSchema/0.1.0",
            "name": "fixture",
            "timeframe": "1d",
            "priceBasis": "corporate_action_adjusted",
            "availability": "completed_bar_close",
            "missingPolicy": "unavailable",
            "binding": {
                "role": "feature",
                "direction": "long_support",
                "threshold": 0.0,
                "maxEffect": 0.25,
            },
            "genome": {"kind": "symbolic_indicator", "root": root, "outputTransform": "tanh"},
        }
    )


def test_common_terminal_calendar_and_long_event_score(market):
    data, panel, protocol = market
    screen = SymbolicScreen(data, panel, protocol, SymbolicScreenConfig(min_effect_sessions=2))
    assert not screen.rows[:67].any()
    assert not screen.rows[-2:].any()
    candidate = pattern()
    result = screen(candidate)
    assert result.failure is None
    values = screen.evaluator.evaluate(candidate).values[screen.rows]
    expected = (values * 0.5 * screen.labels[screen.rows]).mean() * 10000 - 0.01
    assert result.fitness == pytest.approx(expected)
    assert result.diagnostics["complete_training_sessions"] == int(screen.rows.sum())
    assert result.diagnostics["evaluation"]["registry_sha256"]


def test_invalid_arithmetic_cannot_select_a_different_calendar(market):
    data, panel, protocol = market
    screen = SymbolicScreen(data, panel, protocol, SymbolicScreenConfig(min_effect_sessions=2))
    root = {
        "node": "binary",
        "op": "safe_div",
        "left": pattern().genome.root.model_dump(),
        "right": {"node": "constant", "value": 0.0},
    }
    assert screen(pattern(root)).failure == "unavailable_expression_on_common_calendar"
    assert (
        screen(
            pattern({"node": "binary", "op": "sub", "left": root["left"], "right": root["left"]})
        ).failure
        == "constant_or_nearly_constant_expression"
    )


def test_direct_screen_rejects_unregistered_terminal(market):
    data, panel, protocol = market
    screen = SymbolicScreen(data, panel, protocol, SymbolicScreenConfig(min_effect_sessions=2))
    leaf = pattern().genome.root.model_dump()
    leaf["lookback"] = 10
    with pytest.raises(ValueError, match="outside frozen grammar"):
        screen(pattern(leaf))


def test_study_pause_resume_matches_uninterrupted_and_copies_identity(market, tmp_path):
    data, panel, protocol = market
    config = SymbolicScreenConfig(min_effect_sessions=2)
    full, partial = tmp_path / "full", tmp_path / "partial"
    expected = run_symbolic_study(data, panel, protocol, config, full)
    paused = run_symbolic_study(data, panel, protocol, config, partial, max_slots=5)
    assert paused["status"] == "paused"
    actual = run_symbolic_study(data, panel, protocol, config, partial, resume=True)
    assert actual == expected
    assert json.loads((partial / "checkpoint.json").read_text()) == json.loads(
        (full / "checkpoint.json").read_text()
    )
    assert actual["status"] == "completed"
    assert actual["champion"] is not None
    assert actual["symbolic_context"]["feature_panel_sha256"]
    with pytest.raises(ValueError, match="already exists"):
        run_symbolic_study(data, panel, protocol, config, full)


def test_resume_binds_volume_even_when_candle_data_unchanged(market, tmp_path):
    data, panel, protocol = market
    config = SymbolicScreenConfig(min_effect_sessions=2)
    run_symbolic_study(data, panel, protocol, config, tmp_path, max_slots=1)
    changed = replace(panel, session_volumes=panel.session_volumes * 2)
    with pytest.raises(ValueError, match="resume protocol/data/code"):
        run_symbolic_study(data, changed, protocol, config, tmp_path, resume=True)


def test_protocol_only_recovery_and_future_source_guard(market, tmp_path, monkeypatch):
    data, panel, protocol = market
    config = SymbolicScreenConfig(min_effect_sessions=2)
    run_symbolic_study(data, panel, protocol, config, tmp_path, max_slots=1)
    (tmp_path / "checkpoint.json").unlink()
    recovered = run_symbolic_study(data, panel, protocol, config, tmp_path, resume=True)
    assert recovered["recorded_slots"] == protocol.budget
    calls = []

    def changed_source():
        calls.append(1)
        return {"source": str(len(calls))}

    monkeypatch.setattr("autoresearch.genetic.symbolic_study._source_hashes", changed_source)
    out = tmp_path / "changed"
    with pytest.raises(ValueError, match="inputs changed"):
        run_symbolic_study(data, panel, protocol, config, out)
    assert not (out / "report.json").exists()


def test_symbolic_cli_rejects_budget_before_loading_data(monkeypatch, tmp_path):
    from autoresearch.genetic.cli import main

    monkeypatch.setattr(
        "autoresearch.genetic.cli.load_daily", lambda *_args, **_kwargs: pytest.fail("data read")
    )
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "discover-symbolic",
                "--snapshot",
                str(tmp_path),
                "--symbols",
                "SPY",
                "--train-start",
                "2016-01-04",
                "--train-end",
                "2020-12-31",
                "--output",
                str(tmp_path / "out"),
                "--population",
                "100001",
            ]
        )
    assert exc.value.code == 2
