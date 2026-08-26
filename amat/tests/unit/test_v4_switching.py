"""Strategy v4: the breadth selector, the config contract, and the blend.

The two equalities at the bottom are the load-bearing ones: lambda = 1 must be
*bit-identical* to v1, and lambda = 0 must be bit-identical to running the
defensive stance alone. If either drifts, the blend is quietly a third strategy
rather than a combination of the two declared ones.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.runner import StanceBlend, run_backtest
from amat.backtest.stances import assemble_stance, blend_series
from amat.config.switching import load_switching_config, parse_switching_config
from amat.data.synthetic import generate_panel
from amat.domain.types import Symbol
from amat.errors import ConfigurationError, DataIntegrityError
from amat.regime.switching import breadth_blend_weights
from amat.validation.falsification import falsify_blend

SESSIONS = 330
V4_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "strategy" / "amat_v4.toml"


@pytest.fixture(scope="module")
def config():
    return load_switching_config(V4_CONFIG)


@pytest.fixture(scope="module")
def panel(config):
    return generate_panel(
        config.strategy.trend.to_universe(), sessions=SESSIONS, seed=7, trend_strength=0.5
    )


class TestBreadthBlendWeights:
    def test_uniform_rallies_map_to_the_trend_stance(self) -> None:
        rising = np.exp(np.linspace(0.0, 1.0, 200))[:, None] * np.ones((1, 6))
        weights = breadth_blend_weights(
            rising,
            horizon_sessions=21,
            smoothing_half_life_sessions=5,
            floor_breadth=0.35,
            full_breadth=0.65,
        )
        assert np.all(weights[:21] == 1.0)  # no reading yet -> pure trend
        assert np.all(weights[40:] == 1.0)

    def test_uniform_declines_map_to_the_defensive_stance(self) -> None:
        falling = np.exp(np.linspace(1.0, 0.0, 200))[:, None] * np.ones((1, 6))
        weights = breadth_blend_weights(
            falling,
            horizon_sessions=21,
            smoothing_half_life_sessions=5,
            floor_breadth=0.35,
            full_breadth=0.65,
        )
        assert np.all(weights[60:] == 0.0)

    def test_mutating_future_rows_leaves_earlier_weights_identical(self, panel) -> None:
        """The leak test every decision input must pass."""
        cut = 250
        baseline = breadth_blend_weights(
            panel.adjusted_closes,
            horizon_sessions=63,
            smoothing_half_life_sessions=10,
            floor_breadth=0.35,
            full_breadth=0.65,
        )
        mutated = panel.adjusted_closes.copy()
        mutated[cut + 1 :] *= 3.0
        reweighted = breadth_blend_weights(
            mutated,
            horizon_sessions=63,
            smoothing_half_life_sessions=10,
            floor_breadth=0.35,
            full_breadth=0.65,
        )
        assert np.array_equal(baseline[: cut + 1], reweighted[: cut + 1])

    def test_degenerate_parameterizations_are_rejected(self) -> None:
        closes = np.ones((100, 6)) * 100.0
        with pytest.raises(DataIntegrityError, match="floor < full"):
            breadth_blend_weights(
                closes,
                horizon_sessions=21,
                smoothing_half_life_sessions=5,
                floor_breadth=0.65,
                full_breadth=0.35,
            )
        with pytest.raises(DataIntegrityError, match="at least 4"):
            breadth_blend_weights(
                closes[:, :2],
                horizon_sessions=21,
                smoothing_half_life_sessions=5,
                floor_breadth=0.35,
                full_breadth=0.65,
            )


class TestTheSwitchingConfig:
    def test_the_repository_v4_file_validates_and_digests(self, config) -> None:
        assert config.strategy.name == "amat-etf-switching"
        assert config.strategy.trend.constraints.target_annualized_volatility == 0.08
        assert config.strategy.defensive.constraints.max_gross_exposure == 0.70
        assert len(config.content_sha256) == 64

    def test_the_trend_stance_is_the_v1_contract(self, config, canonical_config_path) -> None:
        """v4's trend stance must not drift from v1 -- same core, same numbers."""
        from amat.config.loading import load_strategy_config

        v1 = load_strategy_config(canonical_config_path).strategy
        trend = config.strategy.trend
        assert trend.universe == v1.universe
        assert trend.volatility == v1.volatility
        assert trend.trend == v1.trend
        assert trend.covariance == v1.covariance
        assert trend.constraints == v1.constraints
        assert trend.rebalance == v1.rebalance
        assert trend.execution == v1.execution
        assert trend.costs == v1.costs
        assert trend.cash == v1.cash
        assert trend.warmup == v1.warmup

    def test_a_defensive_symbol_outside_the_trend_universe_is_rejected(self) -> None:
        text = V4_CONFIG.read_text(encoding="utf-8")
        head, marker, tail = text.partition("[defensive]")
        assert marker, "fixture drift: the v4 file must contain a [defensive] table"
        mutated = (
            head + marker + tail.replace('symbols = ["TLT", "IEF"]', 'symbols = ["TLT", "SHY"]')
        )
        with pytest.raises(ConfigurationError, match="not in the trend universe"):
            parse_switching_config(mutated)

    def test_stances_with_different_execution_windows_are_rejected(self) -> None:
        text = V4_CONFIG.read_text(encoding="utf-8").replace(
            '[defensive.execution]\ntimezone = "America/New_York"\nwindow_start = 09:30:00',
            '[defensive.execution]\ntimezone = "America/New_York"\nwindow_start = 09:31:00',
        )
        with pytest.raises(ConfigurationError, match="share the execution configuration"):
            parse_switching_config(text)


