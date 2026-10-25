"""Matched component removals retain coefficients and use paired execution costs."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from autoresearch.genetic import component_study as study
from autoresearch.genetic.data import digest_json


def recipe():
    return {
        "max_effect": 0.2,
        "exit_mode": "both",
        "method": "momentum:63",
        "polarity": -1,
        "risk_scalar": 0.7,
        **{
            f"weight_{frame}": weight
            for frame, weight in zip(study.prior.TIMEFRAMES, (1, 2, 3, 4, 5), strict=True)
        },
    }


@pytest.mark.parametrize(
    "control,retained", [("daily_only", {"1d"}), ("no_intraday", {"1d", "1w", "1mo"})]
)
def test_removal_preserves_retained_coefficients_without_renormalization(control, retained):
    before = recipe()
    after = study.component_recipe(before, control)
    before_total = sum(before[f"weight_{tf}"] for tf in study.prior.TIMEFRAMES)
    after_total = sum(after[f"weight_{tf}"] for tf in study.prior.TIMEFRAMES)
    for tf in study.prior.TIMEFRAMES:
        old = before["max_effect"] * before[f"weight_{tf}"] / before_total
        actual = after["max_effect"] * after[f"weight_{tf}"] / after_total
        assert actual == pytest.approx(old if tf in retained else 0)
    assert before == recipe()
    assert after["risk_scalar"] == before["risk_scalar"]
    assert after["exit_mode"] == before["exit_mode"]


@pytest.mark.parametrize("control", study.CONTROLS)
def test_controls_never_modify_original(control):
    original = recipe()
    study.component_recipe(original, control)
    assert original == recipe()


def test_zero_total_and_noop_mass_are_exact():
    original = recipe()
    original.update({f"weight_{tf}": 0 for tf in study.prior.TIMEFRAMES})
    assert study.component_recipe(original, "daily_only")["max_effect"] == 0
    original["weight_1d"] = 0.12345
    assert study.component_recipe(original, "daily_only") == original
    assert study.component_recipe(original, "no_intraday") == original
    original["max_effect"] = 0
    assert study.component_recipe(original, "no_intraday")["max_effect"] == 0


def fixture_summary():
    baseline = {**recipe(), "max_effect": 0, "exit_mode": "none"}
    candidate = recipe()
    key = digest_json(candidate)
    metrics = {
        "returns": [0.01],
        "dates": ["2025-01-02"],
        "fills": 2,
        "warnings": [],
        "annualized_return": 0.2,
        "max_drawdown": -0.1,
    }
    return baseline, {
        "folds": [
            {
                "fold": {"name": "first", "validation_end": "2024-12-31"},
                "search": {
                    "selected_by_policy": {
                        "balanced": candidate,
                        "return": candidate,
                        "defensive": baseline,
                    },
                    "selected_recipe_sha256_by_policy": {
                        "balanced": key,
                        "return": key,
                        "defensive": digest_json(baseline),
                    },
                    "test": [
                        {
                            "recipe_sha256": digest_json(value),
                            "status": "evaluated",
                            "metrics": metrics,
                        }
                        for value in (baseline, candidate)
                    ],
                },
            }
        ]
    }


def test_jobs_freeze_union_aliases_and_paired_costs():
    baseline, summary = fixture_summary()
    original = deepcopy(summary)
    jobs = study.freeze_jobs(summary, baseline, study.DEFAULT_COSTS)
    assert len(jobs) == 28
    assert len({job["job_sha256"] for job in jobs}) == len(jobs)
    controls = [job for job in jobs if "baseline" in job["profile_aliases"]]
    assert len(controls) == 4
    assert all(job["profile_aliases"] == ["baseline", "defensive"] for job in controls)
    assert {job["cost_bps"] for job in controls} == set(study.DEFAULT_COSTS)
    assert sum(job["p11_expected"] is not None for job in jobs) == 2
    assert summary == original


@pytest.mark.parametrize("costs", [(), (5,), (2, 2), (2, -1), (2, float("nan"))])
def test_cost_registration_rejects_invalid_or_missing_parity(costs):
    baseline, summary = fixture_summary()
    with pytest.raises(ValueError, match="costs"):
        study.freeze_jobs(summary, baseline, costs)


def test_parity_rejects_any_changed_return_or_execution():
    baseline, summary = fixture_summary()
    expected = study.freeze_jobs(summary, baseline, (2,))[0]["p11_expected"]
    study.verify_parity(expected, expected)
    for key, value in (("returns", [0.0100000001]), ("fills", 3), ("warnings", ["warning"])):
        with pytest.raises(ValueError, match="parity"):
            study.verify_parity({**expected, key: value}, expected)


@pytest.mark.parametrize("effect", [0, 0.2])
def test_cost_passed_to_actual_evaluator_and_fold_mask_preserved(monkeypatch, effect):
    captured = {}
    arrays = {tf: np.ones((3, 2)) for tf in study.prior.TIMEFRAMES}
    binding = {
        "eligibility_by_fold": {
            "first": {
                tf: {"training_eligible_assets": [True, False]} for tf in study.prior.TIMEFRAMES
            }
        }
    }
    monkeypatch.setattr(study.p11, "_load_method", lambda _method: (arrays, binding))
    monkeypatch.setattr(
        study,
        "_STATE",
        {"baseline": SimpleNamespace(), "p11_study": "p11", "simulations": {"first": object()}},
    )
    old_context = {"original": True}
    monkeypatch.setattr(study.prior, "_CONTEXT", old_context)

    def evaluate(value, _phase, *, cost):
        captured.update(cost=cost, context=study.prior._CONTEXT, recipe=value)
        return {"returns": []}

    monkeypatch.setattr(study.prior, "evaluate_recipe", evaluate)
    study._evaluate(
        {
            "recipe": {**recipe(), "max_effect": effect},
            "fold": {"name": "first", "validation_end": "2024-12-31"},
            "cost_bps": 20,
            "p11_expected": None,
            "job_sha256": "job",
        }
    )
    assert captured["cost"] == 20
    if effect:
        scores = captured["context"]["feature_cache"]["momentum:63"]["1d"]
        assert np.isnan(scores[:, 1]).all()
        assert (scores[:, 0] == 1).all()
    else:
        assert captured["context"]["feature_cache"] == {}
    assert study.prior._CONTEXT is old_context


def test_nested_output_rejected(tmp_path):
    with pytest.raises(ValueError, match="independent"):
        study.run_component_study(
            tmp_path, tmp_path, tmp_path, p11_study=tmp_path, output=tmp_path / "child"
        )


def test_effective_deduplication_ignores_inactive_placeholders():
    baseline, summary = fixture_summary()
    job = study.freeze_jobs(summary, baseline, (2,))[0]
    changed = deepcopy(job)
    changed["recipe"].update(method="other", polarity=1, weight_1d=999, embargo=400)
    changed["control"] = "no_intraday"
    assert study.replay_key(changed) == study.replay_key(job)
    changed["cost_bps"] = 20
    assert study.replay_key(changed) != study.replay_key(job)
    changed["cost_bps"] = 2
    changed["recipe"]["risk_scalar"] = 0.2
    assert study.replay_key(changed) != study.replay_key(job)


def test_alias_receipts_resume_identically_and_recheck_parity(tmp_path):
    baseline, summary = fixture_summary()
    job = study.freeze_jobs(summary, baseline, (2,))[0]
    records = {}
    study.publish_record(tmp_path, job, {"metrics": job["p11_expected"]}, records)
    path = tmp_path / "receipts" / f"{job['job_sha256']}.json"
    original = path.read_bytes()
    study.publish_record(tmp_path, job, records[job["job_sha256"]], records)
    assert path.read_bytes() == original
    broken = {"metrics": {**job["p11_expected"], "fills": 999}}
    with pytest.raises(ValueError, match="parity"):
        study.publish_record(tmp_path, job, broken, records)


def test_ancestor_output_rejected(tmp_path):
    with pytest.raises(ValueError, match="independent"):
        study.run_component_study(
            tmp_path, tmp_path, tmp_path, p11_study=tmp_path / "child", output=tmp_path
        )


def test_registered_study_resume_makes_zero_evaluation_calls(tmp_path, monkeypatch):
    import datetime as dt
    from pathlib import Path

    from autoresearch.baseline import load_research_config
    from autoresearch.contracts import Candidate

    root = Path(__file__).parents[2]
    config = load_research_config(root / "configs/strategy/amat_v13_return_target.toml")
    candidate = Candidate(config=config)
    baseline = study.prior._baseline_recipe(candidate)
    metrics = {
        "returns": [0.01],
        "dates": ["2020-03-02"],
        "fills": 2,
        "warnings": [],
        "annualized_return": 0.2,
        "max_drawdown": -0.1,
    }
    days = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(90))
    fold = {
        "name": "first",
        "measured_start": str(days[1]),
        "train_end": str(days[30]),
        "validation_end": str(days[50]),
        "test_end": str(days[70]),
    }
    artifact, native = tmp_path / "artifact.json", tmp_path / "native.json"
    snapshot, old, output = tmp_path / "snapshot", tmp_path / "old", tmp_path / "new"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text("{}")
    native.write_text("{}")
    study.p11._write(
        artifact,
        {"candidate": {"config": candidate.model_dump(mode="json")["config"]}, "universe": ["SPY"]},
    )
    context = {
        "folds": [fold],
        "source_sha256": {},
        "dependencies": {},
        "artifact_sha256": study.prior._file_sha(artifact),
        "native_data_sha256": study.prior._file_sha(native),
        "snapshot_manifest_sha256": study.prior._file_sha(snapshot / "manifest.json"),
        "snapshot_partitions": {},
        "panel_sha256": "panel",
    }
    summary = {
        "context_sha256": digest_json(context),
        "folds": [
            {
                "fold": fold,
                "search": {
                    "selected_by_policy": dict.fromkeys(
                        ("balanced", "return", "defensive"), baseline
                    ),
                    "selected_recipe_sha256_by_policy": {
                        policy: digest_json(baseline)
                        for policy in ("balanced", "return", "defensive")
                    },
                    "test": [
                        {
                            "recipe_sha256": digest_json(baseline),
                            "status": "evaluated",
                            "metrics": metrics,
                        }
                    ],
                },
            }
        ],
    }
    study.p11._write(old / "context.json", context)
    study.p11._write(old / "summary.json", summary)
    monkeypatch.setattr(study, "_source_hashes", lambda: {})
    monkeypatch.setattr(study.prior, "_dependencies", lambda: {})
    monkeypatch.setattr(study, "panel_digest", lambda _panel: "panel")
    monkeypatch.setattr(
        study,
        "load_panel",
        lambda *_args, **_kwargs: (
            SimpleNamespace(sessions=days, symbols=("SPY",)),
            SimpleNamespace(partition_sha256={}),
        ),
    )
    monkeypatch.setattr(study, "Simulation", lambda *_args: object())
    calls = []

    def evaluate(job):
        calls.append(job["job_sha256"])
        return {"metrics": metrics, "job_sha256": job["job_sha256"]}

    class Pool:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def map(self, fn, jobs):
            return map(fn, jobs)

    monkeypatch.setattr(study, "ProcessPoolExecutor", Pool)
    monkeypatch.setattr(study, "_evaluate", evaluate)
    original_state = study.p11._STATE
    first = study.run_component_study(artifact, snapshot, native, p11_study=old, output=output)
    assert len(calls) == 4
    assert first["unique_replay_count"] == 4
    calls.clear()
    second = study.run_component_study(artifact, snapshot, native, p11_study=old, output=output)
    assert first == second
    assert calls == []
    assert study.p11._STATE is original_state
    native.write_text("changed")
    with pytest.raises(ValueError, match="identity"):
        study.run_component_study(artifact, snapshot, native, p11_study=old, output=output)
    assert calls == []
