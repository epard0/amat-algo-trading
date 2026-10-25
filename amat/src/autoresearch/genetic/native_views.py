"""Explicit research panels from native 15-minute aggregate components.

A native bar establishes aggregate availability, not completeness of its minute
constituents. This contract never substitutes for the strict minute policy.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.domain.types import SessionDate, Symbol
from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.native_intraday import validate_native_intraday
from autoresearch.genetic.shadow_intraday import _grid, _stamp

SOURCE_PREFIX = "alpaca:sip:all:native-15m-components:"


def native_view_contract() -> dict[str, Any]:
    """Return a fresh semantic contract; receipt and cutoff hashes are separate."""
    return {
        "schema_version": "native-research-views/1",
        "input": "native-intraday-data/1:alpaca:sip:all:15m",
        "calendar": "XNYS",
        "timeframes": ["15m", "1h"],
        "alignment": "session open",
        "timestamp_semantics": "interval end / available at",
        "hourly_rule": "four consecutive native 15m bars; all four required",
        "missing_policy": "whole interval unavailable; preserve calendar row; never fill",
        "partial_session_tail": "omit incomplete final hour",
        "extended_hours": "excluded",
        "minute_completeness": "not asserted",
        "volume_basis": "vendor all-adjusted volume",
        "raw_execution_prices_available": False,
        "authority": "research only; no promotion or execution authority",
    }


def _validated_grids(
    payload: dict[str, Any], as_of: dt.datetime
) -> dict[str, list[tuple[dt.datetime, dt.datetime]]]:
    validate_native_intraday(payload)
    if payload["timeframe"] != "15m":
        raise DataIntegrityError("native research views require native 15m components")
    if as_of.tzinfo is None or as_of.utcoffset() is None or as_of > dt.datetime.now(dt.UTC):
        raise DataIntegrityError("native research views require an aware nonfuture cutoff")
    for bar in payload["native_records"]:
        if _stamp(bar["t"]).minute % 15:
            raise DataIntegrityError("native 15m components must be quarter-hour aligned")
    grids = {timeframe: _grid(payload, timeframe, as_of)[0] for timeframe in ("15m", "1h")}
    if not grids["15m"]:
        raise DataIntegrityError("no completed native research intervals")
    return grids


def native_view_metadata(payload: dict[str, Any], as_of: dt.datetime) -> dict[str, Any]:
    """Bind explicit semantics, source receipt, cutoff and completed calendars."""
    grids = _validated_grids(payload, as_of)
    contract = native_view_contract()
    return {
        "contract": contract,
        "contract_sha256": digest_json(contract),
        "native_payload_sha256": digest_json(payload),
        "as_of": as_of.astimezone(dt.UTC).isoformat(),
        "effective_cutoff": min(as_of, _stamp(payload["collected_at"])).isoformat(),
        "views": {
            timeframe: {
                "interval_count": len(grid),
                "first_interval_start": grid[0][0].isoformat() if grid else None,
                "latest_interval_end": grid[-1][1].isoformat() if grid else None,
                "calendar_sha256": digest_json(
                    [[first.isoformat(), last.isoformat()] for first, last in grid]
                ),
                "omitted_final_minutes_per_session": _grid(payload, timeframe, as_of)[1],
            }
            for timeframe, grid in grids.items()
        },
    }


def native_panels(payload: dict[str, Any], as_of: dt.datetime) -> dict[str, MarketPanel]:
    """Build distinct adjusted research panels; missing components propagate.

    Fifteen-minute values are vendor aggregates. Hourly values combine exactly
    four of those aggregates on the 09:30 exchange-session grid. Calendar rows
    remain present even when every asset is missing. Direct native hourly bars
    are deliberately unsupported because their clock alignment differs.
    """
    grids = _validated_grids(payload, as_of)
    lookup = {(bar["symbol"], _stamp(bar["t"])): bar for bar in payload["native_records"]}
    symbols = tuple(payload["symbols"])
    contract_hash = digest_json(native_view_contract())
    result = {}
    for timeframe, grid in grids.items():
        values = np.full((len(grid), len(symbols), 5), np.nan)
        for row, (first, last) in enumerate(grid):
            stamps = [
                first + dt.timedelta(minutes=offset)
                for offset in range(0, int((last - first).total_seconds() // 60), 15)
            ]
            for column, symbol in enumerate(symbols):
                if any((symbol, stamp) not in lookup for stamp in stamps):
                    continue
                bars = [lookup[(symbol, stamp)] for stamp in stamps]
                values[row, column] = [
                    bars[0]["o"],
                    max(float(bar["h"]) for bar in bars),
                    min(float(bar["l"]) for bar in bars),
                    bars[-1]["c"],
                    sum(float(bar["v"]) for bar in bars),
                ]
        arrays = [values[:, :, index].copy() for index in range(5)]
        shape = values.shape[:2]
        raw, window_vwaps, window_volume = (
            np.full(shape, np.nan),
            np.full(shape, np.nan),
            np.zeros(shape),
        )
        for array in [*arrays, raw, window_vwaps, window_volume]:
            array.flags.writeable = False
        result[timeframe] = MarketPanel(
            sessions=cast(tuple[SessionDate, ...], tuple(last for _, last in grid)),
            symbols=tuple(Symbol(symbol) for symbol in symbols),
            adjusted_opens=arrays[0],
            adjusted_highs=arrays[1],
            adjusted_lows=arrays[2],
            adjusted_closes=arrays[3],
            session_volumes=arrays[4],
            raw_closes=raw,
            window_vwaps=window_vwaps,
            window_volumes=window_volume,
            source=f"{SOURCE_PREFIX}{timeframe}:contract={contract_hash}",
        )
    return result
