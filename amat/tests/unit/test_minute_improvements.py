"""Causality, restart and unified-account checks for the minute research path."""

import datetime as dt
from dataclasses import asdict, replace
from decimal import Decimal

import numpy as np
import pytest

from amat.planning.policy import build_intraday_plan
from amat.risk.active import ActiveRiskConfig, initial_state
from autoresearch.improvements.minute import FeatureStream, MinuteBar, MinuteBatch
from autoresearch.improvements.refit import (
    RefitCoordinator,
    TrainingRow,
    fit_model,
    restore_artifact,
)
from autoresearch.improvements.replay import replay_minutes

START = dt.datetime(2026, 1, 2, 14, 30, tzinfo=dt.UTC)


def bars(n=80):
    for i in range(n):
        stamp = START + dt.timedelta(minutes=i)
        price = 100 + i * 0.05
        yield MinuteBar(
            "SPY",
            stamp,
            price,
            price + 0.1,
            price - 0.1,
            price + 0.05,
            100000,
            stamp + dt.timedelta(minutes=1, seconds=1),
        )


def test_future_gap_does_not_change_prefix_and_revision_abstains():
    stream = FeatureStream(("SPY",))
    observed = list(bars(30))
    for bar in observed[:25]:
        assert stream.admit(bar, bar.available)
    prefix = stream.values("SPY", observed[24].start).copy()
    assert not stream.admit(observed[24], observed[24].available)
    np.testing.assert_array_equal(prefix, stream.values("SPY", observed[24].start))
    revision = replace(observed[24], revision=1, close=observed[24].close + 0.01)
    assert not stream.admit(revision, revision.available)
    assert stream.values("SPY", revision.start) is None
    fresh = FeatureStream(("SPY",))
    with pytest.raises(ValueError, match="unavailable"):
        fresh.admit(observed[0], observed[0].start)


def test_refit_counter_restart_counts_global_minutes_once():
    a = RefitCoordinator()
    b = RefitCoordinator()
    try:
        for i in range(249):
            assert not a.observe(START + dt.timedelta(minutes=i))
        assert not a.observe(START + dt.timedelta(minutes=248))
        b.restore_counter(a.checkpoint())
        assert b.observe(START + dt.timedelta(minutes=249))
        assert b.count == 250
    finally:
        a.close()
        b.close()


def training_rows():
    rng = np.random.default_rng(5)
    return tuple(
        TrainingRow(
            START + dt.timedelta(minutes=i),
            START + dt.timedelta(minutes=i + 5),
            tuple(rng.normal(size=4)),
            0.01,
            "SPY",
        )
        for i in range(200)
    )


def test_fit_is_prefix_bound_and_artifact_is_validated():
    rows = training_rows()
    cutoff = START + dt.timedelta(minutes=180)
    a = fit_model(rows, cutoff)
    changed = tuple(replace(r, outcome=-100) if r.mature > cutoff else r for r in rows)
    b = fit_model(changed, cutoff)
    assert a.identity == b.identity
    payload = asdict(a)
    payload["trained_through"] = a.trained_through.isoformat()
    payload["last_mature_label"] = a.last_mature_label.isoformat()
    assert restore_artifact(payload, a.identity) == a
    payload["expression"]["op"] = "exec"
    with pytest.raises(ValueError):
        restore_artifact(payload, a.identity)


def test_async_fit_never_activates_on_trigger_boundary():
    coordinator = RefitCoordinator(interval=5)
    cutoff = START + dt.timedelta(minutes=180)
    try:
        assert coordinator.submit(training_rows(), cutoff)
        coordinator.pending.result()
        assert not coordinator.activate(cutoff)
        assert coordinator.activate(cutoff + dt.timedelta(minutes=1))
        restored = RefitCoordinator(interval=5)
        try:
            restored.restore_counter(coordinator.checkpoint())
            assert restored.active.identity == coordinator.active.identity
        finally:
            restored.close()
    finally:
        coordinator.close()


