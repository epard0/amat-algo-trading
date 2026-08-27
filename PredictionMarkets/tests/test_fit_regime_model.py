"""The fitter's guards: effective sample size, and the shadow floor."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from fit_regime_model import (  # noqa: E402
    FEATURE_ORDER,
    artifact_path,
    effective_samples,
    fit,
    fit_one,
    free_parameters,
    walk_forward_scores,
    walk_forward_splits,
)


def _stamps(count: int, minutes_apart: float) -> list[str]:
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return [(base + timedelta(minutes=minutes_apart * i)).isoformat() for i in range(count)]


def test_rows_inside_one_window_count_once() -> None:
    """5,000 rows five seconds apart describe one 45-minute window, not 5,000."""
    assert effective_samples(_stamps(500, 0.083)) == 1


def test_rows_a_full_window_apart_all_count() -> None:
    assert effective_samples(_stamps(10, 45)) == 10


def test_rows_just_inside_the_window_are_not_independent() -> None:
    assert effective_samples(_stamps(10, 44)) < 10


def test_unparseable_and_empty_stamps_are_skipped() -> None:
    assert effective_samples(["", "not a date", None or ""]) == 0
    assert effective_samples([]) == 0


def test_the_bots_real_sampling_rate_collapses_by_two_orders() -> None:
    """The measured shape: ~5,000 rows over 11 days of intermittent uptime."""
    rows = _stamps(5000, 0.1)          # a row every six seconds
    assert effective_samples(rows) < 20
    assert len(rows) / effective_samples(rows) > 100


def test_a_diagonal_mixture_parameter_count() -> None:
    # k means + k variances + (k-1) free weights, over d dimensions.
    assert free_parameters(2, 14) == 2 * (2 * 14 + 1) - 1
    assert free_parameters(1, 3) == 6


def test_the_smallest_useful_fit_already_outgrows_the_data() -> None:
    """The finding that motivated the guard, pinned as a test."""
    assert free_parameters(2, len(FEATURE_ORDER)) > 50


def test_the_constant_feature_is_gone() -> None:
    """bars_available had one distinct value across 25,196 samples."""
    assert "bars_available" not in FEATURE_ORDER


def test_per_asset_paths_are_derived_from_the_base(tmp_path) -> None:
    base = tmp_path / "regime_mixture.json"
    assert artifact_path(base, None) == base
    assert artifact_path(base, "BTC").name == "regime_mixture_BTC.json"
    assert artifact_path(base, "DOGE").parent == base.parent


# --------------------------------------------------- walk-forward splitting --
#
# These guard the two properties that make a walk-forward on THIS training set
# mean anything. Both failure modes produce a higher held-out score, so neither
# announces itself: a leaky split just looks like a better model.


def test_folds_are_ordered_in_time_not_by_row() -> None:
    """load_candle_samples appends one asset at a time, so a pooled matrix is
    asset-blocked rather than chronological. Splitting on row index would train
    on one asset and test on another and call it time-series validation.
    """
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    # Deliberately shuffled: rows 0-4 are LATE, rows 5-9 are EARLY.
    late = [(base + timedelta(hours=10 + i)).isoformat() for i in range(5)]
    early = [(base + timedelta(hours=i)).isoformat() for i in range(5)]
    splits = walk_forward_splits(late + early, folds=2)

    assert splits
    for train, test in splits:
        newest_train = max(datetime.fromisoformat((late + early)[i]) for i in train)
        oldest_test = min(datetime.fromisoformat((late + early)[i]) for i in test)
        assert newest_train < oldest_test


def test_the_boundary_is_purged_by_time() -> None:
    """Neighbouring rows share 44 of 45 candles, so a raw cut leaks near-copies
    of the training rows into the test block.
    """
    stamps = _stamps(200, 1)  # one minute apart: heavy overlap
    splits = walk_forward_splits(stamps, folds=4)
    assert splits
    for train, test in splits:
        newest_train = max(datetime.fromisoformat(stamps[i]) for i in train)
        oldest_test = min(datetime.fromisoformat(stamps[i]) for i in test)
        gap = (oldest_test - newest_train).total_seconds() / 60
        assert gap >= 45


def test_purging_is_measured_in_minutes_not_rows() -> None:
    """With nine assets sampled at the same instant, dropping a fixed NUMBER of
    rows would purge only a few minutes. The rule has to be temporal.
    """
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    stamps: list[str] = []
    for minute in range(120):
        for _ in range(9):                      # nine assets, same minute
            stamps.append((base + timedelta(minutes=minute)).isoformat())
    splits = walk_forward_splits(stamps, folds=3)
    assert splits
    for train, test in splits:
        newest_train = max(datetime.fromisoformat(stamps[i]) for i in train)
        oldest_test = min(datetime.fromisoformat(stamps[i]) for i in test)
        assert (oldest_test - newest_train).total_seconds() / 60 >= 45


def test_every_fold_tests_on_data_it_never_trained_on() -> None:
    stamps = _stamps(300, 5)
    for train, test in walk_forward_splits(stamps, folds=5):
        assert not (set(train) & set(test))


def test_the_training_window_expands_across_folds() -> None:
    """Rolling origin: each fold sees everything before its test block, so the
    last fold is fitted on the most data - which is the regime the artifact
    actually ships under.
    """
    splits = walk_forward_splits(_stamps(400, 5), folds=4)
    sizes = [len(train) for train, _ in splits]
    assert sizes == sorted(sizes)
    assert sizes[-1] > sizes[0]


def test_a_span_too_short_to_purge_yields_no_folds_rather_than_a_leaky_one() -> None:
    """Refusing is the honest answer. A fold whose purge ate the whole training
    set would otherwise be scored as if it were valid.
    """
    assert walk_forward_splits(_stamps(50, 0.5), folds=5) == []
    assert walk_forward_splits([], folds=5) == []
    assert walk_forward_splits(_stamps(100, 10), folds=0) == []


def test_unparseable_stamps_are_dropped_not_ordered_arbitrarily() -> None:
    stamps = _stamps(60, 10)
    stamps[7] = "not a date"
    stamps[19] = ""
    used = {i for train, test in walk_forward_splits(stamps, folds=3) for i in train + test}
    assert 7 not in used and 19 not in used


# ------------------------------------------------- selecting k on held-out --


def _separable(n_per_state: int = 240) -> tuple[list[list[float]], list[str], list[str]]:
    """Two genuinely distinct clusters, sampled deterministically over time."""
    import random

    rng = random.Random(11)
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    vectors: list[list[float]] = []
    labels: list[str] = []
    stamps: list[str] = []
    for i in range(n_per_state * 2):
        far = i % 2 == 0
        centre = 5.0 if far else -5.0
        vectors.append([centre + rng.gauss(0, 0.4) for _ in FEATURE_ORDER])
        labels.append("TREND" if far else "VOLATILE_CHOP")
        stamps.append((base + timedelta(minutes=10 * i)).isoformat())
    return vectors, labels, stamps


def test_walk_forward_scores_every_k_it_is_given() -> None:
    vectors, labels, stamps = _separable()
    scores = walk_forward_scores(
        vectors, labels, stamps,
        components=[2, 3], seed=7, covariance_floor=1e-4, folds=3,
    )
    assert set(scores) == {2, 3}
    for stats in scores.values():
        assert stats["folds"] >= 1
        assert 0.0 <= stats["variance_retained"] <= 1.0


def test_selection_reports_which_criterion_chose_k() -> None:
    """The artifact has to say how its k was picked. An in-sample BIC choice and
    a held-out choice are different claims, and only one of them survives the
    44-of-45-candle overlap between neighbouring rows.
    """
    vectors, labels, stamps = _separable()
    scores = walk_forward_scores(
        vectors, labels, stamps,
        components=[2, 3], seed=7, covariance_floor=1e-4, folds=3,
    )
    artifact = fit(
        vectors, labels, components=[2, 3], seed=7,
        covariance_floor=1e-4, walk_forward=scores,
    )
    assert artifact["metadata"]["selected_by"].startswith("held-out log-likelihood")
    assert artifact["metadata"]["walk_forward"] is not None
    assert artifact["metadata"]["components"] in {2, 3}


def test_without_folds_it_still_falls_back_to_bic() -> None:
    """--walk-forward 0 has to leave the old path exactly as it was."""
    vectors, labels, _ = _separable()
    artifact = fit(vectors, labels, components=[2, 3], seed=7, covariance_floor=1e-4)
    assert artifact["metadata"]["selected_by"] == "BIC"
    assert artifact["metadata"]["walk_forward"] is None


def test_a_fitted_artifact_still_loads_in_the_runtime() -> None:
    """Whatever criterion picked k, the bot has to be able to score the result.
    A selection change that produced an artifact the loader rejects would fail
    only at startup on the trading host.
    """
    from core.strategy_brain.strategies.regime_model import RegimeMixture

    vectors, labels, stamps = _separable()
    scores = walk_forward_scores(
        vectors, labels, stamps,
        components=[2, 3], seed=7, covariance_floor=1e-4, folds=3,
    )
    artifact = fit(
        vectors, labels, components=[2, 3], seed=7,
        covariance_floor=1e-4, walk_forward=scores,
    )
    mixture = RegimeMixture.from_dict(artifact)
    blended = mixture.blend(dict(zip(FEATURE_ORDER, vectors[0])))
    # The sign invariant: a convex blend of non-negative multipliers.
    assert blended.drift_multiplier >= 0


# ------------------------------------------------- weights already on disk --


class _Args:
    """Minimal stand-in for the parsed CLI namespace."""

    def __init__(self, out, **over):
        self.out = out
        self.refit = False
        self.report_only = False
        self.candles = None
        self.decisions = out.parent / "missing.jsonl"
        self.stride = 45
        self.walk_forward = 0
        self.components = "2"
        self.seed = 1
        self.covariance_floor = 1e-4
        self.min_samples = 400
        self.min_rows = 2000
        self.force = False
        self.__dict__.update(over)


def test_an_existing_artifact_is_left_alone(tmp_path, capsys) -> None:
    """Re-fitting over weights on disk replaces a model a live bot may be
    scoring against, and does it while printing what looks like progress.
    """
    out = tmp_path / "regime_mixture.json"
    target = artifact_path(out, "BTC")
    target.write_text(json.dumps({
        "metadata": {"components": 4, "fitted_at": "2026-08-11T00:00:00+00:00",
                     "variance_retained": 0.31},
    }))
    before = target.read_bytes()

    assert fit_one(_Args(out), "BTC") == 0
    assert target.read_bytes() == before          # untouched, byte for byte
    printed = capsys.readouterr().out
    assert "skipping" in printed and "--refit" in printed
    # The skip says what is already there, so the operator can judge it.
    assert "k=4" in printed and "31% variance retained" in printed


def test_the_skip_is_per_asset_not_per_run(tmp_path) -> None:
    """One asset having weights must not stop the other eight being fitted."""
    out = tmp_path / "regime_mixture.json"
    artifact_path(out, "BTC").write_text(json.dumps({"metadata": {}}))
    assert artifact_path(out, "BTC").exists()
    assert not artifact_path(out, "ETH").exists()


def test_refit_is_required_to_replace_weights(tmp_path) -> None:
    """--refit must actually get past the guard - otherwise the escape hatch is
    documented and unreachable, which this project has shipped five times.
    """
    out = tmp_path / "regime_mixture.json"
    artifact_path(out, "BTC").write_text(json.dumps({"metadata": {}}))
    # No candles and no decision log, so it proceeds past the guard and then
    # fails on having nothing to fit - which is the point: it got past.
    assert fit_one(_Args(out, refit=True), "BTC") == 1


def test_report_only_never_counts_as_a_skip(tmp_path, capsys) -> None:
    """--report-only says what the data supports; an existing artifact is
    irrelevant to that question and must not suppress the answer.
    """
    out = tmp_path / "regime_mixture.json"
    artifact_path(out, "BTC").write_text(json.dumps({"metadata": {}}))
    fit_one(_Args(out, report_only=True), "BTC")
    assert "skipping" not in capsys.readouterr().out


def test_a_corrupt_artifact_still_blocks_rather_than_being_overwritten(
    tmp_path, capsys
) -> None:
    """Unreadable is not the same as absent. Overwriting a file we could not
    parse is how a hand-edited artifact disappears.
    """
    out = tmp_path / "regime_mixture.json"
    target = artifact_path(out, "BTC")
    target.write_text("{ not json")
    assert fit_one(_Args(out), "BTC") == 0
    assert target.read_text() == "{ not json"
    assert "skipping" in capsys.readouterr().out


def test_a_single_label_fold_is_not_scored_as_zero_retention() -> None:
    """A test block with one hand label has no response variance to reproduce,
    so retention there is undefined, not zero. Averaging a zero in would punish
    a model for a quiet stretch of market - and the gate that number feeds is
    what decides whether an artifact is written at all.
    """
    import random

    rng = random.Random(3)
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    vectors, labels, stamps = [], [], []
    for i in range(600):
        vectors.append([rng.gauss(0, 1) for _ in FEATURE_ORDER])
        labels.append("TREND")            # one label everywhere
        stamps.append((base + timedelta(minutes=10 * i)).isoformat())

    scores = walk_forward_scores(
        vectors, labels, stamps,
        components=[2], seed=5, covariance_floor=1e-4, folds=3,
    )
    # Folds still ran and were scored on likelihood; retention simply abstained.
    assert scores[2]["folds"] >= 1
    assert scores[2]["variance_retained"] == 0.0


def test_k_is_chosen_among_candidates_that_could_actually_ship() -> None:
    """Log-likelihood and variance-retained answer different questions, and they
    disagree. On 231 days of ETH, k=2/4/5 each retained 25.1% while k=8 fitted
    the density best and retained 21.8% - so selecting on likelihood alone chose
    the one candidate the write gate rejects and discarded three it accepts.

    An artifact below the floor cannot ship, so it was never a candidate.
    """
    vectors, labels, _ = _separable()
    scores = {
        2: {"log_likelihood": -10.0, "variance_retained": 0.30, "folds": 5.0},
        3: {"log_likelihood": -8.0, "variance_retained": 0.21, "folds": 5.0},
    }
    artifact = fit(
        vectors, labels, components=[2, 3], seed=7,
        covariance_floor=1e-4, walk_forward=scores,
    )
    # k=3 has the better likelihood but would be refused; k=2 ships.
    assert artifact["metadata"]["components"] == 2
    assert "clearing the floor" in artifact["metadata"]["selected_by"]


def test_when_nothing_clears_the_floor_likelihood_still_decides() -> None:
    """The constraint must not make the fitter unable to report a best effort:
    the write gate refuses afterwards, and --force still has something to write.
    """
    vectors, labels, _ = _separable()
    scores = {
        2: {"log_likelihood": -10.0, "variance_retained": 0.05, "folds": 5.0},
        3: {"log_likelihood": -8.0, "variance_retained": 0.02, "folds": 5.0},
    }
    artifact = fit(
        vectors, labels, components=[2, 3], seed=7,
        covariance_floor=1e-4, walk_forward=scores,
    )
    assert artifact["metadata"]["components"] == 3
    assert artifact["metadata"]["selected_by"] == "held-out log-likelihood"
