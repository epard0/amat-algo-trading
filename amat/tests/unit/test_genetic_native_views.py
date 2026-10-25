"""Native component research contract preserves calendar and missingness."""

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from typing import Any

import numpy as np
import pytest

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.native_intraday import SCHEMA, TIMESTAMP_SEMANTICS
from autoresearch.genetic.native_views import (
    SOURCE_PREFIX,
    native_panels,
    native_view_contract,
    native_view_metadata,
)

NOW = dt.datetime(2025, 11, 29, tzinfo=dt.UTC)


def receipt(days: tuple[tuple[str, int, int], ...] = (("2025-11-28", 14, 14),)) -> dict[str, Any]:
    records = []
    for day, hour, count in days:
        first = dt.datetime.fromisoformat(day).replace(hour=hour, minute=30, tzinfo=dt.UTC)
        for index in range(count):
            records.append(
                {
                    "symbol": "IEF",
                    "t": (first + dt.timedelta(minutes=15 * index)).isoformat(),
                    "o": 100 + index,
                    "h": 102 + index,
                    "l": 99 + index,
                    "c": 101 + index,
                    "v": 10 + index,
                }
            )
    return {
        "schema_version": SCHEMA,
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "all",
        "timeframe": "15m",
        "provider_timeframe": "15Min",
        "alignment": "unknown",
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "symbols": ["IEF", "DBC"],
        "start": days[0][0],
        "end": days[-1][0],
        "collected_at": NOW.isoformat(),
        "native_records": records,
    }


def test_early_close_hourly_all_four_components_and_no_execution_prices() -> None:
    payload = receipt()
    before = deepcopy(payload)
    views = native_panels(payload, NOW)
    fifteen, hour = views["15m"], views["1h"]
    assert len(fifteen) == 14
    assert len(hour) == 3
    assert hour.sessions[0] == dt.datetime(2025, 11, 28, 15, 30, tzinfo=dt.UTC)
    assert hour.sessions[-1] == dt.datetime(2025, 11, 28, 17, 30, tzinfo=dt.UTC)
    assert hour.adjusted_opens[0, 0] == 100
    assert hour.adjusted_highs[0, 0] == 105
    assert hour.adjusted_lows[0, 0] == 99
    assert hour.adjusted_closes[0, 0] == 104
    assert hour.session_volumes[0, 0] == 46
    assert np.isnan(hour.adjusted_closes[:, 1]).all()
    assert payload == before
    for frame, panel in views.items():
        assert panel.source.startswith(f"{SOURCE_PREFIX}{frame}:contract=")
        assert np.isnan(panel.raw_closes).all()
        assert np.isnan(panel.window_vwaps).all()
        assert (panel.window_volumes == 0).all()
        for array in (
            *panel.candle_inputs(),
            panel.session_volumes,
            panel.raw_closes,
            panel.window_vwaps,
            panel.window_volumes,
        ):
            assert not array.flags.writeable


def test_absent_native_component_preserves_row_and_invalidates_hour() -> None:
    payload = receipt()
    payload["native_records"].pop(1)
    views = native_panels(payload, NOW)
    assert len(views["15m"]) == 14
    assert len(views["1h"]) == 3
    for frame, index in (("15m", 1), ("1h", 0)):
        panel = views[frame]
        for array in (*panel.candle_inputs(), panel.session_volumes):
            assert np.isnan(array[index, 0])
    assert np.isfinite(views["1h"].adjusted_closes[1:, 0]).all()


def test_sparse_minute_availability_is_never_inferred_or_required() -> None:
    payload = receipt()
    payload["native_records"][0]["v"] = 1
    views = native_panels(payload, NOW)
    assert np.isfinite(views["15m"].adjusted_closes[:, 0]).all()
    assert views["1h"].session_volumes[0, 0] == 37
    assert native_view_contract()["minute_completeness"] == "not asserted"


def test_dst_and_full_session_axes() -> None:
    payload = receipt((("2025-10-31", 13, 26), ("2025-11-03", 14, 26)))
    views = native_panels(payload, NOW)
    assert len(views["15m"]) == 52
    assert len(views["1h"]) == 12
    assert views["1h"].sessions[0].hour == 14
    assert views["1h"].sessions[6].hour == 15
    assert views["1h"].sessions[-1].hour == 20
    assert views["1h"].sessions[-1].minute == 30


def test_extended_hours_ignored_and_missing_whole_day_preserved() -> None:
    payload = receipt((("2025-11-26", 14, 26), ("2025-11-28", 14, 14)))
    payload["native_records"] = [
        bar for bar in payload["native_records"] if "2025-11-26" in bar["t"]
    ]
    extra = dict(
        payload["native_records"][0], t="2025-11-28T13:30:00+00:00", o=999, h=999, l=999, c=999
    )
    payload["native_records"].append(extra)
    views = native_panels(payload, NOW)
    assert len(views["15m"]) == 40
    assert len(views["1h"]) == 9
    assert np.isnan(views["15m"].adjusted_closes[26:]).all()
    assert np.isnan(views["1h"].adjusted_closes[6:]).all()


def test_cutoff_prefix_invariant_and_empty_hour_allowed() -> None:
    payload = receipt()
    cutoff = dt.datetime(2025, 11, 28, 15, 0, tzinfo=dt.UTC)
    baseline = native_panels(payload, cutoff)
    assert len(baseline["15m"]) == 2
    assert len(baseline["1h"]) == 0
    for bar in payload["native_records"][2:]:
        for field in ("o", "h", "l", "c"):
            bar[field] *= 2
    changed = native_panels(payload, cutoff)
    np.testing.assert_array_equal(baseline["15m"].adjusted_closes, changed["15m"].adjusted_closes)


def test_metadata_hashes_bind_contract_source_calendar_and_cutoff() -> None:
    payload = receipt()
    metadata = native_view_metadata(payload, NOW)
    assert metadata["contract_sha256"] == digest_json(native_view_contract())
    assert metadata["native_payload_sha256"] == digest_json(payload)
    assert metadata["views"]["1h"]["omitted_final_minutes_per_session"] == {"2025-11-28": 30}
    contract = native_view_contract()
    contract["alignment"] = "modified"
    assert native_view_contract()["alignment"] == "session open"
    payload["native_records"][0]["v"] += 1
    changed = native_view_metadata(payload, NOW)
    assert changed["native_payload_sha256"] != metadata["native_payload_sha256"]
    assert changed["contract_sha256"] == metadata["contract_sha256"]
    assert changed["views"] == metadata["views"]


@pytest.mark.parametrize("mode", ["hourly", "cadence", "naive", "future", "before_open"])
def test_bad_contract_or_cutoff_refused(mode: str) -> None:
    payload = receipt()
    cutoff = NOW
    if mode == "hourly":
        payload.update(timeframe="1h", provider_timeframe="1Hour")
    elif mode == "cadence":
        payload["native_records"][0]["t"] = "2025-11-28T14:31:00Z"
    elif mode == "naive":
        cutoff = NOW.replace(tzinfo=None)
    elif mode == "future":
        cutoff = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)
    elif mode == "before_open":
        cutoff = dt.datetime(2025, 11, 28, 14, 30, tzinfo=dt.UTC)
    with pytest.raises(DataIntegrityError):
        native_panels(payload, cutoff)