def test_minute_replay_delays_fills_and_targets_flat_before_close():
    sequence = list(bars())
    end = sequence[-1].start + dt.timedelta(minutes=1)
    batches = [MinuteBatch(b.start, b.available, (b,), end) for b in sequence]
    output = replay_minutes(batches, ("SPY",), "M1", training_sessions=1)
    assert output["fills"]
    assert not output["failed_liquidations"]
    first = dt.datetime.fromisoformat(output["fills"][0]["at"])
    eligible = [d for d in output["decisions"] if dt.datetime.fromisoformat(d["at"]) < first]
    assert eligible
    assert output["records"][-1]["exposure"] == 0
    assert all(r["cash"] >= 0 for r in output["records"])
    prefix = replay_minutes(batches[:40], ("SPY",), "M1", training_sessions=1)
    assert prefix["records"] == output["records"][:40]


def test_intraday_plan_is_cash_feasible_and_never_same_information_time():
    now = START + dt.timedelta(minutes=30)
    state = initial_state("actual-paper-account", "research-policy", now, 1000)
    p = build_intraday_plan(
        {"SPY": 0.5},
        {"SPY": Decimal(100)},
        {},
        equity=Decimal(1000),
        confirmed_cash=Decimal(100),
        committed_cash=Decimal(0),
        state=state,
        risk=ActiveRiskConfig(),
        shocks=({"SPY": -0.01},),
        now=now,
        data_available_at=now,
        session_close=START + dt.timedelta(hours=6),
        config_sha256="a" * 64,
        model_id="frozen",
    )
    assert dt.datetime.fromisoformat(p["valid_after"]) > now
    assert sum(Decimal(i["notional"]) for i in p["plan"]["intents"]) < 90


def test_exchange_grid_keeps_half_day_and_does_not_drop_incomplete_day(tmp_path):
    import hashlib
    import json

    import polars as pl

    from autoresearch.improvements.minute import iter_batches
    from autoresearch.improvements.minute_data import audit_exchange_grid

    frame = pl.DataFrame(
        {
            "t": ["2026-11-27T14:30:00Z"],
            "o": [100.0],
            "h": [101.0],
            "l": [99.0],
            "c": [100.5],
            "v": [1000.0],
        }
    )
    partition = tmp_path / "SPY-2026.parquet"
    frame.write_parquet(partition)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "adjustment": "raw",
                "bar_minutes": 1,
                "partitions": {partition.name: hashlib.sha256(partition.read_bytes()).hexdigest()},
            }
        )
    )
    day = dt.date(2026, 11, 27)
    events = list(iter_batches(tmp_path, ("SPY",), day, day))
    assert len(events) == 210
    assert len(events[0].bars) == 1
    assert not events[1].bars
    result = audit_exchange_grid(tmp_path, ("SPY",), day, day, tmp_path / "audit")
    assert result["expected_regular_asset_minutes"] == 210
    assert result["missing_regular_asset_minutes"] == 209


def test_streamed_evidence_matches_retained_replay_and_warmup_is_nontrading():
    observed = list(bars(80))
    batches = [
        MinuteBatch(b.start, b.available, (b,), START + dt.timedelta(minutes=80)) for b in observed
    ]
    retained = replay_minutes(batches, ("SPY",), "M1")
    streamed = {}
    compact = replay_minutes(
        batches,
        ("SPY",),
        "M1",
        event_sink=lambda kind, row: streamed.setdefault(kind, []).append(row),
    )
    for kind in ("records", "fills", "decisions", "missed"):
        assert streamed.get(kind, []) == retained[kind]
        assert compact[kind] == []
    warmup = replay_minutes(batches, ("SPY",), "M1", trade_start=dt.date(2026, 1, 5))
    assert warmup["fills"] == []
    assert all(r["equity"] == 100000 and r["exposure"] == 0 for r in warmup["records"])


def test_full_refit_budget_exceeds_pilot_limit():
    coordinator = RefitCoordinator(interval=250, max_fits=10000)
    try:
        coordinator.fit_count = 64
        for i in range(250):
            trigger = coordinator.observe(START + dt.timedelta(minutes=i))
        assert trigger
    finally:
        coordinator.close()


