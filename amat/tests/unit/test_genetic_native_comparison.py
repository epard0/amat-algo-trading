"""Native intervals remain distinct from the strict session-open contract."""

import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.native_comparison import audit_native_intraday, compare_native_intraday
from autoresearch.genetic.native_intraday import SCHEMA, TIMESTAMP_SEMANTICS
from autoresearch.genetic.shadow_intraday import SCHEMA as MINUTE_SCHEMA

NOW = dt.datetime(2025, 11, 4, tzinfo=dt.UTC)
CALIBRATION = dt.date(2025, 10, 31)


def receipts(frame: str = "15m") -> tuple[dict[str, Any], dict[str, Any]]:
    width = 15 if frame == "15m" else 60
    minute_records, native_records = [], []
    for month, day, hour in ((10, 31, 13), (11, 3, 14)):
        start = dt.datetime(2025, month, day, hour, 30, tzinfo=dt.UTC)
        for i in range(390):
            minute_records.append(
                {
                    "symbol": "SPY",
                    "t": (start + dt.timedelta(minutes=i)).isoformat(),
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.0,
                    "c": 100.0,
                    "v": 1.0,
                }
            )
        starts = range(0, 390, 15) if width == 15 else range(30, 390, 60)
        for i in starts:
            native_records.append(
                {
                    "symbol": "SPY",
                    "t": (start + dt.timedelta(minutes=i)).isoformat(),
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.0,
                    "c": 100.0,
                    "v": float(width),
                }
            )
    common = {
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "all",
        "symbols": ["SPY"],
        "start": "2025-10-31",
        "end": "2025-11-03",
        "collected_at": NOW.isoformat(),
    }
    minute = {**common, "schema_version": MINUTE_SCHEMA, "minute_records": minute_records}
    native = {
        **common,
        "schema_version": SCHEMA,
        "native_records": native_records,
        "timeframe": frame,
        "provider_timeframe": "15Min" if width == 15 else "1Hour",
        "alignment": "unknown",
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
    }
    return native, minute


