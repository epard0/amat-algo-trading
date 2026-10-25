"""Fold-local feature authority and isolated historical replay contracts."""

import datetime as dt
import json
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate
from autoresearch.genetic import walkforward_study as study
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import PatternDefinition

ROOT = Path(__file__).parents[2]
DAYS = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(90))
FOLD = {
    "name": "first",
    "measured_start": str(DAYS[1]),
    "train_end": str(DAYS[30]),
    "validation_end": str(DAYS[50]),
    "test_end": str(DAYS[70]),
}


@pytest.fixture
def inputs():
    baseline = Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    )
    pattern = PatternDefinition.model_validate_json(
        (ROOT / "docs/genetic-patterns/symbolic.example.json").read_text()
    )
    return baseline, pattern


def test_fold_boundaries_allow_rolling_training_but_disjoint_outer_windows():
    second = {
        **FOLD,
        "name": "second",
        "measured_start": str(DAYS[10]),
        "train_end": str(DAYS[50]),
        "validation_end": str(DAYS[70]),
        "test_end": str(DAYS[80]),
    }
    assert study.validate_folds([FOLD, second], DAYS) == [FOLD, second]
    with pytest.raises(ValueError, match="overlap"):
        study.validate_folds([FOLD, {**second, "validation_end": str(DAYS[69])}], DAYS)
    with pytest.raises(ValueError, match="unique"):
        study.validate_folds([FOLD, FOLD], DAYS)
    with pytest.raises(ValueError, match="ordered"):
        study.validate_folds([{**FOLD, "train_end": FOLD["measured_start"]}], DAYS)


@pytest.mark.parametrize("name", ["../escape", "x/y", "", "."])
def test_unsafe_fold_names_refused(name):
    with pytest.raises(ValueError):
        study.validate_folds([{**FOLD, "name": name}], DAYS)


def test_train_eligibility_cannot_use_future_and_counts_source_bars():
    panel = SimpleNamespace(sessions=DAYS, symbols=("SPY", "QQQ"))
    values = np.ones((len(DAYS), 2))
    values[:31, 1] = np.nan
    result = study.fold_feature_metadata(panel, panel, values, values, FOLD)
    assert result["training_eligible_assets"] == [True, False]
    assert result["training_finite_daily_decision_counts"] == [30, 0]
    assert result["training_source_bar_count"] == 30
    values[31:, 1] = 99
    assert result == study.fold_feature_metadata(panel, panel, values, values, FOLD)
    second = {**FOLD, "measured_start": str(DAYS[31]), "train_end": str(DAYS[60])}
    assert study.fold_feature_metadata(panel, panel, values, values, second)[
        "training_eligible_assets"
    ] == [True, True]


def test_unmasked_cache_recovery_and_tamper_rejection(inputs, tmp_path, monkeypatch):
    _, pattern = inputs
    (tmp_path / "features").mkdir()
    panel = SimpleNamespace(sessions=DAYS, symbols=("SPY", "QQQ"))
    values = np.ones((len(DAYS), 2))
    values[:31, 1] = np.nan
    monkeypatch.setattr(
        study,
        "_STATE",
        {
            "output": tmp_path,
            "panel": panel,
            "pattern": pattern,
            "context_sha256": "a" * 64,
            "folds": [FOLD],
            "views": dict.fromkeys(study.TIMEFRAMES, panel),
        },
    )
    monkeypatch.setattr(study, "evaluate_symbolic", lambda *_args: values)
    monkeypatch.setattr(study, "align_closed_values", lambda *_args, **_kwargs: values)
    study._build_method("genetic_pattern")
    target = tmp_path / "features/genetic_pattern.npz"
    binding = target.with_suffix(".json")
    with np.load(target) as archive:
        assert archive["1d"][-1, 1] == 1  # Shared bank remains unmasked.
        arrays = {key: archive[key] for key in archive.files}
    original = target.read_bytes()
    binding.unlink()
    study._build_method("genetic_pattern")
    assert target.read_bytes() == original
    binding.unlink()
    arrays["1d"][-1, 1] = 7
    np.savez(target, **arrays)
    with pytest.raises(ValueError, match="matrix checksum"):
        study._build_method("genetic_pattern")


def test_immutable_receipts_verify_checksum(tmp_path):
    path = tmp_path / "receipt.json"
    study._write(path, {"one": 1})
    study._write(path, {"one": 1})
    with pytest.raises(ValueError, match="immutable"):
        study._write(path, {"one": 2})
    envelope = json.loads(path.read_text())
    envelope["payload"]["one"] = 3
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="checksum"):
        study._read(path)