def test_lossless_feature_cache_preserves_replay_and_rejects_tampering(tmp_path):
    import hashlib
    import json
    from pathlib import Path

    from autoresearch.improvements import minute
    from autoresearch.improvements.feature_cache import FeatureCache

    observed = list(bars(80))
    stream = FeatureStream(("SPY",))
    values = []
    for b in observed:
        stream.admit(b, b.available)
        value = stream.values("SPY", b.start)
        values.append([value if value is not None else np.full(4, np.nan)])
    np.save(tmp_path / "2026-stamps.npy", np.array([int(b.start.timestamp()) for b in observed]))
    np.save(tmp_path / "2026-values.npy", np.array(values))
    manifest = dict(
        symbols=["SPY"],
        minute_partitions={},
        feature_source_sha256=hashlib.sha256(Path(minute.__file__).read_bytes()).hexdigest(),
        files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob("*.npy")},
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    cache = FeatureCache(tmp_path, ("SPY",), {})
    batches = [
        MinuteBatch(b.start, b.available, (b,), START + dt.timedelta(minutes=80)) for b in observed
    ]
    a = replay_minutes(batches, ("SPY",), "M1")
    b = replay_minutes(batches, ("SPY",), "M1", feature_provider=cache.values)
    assert a == b
    with (tmp_path / "2026-values.npy").open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ValueError, match="file changed"):
        FeatureCache(tmp_path, ("SPY",), {})


def test_cached_training_serialization_preserves_canonical_data_hash():
    import hashlib
    import json

    rows = training_rows()
    cutoff = START + dt.timedelta(minutes=180)
    model = fit_model(rows, cutoff)
    eligible = sorted(
        (r for r in rows if r.mature <= cutoff and r.observed < r.mature),
        key=lambda r: (r.observed, r.symbol),
    )
    expected = hashlib.sha256(
        json.dumps(
            [asdict(r) for r in eligible], default=str, sort_keys=True, allow_nan=False
        ).encode()
    ).hexdigest()
    assert model.data_sha256 == expected


def test_training_serialization_cache_distinguishes_signed_zero():
    from autoresearch.improvements.refit import _row_json

    a = TrainingRow(START, START + dt.timedelta(minutes=15), (0.0, 0.1, 0.2, 0.3), -0.0, "SPY")
    b = replace(a, features=(-0.0, 0.1, 0.2, 0.3), outcome=0.0)
    assert a == b
    assert _row_json(a) != _row_json(b)


def test_verified_raw_cache_preserves_all_bar_fields_and_rejects_invalid_ohlcv(tmp_path):
    import hashlib
    import json

    from autoresearch.improvements.raw_cache import FIELDS, RawMinuteCache

    observed = list(bars(80))
    stamps = np.array(
        [int((START + dt.timedelta(minutes=i)).timestamp()) for i in range(390)], dtype=np.int64
    )
    matrix = np.full((390, 1, 5), np.nan, dtype=np.float64)
    for i, b in enumerate(observed):
        matrix[i, 0] = (b.open, b.high, b.low, b.close, b.volume)
    np.save(tmp_path / "2026-stamps.npy", stamps)
    np.save(tmp_path / "2026-bars.npy", matrix)

    def save_manifest():
        manifest = dict(
            schema="verified-raw-minute-cache/1",
            symbols=["SPY"],
            minute_partitions={},
            columns=FIELDS,
            files={
                p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob("*.npy")
            },
        )
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    save_manifest()
    cached = list(RawMinuteCache(tmp_path, ("SPY",), {}).batches(START.date(), START.date()))
    assert len(cached) == 390
    for i, batch in enumerate(cached):
        assert batch.start == START + dt.timedelta(minutes=i)
        assert batch.close_at == batch.start + dt.timedelta(minutes=1, seconds=1)
        assert batch.session_close == START + dt.timedelta(minutes=390)
        assert batch.bars == ((observed[i],) if i < len(observed) else ())
    matrix[0, 0, 1] = 1.0
    np.save(tmp_path / "2026-bars.npy", matrix)
    save_manifest()
    with pytest.raises(ValueError, match="OHLCV invalid"):
        list(RawMinuteCache(tmp_path, ("SPY",), {}).batches(START.date(), START.date()))
