"""Optional switching rules preserve exits, caps, causality and historical identity."""

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.portfolio.adaptive import rotation_inputs


def inputs(**rules):
    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    value = base.model_dump(mode="json")
    value["adaptive_rotation"].update(rules)
    value["cross_sectional"]["max_positions"] = 1
    cfg = StrategyConfig.model_validate(value)
    dates = [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(310)]
    prices = np.full((310, 11), 100.0)
    scores = np.full_like(prices, -1.0)
    scores[:, 0] = 2
    scores[270:, 1] = 3
    return cfg, dates, prices, scores


def evaluate(cfg, dates, prices, scores):
    return rotation_inputs(
        cfg, prices, scores, np.ones_like(scores), np.ones((3, *scores.shape)), sessions=dates
    )[0]


@pytest.mark.parametrize("days", [2, 3])
def test_confirmation_retains_eligible_incumbent_then_switches(days):
    cfg, dates, prices, scores = inputs(switch_confirmation_sessions=days)
    out = evaluate(cfg, dates, prices, scores)
    assert out[269, 0] > 0
    assert out[270 + days - 2, 0] > 0
    assert out[270 + days - 1, 1] > 0
    assert np.all((out > 0).sum(axis=1) <= 1)
    scores[270, 0] = -1
    assert evaluate(cfg, dates, prices, scores)[270, 1] > 0


def test_cost_hurdle_and_prefix_invariance():
    cfg, dates, prices, scores = inputs(switch_cost_bps=10)
    # Equal trailing returns cannot justify paying switching costs.
    out = evaluate(cfg, dates, prices, scores)
    assert out[-1, 0] > 0 and out[-1, 1] == 0
    np.testing.assert_equal(out[:290], evaluate(cfg, dates[:290], prices[:290], scores[:290]))
    changed = prices.copy()
    changed[290:, 1] *= 2
    np.testing.assert_equal(out[:290], evaluate(cfg, dates, changed, scores)[:290])
    assert evaluate(cfg, dates, changed, scores)[290, 1] > 0


def test_inactive_fields_preserve_serialized_config_identity():
    cfg, *_ = inputs()
    rotation = cfg.adaptive_rotation.model_dump(mode="json")
    assert "switch_confirmation_sessions" not in rotation
    assert "switch_cost_bps" not in rotation
    assert "switch_cost_multiple" not in rotation


def test_higher_known_cost_demands_larger_opportunity():
    cfg, dates, prices, scores = inputs(switch_cost_bps=10)
    prices[270:, 1] *= np.exp(0.012)  # Five-day proxy advantage = 30bps.
    assert evaluate(cfg, dates, prices, scores)[270, 1] > 0
    costly = cfg.model_copy(
        update={
            "adaptive_rotation": cfg.adaptive_rotation.model_copy(update={"switch_cost_bps": 20})
        }
    )
    assert evaluate(costly, dates, prices, scores)[270, 0] > 0


def test_confirmation_must_be_consecutive():
    cfg, dates, prices, scores = inputs(switch_confirmation_sessions=3)
    scores[271, 1] = -1
    out = evaluate(cfg, dates, prices, scores)
    assert out[273, 0] > 0
    assert out[274, 1] > 0
