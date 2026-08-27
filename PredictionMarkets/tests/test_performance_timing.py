"""How long /api/performance takes, which is a correctness property here.

The projections are twenty thousand paths over six starting balances and their
stress cases, in pure Python. That took 53 seconds, and the TTL cache meant one
visitor every five minutes paid it while the page showed "Loading verified
results…". These tests hold the three things that fixed it.
"""

from __future__ import annotations

import time

import pytest

from storefront import performance
from storefront.model_stats import ModelStats, TradedSample
from storefront.verified import VerifiedRecord, build_payload, project


@pytest.fixture
def stats() -> ModelStats:
    samples = tuple(
        TradedSample(0.70 + i * 0.01, 0.60 + i * 0.01, i % 4 != 0) for i in range(12)
    )
    return ModelStats(
        samples=samples, settled=12, mean_probability=0.755, mean_price=0.655,
        realised_win_rate=0.75, source="test",
    )


@pytest.fixture(scope="module")
def record() -> VerifiedRecord:
    loaded = VerifiedRecord.load()
    assert loaded is not None
    return loaded


@pytest.fixture(autouse=True)
def clean_caches():
    import storefront.verified as verified_module

    verified_module._PAYLOAD_CACHE.clear()
    performance._CACHE["payload"] = None
    performance._CACHE["built_at"] = 0.0
    performance._REBUILDING.clear()
    yield
    verified_module._PAYLOAD_CACHE.clear()
    performance._CACHE["payload"] = None
    performance._CACHE["built_at"] = 0.0
    performance._REBUILDING.clear()


# ----------------------------------------------------------- memoisation ---

def test_an_unchanged_ledger_does_not_rebuild_the_projections(record, stats) -> None:
    """The ledger is re-read every 5 minutes; a trade settles every 15 at most."""
    first = build_payload(record, stats=stats, paths=300)

    started = time.perf_counter()
    second = build_payload(record, stats=stats, paths=300)
    elapsed = time.perf_counter() - started

    assert second is first
    assert elapsed < 0.05


def test_a_new_settled_trade_does_rebuild(record, stats) -> None:
    """...and when the input really changes, the answer must change with it."""
    first = build_payload(record, stats=stats, paths=300)
    moved = ModelStats(
        samples=stats.samples + (TradedSample(0.80, 0.70, True),),
        settled=13, mean_probability=0.757, mean_price=0.658, source="test",
    )

    assert build_payload(record, stats=moved, paths=300) is not first


def test_the_memo_does_not_grow_without_bound(record, stats) -> None:
    import storefront.verified as verified_module

    for index in range(14):
        build_payload(
            record,
            paths=200,
            stats=ModelStats(
                samples=stats.samples + (TradedSample(0.8, 0.7 + index * 0.001, True),),
                settled=13, mean_probability=0.75, mean_price=0.65, source="t",
            ),
        )

    assert len(verified_module._PAYLOAD_CACHE) <= 9


# ------------------------------------------------------------- fee table ---

def test_precomputing_the_fee_table_does_not_change_the_answer(record, stats) -> None:
    """The fee is a pure function of (contracts, price) over eighteen prices.

    Hoisting it out of the loop was worth two thirds of the runtime, and it has
    to be exactly equal rather than approximately — these are published figures,
    and a projection whose numbers drift when it is optimised is not a
    projection anyone should trust.
    """
    import math

    from storefront.verified import taker_fee

    for sample in stats.samples:
        for contracts in (1, 7, 100):
            expected = math.ceil(
                round(0.07 * contracts * sample.price * (1 - sample.price) / 1e-4, 9)
            ) * 1e-4
            assert taker_fee(contracts, sample.price) == pytest.approx(expected, abs=1e-12)

    # Same seed, same answer, whichever way the fee was obtained.
    a = project(record, starting_balance=10.0, stats=stats, paths=500)
    b = project(record, starting_balance=10.0, stats=stats, paths=500)
    assert a["percentiles"] == b["percentiles"]


def test_a_projection_is_fast_enough_to_rebuild_in_the_background(record, stats) -> None:
    started = time.perf_counter()
    project(record, starting_balance=10.0, stats=stats, paths=2000)
    elapsed = time.perf_counter() - started

    # 2,000 paths in well under a second means 20,000 lands inside the few
    # seconds a background rebuild can afford.
    assert elapsed < 2.0, f"2,000 paths took {elapsed:.1f}s"


# --------------------------------------------------- stale-while-revalidate --

def test_a_stale_cache_is_served_immediately(monkeypatch) -> None:
    """The rebuild happens behind the response, never in front of it."""
    monkeypatch.setattr(performance, "CACHE_TTL_SECONDS", 0.0)
    performance._CACHE["payload"] = {"source": "live-ledger", "marker": "old"}
    performance._CACHE["built_at"] = time.monotonic() - 999

    rebuilt: list[bool] = []
    monkeypatch.setattr(performance, "refresh_in_background",
                        lambda: rebuilt.append(True) or True)

    started = time.perf_counter()
    payload = performance.current_snapshot()

    assert payload["marker"] == "old"
    assert time.perf_counter() - started < 0.05
    assert rebuilt == [True]


def test_a_cold_cache_serves_the_committed_snapshot_rather_than_blocking(
    monkeypatch,
) -> None:
    """First request after a deploy, with the warm-up still running.

    Serving the shipped file is instant and honestly labelled; starting a
    second rebuild alongside the first is how the first request came to take
    46 seconds even after the warm-up existed.
    """
    performance._REBUILDING.set()
    monkeypatch.setattr(performance, "_committed_snapshot",
                        lambda: {"source": "snapshot-file", "modes": {}})

    started = time.perf_counter()
    payload = performance.current_snapshot()

    assert payload["source"] == "snapshot-file"
    assert time.perf_counter() - started < 0.05


def test_only_one_rebuild_runs_at_a_time(monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(performance, "current_snapshot",
                        lambda **_: calls.append(True))

    assert performance.refresh_in_background() is True
    # The first may already have finished; either way a second must not stack.
    performance._REBUILDING.set()
    assert performance.refresh_in_background() is False


def test_warm_starts_a_build(monkeypatch) -> None:
    monkeypatch.setattr(performance, "current_snapshot", lambda **_: None)

    assert performance.warm() is True


# ------------------------------------------------------- the shipped file ---

def test_the_committed_snapshot_carries_the_projections() -> None:
    """Without them a cold process has nothing to serve but a blocking rebuild."""
    committed = performance._committed_snapshot()

    assert committed.get("verified"), "run: python -m storefront.performance --write"
    assert committed["verified"]["projections"]["start_10"]["percentiles"]["p50"] > 0
