"""Duration screening preserves independent history and prefix causality."""

import datetime as dt
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from amat.condition_screen import screen_conditions
from amat.config.loading import load_strategy_config


@pytest.fixture
def source(tmp_path, project_root, monkeypatch):
    from amat import condition_screen

    dates = [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(450)]
    rng = np.random.default_rng(17)
    data = []
    for symbol, first in [("SPY", 0), ("QQQ", 0), ("LATE", 350)]:
        prices = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.002, len(dates))))
        for index in range(first, len(dates)):
            close = float(prices[index])
            data.append(
                {
                    "symbol": symbol,
                    "session_date": dates[index],
                    "raw_open": close,
                    "raw_high": close * 1.01,
                    "raw_low": close * 0.99,
                    "raw_close": close,
                    "adjusted_close": close,
                    "raw_volume": 1_000_000.0,
                }
            )
    pl.DataFrame(data).write_parquet(tmp_path / "daily.parquet")
    monkeypatch.setattr(
        condition_screen,
        "load_manifest",
        lambda _: SimpleNamespace(
            last_session=dates[-1], symbols=("SPY", "QQQ", "LATE"), snapshot_id="fixture"
        ),
    )
    config = load_strategy_config(
        project_root / "configs/strategy/amat_v13_return_target.toml"
    ).strategy
    return tmp_path, config, dates


def test_late_listing_does_not_shorten_other_assets_history(source):
    path, config, _ = source
    result = screen_conditions(config, path, condition_sessions=5)
    assert {a["symbol"] for a in result["assets"]} == {"SPY", "QQQ"}
    assert result["scanned_symbols"] == 3
    assert all(a["qualifying_sessions"] >= 5 for a in result["assets"])
    assert any(a["symbol"] == "LATE" and "warm-up" in a["reason"] for a in result["excluded"])
    assert "past" in result["duration_scope"] and "discovery_only" in result["selection_scope"]


def test_future_price_and_volume_changes_cannot_change_past_screen(source):
    path, config, dates = source
    before = screen_conditions(config, path, session=dates[400], condition_sessions=5)
    data = pl.read_parquet(path / "daily.parquet").with_columns(
        [
            pl.when(pl.col("session_date") > dates[400])
            .then(pl.col(c) * 100)
            .otherwise(pl.col(c))
            .alias(c)
            for c in [
                "raw_open",
                "raw_high",
                "raw_low",
                "raw_close",
                "adjusted_close",
                "raw_volume",
            ]
        ]
    )
    data.write_parquet(path / "daily.parquet")
    after = screen_conditions(config, path, session=dates[400], condition_sessions=5)
    assert before == after


def test_missing_session_restarts_only_that_assets_warmup(source):
    path, config, dates = source
    data = pl.read_parquet(path / "daily.parquet").filter(
        ~((pl.col("symbol") == "QQQ") & (pl.col("session_date") == dates[380]))
    )
    data.write_parquet(path / "daily.parquet")
    result = screen_conditions(config, path)
    assert [a["symbol"] for a in result["assets"]] == ["SPY"]
    assert any(a["symbol"] == "QQQ" and "contiguous" in a["reason"] for a in result["excluded"])


def test_liquidity_filter_uses_completed_data_and_never_invents_matches(source):
    path, config, _ = source
    assert not screen_conditions(config, path, minimum_dollar_volume=1e15)["assets"]


@pytest.mark.parametrize("duration", [0, -1, 253])
def test_duration_is_bounded(source, duration):
    path, config, _ = source
    with pytest.raises(ValueError, match="sessions"):
        screen_conditions(config, path, condition_sessions=duration)
