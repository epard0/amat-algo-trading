"""Nested orchestration freezes, split isolation, failure accounting and resume."""

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from amat.data.synthetic import generate_panel
from autoresearch.genetic import nested
from autoresearch.genetic.replay import load_incumbent
from autoresearch.genetic.symbolic_search import SymbolicSearchProtocol, SymbolicTerminal
from autoresearch.genetic.symbolic_study import SymbolicScreenConfig
from autoresearch.genetic.validation import ValidationProtocol, WalkForwardFold

ROOT = Path(__file__).resolve().parents[2]


def _portfolio(dates, excess=0.0):
    baseline = np.linspace(-0.01, 0.01, len(dates))
    methods, paired, outer = {}, {}, {"incumbent": {}}
    for role in ("cost_6bps", "delay_1"):
        outer["incumbent"][role] = {"sharpe": 1.0}
    for method in nested.METHODS:
        methods[method] = {"retained_incumbent": excess == 0.0}
        paired[method] = {
            "dates": [str(day) for day in dates],
            "candidate": (baseline + excess).tolist(),
            "incumbent": baseline.tolist(),
        }
        outer[method] = {role: {"sharpe": 1.0 + excess} for role in ("cost_6bps", "delay_1")}
    return {"methods": methods, "paired_returns": paired, "outer": outer}


@pytest.fixture
def harness(monkeypatch, tmp_path):
    base = load_incumbent(ROOT / "configs/strategy/amat_v13_return_target.toml")
    panel = generate_panel(base.config.strategy.to_universe(), sessions=180)
    panel = replace(
        panel,
        source="scenario(nested-test)",
        adjusted_opens=panel.adjusted_closes * 0.999,
        adjusted_highs=panel.adjusted_closes * 1.01,
        adjusted_lows=panel.adjusted_closes * 0.99,
    )
    fold = WalkForwardFold("fold", panel.sessions[89], panel.sessions[129], panel.sessions[-1])
    validation = ValidationProtocol(
        (fold,), seeds=(11,), null_replicates=2, bootstrap_replicates=20, min_effect_sessions=2
    )
    search = SymbolicSearchProtocol(
        population_size=3,
        generations=0,
        elite_count=1,
        max_depth=0,
        max_nodes=1,
        constants=(),
        feature_terminals=(SymbolicTerminal("momentum", 5),),
    )
    screen = SymbolicScreenConfig(min_effect_sessions=2)
    manifest = SimpleNamespace(model_dump=lambda **_: {"snapshot": "fixture"})
    monkeypatch.setattr(nested, "load_panel", lambda *_, **__: (panel, manifest))
    monkeypatch.setattr(nested, "audit_sessions", lambda *_, **__: {})
    monkeypatch.setattr(nested, "_source_hashes", lambda: {"fixture": "source"})
    monkeypatch.setattr(nested, "_runtime_versions", lambda: {"fixture": "runtime"})
    monkeypatch.setattr(nested, "symbolic_registry_manifest", lambda: {"version": "fixture"})
    monkeypatch.setattr(nested, "default_ledger_path", lambda _: tmp_path / "absent-ledger")
    fit = nested.panel_prefix(panel, fold.fit_end)
    common = np.ones(len(panel), dtype=bool)
    prepared = [
        {
            "fold": fold,
            "panel": panel,
            "fit_panel": fit,
            "dataset": object(),
            "common": common,
            "fit_stop": len(fit),
        }
    ]
    monkeypatch.setattr(nested, "_prepare", lambda *_args: (prepared, {"fold": "fixture"}))
    calls = []

    def discovery(_data, supplied, _protocol, _screen, *, output, feature_mapping, **_kwargs):
        assert supplied.sessions[-1] == fold.fit_end
        assert (tmp_path / "study/protocol.json").exists()
        if feature_mapping is not None:
            assert int(feature_mapping.max()) < len(fit)
        calls.append(("search", feature_mapping is not None))
        output.mkdir(parents=True, exist_ok=True)
        (output / "ledger.json").write_text('{"complete": true}')
        return {"contenders": {"genetic": [], "random": []}}

    def portfolio(_incumbent, supplied, _snapshot, *, output, feature_mapping, **_kwargs):
        assert supplied.sessions[-1] == fold.evaluation_end
        calls.append(("portfolio", feature_mapping is not None))
        output.mkdir(parents=True, exist_ok=True)
        (output / "selection.json").write_text('{"retained": true}')
        return _portfolio(panel.sessions[130:])

    monkeypatch.setattr(nested, "discover_fold", discovery)
    monkeypatch.setattr(nested, "evaluate_fold", portfolio)
    args = dict(  # noqa: C408 - readable fixture options
        start=panel.sessions[0],
        validation=validation,
        search=search,
        screen=screen,
        output=tmp_path / "study",
    )
    return base, panel, args, calls, portfolio


