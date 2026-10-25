"""Calendar aligned minute aggregation and immutable adjusted receipts."""

from __future__ import annotations

import datetime as dt
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from amat.errors import DataIntegrityError
from autoresearch.genetic.shadow_intraday import (
    aggregate_intraday,
    aggregation_metadata,
    fetch_intraday,
    load_intraday,
)

UTC = dt.UTC
DAY = dt.date(2025, 11, 28)
NOW = dt.datetime(2025, 11, 28, 18, 1, tzinfo=UTC)


class Feed:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def _paginate(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.params = params
        assert path == "/v2/stocks/bars"
        return [
            {
                "bars": {
                    "SPY": [
                        {
                            "t": (
                                dt.datetime(2025, 11, 28, 14, 30, tzinfo=UTC)
                                + dt.timedelta(minutes=i)
                            ).isoformat(),
                            "o": 100 + i,
                            "h": 102 + i,
                            "l": 99 + i,
                            "c": 101 + i,
                            "v": 10,
                        }
                        for i in range(210)
                    ]
                }
            }
        ]


@pytest.fixture
def payload(tmp_path: Path) -> dict[str, Any]:
    return fetch_intraday(tmp_path / "bars.json", ("SPY",), DAY, DAY, client=Feed(), now=NOW)


def test_receipt_is_immutable_checksum_and_adjusted(tmp_path: Path) -> None:
    file = tmp_path / "bars.json"
    feed = Feed()
    payload = fetch_intraday(file, ("SPY",), DAY, DAY, client=feed, now=NOW)
    assert load_intraday(file) == payload
    assert feed.params["adjustment"] == "all"
    with pytest.raises(FileExistsError):
        fetch_intraday(file, ("SPY",), DAY, DAY, client=feed, now=NOW)
    saved = json.loads(file.read_text())
    saved["payload"]["minute_records"][0]["c"] += 1
    file.write_text(json.dumps(saved))
    with pytest.raises(DataIntegrityError, match="checksum"):
        load_intraday(file)


def test_full_intervals_early_close_and_truthful_raw_context(payload: dict[str, Any]) -> None:
    hourly = aggregate_intraday(payload, "1h", NOW)
    fifteen = aggregate_intraday(payload, "15m", NOW)
    assert len(hourly.sessions) == 3
    assert len(fifteen.sessions) == 14
    assert hourly.sessions[0] == dt.datetime(2025, 11, 28, 15, 30, tzinfo=UTC)
    assert hourly.sessions[-1] == dt.datetime(2025, 11, 28, 17, 30, tzinfo=UTC)
    assert hourly.adjusted_opens[0, 0] == 100
    assert hourly.adjusted_highs[0, 0] == 161
    assert hourly.adjusted_lows[0, 0] == 99
    assert hourly.adjusted_closes[0, 0] == 160
    assert hourly.session_volumes[0, 0] == 600
    assert np.isnan(hourly.raw_closes).all()
    assert np.isnan(hourly.window_vwaps).all()
    assert (hourly.window_volumes == 0).all()
    assert not hourly.adjusted_closes.flags.writeable
    assert aggregation_metadata(payload, "1h", NOW)["omitted_final_minutes_per_session"] == {
        str(DAY): 30
    }


def test_missing_minute_keeps_interval_and_never_compresses(payload: dict[str, Any]) -> None:
    payload["minute_records"].pop(1)
    result = aggregate_intraday(payload, "15m", NOW)
    assert len(result.sessions) == 14
    assert np.isnan(result.adjusted_closes[0, 0])
    assert np.isnan(result.session_volumes[0, 0])
    assert np.isfinite(result.adjusted_closes[1:]).all()


def test_future_minutes_cannot_change_closed_prefix(payload: dict[str, Any]) -> None:
    cutoff = dt.datetime(2025, 11, 28, 15, 0, tzinfo=UTC)
    before = aggregate_intraday(payload, "15m", cutoff)
    changed = deepcopy(payload)
    for bar in changed["minute_records"][30:]:
        for key in ("o", "h", "l", "c"):
            bar[key] *= 2
    after = aggregate_intraday(changed, "15m", cutoff)
    assert len(before.sessions) == 2
    np.testing.assert_array_equal(before.adjusted_closes, after.adjusted_closes)


@pytest.mark.parametrize(
    "mode", ["duplicate", "nan", "range", "negative_volume", "naive", "unclosed", "raw"]
)
def test_invalid_minutes_rejected(payload: dict[str, Any], mode: str) -> None:
    bar = payload["minute_records"][0]
    if mode == "duplicate":
        payload["minute_records"].append(bar.copy())
    elif mode == "nan":
        bar["o"] = float("nan")
    elif mode == "range":
        bar["h"] = 90
    elif mode == "negative_volume":
        bar["v"] = -1
    elif mode == "naive":
        bar["t"] = "2025-11-28T14:30:00"
    elif mode == "unclosed":
        bar["t"] = "2025-11-28T18:01:00+00:00"
    elif mode == "raw":
        payload["adjustment"] = "raw"
    with pytest.raises(DataIntegrityError):
        aggregate_intraday(payload, "15m", NOW)


def test_unclosed_session_not_downloaded(tmp_path: Path) -> None:
    feed = Feed()
    with pytest.raises(DataIntegrityError, match="closed"):
        fetch_intraday(
            tmp_path / "x.json", ("SPY",), DAY, DAY, client=feed, now=NOW - dt.timedelta(minutes=2)
        )
    assert not feed.params


def test_session_boundaries_dst_and_missing_whole_session(payload: dict[str, Any]) -> None:
    payload["start"] = "2025-10-31"
    payload["end"] = "2025-11-03"
    payload["collected_at"] = "2025-11-04T00:00:00+00:00"
    payload["minute_records"] = []
    result = aggregate_intraday(payload, "1h", dt.datetime(2025, 11, 4, tzinfo=UTC))
    assert len(result.sessions) == 12
    assert result.sessions[0] == dt.datetime(2025, 10, 31, 14, 30, tzinfo=UTC)
    assert result.sessions[6] == dt.datetime(2025, 11, 3, 15, 30, tzinfo=UTC)
    assert np.isnan(result.adjusted_closes).all()
