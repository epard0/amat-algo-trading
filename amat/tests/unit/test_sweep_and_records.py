"""The volatility-target sweep's discipline, and the run-record artifacts."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from amat.config.loading import load_strategy_config
from amat.data.snapshot import SnapshotManifest
from amat.data.synthetic import generate_panel
from amat.errors import ImmutableArtifactError
from amat.provenance import RunManifest
from amat.reporting.artifacts import (
    dataset_reference,
    new_run_manifest,
    persist_run_record,
    universe_battery_payload,
)
from amat.validation.exitsweep import ExitCandidate, ExitRulesSweep, _candidate_grid
from amat.validation.falsification import UniverseFalsification, UniverseOutcome
from amat.validation.optimize import WindowStatistics
from amat.validation.sweep import (
    CandidateOutcome,
    TargetVolatilitySweep,
    _with_target,
    sweep_target_volatility,
)


def _statistics(*, sharpe: float, trades: int = 100) -> WindowStatistics:
    return WindowStatistics(
        sessions=500,
        first_session=dt.date(2020, 1, 2),
        last_session=dt.date(2021, 12, 30),
        total_return=0.10,
        annualized_return=0.05,
        annualized_volatility=0.05,
        sharpe=sharpe,
        max_drawdown=-0.05,
        trades=trades,
        win_rate=0.0,
        expectancy_r=0.0,
        fold_sharpes=(sharpe, sharpe, sharpe, sharpe),
    )


def _candidate(target: float, *, fold_score: float, validation_sharpe: float) -> CandidateOutcome:
    return CandidateOutcome(
        name=f"tv{target}",
        target_volatility=target,
        training=_statistics(sharpe=fold_score),
        training_fold_score=fold_score,
        validation=_statistics(sharpe=validation_sharpe),
    )


def _sweep(*candidates: CandidateOutcome) -> TargetVolatilitySweep:
    return TargetVolatilitySweep(
        strategy="amat-etf v1.0.0",
        train_end=dt.date(2022, 12, 30),
        baseline_target=0.08,
        candidates=tuple(candidates),
    )


class TestSweepVerdicts:
    def test_a_winning_baseline_indicates_no_change(self) -> None:
        sweep = _sweep(
            _candidate(0.08, fold_score=1.0, validation_sharpe=1.0),
            _candidate(0.10, fold_score=0.5, validation_sharpe=1.2),
        )
        assert sweep.winner.target_volatility == 0.08
        assert "no change is indicated" in sweep.verdict

    def test_a_training_winner_that_folds_on_validation_is_kept_out(self) -> None:
        sweep = _sweep(
            _candidate(0.08, fold_score=0.5, validation_sharpe=1.0),
            _candidate(0.12, fold_score=1.0, validation_sharpe=0.7),
        )
        assert sweep.winner.target_volatility == 0.12
        assert not sweep.winner_holds_on_validation
        assert "keep the frozen target" in sweep.verdict

    def test_a_winner_that_holds_earns_registration_never_promotion(self) -> None:
        sweep = _sweep(
            _candidate(0.08, fold_score=0.5, validation_sharpe=0.8),
            _candidate(0.10, fold_score=1.0, validation_sharpe=1.1),
        )
        assert sweep.winner_holds_on_validation
        assert "does not change the frozen configuration" in sweep.verdict

    def test_the_verdict_always_names_the_multiple_testing_burden(self) -> None:
        sweep = _sweep(_candidate(0.08, fold_score=1.0, validation_sharpe=1.0))
        assert "multiple-testing" in sweep.verdict

    def test_an_unmeasured_field_ranks_nothing(self) -> None:
        # fold_score is -inf below the minimum trade count; a field of -inf
        # scores "ranks" by input order, which is no ranking at all, and the
        # verdict must not pretend otherwise.
        sweep = _sweep(
            _candidate(0.05, fold_score=float("-inf"), validation_sharpe=1.2),
            _candidate(0.08, fold_score=float("-inf"), validation_sharpe=1.0),
        )
        assert not sweep.winner_is_measured
        assert not sweep.winner_holds_on_validation
        assert "ranks nothing" in sweep.verdict
        assert "outranks" not in sweep.verdict

    def test_a_training_tie_declares_no_winner(self) -> None:
        sweep = _sweep(
            _candidate(0.08, fold_score=0.75, validation_sharpe=1.0),
            _candidate(0.10, fold_score=0.75, validation_sharpe=1.4),
        )
        assert sweep.winner_is_tied
        assert not sweep.winner_holds_on_validation
        assert "indistinguishable" in sweep.verdict

    def test_the_caveat_denies_out_of_sample_status(self) -> None:
        sweep = _sweep(_candidate(0.08, fold_score=1.0, validation_sharpe=1.0))
        assert "not out-of-sample" in sweep.caveat


def _exit_candidate(
    stop: float | None,
    profit: float | None,
    *,
    fold_score: float,
    validation_sharpe: float,
) -> ExitCandidate:
    return ExitCandidate(
        name="no exits (frozen core)" if stop is None and profit is None else f"sl{stop}tp{profit}",
        stop_loss=stop,
        take_profit=profit,
        training=_statistics(sharpe=fold_score),
        training_fold_score=fold_score,
        validation=_statistics(sharpe=validation_sharpe),
    )


def _exit_sweep(*candidates: ExitCandidate) -> ExitRulesSweep:
    return ExitRulesSweep(
        strategy="amat-etf v1.0.0",
        train_end=dt.date(2022, 12, 30),
        reentry_embargo_sessions=21,
        candidates=tuple(candidates),
    )


class TestExitSweepVerdicts:
    def test_the_grid_always_leads_with_the_no_exit_baseline(self) -> None:
        grid = _candidate_grid([0.10, 0.05], [0.20])
        assert grid[0] == (None, None)
        assert len(grid) == 6  # {None, .05, .10} x {None, .20}

    def test_a_winning_baseline_indicates_no_exit_rule(self) -> None:
        sweep = _exit_sweep(
            _exit_candidate(None, None, fold_score=1.0, validation_sharpe=1.0),
            _exit_candidate(0.10, None, fold_score=0.5, validation_sharpe=1.2),
        )
        assert sweep.winner.is_baseline
        assert "no stop-loss or take-profit is indicated" in sweep.verdict

    def test_a_winner_that_folds_on_validation_is_kept_out(self) -> None:
        sweep = _exit_sweep(
            _exit_candidate(None, None, fold_score=0.5, validation_sharpe=1.0),
            _exit_candidate(0.10, 0.20, fold_score=1.0, validation_sharpe=0.7),
        )
        assert not sweep.winner_holds_on_validation
        assert "keep the frozen no-exit core" in sweep.verdict

    def test_a_surviving_winner_earns_registration_never_promotion(self) -> None:
        sweep = _exit_sweep(
            _exit_candidate(None, None, fold_score=0.5, validation_sharpe=0.8),
            _exit_candidate(0.10, None, fold_score=1.0, validation_sharpe=1.1),
        )
        assert sweep.winner_holds_on_validation
        assert "does not change the frozen configuration" in sweep.verdict
        assert "multiple-testing" in sweep.verdict

    def test_unmeasured_and_tied_fields_rank_nothing(self) -> None:
        unmeasured = _exit_sweep(
            _exit_candidate(None, None, fold_score=float("-inf"), validation_sharpe=1.0),
            _exit_candidate(0.05, None, fold_score=float("-inf"), validation_sharpe=1.3),
        )
        assert "ranks nothing" in unmeasured.verdict
        assert not unmeasured.winner_holds_on_validation
        tied = _exit_sweep(
            _exit_candidate(None, None, fold_score=0.75, validation_sharpe=1.0),
            _exit_candidate(0.05, None, fold_score=0.75, validation_sharpe=1.4),
        )
        assert "indistinguishable" in tied.verdict
        assert not tied.winner_holds_on_validation


class TestWithTarget:
    def test_only_the_target_and_the_name_move(self, canonical_config_path) -> None:
        strategy = load_strategy_config(canonical_config_path).strategy
        variant = _with_target(strategy, 0.10)
        assert variant.constraints.target_annualized_volatility == 0.10
        assert variant.name == "amat-etf-tv1000bp"
        left = strategy.model_dump()
        right = variant.model_dump()
        left["constraints"].pop("target_annualized_volatility")
        right["constraints"].pop("target_annualized_volatility")
        left.pop("name")
        right.pop("name")
        assert left == right


class TestSweepEndToEnd:
    def test_the_sweep_measures_both_windows_for_every_candidate(
        self, canonical_config_path
    ) -> None:
        strategy = load_strategy_config(canonical_config_path).strategy
        panel = generate_panel(strategy.to_universe(), sessions=330, seed=5, trend_strength=0.5)
        train_end = panel.sessions[289]
        sweep = sweep_target_volatility(
            strategy,
            panel,
            train_end=train_end,
            targets=(0.10,),
            initial_cash=Decimal("100000"),
        )
        assert {row.target_volatility for row in sweep.candidates} == {0.08, 0.10}
        assert sweep.baseline_target == 0.08
        for row in sweep.candidates:
            assert row.training.last_session <= train_end
            assert row.validation.last_session == panel.sessions[-1]
            # The validation window's base session is the last training one,
            # so its first measured return is the first post-split move.
            assert row.validation.first_session <= train_end
        scores = [row.training_fold_score for row in sweep.ranked]
        assert scores == sorted(scores, reverse=True)


def _snapshot_manifest(**overrides: object) -> SnapshotManifest:
    payload: dict[str, object] = {
        "snapshot_id": "alpaca-sip-test",
        "provider": "alpaca",
        "feed": "sip",
        "as_of": dt.datetime(2026, 8, 11, 0, 0, tzinfo=dt.UTC),
        "symbols": ("SPY", "TLT"),
        "first_session": dt.date(2016, 1, 4),
        "last_session": dt.date(2026, 8, 10),
        "session_count": 2665,
        "daily_rows": 5330,
        "window_rows": 5330,
        "action_rows": 100,
        "execution_window_start": dt.time(9, 30),
        "execution_window_end": dt.time(9, 35),
        "exchange_timezone": "America/New_York",
        "partition_sha256": {
            "daily.parquet": "a" * 64,
            "window.parquet": "b" * 64,
            "actions.parquet": "c" * 64,
        },
        "window_coverage": 1.0,
    }
    payload.update(overrides)
    return SnapshotManifest.model_validate(payload)


class TestRunRecords:
    def _manifest(self, *, strategy_name: str = "amat-etf") -> RunManifest:
        return new_run_manifest(
            command="study",
            strategy_name=strategy_name,
            strategy_version="1.0.0",
            config_sha256="0" * 64,
            config_source="configs/strategy/amat_v1.toml",
            datasets=(dataset_reference(_snapshot_manifest()),),
            execution_model_version="sim-vwap-09:30:00-09:35:00-lag1",
            cost_model_version="one-way-2bps-primary-True",
            started_at=dt.datetime(2026, 8, 12, 15, 0, 0, 123456, tzinfo=dt.UTC),
        )

    def test_the_dataset_digest_moves_when_any_partition_moves(self) -> None:
        base = dataset_reference(_snapshot_manifest())
        moved = dataset_reference(
            _snapshot_manifest(
                partition_sha256={
                    "daily.parquet": "a" * 64,
                    "window.parquet": "b" * 64,
                    "actions.parquet": "d" * 64,
                }
            )
        )
        assert base.partition_sha256 != moved.partition_sha256

    def test_the_run_id_is_a_safe_path_component_whatever_the_name(self) -> None:
        manifest = self._manifest(strategy_name="Weird/Name  v2!")
        assert "/" not in manifest.run_id
        assert manifest.run_id.startswith("study-weird-name-v2-")

    def test_a_record_writes_both_files_and_binds_them_by_digest(self, tmp_path) -> None:
        manifest = self._manifest()
        target = persist_run_record(tmp_path, manifest, {"note": {"funding": Decimal("100000")}})
        written = json.loads((target / "results.json").read_text(encoding="utf-8"))
        assert written["manifest_sha256"] == manifest.digest()
        assert written["results"]["note"]["funding"] == "100000"
        stored = (target / "manifest.json").read_text(encoding="utf-8").strip()
        assert stored == manifest.to_json()

    def test_records_are_append_only(self, tmp_path) -> None:
        manifest = self._manifest()
        persist_run_record(tmp_path, manifest, {})
        with pytest.raises(FileExistsError):
            persist_run_record(tmp_path, manifest, {})

    def test_a_traversal_shaped_run_id_cannot_escape_the_run_root(self, tmp_path) -> None:
        manifest = self._manifest().model_copy(update={"run_id": "../escape"})
        with pytest.raises(ImmutableArtifactError, match="escapes"):
            persist_run_record(tmp_path / "runs", manifest, {})

    def test_a_run_root_under_immutable_evidence_is_refused(self, tmp_path) -> None:
        # `--run-root outputs/validated` must refuse, not comply: run records
        # have not passed the validation protocol and may not squat in an
        # immutable evidence root of the owning repository.
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        run_root = tmp_path / "outputs" / "validated"
        with pytest.raises(ImmutableArtifactError, match="immutable"):
            persist_run_record(run_root, self._manifest(), {})
        assert not run_root.exists(), "the refusal must not create directories"

    def test_the_universe_battery_payload_is_recordable_json(self) -> None:
        row = UniverseOutcome(
            name="baseline (amat-etf)",
            symbols=("SPY", "TLT"),
            total_return=0.85,
            annualized_return=0.066,
            annualized_volatility=0.065,
            sharpe=1.01,
            max_drawdown=-0.087,
            fills=1000,
        )
        battery = UniverseFalsification(baseline=row, candidate=row, leave_one_out=())
        payload = universe_battery_payload(battery)
        assert payload["kind"] == "universe_falsification"
        assert "NO ADDITIONS" in str(payload["verdict"])
        json.dumps(payload)