def test_nested_freeze_full_null_pipeline_and_completed_resume(harness, tmp_path):
    base, panel, args, calls, _ = harness
    report = nested.run_validation(base, tmp_path, **args)
    assert report["completed_null_replicates"] == 2
    assert report["candidate_slot_budget"] == 18
    assert calls == [
        ("search", False),
        ("portfolio", False),
        ("search", True),
        ("portfolio", True),
        ("search", True),
        ("portfolio", True),
    ]
    stats = report["statistics"]["methods"]["genetic"]
    assert stats["family_adjusted_null"]["p_value"] == 1.0
    assert "no_selected_incremental_policy" in stats["failed_gates"]
    assert not stats["historical_support"]
    calls.clear()
    assert nested.run_validation(base, tmp_path, **args, resume=True) == report
    assert not calls
    assert panel.sessions[-1] == args["validation"].folds[-1].evaluation_end


def test_failed_null_is_not_counted_as_zero_and_can_resume(harness, monkeypatch, tmp_path):
    base, _, args, calls, portfolio = harness

    def fail(*positional, **kwargs):
        if kwargs["feature_mapping"] is not None:
            raise RuntimeError("null runner failed")
        return portfolio(*positional, **kwargs)

    monkeypatch.setattr(nested, "evaluate_fold", fail)
    with pytest.raises(RuntimeError, match="null runner failed"):
        nested.run_validation(base, tmp_path, **args)
    assert not (args["output"] / "report.json").exists()
    progress = json.loads((args["output"] / "progress.json").read_text())
    assert progress["status"] == "failed"
    assert "never converted to zero" in progress["statistical_result"]
    calls.clear()
    monkeypatch.setattr(nested, "evaluate_fold", portfolio)
    assert nested.run_validation(base, tmp_path, **args, resume=True)["status"] == "completed"
    assert ("search", False) not in calls


def test_completed_artifact_tamper_and_changed_protocol_refuse_resume(harness, tmp_path):
    base, _, args, _, _ = harness
    nested.run_validation(base, tmp_path, **args)
    changed = {**args, "validation": replace(args["validation"], null_seed=13)}
    with pytest.raises(ValueError, match="resume protocol"):
        nested.run_validation(base, tmp_path, **changed, resume=True)
    path = args["output"] / "observed/fold/discovery/ledger.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="evidence changed"):
        nested.run_validation(base, tmp_path, **args, resume=True)


def test_panel_prefix_copies_optional_ohlc_and_rejects_missing_boundary(harness):
    _, panel, args, _, _ = harness
    prefix = nested.panel_prefix(panel, args["validation"].folds[0].fit_end)
    assert len(prefix) == 90
    original = prefix.adjusted_opens.copy()
    panel.adjusted_opens[:] = 1
    np.testing.assert_array_equal(prefix.adjusted_opens, original)
    with pytest.raises(ValueError):
        nested.panel_prefix(panel, dt.date(1900, 1, 1))


def test_outer_dates_cannot_overlap_or_silently_drop_nonfinite_returns():
    dates = (dt.date(2021, 1, 4), dt.date(2021, 1, 5))
    job = {"portfolio": _portfolio(dates)}
    with pytest.raises(ValueError, match="overlap"):
        nested._series([job, job], "genetic")
    job["portfolio"]["paired_returns"]["genetic"]["candidate"][0] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        nested._series([job], "genetic")


def test_support_gate_requires_both_paired_interval_and_family_null(harness):
    _, _, args, _, _ = harness
    validation = replace(
        args["validation"], null_replicates=19, bootstrap_replicates=100, block_sessions=4
    )
    dates = [dt.date(2021, 1, 1) + dt.timedelta(days=i) for i in range(24)]
    observed = [
        {"portfolio": _portfolio(dates[:12], 0.001)},
        {"portfolio": _portfolio(dates[12:], 0.001)},
    ]
    null = [{"portfolio": _portfolio(dates[:12])}, {"portfolio": _portfolio(dates[12:])}]
    report = nested._aggregate(observed, [null] * 19, validation)
    for row in report["methods"].values():
        assert row["historical_support"]
        assert row["family_adjusted_null"]["p_value"] == pytest.approx(0.05)
    report = nested._aggregate(observed, [observed] * 19, validation)
    for row in report["methods"].values():
        assert not row["historical_support"]
        assert "alignment_null_not_rejected" in row["failed_gates"]
