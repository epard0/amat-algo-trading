"""Rank persistence must reduce replacements without trapping failed trends."""

import numpy as np
import pytest
from pydantic import ValidationError

from amat.config.models import AllocationConfig, CrossSectionConfig
from amat.config.v6 import load_v6_config
from amat.portfolio import decide, precompute_decision_inputs
from amat.portfolio.cross_section import select_cross_section, select_on_schedule


def test_retains_rank_members_but_keeps_daily_exit_and_refresh():
    scores = np.array([[0.8, 0.7], [0.6, 0.9], [-0.1, 0.9], [0.5, 0.9], [0.4, 0.9], [0.4, 0.9]])
    config = CrossSectionConfig(enabled=True, max_positions=1, selection_interval_sessions=5)
    selected = [
        select_on_schedule(
            scores, np.ones_like(scores), config, decision_index=i, first_decision_index=0
        )
        for i in range(len(scores))
    ]
    np.testing.assert_array_equal(selected[1], [0.6, 0])
    np.testing.assert_array_equal(selected[2], [0, 0])
    np.testing.assert_array_equal(selected[3], [0.5, 0])
    np.testing.assert_array_equal(selected[5], [0, 0.9])


def test_lost_horizon_agreement_can_exit_before_refresh():
    scores = np.array([[0.8, 0.7], [0.6, 0.9]])
    horizons = np.ones((3, 2, 2))
    horizons[:2, 1, 0] = -1
    config = CrossSectionConfig(
        enabled=True, max_positions=1, min_horizon_agreement=2, selection_interval_sessions=21
    )
    result = select_on_schedule(
        scores,
        np.ones_like(scores),
        config,
        decision_index=1,
        first_decision_index=0,
        horizon_scores=horizons,
    )
    np.testing.assert_array_equal(result, [0, 0])


def test_default_serialization_and_daily_selection_remain_compatible():
    config = CrossSectionConfig(enabled=True, max_positions=1)
    assert "selection_interval_sessions" not in config.model_dump(mode="json")
    scores = np.array([[0.8, 0.7], [0.6, 0.9]])
    result = select_on_schedule(
        scores, np.ones_like(scores), config, decision_index=1, first_decision_index=0
    )
    np.testing.assert_array_equal(result, select_cross_section(scores[1], config).scores)


@pytest.mark.parametrize("interval", [0, 64, -1])
def test_invalid_interval_is_rejected(interval):
    with pytest.raises(ValidationError):
        CrossSectionConfig(enabled=True, max_positions=1, selection_interval_sessions=interval)


def test_unused_schedule_cannot_silently_pass_configuration():
    with pytest.raises(ValidationError):
        CrossSectionConfig(selection_interval_sessions=21)


def test_monthly_selection_full_panel_matches_visible_prefix(project_root):
    base = load_v6_config(project_root / "configs/strategy/amat_v6.toml").config.strategy
    strategy = base.model_copy(
        update={
            "allocation": AllocationConfig(volatility_power=0),
            "cross_sectional": CrossSectionConfig(
                enabled=True,
                max_positions=4,
                min_horizon_agreement=2,
                selection_interval_sessions=21,
            ),
        }
    )
    rng = np.random.default_rng(117)
    prices = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.014, (400, 11)), axis=0))
    cache = precompute_decision_inputs(strategy, prices)
    for index in (251, 252, 272, 273, 300, 320):
        full = decide(
            strategy, prices, strategy.to_universe(), decision_index=index, precomputed=cache
        )
        prefix = decide(strategy, prices[: index + 1], strategy.to_universe(), decision_index=index)
        np.testing.assert_array_equal(full.weights, prefix.weights)


@pytest.mark.parametrize("bad_horizons", [np.ones(2), np.ones((2, 2, 2))])
def test_malformed_horizon_rows_raise_domain_error(bad_horizons):
    from amat.errors import DataIntegrityError

    with pytest.raises(DataIntegrityError, match="must be"):
        select_cross_section(
            np.ones(2),
            CrossSectionConfig(enabled=True, min_horizon_agreement=1),
            horizon_z=bad_horizons,
        )


@pytest.mark.parametrize("sigma", [np.ones(2), np.ones((3, 2))])
def test_schedule_rejects_misaligned_feature_panels(sigma):
    from amat.errors import DataIntegrityError

    with pytest.raises(DataIntegrityError, match="aligned"):
        select_on_schedule(
            np.ones((2, 2)),
            sigma,
            CrossSectionConfig(enabled=True, max_positions=1),
            decision_index=1,
            first_decision_index=0,
        )
