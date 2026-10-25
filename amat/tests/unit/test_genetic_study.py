"""Causal event-screen accounting and reproducible research artifact contracts."""

import copy
import json
from dataclasses import replace

import numpy as np
import pytest

from autoresearch.genetic.data import CandleDataset, training_labels
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.search import SearchProtocol
from autoresearch.genetic.study import CandleScreen, ScreenConfig, run_study


@pytest.fixture
def dataset():
    rows = np.arange(24, dtype=float)[:, None]
    opens = 100 + rows + np.array([[0, 12]])
    closes = opens + np.where(rows % 3 == 0, 1.0, -0.5)
    fields = {
        "open": opens,
        "high": np.maximum(opens, closes) + 2,
        "low": np.minimum(opens, closes) - 2,
        "close": closes,
    }
    return CandleDataset(
        timestamps=tuple(f"2024-01-{i + 1:02d}" for i in range(24)),
        symbols=("AAA", "BBB"),
        fields=fields,
        execution_prices=opens.copy(),
        timeframe="1d",
        price_basis="corporate_action_adjusted",
        manifest={"training_start": "2024-01-01", "training_end": "2024-01-24"},
    )


def candle_pattern():
    return parse_pattern(
        {
            "schemaVersion": "patternSchema/0.1.0",
            "name": "up_candle",
            "timeframe": "1d",
            "priceBasis": "corporate_action_adjusted",
            "availability": "completed_bar_close",
            "missingPolicy": "unavailable",
            "binding": {
                "role": "feature",
                "direction": "long_support",
                "threshold": 0,
                "maxEffect": 0.25,
            },
            "genome": {
                "kind": "candle_conjunction",
                "maxLag": 0,
                "rules": [
                    {
                        "left": {"field": "close", "lag": 0},
                        "op": "gt",
                        "right": {"field": "open", "lag": 0},
                    }
                ],
            },
        }
    )


def test_next_bar_entry_exit_cost_and_maturity(dataset):
    labels, support = training_labels(dataset, max_lag=2, one_way_bps=10)
    expected = dataset.execution_prices[4] / dataset.execution_prices[3] * 0.999 / 1.001 - 1
    np.testing.assert_allclose(labels[2], expected, atol=0, rtol=1e-13)
    assert not support[:2].any()
    assert not support[-2:].any()
    assert np.isnan(labels[:2]).all()
    assert np.isnan(labels[-2:]).all()
    assert support[2:-2].all()


@pytest.mark.parametrize("gap_offset", [-2, -1, 0, 1, 2])
def test_every_bar_in_feature_and_label_window_masks_event(dataset, gap_offset):
    dataset.fields["close"][10 + gap_offset, 0] = np.nan
    labels, support = training_labels(dataset, max_lag=2, one_way_bps=2)
    assert not support[10, 0]
    assert np.isnan(labels[10, 0])
    assert support[10, 1]
    assert support[17, 0]


def test_missing_execution_price_masks_event(dataset):
    dataset.execution_prices[11, 0] = np.nan
    labels, support = training_labels(dataset, max_lag=2, one_way_bps=2)
    assert not support[10, 0]
    assert np.isnan(labels[10, 0])


def test_distinct_dates_not_asset_events_determine_support(dataset):
    protocol = SearchProtocol(population_size=4, generations=1, max_lag=0)
    config = ScreenConfig(min_matched_sessions=10)
    screen = CandleScreen(dataset, protocol, config)
    result = screen(candle_pattern())
    assert result.failure == "insufficient_distinct_matched_sessions"
    assert result.diagnostics["matched_sessions"] == 8
    assert result.diagnostics["matched_asset_events"] == 16
    assert result.fitness is None


def test_common_calendar_and_diagnostic_fitness(dataset):
    dataset.fields["open"][10, 1] = np.nan
    protocol = SearchProtocol(population_size=4, generations=1, max_lag=0)
    config = ScreenConfig(min_matched_sessions=2, complexity_penalty_bps=0.25)
    screen = CandleScreen(dataset, protocol, config)
    assert not screen.rows[8:11].any()
    result = screen(candle_pattern())
    selected = np.arange(24) % 3 == 0
    expected = np.where(selected[screen.rows, None], screen.labels[screen.rows], 0).mean()
    assert result.fitness == pytest.approx(expected * 10_000 - 0.25)