def compare(native: dict[str, Any], minute: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return compare_native_intraday(
        native,
        minute,
        as_of=kwargs.get("as_of", NOW),
        calibration_end=kwargs.get("calibration_end", CALIBRATION),
    )


@pytest.mark.parametrize(
    "frame,count,first,last", [("15m", 52, "13:30", "21:00"), ("1h", 12, "14:00", "21:00")]
)
def test_exact_parity_dst_calendar_and_calibration(
    frame: str, count: int, first: str, last: str
) -> None:
    native, minute = receipts(frame)
    result = compare(native, minute)
    asset = result["assets"][0]
    assert result["interval_count"] == count
    assert first in result["first_interval_start"]
    assert last in result["latest_interval_end"]
    assert asset["exact_parity_intervals"] == count
    assert asset["calibration"]["longest_native_contiguous_run"] == count // 2
    assert asset["parity_violations"] == 0
    if frame == "15m":
        assert (
            asset["session_open_hourly_component_coverage"]["all_four_native_components_present"]
            == 12
        )
    json.dumps(result, allow_nan=False)


def test_sparse_minutes_compare_available_constituents_without_fill() -> None:
    native, minute = receipts()
    minute["minute_records"].pop(1)
    native["native_records"][0]["v"] = 14
    before = copy.deepcopy(minute)
    asset = compare(native, minute)["assets"][0]
    assert asset["strict_minute_complete_intervals"] == 51
    assert asset["sparse_native_available_intervals"] == 1
    assert asset["exact_parity_intervals"] == 52
    assert minute == before


def test_missing_native_and_native_without_minutes_reported() -> None:
    native, minute = receipts()
    native["native_records"].pop(0)
    del minute["minute_records"][15:30]
    asset = compare(native, minute)["assets"][0]
    assert asset["missing_native_with_minutes"] == 1
    assert asset["native_without_minutes"] == 1
    assert asset["compared_intervals"] == 50


@pytest.mark.parametrize(
    "field,amount,violations", [("c", 0.005, 0), ("c", 0.02, 1), ("v", 0.0000005, 0), ("v", 0.1, 1)]
)
def test_declared_tolerances(field: str, amount: float, violations: int) -> None:
    native, minute = receipts()
    native["native_records"][0][field] += amount
    asset = compare(native, minute)["assets"][0]
    assert asset["parity_violations"] == violations
    assert asset["exact_parity_intervals"] == 51
    assert asset["maximum_absolute_errors"][field] == pytest.approx(amount)


def test_cutoff_ignores_partial_and_future_native_intervals() -> None:
    native, minute = receipts()
    result = compare(native, minute, as_of=dt.datetime(2025, 11, 3, 14, 50, tzinfo=dt.UTC))
    assert result["interval_count"] == 27
    assert result["assets"][0]["excluded_native_records"] == 25
    assert result["assets"][0]["calibration"]["native_present_intervals"] == 26


def test_whole_hour_native_alignment_rejects_session_open_relabel() -> None:
    native, minute = receipts("1h")
    native["native_records"][0]["t"] = "2025-10-31T13:30:00Z"
    with pytest.raises(DataIntegrityError, match="clock alignment"):
        compare(native, minute)


def test_hourly_boundary_straddler_is_excluded() -> None:
    native, minute = receipts("1h")
    native["native_records"].append(dict(native["native_records"][0], t="2025-10-31T13:00:00Z"))
    asset = compare(native, minute)["assets"][0]
    assert asset["excluded_native_records"] == 1
    assert asset["exact_parity_intervals"] == 12


def test_calibration_prefix_not_changed_by_later_native_gap() -> None:
    native, minute = receipts()
    baseline = compare(native, minute)["assets"][0]["calibration"]
    native["native_records"].pop()
    assert compare(native, minute)["assets"][0]["calibration"] == baseline


@pytest.mark.parametrize(
    "key,value", [("start", "2025-10-30"), ("symbols", ["SPY", "QQQ"]), ("end", "2025-11-04")]
)
def test_incompatible_contracts_refused(key: str, value: Any) -> None:
    native, minute = receipts()
    minute[key] = value
    with pytest.raises(DataIntegrityError, match="contract mismatch"):
        compare(native, minute)


def test_early_close_full_intervals_only() -> None:
    native, minute = receipts("1h")
    for payload in (native, minute):
        payload.update(start="2025-11-26", end="2025-11-28", collected_at="2025-11-29T00:00:00Z")
    minute["minute_records"] = []
    native["native_records"] = [dict(native["native_records"][0], t="2025-11-28T15:00:00Z")]
    result = compare(
        native,
        minute,
        as_of=dt.datetime(2025, 11, 29, tzinfo=dt.UTC),
        calibration_end=dt.date(2025, 11, 26),
    )
    assert result["interval_count"] == 9
    assert result["latest_interval_end"] == "2025-11-28T18:00:00+00:00"


def test_wrapper_immutable_and_bound(tmp_path: Path) -> None:
    native, minute = receipts()
    paths = [tmp_path / name for name in ("native.json", "minute.json", "report.json")]
    for path, payload in zip(paths[:2], (native, minute), strict=True):
        path.write_text(json.dumps({"payload": payload, "payload_sha256": digest_json(payload)}))
    result = audit_native_intraday(paths[0], paths[1], CALIBRATION, as_of=NOW, output=paths[2])
    assert result["payload_sha256"] == digest_json(result["payload"])
    assert (
        audit_native_intraday(paths[0], paths[1], CALIBRATION, as_of=NOW, output=paths[2]) == result
    )
    with pytest.raises(DataIntegrityError, match="distinct"):
        audit_native_intraday(paths[0], paths[1], CALIBRATION, as_of=NOW, output=paths[0])


@pytest.mark.parametrize(
    "as_of",
    [
        dt.datetime(2025, 11, 4, tzinfo=dt.UTC).replace(tzinfo=None),
        dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
    ],
)
def test_invalid_cutoff(as_of: dt.datetime) -> None:
    native, minute = receipts()
    with pytest.raises(DataIntegrityError, match="nonfuture"):
        compare(native, minute, as_of=as_of)
