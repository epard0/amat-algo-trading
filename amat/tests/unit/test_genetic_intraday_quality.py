"""Coverage evidence follows strict calendar aggregation without repairing gaps."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.intraday_quality import audit_intraday, intraday_quality
from autoresearch.genetic.shadow_intraday import SCHEMA, aggregate_intraday

NOW = dt.datetime(2025, 11, 4, tzinfo=dt.UTC)
CALIBRATION = dt.date(2025, 10, 31)


def receipt() -> dict[str, Any]:
    records = []
    for day, hour in ((31, 13), (3, 14)):
        month = 10 if day == 31 else 11
        start = dt.datetime(2025, month, day, hour, 30, tzinfo=dt.UTC)
        for minute in range(390):
            records.append(
                {
                    "symbol": "SPY",
                    "t": (start + dt.timedelta(minutes=minute)).isoformat(),
                    "o": 100,
                    "h": 101,
                    "l": 99,
                    "c": 100,
                    "v": 1,
                }
            )
    return {
        "schema_version": SCHEMA,
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "all",
        "symbols": ["SPY"],
        "start": "2025-10-31",
        "end": "2025-11-03",
        "collected_at": NOW.isoformat(),
        "minute_records": records,
    }


@pytest.mark.parametrize("frame,count", [("15m", 52), ("1h", 12)])
def test_dst_grid_and_calibration_boundary(frame: str, count: int) -> None:
    payload = receipt()
    report = intraday_quality(payload, frame, NOW, CALIBRATION)
    asset = report["assets"][0]
    assert report["interval_count"] == count
    assert asset["complete_intervals"] == count
    assert asset["calibration"]["complete_intervals"] == count // 2
    assert asset["calibration"]["longest_contiguous_complete_run"] == count // 2
    assert asset["longest_contiguous_complete_run"] == count
    assert asset["missing_minutes"] == 0
    assert report["calibration_interval_count"] == count // 2
    json.dumps(report, allow_nan=False)


def test_missing_minutes_boundaries_and_latest_interval_match_aggregation() -> None:
    payload = receipt()
    # Adjacent missing minutes straddle two interval rows; one gap, two bad bars.
    del payload["minute_records"][14:16]
    payload["minute_records"].pop()
    report = intraday_quality(payload, "15m", NOW, CALIBRATION)
    asset = report["assets"][0]
    assert asset["missing_minutes"] == 3
    assert asset["incomplete_intervals"] == 3
    assert asset["longest_contiguous_complete_run"] == 49
    assert asset["calibration"]["longest_contiguous_complete_run"] == 24
    assert asset["latest_interval"]["missing_timestamps"] == ["2025-11-03T20:59:00+00:00"]
    assert asset["missing_gap_examples"][0]["minutes"] == 2
    assert asset["missing_gap_count"] == 2
    panel = aggregate_intraday(payload, "15m", NOW)
    assert np.isfinite(panel.adjusted_closes).sum() == asset["complete_intervals"]


def test_hourly_tail_and_extended_hours_never_count_as_gaps() -> None:
    payload = receipt()
    payload["minute_records"] = payload["minute_records"][:360] + payload["minute_records"][390:750]
    extra = dict(payload["minute_records"][0], t="2025-10-31T12:00:00+00:00")
    payload["minute_records"].append(extra)
    asset = intraday_quality(payload, "1h", NOW, CALIBRATION)["assets"][0]
    assert asset["expected_minutes"] == asset["present_minutes"] == 720
    assert asset["missing_minutes"] == 0
    fifteen = intraday_quality(payload, "15m", NOW, CALIBRATION)["assets"][0]
    assert fifteen["missing_minutes"] == 60


def test_cutoff_excludes_partial_interval_and_future_observations() -> None:
    payload = receipt()
    cutoff = dt.datetime(2025, 11, 3, 14, 50, tzinfo=dt.UTC)
    report = intraday_quality(payload, "15m", cutoff, CALIBRATION)
    assert report["interval_count"] == 27
    assert report["assets"][0]["latest_interval"]["end"] == "2025-11-03T14:45:00+00:00"
    assert report["assets"][0]["present_minutes"] == 405


def test_gap_examples_bounded_and_empty_grid_serializable() -> None:
    payload = receipt()
    payload["minute_records"] = payload["minute_records"][1::2]
    asset = intraday_quality(payload, "15m", NOW, CALIBRATION)["assets"][0]
    assert asset["missing_gap_count"] == 390
    assert len(asset["missing_gap_examples"]) == 10
    assert asset["missing_gap_examples_truncated"]
    empty = intraday_quality(payload, "1h", dt.datetime(2025, 10, 30, tzinfo=dt.UTC), CALIBRATION)
    assert empty["interval_count"] == 0
    assert empty["assets"][0]["latest_interval"] is None
    json.dumps(empty, allow_nan=False)


def test_early_close_tail() -> None:
    payload = receipt()
    payload.update(
        start="2025-11-28",
        end="2025-11-28",
        collected_at="2025-11-29T00:00:00+00:00",
        minute_records=[],
    )
    result = intraday_quality(payload, "1h", dt.datetime(2025, 11, 29, tzinfo=dt.UTC), CALIBRATION)
    assert result["interval_count"] == 3
    assert result["assets"][0]["expected_minutes"] == 180
    assert result["omitted_final_minutes_per_session"] == {"2025-11-28": 30}


def save(path: Path) -> None:
    payload = receipt()
    path.write_text(json.dumps({"payload": payload, "payload_sha256": digest_json(payload)}))


def test_audit_is_immutable_checksummed_and_read_only(tmp_path: Path) -> None:
    source, output = tmp_path / "source.json", tmp_path / "audit.json"
    save(source)
    before = source.read_bytes()
    report = audit_intraday(source, CALIBRATION, as_of=NOW, output=output)
    assert report["payload_sha256"] == digest_json(report["payload"])
    assert report["payload"]["diagnostic_only"]
    assert [view["timeframe"] for view in report["payload"]["views"]] == ["15m", "1h"]
    assert source.read_bytes() == before
    assert audit_intraday(source, CALIBRATION, as_of=NOW, output=output) == report
    with pytest.raises(ValueError, match="immutable"):
        audit_intraday(source, CALIBRATION, as_of=NOW - dt.timedelta(hours=1), output=output)


@pytest.mark.parametrize(
    "mode",
    ["naive", "future", "calibration_before", "no_observation", "same", "extension", "checksum"],
)
def test_audit_rejects_invalid_boundary_or_output(tmp_path: Path, mode: str) -> None:
    source, output = tmp_path / "source.json", tmp_path / "audit.json"
    save(source)
    cutoff, calibration = NOW, CALIBRATION
    if mode == "naive":
        cutoff = NOW.replace(tzinfo=None)
    elif mode == "future":
        cutoff = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    elif mode == "calibration_before":
        calibration = CALIBRATION - dt.timedelta(days=1)
    elif mode == "no_observation":
        calibration = dt.date(2025, 11, 3)
    elif mode == "same":
        output = source
    elif mode == "extension":
        output = output.with_suffix(".txt")
    elif mode == "checksum":
        source.write_text(source.read_text().replace('"v": 1', '"v": 2', 1))
    with pytest.raises(DataIntegrityError):
        audit_intraday(source, calibration, as_of=cutoff, output=output)
