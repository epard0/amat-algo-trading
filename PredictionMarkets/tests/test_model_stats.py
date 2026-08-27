"""The model's own statistics, and the projection built on them.

The point of this module is that a win rate is 74 coin flips while a
calibration gap is a mean over trades, and the second is far better conditioned
than the first. These tests hold that reasoning in place, and guard the two
places it could quietly turn into wishful thinking.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from storefront.model_stats import (
    MIN_SETTLED_FOR_CALIBRATION,
    ModelStats,
    TradedSample,
    from_ledger,
    load,
    write_snapshot,
)
from storefront.verified import VerifiedRecord, project, stress_projection

BASE = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def build_ledger(path, rows) -> None:
    """A ledger with just the columns model_stats reads."""
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE trades (trade_id TEXT, model_probability TEXT, "
        "entry_price TEXT, status TEXT, placed_at TEXT)"
    )
    for index, (probability, price, status) in enumerate(rows):
        connection.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?)",
            (f"t{index}", str(probability), str(price), status,
             (BASE + timedelta(hours=index * 8)).isoformat()),
        )
    connection.commit()
    connection.close()


@pytest.fixture
def ledger(tmp_path):
    # Twelve trades: calibrated on average, spread across prices, plus two the
    # edge ceiling should refuse.
    rows = [
        (0.70, 0.60, "WON"), (0.70, 0.60, "WON"), (0.70, 0.60, "LOST"),
        (0.80, 0.70, "WON"), (0.80, 0.70, "WON"), (0.80, 0.72, "LOST"),
        (0.66, 0.55, "WON"), (0.66, 0.58, "WON"), (0.66, 0.57, "LOST"),
        (0.90, 0.80, "WON"), (0.90, 0.82, "WON"), (0.75, 0.65, "WON"),
        (0.68, 0.20, "LOST"), (0.72, 0.30, "LOST"),        # edge > 0.18
    ]
    path = tmp_path / "ledger.db"
    build_ledger(path, rows)
    return path


@pytest.fixture(scope="module")
def record() -> VerifiedRecord:
    loaded = VerifiedRecord.load()
    assert loaded is not None
    return loaded


# ------------------------------------------------------------ extraction ---

def test_it_reads_what_the_model_believed_and_what_happened(ledger) -> None:
    stats = from_ledger(ledger)

    assert stats.settled == 14
    assert stats.source == "live-ledger"
    assert 0.6 < stats.mean_probability < 0.85
    assert stats.usable


def test_the_calibration_gap_is_realised_minus_predicted(ledger) -> None:
    stats = from_ledger(ledger)

    assert stats.calibration_gap == pytest.approx(
        stats.realised_win_rate - stats.mean_probability, abs=1e-9
    )


def test_a_perfectly_calibrated_model_has_no_gap(tmp_path) -> None:
    path = tmp_path / "l.db"
    build_ledger(path, [(0.75, 0.6, "WON")] * 75 + [(0.75, 0.6, "LOST")] * 25)

    assert from_ledger(path).calibration_gap == pytest.approx(0.0, abs=1e-9)


def test_brier_rewards_being_right_and_confident(tmp_path) -> None:
    confident = tmp_path / "a.db"
    build_ledger(confident, [(0.95, 0.9, "WON")] * 20)
    hedged = tmp_path / "b.db"
    build_ledger(hedged, [(0.55, 0.5, "WON")] * 20)

    assert from_ledger(confident).brier < from_ledger(hedged).brier


def test_a_missing_ledger_is_not_an_error(tmp_path) -> None:
    """A sales page must render without the trading host's disk."""
    stats = from_ledger(tmp_path / "absent.db")

    assert stats.settled == 0
    assert not stats.usable


def test_a_corrupt_ledger_is_not_an_error(tmp_path) -> None:
    path = tmp_path / "junk.db"
    path.write_bytes(b"this is not sqlite")

    assert from_ledger(path).settled == 0


def test_buckets_compare_predicted_with_realised(ledger) -> None:
    stats = from_ledger(ledger)

    assert stats.buckets
    for bucket in stats.buckets:
        assert bucket.count > 0
        assert bucket.gap == pytest.approx(bucket.actual - bucket.predicted, abs=1e-9)


def test_calibration_needs_a_minimum_sample() -> None:
    assert ModelStats(settled=3).calibration_is_measurable is False
    assert ModelStats(settled=MIN_SETTLED_FOR_CALIBRATION).calibration_is_measurable


# ---------------------------------------------------------- the gate filter --

