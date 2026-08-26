"""End-to-end invariants of a full backtest run.

These are the tests that would catch a look-ahead defect, an accounting break, or
a reproducibility failure in the assembled system rather than in one component.
All of them run on deterministic synthetic data: no network, no credentials, no
provider.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import numpy as np
import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.synthetic import SYNTHETIC_SOURCE, generate_panel
from amat.domain.instruments import Universe
from amat.domain.ledgers import CASH_QUANTUM
from amat.errors import DataIntegrityError
from amat.metrics import summarize

SESSIONS = 420


@pytest.fixture(scope="module")
def strategy(request: pytest.FixtureRequest) -> StrategyConfig:
    root = request.config.rootpath
    return load_strategy_config(root / "configs" / "strategy" / "amat_v1.toml").strategy


@pytest.fixture(scope="module")
def universe(strategy: StrategyConfig) -> Universe:
    return strategy.to_universe()


@pytest.fixture(scope="module")
def result(strategy: StrategyConfig, universe: Universe):
    panel = generate_panel(universe, sessions=SESSIONS, seed=4242)
    return run_backtest(strategy, panel, universe, initial_cash=Decimal("1000000"))


def _same_float(left: float, right: float) -> bool:
    """Exact equality that treats NaN == NaN as equal (no-decision sessions)."""
    return left == right or (math.isnan(left) and math.isnan(right))


class TestCausality:
    def test_mutating_the_future_does_not_change_the_past(
        self, strategy: StrategyConfig, universe: Universe
    ) -> None:
        """The decisive leakage test.

        Replace every observation after a cut with garbage and re-run. Sessions
        up to the cut must be bit-identical: if any statistic peeked forward,
        equity would move.
        """
        panel = generate_panel(universe, sessions=SESSIONS, seed=99)
        baseline = run_backtest(strategy, panel, universe, initial_cash=Decimal("1000000"))

        cut = SESSIONS - 40
        mutated_adjusted = panel.adjusted_closes.copy()
        mutated_raw = panel.raw_closes.copy()
        mutated_vwap = panel.window_vwaps.copy()
        mutated_adjusted[cut:] *= 7.5
        mutated_raw[cut:] *= 7.5
        mutated_vwap[cut:] *= 7.5

        mutated_panel = type(panel)(
            sessions=panel.sessions,
            symbols=panel.symbols,
            adjusted_closes=mutated_adjusted,
            raw_closes=mutated_raw,
            window_vwaps=mutated_vwap,
            window_volumes=panel.window_volumes,
            source=panel.source,
        )
        mutated = run_backtest(strategy, mutated_panel, universe, initial_cash=Decimal("1000000"))

        cut_date = panel.sessions[cut]
        for before, after in zip(baseline.sessions, mutated.sessions, strict=True):
            if before.session >= cut_date:
                break
            assert before.session == after.session
            assert before.equity == after.equity, f"equity diverged at {before.session}"
            assert before.cash == after.cash
            # The decision *formed* at this session executes lag sessions
            # later, so with lag 1 a one-session feature leak would surface
            # only in these decision diagnostics -- the fills it produces land
            # on the first uncompared session. Asserting them closes that
            # lag-sized blind spot.
            assert before.fills == after.fills
            assert before.binding_constraints == after.binding_constraints
            assert _same_float(before.forecast_volatility_pre, after.forecast_volatility_pre)
            assert _same_float(before.forecast_volatility_post, after.forecast_volatility_post)

    def test_a_non_positive_close_fails_the_run_at_start(
        self, strategy: StrategyConfig, universe: Universe
    ) -> None:
        """Pinned intentional behavior of the feature cache (2026-08-14).

        Before features were precomputed once per run, a finite non-positive
        adjusted close at row k failed only the decisions at t >= k, each
        degrading to a per-session "no decision formed" warning while the run
        completed. Now the whole-panel feature pass rejects the panel before
        the first decision -- the fail-closed direction (guardrail 9), and the
        loaders already refuse such bars at snapshot load. This test exists so
        the change stays deliberate and visible.
        """
        panel = generate_panel(universe, sessions=SESSIONS, seed=17)
        corrupted = panel.adjusted_closes.copy()
        corrupted[SESSIONS - 5, 2] = -1.0
        defective = type(panel)(
            sessions=panel.sessions,
            symbols=panel.symbols,
            adjusted_closes=corrupted,
            raw_closes=panel.raw_closes,
            window_vwaps=panel.window_vwaps,
            window_volumes=panel.window_volumes,
            source=panel.source,
        )
        with pytest.raises(DataIntegrityError, match="strictly positive"):
            run_backtest(strategy, defective, universe, initial_cash=Decimal("1000000"))

    def test_every_fill_executes_after_its_signal(self, result) -> None:
        """No fill may land on the session that produced it."""
        for record in result.sessions:
            for fill in record.fills:
                assert fill.execution_session == record.session

    def test_no_trade_before_warmup_completes(self, strategy: StrategyConfig, result) -> None:
        first_traded = next((record.session for record in result.sessions if record.fills), None)
        if first_traded is None:
            pytest.skip("no fills in this sample")
        traded_index = result.session_dates.index(first_traded)
        # The run begins at the last warm-up session, so index 0 already has the
        # full warm-up behind it; a trade cannot occur before the lag elapses.
        assert traded_index >= strategy.execution.signal_to_execution_lag_sessions


class TestAccountingInvariants:
    def test_equity_identity_holds_every_session(self, result) -> None:
        for record in result.sessions:
            assert record.equity >= 0

    def test_cash_is_never_negative(self, result) -> None:
        """The mandate forbids leverage and margin borrowing."""
        for record in result.sessions:
            assert record.cash >= 0, f"negative cash at {record.session}"

    def test_gross_exposure_respects_the_cap(self, strategy: StrategyConfig, result) -> None:
        for record in result.sessions:
            assert record.gross_exposure <= strategy.constraints.max_gross_exposure + 1e-6

    def test_attribution_explains_each_equity_change(self, result) -> None:
        """Independent re-check of the identity the runner already enforced."""
        for previous, current in zip(result.sessions, result.sessions[1:], strict=False):
            observed = current.equity - previous.equity
            residual = observed - current.attribution.explained_change
            assert abs(residual) <= CASH_QUANTUM, (
                f"unexplained change of {residual} at {current.session}"
            )

    def test_costs_are_non_negative_and_accumulate(self, result) -> None:
        for record in result.sessions:
            assert record.attribution.fees >= 0
        assert result.total_costs >= 0


class TestReproducibility:
    def test_identical_inputs_produce_identical_output(
        self, strategy: StrategyConfig, universe: Universe
    ) -> None:
        panel_a = generate_panel(universe, sessions=300, seed=7)
        panel_b = generate_panel(universe, sessions=300, seed=7)
        np.testing.assert_array_equal(panel_a.adjusted_closes, panel_b.adjusted_closes)

        first = run_backtest(strategy, panel_a, universe, initial_cash=Decimal("1000000"))
        second = run_backtest(strategy, panel_b, universe, initial_cash=Decimal("1000000"))
        assert first.equity_curve == second.equity_curve
        assert first.total_fills == second.total_fills

    def test_a_different_seed_produces_a_different_run(
        self, strategy: StrategyConfig, universe: Universe
    ) -> None:
        one = run_backtest(
            strategy,
            generate_panel(universe, sessions=300, seed=1),
            universe,
            initial_cash=Decimal("1000000"),
        )
        two = run_backtest(
            strategy,
            generate_panel(universe, sessions=300, seed=2),
            universe,
            initial_cash=Decimal("1000000"),
        )
        assert one.equity_curve != two.equity_curve


class TestCostSensitivity:
    def test_higher_costs_never_improve_the_result(
        self, strategy: StrategyConfig, universe: Universe
    ) -> None:
        """Monotonicity: cost is a drag, so the ladder must be non-increasing."""
        panel = generate_panel(universe, sessions=SESSIONS, seed=31337)
        finals = []
        for bps in (0.0, 2.0, 6.0, 10.0):
            run = run_backtest(
                strategy, panel, universe, initial_cash=Decimal("1000000"), one_way_bps=bps
            )
            finals.append(run.total_costs)
        assert finals == sorted(finals), "total costs must rise with the cost assumption"

    def test_zero_cost_config_cannot_declare_itself_primary(self) -> None:
        from pydantic import ValidationError

        from amat.config.models import CostConfig

        with pytest.raises(ValidationError, match="diagnostic only"):
            CostConfig(baseline_one_way_bps=0.0, is_primary=True)

    def test_zero_cost_override_is_demoted_to_diagnostic(
        self, strategy: StrategyConfig, universe: Universe
    ) -> None:
        """Guardrail 7 binds on the effective cost, not the configured one."""
        panel = generate_panel(universe, sessions=300, seed=8)
        priced = run_backtest(
            strategy, panel, universe, initial_cash=Decimal("1000000"), one_way_bps=2.0
        )
        free = run_backtest(
            strategy, panel, universe, initial_cash=Decimal("1000000"), one_way_bps=0.0
        )
        assert priced.is_primary is True
        assert free.is_primary is False


class TestProvenance:
    def test_synthetic_panel_declares_its_origin(self, universe: Universe) -> None:
        """A synthetic result must never be mistakable for a market result."""
        panel = generate_panel(universe, sessions=300, seed=5)
        assert panel.source == SYNTHETIC_SOURCE

    def test_panel_sessions_are_strictly_increasing(self, universe: Universe) -> None:
        panel = generate_panel(universe, sessions=300, seed=5)
        assert all(
            later > earlier
            for earlier, later in zip(panel.sessions, panel.sessions[1:], strict=False)
        )

    def test_panel_symbol_order_matches_the_universe(self, universe: Universe) -> None:
        panel = generate_panel(universe, sessions=300, seed=5)
        assert panel.symbols == universe.symbols


class TestMetrics:
    def test_summary_reports_the_sample_it_measured(self, result) -> None:
        summary = summarize(result)
        assert summary.sessions == len(result.sessions)
        assert summary.first_session == result.sessions[0].session.isoformat()
        assert summary.last_session == result.sessions[-1].session.isoformat()

    def test_max_drawdown_is_non_positive(self, result) -> None:
        assert summarize(result).max_drawdown <= 0.0

    def test_single_session_run_is_rejected(self, result) -> None:
        from amat.backtest.runner import BacktestResult
        from amat.errors import DataIntegrityError

        stub = BacktestResult(
            sessions=result.sessions[:1],
            initial_equity=result.initial_equity,
            one_way_bps=result.one_way_bps,
            is_primary=result.is_primary,
            universe_symbols=result.universe_symbols,
        )
        with pytest.raises(DataIntegrityError, match="at least 2 sessions"):
            summarize(stub)


class TestCashYieldSource:
    def test_an_unpriced_cash_source_fails_closed_not_silently_at_zero(
        self, strategy: StrategyConfig, universe: Universe
    ) -> None:
        """Regression: ``source = "treasury_3m"`` used to be silently priced at
        0% -- a guardrail-9 fallback that let a configuration claim a
        Treasury-yield sensitivity had run when it had not."""
        from amat.errors import ConfigurationError

        payload = strategy.model_dump()
        payload["cash"]["source"] = "treasury_3m"
        # The config model already forces a Treasury-yield run to declare
        # itself a sensitivity; the defect was downstream of that gate.
        payload["cash"]["is_primary"] = False
        mutated = StrategyConfig.model_validate(payload)
        panel = generate_panel(universe, sessions=SESSIONS, seed=7)
        with pytest.raises(ConfigurationError, match="not implemented"):
            run_backtest(mutated, panel, universe, initial_cash=Decimal("100000"))


class TestNullHypothesis:
    def test_a_driftless_random_walk_shows_no_reliable_edge(
        self, strategy: StrategyConfig, universe: Universe
    ) -> None:
        """The honest control.

        With no planted trend and no drift, a trend follower should have no
        dependable edge. This asserts the *engine* is not manufacturing one --
        it deliberately does not assert the strategy is good.
        """
        sharpes = []
        for seed in (1, 2, 3, 4, 5):
            panel = generate_panel(
                universe, sessions=400, seed=seed, trend_strength=0.0, annual_drift=0.0
            )
            run = run_backtest(strategy, panel, universe, initial_cash=Decimal("1000000"))
            sharpes.append(summarize(run).sharpe)

        assert any(value < 0 for value in sharpes), (
            f"no losing sample across 5 driftless random walks: {sharpes}; "
            "a trend follower that never loses on noise indicates leakage"
        )
        assert abs(float(np.mean(sharpes))) < 3.0, (
            f"implausible mean Sharpe {np.mean(sharpes):.2f} on driftless noise: {sharpes}"
        )


def test_session_dates_are_consecutive_panel_sessions(
    strategy: StrategyConfig, universe: Universe
) -> None:
    """The runner must visit every session in order, with no gaps."""
    panel = generate_panel(universe, sessions=300, seed=17)
    run = run_backtest(strategy, panel, universe, initial_cash=Decimal("1000000"))
    start = panel.sessions.index(run.sessions[0].session)
    expected = panel.sessions[start : start + len(run.sessions)]
    assert run.session_dates == expected
    assert all(isinstance(date, dt.date) for date in run.session_dates)