class TestTheBlendIsExactlyItsEndpoints:
    def test_lambda_one_is_bit_identical_to_v1(self, config, panel) -> None:
        trend = config.strategy.trend
        universe = trend.to_universe()
        pure = run_backtest(trend, panel, universe, initial_cash=Decimal("1200000"))
        blended = run_backtest(
            trend,
            panel,
            universe,
            initial_cash=Decimal("1200000"),
            stance=assemble_stance(config.strategy, panel, weights=np.ones(len(panel))),
        )
        assert pure.equity_curve == blended.equity_curve
        assert pure.total_fills == blended.total_fills

    def test_lambda_zero_is_bit_identical_to_the_defensive_stance_alone(
        self, config, panel
    ) -> None:
        defensive = config.strategy.defensive
        defensive_universe = defensive.to_universe()
        narrowed = panel.slice_symbols([Symbol(s) for s in defensive_universe.symbols])
        alone = run_backtest(
            defensive, narrowed, defensive_universe, initial_cash=Decimal("1200000")
        )
        blended = run_backtest(
            config.strategy.trend,
            panel,
            config.strategy.trend.to_universe(),
            initial_cash=Decimal("1200000"),
            stance=assemble_stance(config.strategy, panel, weights=np.zeros(len(panel))),
        )
        assert alone.equity_curve == blended.equity_curve

    def test_every_blend_respects_the_looser_gross_cap(self, config, panel) -> None:
        """Convexity in practice: gross exposure never exceeds the trend cap."""
        result = run_backtest(
            config.strategy.trend,
            panel,
            config.strategy.trend.to_universe(),
            initial_cash=Decimal("1200000"),
            stance=assemble_stance(config.strategy, panel),
        )
        cap = config.strategy.trend.constraints.max_gross_exposure
        # Fill prices move between decision and execution, so realized gross can
        # exceed the decision-time cap by drift; a small tolerance separates
        # drift from a genuine cap violation.
        assert all(record.gross_exposure <= cap * 1.05 for record in result.sessions)


class TestStanceValidation:
    def test_misaligned_blend_weights_are_rejected(self, config, panel) -> None:
        stance = assemble_stance(config.strategy, panel, weights=np.ones(len(panel) - 1))
        with pytest.raises(DataIntegrityError, match="expected"):
            run_backtest(
                config.strategy.trend,
                panel,
                config.strategy.trend.to_universe(),
                stance=stance,
            )

    def test_out_of_range_blend_weights_are_rejected(self, config, panel) -> None:
        weights = np.ones(len(panel))
        weights[10] = 1.5
        stance = assemble_stance(config.strategy, panel, weights=weights)
        with pytest.raises(DataIntegrityError, match="convex"):
            run_backtest(
                config.strategy.trend,
                panel,
                config.strategy.trend.to_universe(),
                stance=stance,
            )

    def test_a_stance_symbol_missing_from_the_panel_is_rejected(self, config, panel) -> None:
        """Defends against a panel loaded for the wrong universe: the defensive
        stance holds UUP, but the (trimmed) trend panel does not carry it."""
        from amat.config.models import StrategyConfig

        trend = config.strategy.trend
        payload = trend.model_dump()
        payload["universe"]["clusters"] = [
            cluster for cluster in payload["universe"]["clusters"] if cluster["name"] != "us_dollar"
        ]
        trimmed = StrategyConfig.model_validate(payload)
        narrowed = panel.slice_symbols([Symbol(s) for s in trimmed.universe.symbols])

        defensive_universe = config.strategy.defensive.to_universe()
        stance = StanceBlend(
            strategy=config.strategy.defensive,
            universe=defensive_universe,
            adjusted_closes=np.ones((len(narrowed), len(defensive_universe.symbols))),
            weights=np.ones(len(narrowed)),
        )
        with pytest.raises(DataIntegrityError, match="not in the panel"):
            run_backtest(trimmed, narrowed, trimmed.to_universe(), stance=stance)


class TestFalsifyBlend:
    @pytest.mark.slow
    def test_the_harness_reports_baseline_candidate_and_four_controls(self, config, panel) -> None:
        outcome = falsify_blend(config.strategy, panel)
        assert outcome.baseline.name.startswith("trend stance only")
        assert outcome.candidate.name == "v4 breadth-blended"
        assert len(outcome.controls) == 4
        assert {control.name for control in outcome.controls} == {
            "lambda shuffled",
            "lambda random_matched_turnover",
            "lambda static_mean",
            "defensive_only",
        }
        assert "REJECTED" in outcome.verdict or "SURVIVES" in outcome.verdict

    def test_the_candidate_series_is_the_configured_rule(self, config, panel) -> None:
        lam = blend_series(config.strategy, panel)
        assert lam.shape == (len(panel),)
        assert np.all((lam >= 0.0) & (lam <= 1.0))