def test_the_shipped_edge_ceiling_filters_the_population(ledger) -> None:
    """The ledger predates the ceiling; the projection must not use what it refuses."""
    stats = from_ledger(ledger)

    assert len(stats.samples) == 14
    assert len(stats.tradeable()) == 12
    assert all(s.edge <= 0.18 for s in stats.tradeable())


def test_the_refused_trades_are_the_ones_that_lost(ledger) -> None:
    """Not a coincidence in the real ledger either — it is why the gate exists."""
    stats = from_ledger(ledger)
    refused = [s for s in stats.samples if s.edge > 0.18]

    assert refused
    assert all(s.won is False for s in refused)


# ------------------------------------------------------------- snapshot ---

def test_a_snapshot_round_trips(ledger, tmp_path) -> None:
    original = from_ledger(ledger)
    path = tmp_path / "snap.json"
    write_snapshot(original, path)

    restored = ModelStats.from_dict(json.loads(path.read_text()))

    assert restored.settled == original.settled
    assert restored.source == "snapshot-file"
    assert len(restored.tradeable()) == len(original.tradeable())


def test_load_prefers_a_live_ledger(ledger) -> None:
    assert load(ledger).source == "live-ledger"


def test_load_falls_back_when_there_is_no_ledger(tmp_path) -> None:
    """The committed snapshot is what a storefront host without a ledger uses."""
    assert load(tmp_path / "absent.db").source in {"snapshot-file", "unavailable"}


# ----------------------------------------------------------- projection ---

def test_the_projection_uses_the_model_when_it_can(record, ledger) -> None:
    result = project(record, starting_balance=10.0, stats=from_ledger(ledger))

    assert result["basis"] == "model-output"
    assert result["sampled_trades"] == 12
    assert result["refused_by_edge_ceiling"] == 2


def test_the_projection_still_works_without_model_output(record) -> None:
    """A buyer's page must render on a host that has never seen the ledger."""
    result = project(record, starting_balance=10.0, stats=ModelStats())

    assert result["basis"] == "flat-win-rate"
    assert result["percentiles"]["p5"] < result["percentiles"]["p95"]


def test_the_level_is_anchored_to_the_larger_sample(record, ledger) -> None:
    """The ledger subset ran hotter than the account; the account wins.

    Anchoring the other way produced a median of $525 from $10, which is what
    happens when a favourable subsample is projected over a horizon it never
    covered.
    """
    stats = from_ledger(ledger)
    result = project(record, starting_balance=10.0, stats=stats)

    implied = record.win_rate - stats.mean_price
    population = sum(s.edge for s in stats.tradeable()) / len(stats.tradeable())
    # The payload rounds to four places for the page.
    assert result["calibration_gap"] == pytest.approx(implied - population, abs=1e-4)


def test_the_stress_case_is_actually_worse(record, ledger) -> None:
    """It was not, once. The stress shift replaced the anchor instead of
    composing with it, so the stressed run sat six points above the base."""
    stats = from_ledger(ledger)
    base = project(record, starting_balance=10.0, stats=stats)
    stressed = stress_projection(record, starting_balance=10.0, stats=stats)

    assert stressed["percentiles"]["p50"] < base["percentiles"]["p50"]
    assert stressed["probability_of_profit"] < base["probability_of_profit"]
    assert stressed["shift"] < base["calibration_gap"]


def test_the_pairing_of_probability_and_price_is_preserved(record) -> None:
    """Averaging a set of small well-priced edges makes one large mispriced one.

    Same mean probability and mean price in both populations; only the pairing
    differs. If the projection averaged, these would agree.
    """
    paired = ModelStats(
        samples=tuple(TradedSample(p, pr, True) for p, pr in
                      [(0.65, 0.55), (0.65, 0.55), (0.85, 0.75), (0.85, 0.75),
                       (0.75, 0.65), (0.75, 0.65)]),
        settled=6, mean_probability=0.75, mean_price=0.65, source="test",
    )
    crossed = ModelStats(
        samples=tuple(TradedSample(p, pr, True) for p, pr in
                      [(0.65, 0.75), (0.65, 0.75), (0.85, 0.55), (0.85, 0.55),
                       (0.75, 0.65), (0.75, 0.65)]),
        settled=6, mean_probability=0.75, mean_price=0.65, source="test",
    )

    a = project(record, starting_balance=10.0, stats=paired)
    b = project(record, starting_balance=10.0, stats=crossed)

    assert a["percentiles"]["p50"] != b["percentiles"]["p50"]


def test_the_projection_is_deterministic(record, ledger) -> None:
    stats = from_ledger(ledger)

    assert (project(record, starting_balance=10.0, stats=stats)["percentiles"]
            == project(record, starting_balance=10.0, stats=stats)["percentiles"])
