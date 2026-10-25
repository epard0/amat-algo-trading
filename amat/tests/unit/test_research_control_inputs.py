"""Signal controls retain configured context and run from frozen code."""

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from amat.backtest.runner import _apply_signal_override
from amat.data.synthetic import generate_panel
from amat.portfolio import PrecomputedDecisionInputs
from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate, Program
from autoresearch.experiments import Simulation
from autoresearch.summaries import evidence

ROOT = Path(__file__).resolve().parents[2]


def test_signal_override_retains_all_context_and_availability():
    scores = np.array([[np.nan, 0.2], [0.5, 0.8]])
    original = PrecomputedDecisionInputs(
        returns=np.zeros((2, 2)),
        sigma_panel=np.ones((2, 2)),
        long_scores=scores,
        horizon_scores=np.ones((3, 2, 2)),
        signal_quality=np.full((2, 2), 0.4),
        structure_multipliers=np.full((2, 2), 0.7),
    )
    result = _apply_signal_override(original, np.ones((2, 2)), 2)
    for name in (
        "returns",
        "sigma_panel",
        "horizon_scores",
        "signal_quality",
        "structure_multipliers",
    ):
        assert getattr(result, name) is getattr(original, name)
    assert np.isnan(result.long_scores[0, 0])
    np.testing.assert_array_equal(result.long_scores[1], [1, 1])
    np.testing.assert_array_equal(original.long_scores, scores)


@pytest.mark.parametrize("options", [{"delay": 1}, {"delay": 2}, {"shuffle": True}])
def test_v13_controls_form_trades_with_horizon_agreement(options):
    base = Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    )
    assert base.config.strategy.cross_sectional.min_horizon_agreement > 0
    panel = replace(
        generate_panel(
            base.config.strategy.to_universe(), sessions=850, seed=21, trend_strength=0.5
        ),
        source="scenario(control-test)",
    )
    program = Program(
        name="control-test",
        baseline=base,
        load_start=panel.sessions[0],
        measured_start=panel.sessions[503],
        train_end=panel.sessions[-1],
        validation_end=panel.sessions[-1] + dt.timedelta(days=1),
        frozen_files={},
        dependencies={},
    )
    run = Simulation(panel, program, None).run(base, **options)
    assert run.total_fills > 0
    assert not run.warnings


def test_review_reports_control_failure_as_incomplete_evidence():
    warning = "min_horizon_agreement is set but no per-horizon panel was supplied"
    row = {
        "id": 2,
        "candidate": "candidate",
        "parent": "baseline",
        "status": "discard",
        "proposal": "{}",
        "error": None,
        "result": json.dumps(
            {
                "control_summary": {
                    "runner_warnings": {"delay_1": [warning] * 100},
                    "measurements": {"delay_1": {"total_fills": 0}},
                }
            }
        ),
    }
    payload = evidence(row)
    assert payload["control_validity"]["fill_counts"]["delay_1"] == 0
    assert payload["control_validity"]["runner_warnings"]["delay_1"]["count"] == 100
    assert len(payload["control_validity"]["runner_warnings"]["delay_1"]["examples"]) == 2
    assert "control runner warnings" in str(payload["failure_analysis"])


def test_start_executes_frozen_controller(tmp_path, monkeypatch):
    from autoresearch.cli import run_frozen_start

    def run(argv, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["PYTHONPATH"] == str(tmp_path / "runtime")
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert argv[-3:] == [str(tmp_path), "20", "1"]
        assert "frozen_start_batch" in argv[2]
        (tmp_path / "report.json").write_text('{"status":"batch_completed"}')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("autoresearch.cli.subprocess.run", run)
    assert run_frozen_start(tmp_path, 20, True)["status"] == "batch_completed"


def test_frozen_child_imports_session_code_instead_of_checkout(tmp_path):
    from autoresearch.cli import run_frozen_start

    package = tmp_path / "runtime/autoresearch"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "import json\n"
        "def frozen_start_batch(root, iterations, built_in):\n"
        '    (root / "report.json").write_text(json.dumps({\n'
        '        "loaded_from": __file__, "iterations": iterations, "built_in": built_in}))\n'
    )
    result = run_frozen_start(tmp_path, 3, True)
    assert result == {"loaded_from": str(package / "cli.py"), "iterations": 3, "built_in": True}
    assert not list(package.rglob("*.pyc"))
