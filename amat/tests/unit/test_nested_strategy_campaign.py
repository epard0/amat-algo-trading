"""Economic comparisons and causal scheduled decisions for the research driver."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.runner import ScheduledDecisionContext, run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from autoresearch.genetic.data import CandleDataset, training_labels

SPEC = importlib.util.spec_from_file_location(
    "nested_strategy_campaign", Path(__file__).parents[2] / "scripts/nested_strategy_campaign.py"
)
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)


def test_inactive_parameters_do_not_create_distinct_strategies():
    reference = campaign.baseline_recipe()
    changed = {
        **reference,
        "stop": 0.14,
        "reward": 3.8,
        "embargo": 21,
        "feature_a": "gp:1",
        "feature_b": "momentum:63",
        "polarity": -1,
        "mix": 0.4,
    }
    assert campaign.canonical(changed) == reference


@pytest.mark.parametrize("family", [f for f in campaign.FAMILIES if f != "portfolio"])
def test_global_slots_cover_feature_polarity_exit_combinations(family):
    proposals = [campaign.proposal(family, slot, 11) for slot in range(12)]
    actual = {(p["feature_a"], p["polarity"], p["exit_mode"]) for p in proposals}
    expected = {
        (feature, polarity, exit_mode)
        for feature in campaign.FAMILIES[family]
        for polarity in (-1, 1)
        for exit_mode in ("none", "stop", "both")
    }
    assert actual == expected
    assert proposals == [campaign.proposal(family, slot, 11) for slot in range(12)]


def metric(returns, **overrides):
    return {
        "returns": returns,
        "dates": list(range(len(returns))),
        "max_drawdown": -0.1,
        "expected_shortfall_95": 0.02,
        "warnings": [],
        **overrides,
    }


def test_risk_gate_rejects_higher_return_with_worse_tail_loss():
    base = metric([0.001] * 40)
    candidate = metric([0.002] * 40, expected_shortfall_95=0.021)
    result = campaign.score(candidate, base)
    assert result["score"] > 0
    assert not result["eligible"]
    assert result["failed_gates"] == ["tail_loss_worse_than_baseline"]
    assert campaign.score(base, base)["score"] == 0


def test_paired_metrics_reject_mismatched_calendar():
    with pytest.raises(ValueError, match="Unpaired"):
        campaign.score(metric([0.001] * 40, dates=list(range(1, 41))), metric([0.001] * 40))


def test_evidence_is_immutable_and_checksum_validated(tmp_path):
    path = tmp_path / "receipt.json"
    campaign.write(path, {"selected": "baseline"})
    campaign.write(path, {"selected": "baseline"})
    with pytest.raises(ValueError, match="changed evidence"):
        campaign.write(path, {"selected": "new"})
    path.write_text(path.read_text().replace("baseline", "modified"))
    with pytest.raises(ValueError, match="checksum"):
        campaign.read(path)


def test_label_purge_uses_execution_horizon():
    close = (100 + np.arange(40))[:, None].astype(float)
    dataset = CandleDataset(
        tuple(str(i) for i in range(40)),
        ("AAA",),
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close},
        close,
        "1d",
        "corporate_action_adjusted",
        {},
    )
    labels, usable = training_labels(dataset, max_lag=0, one_way_bps=2)
    assert not usable[-2:].any()
    assert usable[-3].all()
    expected = close[-1] / close[-2] * (1 - 0.0002) / (1 + 0.0002) - 1
    np.testing.assert_allclose(labels[-3], expected)


def test_new_schedule_fields_preserve_prefix_and_execution_contract():
    config = Path(__file__).parents[2] / "configs/strategy/amat_v13_return_target.toml"
    base = load_strategy_config(config).strategy
    panel = generate_panel(base.to_universe(), sessions=350, seed=22, trend_strength=0.5)
    modified = campaign.configured(
        base, {**campaign.baseline_recipe(), "cadence": 1, "positions": 3, "vol_power": 1.0}
    )
    declaration = ScheduledDecisionContext(
        "candidate", panel.sessions[280], panel.sessions[279], modified
    )
    reference = run_backtest(
        base, panel, base.to_universe(), decision_schedule=(), full_history=True
    )
    result = run_backtest(
        base, panel, base.to_universe(), decision_schedule=[declaration], full_history=True
    )
    assert result.equity_curve[:281] == reference.equity_curve[:281]
    assert result.equity_curve[282:] != reference.equity_curve[282:]
    measured = campaign.metrics(result, panel, str(panel.sessions[279]), str(panel.sessions[-1]))
    expected_turnover = (
        sum(
            sum(float(f.quantity * f.price) for f in result.sessions[i].fills)
            / float(result.sessions[i - 1].equity)
            for i in range(280, len(panel))
        )
        * 252
        / (len(panel) - 280)
    )
    assert measured["annualized_turnover"] == pytest.approx(expected_turnover)
    assert measured["accounting_residual_max"] < 1e-8
    altered_execution = modified.model_copy(
        update={
            "execution": modified.execution.model_copy(
                update={"signal_to_execution_lag_sessions": 2}
            )
        }
    )
    with pytest.raises(DataIntegrityError):
        run_backtest(
            base,
            panel,
            base.to_universe(),
            decision_schedule=[replace(declaration, strategy=altered_execution)],
        )


@pytest.mark.parametrize("phase", ["train", "selection"])
def test_replay_feature_override_preserves_unavailable_warmup(tmp_path, monkeypatch, phase):
    config = Path(__file__).parents[2] / "configs/strategy/amat_v13_return_target.toml"
    base = load_strategy_config(config).strategy
    panel = generate_panel(base.to_universe(), sessions=350, seed=22, trend_strength=0.5)
    arrays = {"momentum:63": np.zeros_like(panel.adjusted_closes)}
    monkeypatch.setattr(campaign, "inputs", lambda _root: ({}, base, panel, {}))
    monkeypatch.setattr(campaign, "market", lambda _root, _world: (panel, {}))
    monkeypatch.setattr(campaign, "feature_bank", lambda _root, _world, _fold: arrays)
    recipe = {**campaign.baseline_recipe(), "effect": 0.2, "feature_a": "momentum:63"}
    declaration = {
        "root": str(tmp_path),
        "world": "historical",
        "fit_world": "historical",
        "fold": "fixture",
        "phase": phase,
        "lower": str(panel.sessions[279]),
        "upper": str(panel.sessions[-1]),
        "recipe": recipe,
        "cost": 2,
    }
    receipt = campaign.job(declaration)
    folder = tmp_path / "runs" / campaign.digest_json(declaration)
    with np.load(folder / "path.npz") as archive:
        equity = archive["equity"]
    reference = run_backtest(
        base,
        panel,
        base.to_universe(),
        one_way_bps=2,
        max_entry_window_participation=0.01,
        decision_schedule=(),
        full_history=True,
    )
    np.testing.assert_array_equal(equity, [float(row.equity) for row in reference.sessions])
    assert receipt["metrics"]["annualized_turnover"] > 0
    assert campaign.job(declaration) == receipt
