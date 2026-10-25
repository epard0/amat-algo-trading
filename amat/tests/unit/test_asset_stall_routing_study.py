"""Routing arithmetic, no new eligibility and future-mutation checks."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.runner import DecisionOverlay, _precompute_run_inputs, run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "asset_stall_routing", ROOT / "scripts/run_asset_stall_routing_study.py"
)
assert SPEC and SPEC.loader
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def test_registration_requires_exact_source_sealed_pre_outcome_approval(tmp_path):
    review = tmp_path / "review.json"
    dedup = tmp_path / "dedup.json"
    verdict = {
        "implementation_approved": True,
        "candidate_outcomes_seen": False,
        "driver_sha256": driver.file_hash(Path(driver.__file__)),
    }

    def save(path, value):
        driver.write(path, value)
        path.with_suffix(".sha256").write_text(driver.digest(value))

    save(review, verdict)
    save(dedup, {"economic_duplication_found": False})
    driver.approved_inputs(review, dedup)
    for change in (
        {"implementation_approved": False},
        {"implementation_approved": 1},
        {"candidate_outcomes_seen": True},
        {"driver_sha256": "a" * 64},
    ):
        save(review, {**verdict, **change})
        with pytest.raises(ValueError, match="implementation approval"):
            driver.approved_inputs(review, dedup)
    save(review, verdict)
    dedup.write_text("{}")
    with pytest.raises(ValueError, match="Unsealed evidence"):
        driver.approved_inputs(review, dedup)


def test_shared_gate_ratio_zero_and_unavailable_handling():
    prices = np.full((80, 3), 100.0)
    prices[0] = 80
    prices[42] = 110
    prices[1:3] = 80
    prices[43:45] = 110
    prices[63] = [100, 120, 70]
    base = np.full_like(prices, 0.8)
    fast = np.full_like(prices, 0.2)
    base[64, 0] = 0
    base[65, 0] = np.nan
    routed, ratio, gate = driver.combine_scores(prices, base, fast)
    assert gate[63].tolist() == [True, False, False]
    assert routed[63].tolist() == [0.2, 0.8, 0.8]
    assert ratio[63].tolist() == [0.25, 1, 1]
    assert ratio[64, 0] == 1
    assert gate[64, 0]
    assert ratio[65, 0] == 1
    assert routed[65, 0] == 0
    assert not gate[:63].any()


def test_future_mutation_cannot_change_routing_prefix_or_base_eligibility():
    strategy = load_strategy_config(
        ROOT / "configs/strategy/amat_group_selection_volatility.toml"
    ).strategy
    panel = generate_panel(strategy.to_universe(), sessions=380, seed=42)
    routed, ratio, gate = driver.routing_inputs(strategy, panel)
    changed = panel.adjusted_closes.copy()
    changed[330:] *= np.linspace(1, 4, len(changed) - 330)[:, None]
    future = driver.routing_inputs(strategy, replace(panel, adjusted_closes=changed))
    for original, updated in zip((routed, ratio, gate), future, strict=True):
        np.testing.assert_array_equal(original[:330], updated[:330])
    base = _precompute_run_inputs(strategy, None, panel)[0]
    selected = _precompute_run_inputs(strategy, None, panel, signal_override=routed)[0]
    np.testing.assert_array_equal(np.isnan(base.long_scores), np.isnan(selected.long_scores))
    np.testing.assert_array_equal(base.horizon_scores, selected.horizon_scores)
    available = np.isfinite(base.long_scores)
    assert np.all(selected.long_scores[available] <= base.long_scores[available])
    assert np.all((ratio >= 0) & (ratio <= 1))
    # Compare actual native decisions and fills, not only the input cache.
    for arm in ("signal", "allocation"):
        results = []
        for visible, inputs in (
            (panel, (routed, ratio, gate)),
            (replace(panel, adjusted_closes=changed), future),
        ):
            kwargs = (
                {"signal_override": inputs[0]}
                if arm == "signal"
                else {
                    "decision_overlay": DecisionOverlay(inputs[1], np.ones(inputs[1].shape, bool))
                }
            )
            replay = run_backtest(
                strategy,
                visible,
                strategy.to_universe(),
                one_way_bps=6,
                max_entry_window_participation=0.01,
                whole_share_buys=True,
                **kwargs,
            )
            results.append(
                [
                    (r.session, r.equity, r.cash, r.instructions, r.fills, r.missed)
                    for r in replay.sessions
                    if r.session < panel.sessions[330]
                ]
            )
        assert results[0] == results[1]
