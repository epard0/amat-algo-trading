import datetime as dt
import json

import polars as pl
import pytest

from server.market import MarketData, MarketError


def snapshot(root, name, dates, closes):
    target = root / "amat/data/snapshots" / name
    target.mkdir(parents=True)
    pl.DataFrame(
        {
            "session_date": [dt.date.fromisoformat(d) for d in dates],
            "symbol": ["SPY"] * len(dates),
            "raw_open": closes,
            "raw_high": [v + 2 for v in closes],
            "raw_low": [v - 2 for v in closes],
            "raw_close": closes,
            "raw_volume": [100.0] * len(dates),
        }
    ).write_parquet(target / "daily.parquet")
    (target / "manifest.json").write_text(
        json.dumps({"last_session": "2099-01-01", "as_of": "2099-01-01"})
    )


def test_default_selection_uses_actual_bars_and_historical_quotes(tmp_path):
    snapshot(tmp_path, "zzz-old-2099-01-01", ["2026-08-01"], [90.0])
    snapshot(tmp_path, "aaa-new", ["2026-09-21", "2026-09-22"], [100.0, 102.0])
    m = MarketData(tmp_path)
    assert m.snapshots()["default_snapshot"] == "aaa-new"
    quote = m.quotes()["items"][0]
    assert quote["change"] == "2" and quote["change_percent"] == "2"
    assert quote["live"] is False and quote["bid"] is None and quote["ask"] is None
    assert quote["as_of"] == "2026-09-22"
    assert m.symbols()["items"] == ["SPY"]


def test_weekly_bars_range_normalization_and_bounds(tmp_path):
    snapshot(
        tmp_path,
        "daily",
        ["2026-09-21", "2026-09-22", "2026-09-28"],
        [100.0, 102.0, 103.0],
    )
    m = MarketData(tmp_path)
    weekly = m.bars(resolution="1w")["items"]
    assert len(weekly) == 2
    assert weekly[0]["open"] == 100 and weekly[0]["close"] == 102
    assert weekly[0]["high"] == 104 and weekly[0]["low"] == 98 and weekly[0]["volume"] == 200
    assert len(m.bars(start="2026-09-22", end="2026-09-22")["items"]) == 1
    for kwargs in [
        {"snapshot": "../../etc"},
        {"symbol": "QQQ"},
        {"limit": 2001},
        {"resolution": "1m"},
        {"start": "invalid"},
        {"start": "2026-10-01", "end": "2026-09-01"},
    ]:
        with pytest.raises(MarketError):
            m.bars(**kwargs)


def test_anomalous_source_wick_is_preserved_and_flagged(tmp_path):
    snapshot(tmp_path, "anomaly", ["2026-02-02"], [695.41])
    path = tmp_path / "amat/data/snapshots/anomaly/daily.parquet"
    pl.read_parquet(path).with_columns(pl.lit(69.005).alias("raw_low")).write_parquet(path)
    response = MarketData(tmp_path).bars()
    assert response["items"][0]["low"] == 69.005
    assert response["quality_status"] == "review_required"
    assert response["quality_warnings"][0]["kinds"] == ["extreme_intraperiod_range"]


def test_default_quotes_support_large_registered_universe_but_bound_explicit_queries(tmp_path):
    names = [f"S{i:03}" for i in range(201)]
    path = tmp_path / "amat/data/snapshots/large"
    path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": names,
            "session_date": [dt.date(2026, 10, 16)] * 201,
            "raw_open": [10.0] * 201,
            "raw_high": [11.0] * 201,
            "raw_low": [9.0] * 201,
            "raw_close": [10.0] * 201,
            "raw_volume": [100.0] * 201,
        }
    ).write_parquet(path / "daily.parquet")
    data = MarketData(tmp_path)
    assert len(data.quotes()["items"]) == 201
    with pytest.raises(MarketError, match="200"):
        data.quotes(symbols=",".join(names))


def test_five_minute_candles_aggregate_minutes_and_exclude_incomplete_bucket(tmp_path):
    root = tmp_path / "amat/data/minute/provider"
    root.mkdir(parents=True)
    times = [f"2026-10-20T12:{i:02d}:00Z" for i in range(12)]
    pl.DataFrame(
        {
            "t": times,
            "o": [100.0 + i for i in range(12)],
            "h": [102.0 + i for i in range(12)],
            "l": [99.0 + i for i in range(12)],
            "c": [101.0 + i for i in range(12)],
            "v": [10] * 12,
        }
    ).write_parquet(root / "SPY-2026.parquet")
    response = MarketData(tmp_path).bars(symbol="SPY", resolution="5m")
    assert response["live"] is False and response["resolution"] == "5m"
    assert len(response["items"]) == 2
    first = response["items"][0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (
        100,
        106,
        99,
        105,
        50,
    )
    with pytest.raises(MarketError, match="No minute history"):
        MarketData(tmp_path).bars(symbol="MISSING", resolution="5m")
