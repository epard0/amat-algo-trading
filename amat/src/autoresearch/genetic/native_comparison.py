"""Diagnostic parity of provider native bars and available minute constituents."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import exchange_calendars as xcals  # type: ignore[import-untyped]

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.intraday_quality import _longest
from autoresearch.genetic.native_intraday import load_native_intraday
from autoresearch.genetic.native_intraday import validate_native_intraday as validate_native
from autoresearch.genetic.shadow_campaign import _immutable
from autoresearch.genetic.shadow_intraday import _grid, _stamp, _validate, load_intraday

PRICE_TOLERANCE = 0.0100000001
VOLUME_TOLERANCE = 1e-6
MAX_EXAMPLES = 10
FIELDS = ("o", "h", "l", "c", "v")


def _native_grid(
    start: dt.date, end: dt.date, cutoff: dt.datetime, width: int
) -> list[tuple[dt.datetime, dt.datetime]]:
    step = dt.timedelta(minutes=width)
    schedule = xcals.get_calendar(
        "XNYS", start=str(start - dt.timedelta(days=1)), end=str(end + dt.timedelta(days=1))
    ).schedule.loc[str(start) : str(end)]
    grid: list[tuple[dt.datetime, dt.datetime]] = []
    for _, row in schedule.iterrows():
        opening, closing = row["open"].to_pydatetime(), row["close"].to_pydatetime()
        first = opening.replace(minute=(opening.minute // width) * width, second=0, microsecond=0)
        if first < opening:
            first += step
        while first + step <= min(closing, cutoff):
            grid.append((first, first + step))
            first += step
    return grid


def compare_native_intraday(  # noqa: PLR0912, PLR0915
    native_payload: dict[str, Any],
    minute_payload: dict[str, Any],
    *,
    as_of: dt.datetime,
    calibration_end: dt.date,
) -> dict[str, Any]:
    """Compare identical intervals without repairing minutes or changing policy."""
    validate_native(native_payload)
    _validate(minute_payload)
    if as_of.tzinfo is None or as_of.utcoffset() is None or as_of > dt.datetime.now(dt.UTC):
        raise DataIntegrityError("native comparison requires an aware nonfuture cutoff")
    for key in ("symbols", "start", "end", "provider", "feed", "adjustment"):
        if native_payload[key] != minute_payload[key]:
            raise DataIntegrityError(f"native/minute contract mismatch: {key}")
    start = dt.date.fromisoformat(native_payload["start"])
    end = dt.date.fromisoformat(native_payload["end"])
    cutoff = min(
        as_of, _stamp(native_payload["collected_at"]), _stamp(minute_payload["collected_at"])
    )
    if not start <= calibration_end <= end or cutoff.date() <= calibration_end:
        raise DataIntegrityError("calibration must be within coverage and precede observation")
    timeframe = native_payload["timeframe"]
    width = 15 if timeframe == "15m" else 60
    grid = _native_grid(start, end, cutoff, width)
    calibration = [first.date() <= calibration_end for first, _ in grid]
    if not grid or sum(calibration) == len(grid):
        raise DataIntegrityError("native comparison requires closed observation intervals")
    native = {}
    for record in native_payload["native_records"]:
        stamp = _stamp(record["t"])
        if stamp.second or stamp.microsecond or stamp.minute % width:
            raise DataIntegrityError("native comparison requires provider clock alignment")
        native[(record["symbol"], stamp)] = record
    minutes = {(bar["symbol"], _stamp(bar["t"])): bar for bar in minute_payload["minute_records"]}
    expected_starts = {first for first, _ in grid}
    assets = []
    for symbol in native_payload["symbols"]:
        flags, strict, sparse = [], [], []
        compared = exact = violations = without_minutes = missing_with_minutes = 0
        maximum = dict.fromkeys(FIELDS, 0.0)
        field_exact = dict.fromkeys(FIELDS, 0)
        examples: list[dict[str, Any]] = []
        for first, last in grid:
            bar = native.get((symbol, first))
            constituents = [
                minutes[(symbol, stamp)]
                for index in range(width)
                if (symbol, stamp := first + dt.timedelta(minutes=index)) in minutes
            ]
            flags.append(bar is not None)
            strict.append(len(constituents) == width)
            sparse.append(bar is not None and 0 < len(constituents) < width)
            if bar is None:
                missing_with_minutes += bool(constituents)
                continue
            if not constituents:
                without_minutes += 1
                continue
            aggregate = {
                "o": constituents[0]["o"],
                "h": max(value["h"] for value in constituents),
                "l": min(value["l"] for value in constituents),
                "c": constituents[-1]["c"],
                "v": sum(value["v"] for value in constituents),
            }
            errors = {key: abs(float(bar[key]) - float(aggregate[key])) for key in FIELDS}
            compared += 1
            exact += all(error == 0 for error in errors.values())
            for key, error in errors.items():
                maximum[key] = max(maximum[key], error)
                field_exact[key] += error == 0
            failed = any(
                error > (VOLUME_TOLERANCE if key == "v" else PRICE_TOLERANCE)
                for key, error in errors.items()
            )
            violations += failed
            if failed and len(examples) < MAX_EXAMPLES:
                examples.append(
                    {
                        "start": first.isoformat(),
                        "end": last.isoformat(),
                        "present_minutes": len(constituents),
                        "absolute_errors": errors,
                    }
                )
        calibration_flags = [
            flag for flag, included in zip(flags, calibration, strict=True) if included
        ]
        asset = {
            "symbol": symbol,
            "expected_intervals": len(grid),
            "native_present_intervals": sum(flags),
            "strict_minute_complete_intervals": sum(strict),
            "sparse_native_available_intervals": sum(sparse),
            "native_without_minutes": without_minutes,
            "missing_native_with_minutes": missing_with_minutes,
            "compared_intervals": compared,
            "exact_parity_intervals": exact,
            "exact_parity_fields": field_exact,
            "parity_violations": violations,
            "maximum_absolute_errors": maximum,
            "violation_examples": examples,
            "violation_examples_truncated": violations > MAX_EXAMPLES,
            "calibration": {
                "expected_intervals": sum(calibration),
                "native_present_intervals": sum(calibration_flags),
                "longest_native_contiguous_run": _longest(calibration_flags),
                "strict_minute_complete_intervals": sum(
                    flag and included for flag, included in zip(strict, calibration, strict=True)
                ),
            },
            "longest_native_contiguous_run": _longest(flags),
            "excluded_native_records": sum(
                key[0] == symbol and key[1] not in expected_starts for key in native
            ),
        }
        if timeframe == "15m":
            hourly_grid, _ = _grid(minute_payload, "1h", cutoff)
            component_flags = [
                all((symbol, first + dt.timedelta(minutes=15 * i)) in native for i in range(4))
                for first, _ in hourly_grid
            ]
            component_calibration = [
                flag
                for flag, (first, _) in zip(component_flags, hourly_grid, strict=True)
                if first.date() <= calibration_end
            ]
            asset["session_open_hourly_component_coverage"] = {
                "diagnostic_only": True,
                "expected_intervals": len(hourly_grid),
                "all_four_native_components_present": sum(component_flags),
                "calibration_intervals_present": sum(component_calibration),
                "longest_calibration_run": _longest(component_calibration),
                "contract": "four native 15m bars; does not assert 60 minute constituents",
            }
        assets.append(asset)
    return {
        "schema_version": "shadow-native-comparison/1",
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "timeframe": timeframe,
        "alignment": "provider clock; full intervals entirely within XNYS regular session",
        "hourly_alignment_differs_from_existing_session_open_grid": timeframe == "1h",
        "as_of": as_of.astimezone(dt.UTC).isoformat(),
        "effective_cutoff": cutoff.isoformat(),
        "calibration_end": str(calibration_end),
        "price_absolute_tolerance": PRICE_TOLERANCE,
        "volume_absolute_tolerance": VOLUME_TOLERANCE,
        "tolerance_basis": "two cent-rounded price observations; numerical volume tolerance",
        "native_payload_sha256": digest_json(native_payload),
        "minute_payload_sha256": digest_json(minute_payload),
        "missing_minute_policy": (
            "compare available constituents; never fill or modify strict policy"
        ),
        "interpretation": (
            "same-provider parity cannot establish no-trade gaps or independent vendor validation"
        ),
        "interval_count": len(grid),
        "first_interval_start": grid[0][0].isoformat(),
        "latest_interval_end": grid[-1][1].isoformat(),
        "assets": assets,
    }


def audit_native_intraday(
    native_path: Path,
    minute_path: Path,
    calibration_end: dt.date,
    *,
    as_of: dt.datetime,
    output: Path,
) -> dict[str, Any]:
    """Persist an immutable comparison with both source file hashes."""
    native_path, minute_path, output = Path(native_path), Path(minute_path), Path(output)
    if (
        output.resolve() in {native_path.resolve(), minute_path.resolve()}
        or output.suffix.lower() != ".json"
    ):
        raise DataIntegrityError("native audit requires a distinct JSON output path")
    result = compare_native_intraday(
        load_native_intraday(native_path),
        load_intraday(minute_path),
        as_of=as_of,
        calibration_end=calibration_end,
    )
    result["sources"] = {
        name: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for name, path in (("native", native_path), ("minute", minute_path))
    }
    envelope = {"payload": result, "payload_sha256": digest_json(result)}
    _immutable(output, envelope)
    return envelope
