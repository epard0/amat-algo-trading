"""Read-only coverage diagnostics for the existing strict intraday grid."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.shadow_campaign import _immutable
from autoresearch.genetic.shadow_intraday import _grid, _stamp, _validate, load_intraday

SCHEMA = "shadow-intraday-quality/1"
MAX_GAP_EXAMPLES = 10


def _longest(flags: list[bool]) -> int:
    longest = current = 0
    for complete in flags:
        current = current + 1 if complete else 0
        longest = max(longest, current)
    return longest


def intraday_quality(
    payload: dict[str, Any],
    timeframe: str,
    as_of: dt.datetime,
    calibration_end: dt.date,
) -> dict[str, Any]:
    """Describe missing constituents without filling or changing aggregation.

    Minute counts cover only closed, full intervals on the shared XNYS grid.
    Thus extended hours, partial current intervals and the hourly 30-minute tail
    never count as gaps. Runs count adjacent *grid bars*, including across session
    boundaries, matching feature lookbacks. Calibration includes all bars whose
    session date is on or before ``calibration_end``.
    """
    _validate(payload)
    grid, omitted = _grid(payload, timeframe, as_of)
    width = 15 if timeframe == "15m" else 60
    calibration = [end.date() <= calibration_end for _, end in grid]
    minute_sets: dict[str, set[dt.datetime]] = {symbol: set() for symbol in payload["symbols"]}
    for record in payload["minute_records"]:
        minute_sets[record["symbol"]].add(_stamp(record["t"]))
    expected = [[start + dt.timedelta(minutes=i) for i in range(width)] for start, _ in grid]
    assets = []
    for symbol, available in minute_sets.items():
        flags: list[bool] = []
        missing_count = calibration_missing = 0
        gaps: list[dict[str, Any]] = []
        gap_count = 0
        gap_start: dt.datetime | None = None
        gap_last: dt.datetime | None = None
        gap_size = 0

        def finish_gap(examples: list[dict[str, Any]] = gaps) -> None:
            nonlocal gap_start, gap_last, gap_size, gap_count
            if gap_start is not None and gap_last is not None:
                gap_count += 1
                if len(examples) < MAX_GAP_EXAMPLES:
                    examples.append(
                        {
                            "first_missing": gap_start.isoformat(),
                            "last_missing": gap_last.isoformat(),
                            "minutes": gap_size,
                        }
                    )
            gap_start = gap_last = None
            gap_size = 0

        latest_missing: list[str] = []
        for index, stamps in enumerate(expected):
            missing = [stamp for stamp in stamps if stamp not in available]
            flags.append(not missing)
            missing_count += len(missing)
            if calibration[index]:
                calibration_missing += len(missing)
            if index == len(expected) - 1:
                latest_missing = [stamp.isoformat() for stamp in missing]
            for stamp in missing:
                if gap_last is not None and stamp != gap_last + dt.timedelta(minutes=1):
                    finish_gap()
                if gap_start is None:
                    gap_start = stamp
                gap_last = stamp
                gap_size += 1
        finish_gap()
        calibration_flags = [
            flag for flag, included in zip(flags, calibration, strict=True) if included
        ]
        expected_count = len(grid) * width
        calibration_expected = len(calibration_flags) * width
        assets.append(
            {
                "symbol": symbol,
                "expected_minutes": expected_count,
                "present_minutes": expected_count - missing_count,
                "missing_minutes": missing_count,
                "complete_intervals": sum(flags),
                "incomplete_intervals": len(flags) - sum(flags),
                "longest_contiguous_complete_run": _longest(flags),
                "calibration": {
                    "expected_minutes": calibration_expected,
                    "present_minutes": calibration_expected - calibration_missing,
                    "missing_minutes": calibration_missing,
                    "complete_intervals": sum(calibration_flags),
                    "incomplete_intervals": len(calibration_flags) - sum(calibration_flags),
                    "longest_contiguous_complete_run": _longest(calibration_flags),
                },
                "latest_interval": {
                    "start": grid[-1][0].isoformat(),
                    "end": grid[-1][1].isoformat(),
                    "complete": flags[-1],
                    "missing_timestamps": latest_missing,
                }
                if grid
                else None,
                "missing_gap_count": gap_count,
                "missing_gap_examples": gaps,
                "missing_gap_examples_truncated": gap_count > len(gaps),
            }
        )
    return {
        "schema_version": SCHEMA,
        "receipt_payload_sha256": digest_json(payload),
        "timeframe": timeframe,
        "as_of": as_of.astimezone(dt.UTC).isoformat(),
        "effective_cutoff": min(as_of, _stamp(payload["collected_at"])).isoformat(),
        "calibration_end": calibration_end.isoformat(),
        "calendar": "XNYS",
        "interval_count": len(grid),
        "calibration_interval_count": sum(calibration),
        "minute_count_scope": "closed full regular-session intervals only",
        "run_unit": "adjacent grid bars, including across session boundaries",
        "missing_minute_policy": "whole interval unavailable; no imputation",
        "omitted_final_minutes_per_session": omitted,
        "max_gap_examples_per_asset": MAX_GAP_EXAMPLES,
        "assets": assets,
    }


def audit_intraday(
    data: Path,
    calibration_end: dt.date,
    *,
    as_of: dt.datetime,
    output: Path,
) -> dict[str, Any]:
    """Write an immutable diagnostic receipt for both supported intraday views."""
    data, output = Path(data), Path(output)
    if data.resolve() == output.resolve() or output.suffix.lower() != ".json":
        raise DataIntegrityError("intraday audit requires a distinct JSON output path")
    if as_of.tzinfo is None or as_of.utcoffset() is None or as_of > dt.datetime.now(dt.UTC):
        raise DataIntegrityError("intraday audit requires an aware nonfuture cutoff")
    payload = load_intraday(data)
    start, end = dt.date.fromisoformat(payload["start"]), dt.date.fromisoformat(payload["end"])
    effective = min(as_of, _stamp(payload["collected_at"]))
    if not start <= calibration_end <= end or effective.date() <= calibration_end:
        raise DataIntegrityError("calibration must be within coverage and precede observation")
    views = [intraday_quality(payload, frame, as_of, calibration_end) for frame in ("15m", "1h")]
    if any(view["interval_count"] <= view["calibration_interval_count"] for view in views):
        raise DataIntegrityError("intraday audit requires closed intervals after calibration")
    result = {
        "schema_version": "shadow-intraday-audit/1",
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "freshness_scope": "receipt coverage only; cannot establish freshness beyond coverage_end",
        "source": {
            "path": str(data.resolve()),
            "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
        },
        "receipt_payload_sha256": digest_json(payload),
        "provider": payload["provider"],
        "feed": payload["feed"],
        "adjustment": payload["adjustment"],
        "coverage_start": payload["start"],
        "coverage_end": payload["end"],
        "collected_at": payload["collected_at"],
        "as_of": as_of.astimezone(dt.UTC).isoformat(),
        "calibration_end": calibration_end.isoformat(),
        "views": views,
    }
    envelope = {"payload": result, "payload_sha256": digest_json(result)}
    _immutable(output, envelope)
    return envelope