def test_worker_masks_each_fold_and_restores_prior_global(inputs, monkeypatch):
    base, _ = inputs
    recipe = {**study.prior._baseline_recipe(base), "max_effect": 0.1}
    arrays = {frame: np.ones((90, 2)) for frame in study.TIMEFRAMES}
    eligible = {
        "first": {frame: {"training_eligible_assets": [True, False]} for frame in study.TIMEFRAMES},
        "second": {frame: {"training_eligible_assets": [True, True]} for frame in study.TIMEFRAMES},
    }
    state = {
        "baseline": base,
        "output": Path("unused"),
        "fold_by_name": {"first": FOLD, "second": {**FOLD, "name": "second"}},
        "simulations": {"first": {}, "second": {}},
        "feature_cache": OrderedDict(),
    }
    monkeypatch.setattr(study, "_STATE", state)
    monkeypatch.setattr(
        study, "_load_method", lambda _method: (arrays, {"eligibility_by_fold": eligible})
    )
    original = study.prior._CONTEXT
    observed = []

    def evaluate(_recipe, _phase):
        observed.append(study.prior._CONTEXT["feature_cache"]["genetic_pattern"]["1d"].copy())
        return {"score": 1}

    monkeypatch.setattr(study.prior, "evaluate_recipe", evaluate)
    study._evaluate_task((recipe, "first", "train"))
    study._evaluate_task((recipe, "second", "test"))
    assert np.isnan(observed[0][:, 1]).all()
    assert (observed[1] == 1).all()
    assert study.prior._CONTEXT is original
    assert (arrays["1d"] == 1).all()


def test_worker_candidate_failure_is_local_and_data_failure_propagates(inputs, monkeypatch):
    base, _ = inputs
    recipe = study.prior._baseline_recipe(base)
    monkeypatch.setattr(study, "_STATE", {"baseline": base, "fold_by_name": {"first": FOLD}})
    invalid = study._evaluate_task(({**recipe, "target_vol": -1}, "first", "train"))
    assert invalid["status"] == "invalid_candidate" and invalid["score"] is None
    monkeypatch.setattr(
        study, "_load_method", lambda _method: (_ for _ in ()).throw(ValueError("corrupt data"))
    )
    with pytest.raises(ValueError, match="corrupt data"):
        study._evaluate_task(({**recipe, "max_effect": 0.1}, "first", "train"))


def test_summary_excludes_inactive_placeholders_and_reports_missing_folds(inputs):
    base, _ = inputs
    recipe = study.prior._baseline_recipe(base)
    sha = digest_json(recipe)
    metrics = {"annualized_return": 0.1, "total_return": 0.2, "max_drawdown": -0.1}
    search = {
        "selected_recipe_sha256_by_policy": dict.fromkeys(("balanced", "return", "defensive"), sha),
        "selected_by_policy": dict.fromkeys(("balanced", "return", "defensive"), recipe),
        "test": [{"status": "evaluated", "recipe_sha256": sha, "metrics": metrics}],
    }
    results = [
        {"name": "first", "search": search},
        {"name": "second", "search": {**search, "test": []}},
    ]
    policy = study.summarize_folds(results, sha)["policies"]["balanced"]
    assert policy["registered_fold_count"] == 2 and policy["omitted_fold_count"] == 1
    assert policy["active_method_counts"] == {}
    assert policy["active_family_counts"] == {"portfolio_only": 1}
    assert policy["active_exit_parameter_ranges"]["stop_threshold"] is None
    assert policy["selected_baseline_count"] == 1


def test_search_spec_contains_exact_baseline(inputs):
    from autoresearch.genetic.parameter_search import _validate_spec

    baseline, _ = inputs
    spec = study._spec(baseline, ["genetic_pattern"], "a" * 64, 1, 640, 128)
    _validate_spec(spec)
    assert study.prior._candidate(spec["baselines"][0], baseline).digest() == baseline.digest()


def test_stability_requires_available_active_views(inputs):
    base, _ = inputs
    baseline = study.prior._baseline_recipe(base)
    baseline_sha = digest_json(baseline)
    recipe = {**baseline, "max_effect": 0.2, "weight_1d": 0.5, "weight_1mo": 0.5}
    sha = digest_json(recipe)
    policies = ("balanced", "return", "defensive")
    metrics = {"annualized_return": 0.1, "total_return": 0.2, "max_drawdown": -0.1}
    records = [
        {"status": "evaluated", "recipe_sha256": value, "metrics": metrics}
        for value in (sha, baseline_sha)
    ]
    search = {
        "selected_recipe_sha256_by_policy": dict.fromkeys(policies, sha),
        "selected_by_policy": dict.fromkeys(policies, recipe),
        "test": records,
    }
    eligibility = {
        "1d": {"training_eligible_assets": [True]},
        "1mo": {"training_eligible_assets": [False]},
    }
    results = [
        {
            "name": "fold",
            "search": search,
            "selected_feature_eligibility": dict.fromkeys(policies, eligibility),
        }
    ]
    policy = study.summarize_folds(results, baseline_sha)["policies"]["balanced"]
    assert policy["active_family_counts"] == {"genetic": 1}
    assert policy["active_timeframe_coefficient_ranges"]["1d"] == {"min": 0.1, "max": 0.1}
    assert policy["active_timeframe_coefficient_ranges"]["1mo"] is None
    assert policy["active_exit_parameter_ranges"]["reward_risk"] is None
