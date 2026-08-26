"""Per-asset sleeve derivation and the parameter-search protocol."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import RunResult
from amat.backtest.sleeves import (
    DEFAULT_COMBINED_EQUITY,
    DEFAULT_SLEEVE_EQUITY,
    ENGINE_LABELS,
    STUDY_ENGINES,
    run_sleeve_study,
    sleeve_strategy,
)
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from amat.validation.optimize import (
    _MIN_TRAINING_TRADES,
    WindowStatistics,
    expand_grid,
    fold_score,
    split_panel,
    window_statistics,
)

CONFIG = "configs/strategy/amat_v1.toml"


@pytest.fixture(scope="module")
def strategy():
    return load_strategy_config(CONFIG).strategy


class TestSleeveDerivation:
    def test_universe_collapses_to_one_symbol(self, strategy) -> None:
        sleeve = sleeve_strategy(strategy, "GLD")
        assert sleeve.universe.symbols == ("GLD",)
        assert len(sleeve.universe.clusters) == 1

    def test_caps_open_so_the_sleeve_is_not_mostly_cash(self, strategy) -> None:
        """A 20% portfolio symbol cap would report an 80%-cash single-asset account."""
        sleeve = sleeve_strategy(strategy, "GLD")
        assert sleeve.constraints.max_symbol_weight == 1.0
        assert sleeve.universe.clusters[0].gross_cap == 1.0

    def test_the_symbol_cap_is_lifted_for_a_single_name_account(self, strategy) -> None:
        """A cap limiting one name's share of a *portfolio* is meaningless in an
        account holding only that name; leaving it in place would report a
        mostly-cash sleeve rather than the instrument's behaviour."""
        sleeve = sleeve_strategy(strategy, "GLD")
        assert sleeve.constraints.max_symbol_weight == 1.0
        assert sleeve.universe.symbols == ("GLD",)

    def test_signal_and_cost_settings_are_inherited_unchanged(self, strategy) -> None:
        sleeve = sleeve_strategy(strategy, "GLD")
        assert sleeve.trend == strategy.trend
        assert sleeve.volatility == strategy.volatility
        assert sleeve.costs == strategy.costs
        assert sleeve.execution == strategy.execution

    def test_the_derived_config_names_its_symbol(self, strategy) -> None:
        assert sleeve_strategy(strategy, "GLD").name.endswith("-sleeve-GLD")


@pytest.fixture(scope="module")
def study(strategy):
    panel = generate_panel(strategy.to_universe(), sessions=460, seed=7, trend_strength=0.3)
    return run_sleeve_study(
        strategy, panel, sleeve_equity=Decimal("100000"), symbols=["SPY", "TLT"]
    )


class TestSleeveStudy:
    def test_combined_funding_is_flat_and_independent_of_the_sleeve_list(self, study) -> None:
        """Every strategy view opens with the same 100,000.

        The combined portfolio's funding must not depend on how many sleeves
        were requested: the run trades the whole universe either way, and
        ``--no-sleeves`` (an empty list) must not change what the portfolio is.
        """
        assert study.combined.initial_equity == Decimal("100000")

    def test_every_requested_sleeve_is_reported(self, study) -> None:
        assert [outcome.symbol for outcome in study.sleeves] == ["SPY", "TLT"]

    def test_each_sleeve_is_funded_independently(self, study) -> None:
        for outcome in study.ran_sleeves:
            assert outcome.result is not None
            assert outcome.result.initial_equity == Decimal("100000")

    def test_a_sleeve_holds_only_its_own_symbol(self, study) -> None:
        for outcome in study.ran_sleeves:
            assert outcome.result is not None
            assert outcome.result.universe_symbols == (outcome.symbol,)

    def test_an_absent_symbol_is_rejected_rather_than_skipped(self, strategy) -> None:
        panel = generate_panel(strategy.to_universe(), sessions=400, seed=1)
        with pytest.raises(DataIntegrityError, match="lacks requested sleeve symbols"):
            run_sleeve_study(strategy, panel, symbols=["SPY", "NOTREAL"])

    def test_default_funding_is_one_hundred_thousand(self) -> None:
        assert Decimal("100000") == DEFAULT_SLEEVE_EQUITY
        assert Decimal("100000") == DEFAULT_COMBINED_EQUITY


@pytest.fixture(scope="module")
def v1_study(strategy):
    panel = generate_panel(strategy.to_universe(), sessions=460, seed=7, trend_strength=0.3)
    return run_sleeve_study(
        strategy,
        panel,
        sleeve_equity=Decimal("100000"),
        symbols=["SPY", "TLT"],
        engine="v1-frozen",
    )


