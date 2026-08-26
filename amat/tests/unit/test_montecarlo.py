"""The bootstrap panel builder and the Monte Carlo study.

The resampling invariants worth pinning:

* a resampled session is one whole joint row of the source's per-symbol
  relatives -- cross-sectional structure survives exactly;
* the calendar, symbols, warm-up, and funding are the source run's own, so
  the observed curve and the bands are commensurable;
* the same seed reproduces the same study bit-for-bit at any worker count.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from amat.validation.montecarlo import bootstrap_panel, run_monte_carlo

SESSIONS = 330


@pytest.fixture(scope="module")
def strategy(canonical_config_path):
    return load_strategy_config(canonical_config_path).strategy


@pytest.fixture(scope="module")
def panel(strategy):
    return generate_panel(strategy.to_universe(), sessions=SESSIONS, seed=3, trend_strength=0.5)


class TestBootstrapPanel:
    def test_calendar_symbols_and_funding_frame_are_preserved(self, panel) -> None:
        resampled = bootstrap_panel(panel, seed=11)
        assert resampled.sessions == panel.sessions
        assert resampled.symbols == panel.symbols
        assert resampled.source.startswith("bootstrap(")
        assert np.array_equal(resampled.adjusted_closes[0], panel.adjusted_closes[0])

    def test_the_resampled_world_is_total_return_with_no_separate_dividends(self, panel) -> None:
        resampled = bootstrap_panel(panel, seed=11)
        assert np.array_equal(resampled.raw_closes, resampled.adjusted_closes)

    def test_every_resampled_session_is_one_joint_source_row(self, panel) -> None:
        """Cross-sectional integrity: rows are resampled whole, never per-symbol."""
        resampled = bootstrap_panel(panel, seed=11)
        source_rows = panel.adjusted_closes[1:] / panel.adjusted_closes[:-1]
        resampled_rows = resampled.adjusted_closes[1:] / resampled.adjusted_closes[:-1]
        for row in resampled_rows:
            distances = np.abs(source_rows - row).max(axis=1)
            assert distances.min() < 1e-9, "a resampled row matches no source session"

    def test_same_seed_reproduces_the_panel_and_a_new_seed_moves_it(self, panel) -> None:
        first = bootstrap_panel(panel, seed=11)
        second = bootstrap_panel(panel, seed=11)
        different = bootstrap_panel(panel, seed=12)
        assert np.array_equal(first.adjusted_closes, second.adjusted_closes)
        assert np.array_equal(first.window_vwaps, second.window_vwaps)
        assert not np.array_equal(first.adjusted_closes, different.adjusted_closes)

    def test_unusable_inputs_fail_closed(self, panel) -> None:
        with pytest.raises(DataIntegrityError, match="mean_block_sessions"):
            bootstrap_panel(panel, seed=1, mean_block_sessions=0)


@pytest.fixture(scope="module")
def study(strategy, panel):
    return run_monte_carlo(
        strategy,
        panel,
        strategy.to_universe(),
        paths=2,
        seed=99,
        initial_cash=Decimal("100000"),
        workers=1,
    )


class TestRunMonteCarlo:
    def test_the_summary_is_complete_and_bounded(self, study) -> None:
        assert study.paths == 2
        assert len(study.outcomes) == 2
        for metric, row in study.metric_percentiles.items():
            assert set(row) == {"p05", "p25", "p50", "p75", "p95"}
            assert 0.0 <= study.observed_percentile[metric] <= 1.0
        for probability in study.probabilities.values():
            assert 0.0 <= probability <= 1.0
        assert len(study.observed_equity) == len(study.band_dates)
        assert all(len(band) == len(study.band_dates) for band in study.equity_bands.values())

    def test_worker_count_cannot_change_the_result(self, strategy, panel, study) -> None:
        pooled = run_monte_carlo(
            strategy,
            panel,
            strategy.to_universe(),
            paths=2,
            seed=99,
            initial_cash=Decimal("100000"),
            workers=2,
        )
        assert pooled.outcomes == study.outcomes
        assert pooled.metric_percentiles == study.metric_percentiles
        assert pooled.equity_bands == study.equity_bands

    def test_zero_paths_fail_closed(self, strategy, panel) -> None:
        with pytest.raises(DataIntegrityError, match="paths"):
            run_monte_carlo(strategy, panel, strategy.to_universe(), paths=0)

    def test_a_universe_that_differs_from_the_strategy_fails_closed(self, strategy, panel) -> None:
        # Workers rebuild strategy.to_universe(); a different caller-supplied
        # universe would put an observed run of one symbol set inside a
        # bootstrap distribution of another.
        from amat.validation.falsification import _without_symbol

        smaller = _without_symbol(strategy, "USO").to_universe()
        with pytest.raises(DataIntegrityError, match="universe"):
            run_monte_carlo(strategy, panel, smaller, paths=1, workers=1)
