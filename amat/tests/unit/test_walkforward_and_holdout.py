"""Walk-forward folds and the locked-holdout declaration.

The fold invariant worth a hand check: folds chain on shared boundary
sessions, so compounding the fold returns must reproduce the full-sample
return exactly (up to float epsilon). A fold scheme that dropped or
double-counted a session's return could not satisfy it.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel, trading_sessions
from amat.errors import DataIntegrityError, ImmutableArtifactError
from amat.validation.optimize import WindowStatistics
from amat.validation.walkforward import (
    FoldOutcome,
    HoldoutDeclaration,
    WalkForwardResult,
    _fold_boundaries,
    assert_respects_holdout,
    declare_holdout,
    default_holdout_root,
    load_holdout_declaration,
    walk_forward,
)

SESSIONS = 600


@pytest.fixture(scope="module")
def strategy(canonical_config_path):
    return load_strategy_config(canonical_config_path).strategy


@pytest.fixture(scope="module")
def panel(strategy):
    return generate_panel(strategy.to_universe(), sessions=SESSIONS, seed=7, trend_strength=0.5)


@pytest.fixture(scope="module")
def result(strategy, panel):
    return walk_forward(strategy, panel, strategy.to_universe(), initial_cash=Decimal("100000"))


def _tiny_panel(last_session: dt.date) -> MarketPanel:
    """A two-session, one-symbol panel ending on ``last_session``."""
    sessions = (last_session - dt.timedelta(days=1), last_session)
    ones = np.ones((2, 1))
    return MarketPanel(
        sessions=sessions,
        symbols=("SPY",),
        adjusted_closes=ones * 100.0,
        raw_closes=ones * 100.0,
        window_vwaps=ones * 100.0,
        window_volumes=ones * 1000.0,
        source="synthetic",
    )


class TestFoldBoundaries:
    def test_folds_share_their_boundary_session(self) -> None:
        import itertools

        dates = trading_sessions(dt.date(2020, 1, 6), 600)
        boundaries = _fold_boundaries(dates)
        assert len(boundaries) >= 2
        for (_l1, _b1, end), (_l2, begin, _e2) in itertools.pairwise(boundaries):
            assert begin == end, "each fold must start at the prior fold's last session"
        assert boundaries[0][1] == 0
        assert boundaries[-1][2] == len(dates) - 1

    def test_a_trailing_stub_is_merged_and_the_label_says_so(self) -> None:
        # 510 weekday sessions from early January reach a few days into the
        # third year -- far fewer than the 40-session fold minimum.
        dates = trading_sessions(dt.date(2020, 1, 6), 527)
        labels = [label for label, _b, _e in _fold_boundaries(dates)]
        assert labels[-1] == "2021+2022"

    def test_a_leading_stub_is_merged_forward(self) -> None:
        # Ten December sessions cannot stand as a "2019" fold.
        dates = trading_sessions(dt.date(2019, 12, 16), 270)
        labels = [label for label, _b, _e in _fold_boundaries(dates)]
        assert labels[0] == "2019+2020"


class TestWalkForward:
    def test_fold_returns_compound_to_the_full_sample_return(self, result) -> None:
        compounded = 1.0
        for fold in result.folds:
            compounded *= 1.0 + fold.statistics.total_return
        assert compounded == pytest.approx(1.0 + result.full_sample.total_return, rel=1e-12)

    def test_fold_sessions_cover_the_run_without_double_counting(self, result) -> None:
        # Each fold shares exactly one boundary session with its predecessor.
        total = sum(fold.statistics.sessions for fold in result.folds)
        assert total == result.full_sample.sessions + len(result.folds) - 1

    def test_fold_trades_sum_to_the_run_total(self, result) -> None:
        assert sum(fold.statistics.trades for fold in result.folds) == (result.full_sample.trades)

    def test_the_result_names_itself_consistency_not_out_of_sample(self, result) -> None:
        assert "not out-of-sample" in result.caveat

    def test_gates_are_measured_and_conservatively_scoped(self, result) -> None:
        gates = result.research_gates()
        assert len(gates) == 3
        sharpe_gate = gates[0]
        assert sharpe_gate.passed == (result.full_sample.sharpe >= 0.50)
        assert sharpe_gate.measured == f"{result.full_sample.sharpe:.2f}"

    def test_gate_names_admit_they_are_in_sample_analogues(self, result) -> None:
        # The promotion gates these mirror are defined out-of-sample; a name
        # quoted into a registry without the qualifier claims too much.
        for gate in result.research_gates():
            assert "in-sample analogue" in gate.name

    def test_the_fold_gate_refuses_to_decide_on_too_few_folds(self) -> None:
        def _statistics() -> WindowStatistics:
            return WindowStatistics(
                sessions=100,
                first_session=dt.date(2020, 1, 2),
                last_session=dt.date(2020, 5, 29),
                total_return=0.05,
                annualized_return=0.10,
                annualized_volatility=0.10,
                sharpe=1.0,
                max_drawdown=-0.05,
                trades=50,
                win_rate=0.0,
                expectancy_r=0.0,
                fold_sharpes=(),
            )

        thin = WalkForwardResult(
            strategy="amat-etf v1.0.0",
            folds=tuple(
                FoldOutcome(label=str(2020 + year), statistics=_statistics()) for year in range(2)
            ),
            full_sample=_statistics(),
            equity_curve=(100.0, 105.0),
            session_dates=(dt.date(2020, 1, 2), dt.date(2020, 1, 3)),
        )
        fold_gate = next(
            gate for gate in thin.research_gates() if "positive annual folds" in gate.name
        )
        # Both folds are positive (100%), and it still must not pass: two
        # observations cannot carry a 60% threshold.
        assert fold_gate.measurable is False
        assert fold_gate.passed is False
        assert "only 2 fold(s)" in fold_gate.measured


class TestHoldoutDeclaration:
    def _declare(self, root, *, boundary=dt.date(2026, 8, 10)) -> HoldoutDeclaration:
        declare_holdout(
            root,
            boundary_session=boundary,
            snapshot_id="alpaca-sip-test",
            rationale="freeze before the walk-forward phase",
            declared_at=dt.datetime(2026, 8, 12, 12, 0, tzinfo=dt.UTC),
        )
        loaded = load_holdout_declaration(root)
        assert loaded is not None
        return loaded

    def test_declaration_roundtrips_and_records_the_burned_history(self, tmp_path) -> None:
        loaded = self._declare(tmp_path)
        assert loaded.boundary_session == dt.date(2026, 8, 10)
        assert loaded.snapshot_id == "alpaca-sip-test"
        assert loaded.history_burned is True

    def test_redeclaring_the_same_day_is_refused(self, tmp_path) -> None:
        self._declare(tmp_path)
        with pytest.raises(ImmutableArtifactError, match="append-only"):
            self._declare(tmp_path)

    def test_moving_the_boundary_earlier_is_refused(self, tmp_path) -> None:
        # A later declaration with an earlier boundary would silently unlock
        # sessions the standing declaration froze.
        self._declare(tmp_path)
        with pytest.raises(ImmutableArtifactError, match="predates"):
            declare_holdout(
                tmp_path,
                boundary_session=dt.date(2026, 8, 1),
                snapshot_id="alpaca-sip-test",
                rationale="attempt to unlock frozen sessions",
                declared_at=dt.datetime(2026, 8, 13, 12, 0, tzinfo=dt.UTC),
            )

    def test_a_stray_file_beside_declarations_is_ignored(self, tmp_path) -> None:
        declared = self._declare(tmp_path)
        stray = tmp_path / "outputs" / "holdouts" / "holdout-declaration-zzz"
        stray.write_text("not a declaration directory", encoding="utf-8")
        loaded = load_holdout_declaration(tmp_path)
        assert loaded is not None
        assert loaded.boundary_session == declared.boundary_session

    def test_a_naive_declaration_timestamp_is_refused(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="UTC"):
            declare_holdout(
                tmp_path,
                boundary_session=dt.date(2026, 8, 10),
                snapshot_id="s",
                rationale="r",
                declared_at=dt.datetime(2026, 8, 12, 12, 0),  # noqa: DTZ001 - the defect under test
            )

    def test_no_declaration_means_no_restriction(self, tmp_path) -> None:
        assert load_holdout_declaration(tmp_path) is None
        panel = _tiny_panel(dt.date(2026, 9, 1))
        assert assert_respects_holdout(panel, None) == (False, None)

    def test_crossing_the_boundary_without_a_reason_fails_closed(self, tmp_path) -> None:
        declaration = self._declare(tmp_path)
        panel = _tiny_panel(dt.date(2026, 9, 1))
        with pytest.raises(DataIntegrityError, match="holdout boundary"):
            assert_respects_holdout(panel, declaration)
        with pytest.raises(DataIntegrityError, match="holdout boundary"):
            assert_respects_holdout(panel, declaration, access_reason="   ")

    def test_crossing_with_a_reason_is_recorded_not_blocked(self, tmp_path) -> None:
        declaration = self._declare(tmp_path)
        panel = _tiny_panel(dt.date(2026, 9, 1))
        accessed, reason = assert_respects_holdout(
            panel, declaration, access_reason="scheduled holdout evaluation per protocol"
        )
        assert accessed is True
        assert reason == "scheduled holdout evaluation per protocol"

    def test_staying_at_or_before_the_boundary_needs_no_reason(self, tmp_path) -> None:
        declaration = self._declare(tmp_path)
        panel = _tiny_panel(dt.date(2026, 8, 10))
        assert assert_respects_holdout(panel, declaration) == (False, None)


class TestDefaultHoldoutRoot:
    def test_the_root_is_found_by_marker_from_any_subdirectory(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        assert default_holdout_root(nested) == tmp_path

    def test_no_marker_anywhere_fails_closed(self, tmp_path) -> None:
        # Fail loud rather than quietly reporting "no declaration exists":
        # the fail-closed guarantee must not depend on the shell's cwd.
        with pytest.raises(DataIntegrityError, match=r"pyproject\.toml"):
            default_holdout_root(tmp_path)


class TestEnforcementIsSharedByEveryLoader:
    def test_enforce_holdout_fails_closed_and_records_authorized_access(
        self, tmp_path, monkeypatch
    ) -> None:
        """The one choke point every panel-loading command runs through.

        This is the regression test for the defect where only ``walkforward``
        checked the declaration and the other measuring commands recorded an
        affirmative ``holdout_accessed: false`` while reading past the
        boundary.
        """
        from amat.cli.main import _enforce_holdout

        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        declare_holdout(
            tmp_path,
            boundary_session=dt.date(2026, 8, 10),
            snapshot_id="alpaca-sip-test",
            rationale="freeze before the walk-forward phase",
            declared_at=dt.datetime(2026, 8, 12, 12, 0, tzinfo=dt.UTC),
        )
        monkeypatch.chdir(tmp_path)
        crossing = _tiny_panel(dt.date(2026, 9, 1))

        with pytest.raises(DataIntegrityError, match="holdout boundary"):
            _enforce_holdout(crossing, None, lambda _message: None)

        declaration, accessed, reason = _enforce_holdout(
            crossing, "scheduled holdout evaluation per protocol", lambda _message: None
        )
        assert declaration is not None
        assert accessed is True
        assert reason == "scheduled holdout evaluation per protocol"

        inside = _tiny_panel(dt.date(2026, 8, 10))
        declaration, accessed, reason = _enforce_holdout(inside, None, lambda _message: None)
        assert (accessed, reason) == (False, None)