class TestEngineSelection:
    """A study must record which runner produced it, and report only what that
    runner actually measured."""

    def test_the_study_records_its_engine(self, v1_study, study) -> None:
        assert v1_study.engine == "v1-frozen"
        assert study.engine == "v1-frozen"

    def test_the_registry_holds_only_the_surviving_runner(self) -> None:
        """v2 was rejected on 2026-08-10 and its implementation removed."""
        assert STUDY_ENGINES == {"v1": ("v1-frozen",)}

    def test_every_selectable_engine_has_a_description(self) -> None:
        """An unlabelled engine in a report is a number with no provenance."""
        for engines in STUDY_ENGINES.values():
            for engine in engines:
                assert ENGINE_LABELS[engine]

    def test_both_runners_satisfy_the_shared_result_protocol(self, v1_study, study) -> None:
        assert isinstance(v1_study.combined, RunResult)
        assert isinstance(study.combined, RunResult)

    def test_v1_sleeves_are_funded_like_v2_sleeves(self, v1_study) -> None:
        """Only the engine may differ between the two studies; funding may not."""
        assert v1_study.ran_sleeves
        for outcome in v1_study.ran_sleeves:
            assert outcome.result is not None
            assert outcome.result.initial_equity == Decimal("100000")

    def test_trade_statistics_are_reconstructed_from_fills(self, v1_study) -> None:
        """These columns used to read the v2 trade journal and print an em dash
        for v1, on the reasoning that v1 "keeps no trade journal". Half right:
        v1 keeps no journal but does emit fills, and a fill stream reconstructs
        into round trips -- which is what the concentration gates have always
        done. The statistic was available; nothing was reading it.
        """
        from amat.reporting.sleeves import build_sleeve_reports

        ran = [report for report in build_sleeve_reports(v1_study) if report.ran]
        assert ran
        assert any(report.trades > 0 for report in ran)
        for report in ran:
            if report.trades:
                assert report.win_rate is not None
                assert 0.0 <= report.win_rate <= 1.0
            else:
                assert report.win_rate is None, "a rate over zero trades is undefined, not zero"


def _panel(sessions: int) -> MarketPanel:
    dates = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=index) for index in range(sessions))
    values = np.ones((sessions, 1))
    return MarketPanel(
        sessions=dates,
        symbols=("SPY",),
        adjusted_closes=values,
        raw_closes=values,
        window_vwaps=values,
        window_volumes=values,
        source="test",
    )


class TestSplit:
    def test_training_panel_stops_at_the_split(self) -> None:
        panel = _panel(100)
        training, _ = split_panel(panel, dt.date(2020, 2, 1))
        assert training.sessions[-1] <= dt.date(2020, 2, 1)
        assert len(training) < len(panel)

    def test_validation_panel_retains_full_history_for_warm_up(self) -> None:
        """A validation run starting cold at the split would spend a year warming up."""
        panel = _panel(100)
        _, full = split_panel(panel, dt.date(2020, 2, 1))
        assert full.sessions[0] == panel.sessions[0]
        assert len(full) == len(panel)

    def test_a_split_outside_the_sample_fails_closed(self) -> None:
        panel = _panel(100)
        with pytest.raises(DataIntegrityError, match="does not split the panel"):
            split_panel(panel, dt.date(2030, 1, 1))


class TestGrid:
    def test_expansion_is_a_full_deterministic_product(self) -> None:
        grid = {"a": [1, 2], "b": [10, 20, 30]}
        combinations = list(expand_grid(grid))
        assert len(combinations) == 6
        assert combinations == list(expand_grid(grid))
        assert combinations[0] == {"a": 1, "b": 10}


def _statistics(fold_sharpes: tuple[float, ...], trades: int = 100) -> WindowStatistics:
    return WindowStatistics(
        sessions=500,
        first_session=dt.date(2020, 1, 1),
        last_session=dt.date(2022, 1, 1),
        total_return=0.2,
        annualized_return=0.1,
        annualized_volatility=0.08,
        sharpe=1.25,
        max_drawdown=-0.1,
        trades=trades,
        win_rate=0.55,
        expectancy_r=0.1,
        fold_sharpes=fold_sharpes,
    )


class TestFoldScore:
    def test_a_steady_candidate_beats_a_one_lucky_year_candidate(self) -> None:
        """Both average 1.0; only one of them earned it consistently."""
        steady = _statistics((1.0, 1.0, 1.0, 1.0))
        lucky = _statistics((4.0, 0.0, 0.0, 0.0))
        assert fold_score(steady) > fold_score(lucky)

    def test_an_undertraded_candidate_is_excluded_not_merely_penalized(self) -> None:
        thin = _statistics((3.0, 3.0, 3.0, 3.0), trades=_MIN_TRAINING_TRADES - 1)
        assert fold_score(thin) == float("-inf")

    def test_a_candidate_without_folds_is_excluded(self) -> None:
        assert fold_score(_statistics(())) == float("-inf")

    def test_the_worst_fold_carries_half_the_weight(self) -> None:
        statistics = _statistics((2.0, 2.0, 2.0, 0.0))
        # median of (0, 2, 2, 2) is 2.0; minimum is 0.0
        assert fold_score(statistics) == pytest.approx(1.0)


class TestWindowStatistics:
    def test_drawdown_is_non_positive_and_finds_the_trough(self) -> None:
        equity = [Decimal(value) for value in ("100", "110", "99", "120")]
        sessions = [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(4)]
        statistics = window_statistics(equity, sessions, trades=10, win_rate=0.5, expectancy_r=0.0)
        assert statistics.max_drawdown == pytest.approx(-11 / 110)
        assert statistics.total_return == pytest.approx(0.20)

    def test_a_ruined_equity_curve_is_void_not_merely_poor(self) -> None:
        equity = [Decimal("100"), Decimal("0")]
        sessions = [dt.date(2020, 1, 1), dt.date(2020, 1, 2)]
        with pytest.raises(DataIntegrityError, match="zero or below"):
            window_statistics(equity, sessions, trades=1, win_rate=0.0, expectancy_r=0.0)

    def test_calmar_is_zero_when_there_was_no_drawdown(self) -> None:
        assert _statistics((1.0,)).calmar == pytest.approx(0.1 / 0.1)