def _protocol():
    return SearchProtocol(population_size=4, generations=2, max_lag=1, seed=8)


def _screen():
    return ScreenConfig(min_matched_sessions=2)


def test_study_pause_resume_matches_uninterrupted(dataset, tmp_path):
    resumed_path, full_path = tmp_path / "resumed", tmp_path / "full"
    first = run_study(dataset, _protocol(), _screen(), resumed_path, max_slots=5)
    assert first["status"] == "paused"
    assert first["recorded_slots"] == 5
    resumed = run_study(dataset, _protocol(), _screen(), resumed_path, resume=True)
    full = run_study(dataset, _protocol(), _screen(), full_path)
    assert resumed == full
    assert json.loads((resumed_path / "checkpoint.json").read_text()) == json.loads(
        (full_path / "checkpoint.json").read_text()
    )
    assert resumed["status"] == "completed"
    assert resumed["execution_authority"] == "none"
    assert resumed["evidence_level"] == "adaptive_training_screen_only"
    assert "P2" in resumed["next_required_gate"]
    assert "Not a portfolio" in " ".join(resumed["limitations"])
    assert resumed["recorded_slots"] == _protocol().budget
    assert run_study(dataset, _protocol(), _screen(), resumed_path, resume=True) == resumed


@pytest.mark.parametrize("change", ["arrays", "costs", "protocol", "execution_prices"])
def test_resume_rejects_changed_inputs(dataset, tmp_path, change):
    run_study(dataset, _protocol(), _screen(), tmp_path, max_slots=3)
    protocol, config = _protocol(), _screen()
    if change == "arrays":
        dataset.fields["high"][5, 0] += 0.25
    elif change == "execution_prices":
        dataset.execution_prices[5, 0] += 0.25
    elif change == "costs":
        config = replace(config, one_way_bps=3)
    else:
        protocol = replace(protocol, seed=9)
    with pytest.raises(ValueError, match=r"differs|mismatch"):
        run_study(dataset, protocol, config, tmp_path, resume=True)


def test_corrupted_checkpoint_is_rejected(dataset, tmp_path):
    run_study(dataset, _protocol(), _screen(), tmp_path, max_slots=3)
    path = tmp_path / "checkpoint.json"
    wrapper = json.loads(path.read_text())
    wrapper["state"]["next_slot"] = 200
    path.write_text(json.dumps(wrapper))
    with pytest.raises(ValueError, match="checksum"):
        run_study(dataset, _protocol(), _screen(), tmp_path, resume=True)


def test_study_refuses_to_overwrite_existing_run(dataset, tmp_path):
    run_study(dataset, _protocol(), _screen(), tmp_path, max_slots=3)
    before = (tmp_path / "checkpoint.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        run_study(dataset, _protocol(), _screen(), tmp_path)
    assert (tmp_path / "checkpoint.json").read_bytes() == before


def test_initialization_interruption_is_resumable(dataset, tmp_path, monkeypatch):
    from autoresearch.genetic import study

    original = study.run_search

    def interrupted(*args, **kwargs):
        if kwargs.get("max_slots") != 0:
            raise RuntimeError("process interrupted before population completed")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(study, "run_search", interrupted)
        with pytest.raises(RuntimeError, match="process interrupted"):
            run_study(dataset, _protocol(), _screen(), tmp_path)
    state = json.loads((tmp_path / "checkpoint.json").read_text())["state"]
    assert state["next_slot"] == 0
    report = run_study(dataset, _protocol(), _screen(), tmp_path, resume=True)
    assert report["status"] == "completed"


def test_actual_array_content_changes_identity(dataset):
    changed = copy.deepcopy(dataset)
    changed.fields["high"][5, 0] += 0.25
    assert changed.manifest == dataset.manifest
    assert changed.digest != dataset.digest
