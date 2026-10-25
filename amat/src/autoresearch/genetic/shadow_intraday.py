"""Immutable adjusted minute receipts and XNYS-aligned diagnostic aggregates."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, cast

import exchange_calendars as xcals  # type: ignore[import-untyped]
import numpy as np

from amat.backtest.panel import MarketPanel
from amat.data.alpaca import AlpacaMarketData
from amat.domain.types import SessionDate, Symbol
from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json

SCHEMA = "shadow-intraday-data/1"


def _stamp(value: str) -> dt.datetime:
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise DataIntegrityError("intraday timestamps must be timezone-aware")
    return result.astimezone(dt.UTC)


def _validate(payload: dict[str, Any]) -> None:
    if (
        payload.get("schema_version") != SCHEMA
        or payload.get("provider") != "alpaca"
        or payload.get("feed") != "sip"
        or payload.get("adjustment") != "all"
    ):
        raise DataIntegrityError("unsupported intraday data contract")
    symbols = payload["symbols"]
    if not symbols or len(set(symbols)) != len(symbols):
        raise DataIntegrityError("intraday universe must be nonempty and unique")
    start, end = dt.date.fromisoformat(payload["start"]), dt.date.fromisoformat(payload["end"])
    if start > end:
        raise DataIntegrityError("intraday range is reversed")
    collected = _stamp(payload["collected_at"])
    seen = set()
    for bar in payload["minute_records"]:
        stamp = _stamp(bar["t"])
        key = (bar["symbol"], stamp)
        if key in seen or key[0] not in symbols or not start <= stamp.date() <= end:
            raise DataIntegrityError("duplicate or unexpected intraday minute")
        if stamp.second or stamp.microsecond or stamp + dt.timedelta(minutes=1) > collected:
            raise DataIntegrityError("unaligned or unclosed intraday minute")
        seen.add(key)
        o, h, low, c, v = (float(bar[key]) for key in ("o", "h", "l", "c", "v"))
        if (
            not np.isfinite([o, h, low, c, v]).all()
            or min(o, h, low, c) <= 0
            or v < 0
            or h < max(o, c, low)
            or low > min(o, c, h)
        ):
            raise DataIntegrityError("invalid intraday OHLCV")


def fetch_intraday(
    output: Path,
    symbols: tuple[str, ...],
    start: dt.date,
    end: dt.date,
    *,
    client: AlpacaMarketData | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Download read-only SIP all-adjusted minutes to a new immutable JSON file.

    The collection clock is sampled before the request and excludes any later
    minute. Only fully closed exchange sessions may be requested. Vendor volume
    shares the vendor's all-adjusted basis; no raw execution prices are inferred.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    moment = now or dt.datetime.now(dt.UTC)
    if moment.tzinfo is None or moment.utcoffset() is None or start > end:
        raise DataIntegrityError("intraday collection requires aware clock and ordered dates")
    schedule = xcals.get_calendar(
        "XNYS", start=str(start - dt.timedelta(days=1)), end=str(end + dt.timedelta(days=1))
    ).schedule.loc[str(start) : str(end)]
    if schedule.empty or schedule.iloc[-1]["close"].to_pydatetime() > moment:
        raise DataIntegrityError("intraday download requires closed exchange sessions")
    if not symbols or len(set(symbols)) != len(symbols):
        raise DataIntegrityError("intraday universe must be nonempty and unique")
    api = client or AlpacaMarketData(feed="sip")
    records = []
    for page in api._paginate(
        "/v2/stocks/bars",
        {
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "feed": "sip",
            "adjustment": "all",
            "start": f"{start}T00:00:00Z",
            "end": f"{end}T23:59:59Z",
            "limit": 5000,
            "sort": "asc",
        },
    ):
        for symbol, bars in page.get("bars", {}).items():
            for bar in bars:
                stamp = _stamp(bar["t"])
                if stamp + dt.timedelta(minutes=1) > moment:
                    continue
                records.append(
                    {
                        "symbol": symbol,
                        "t": stamp.isoformat(),
                        **{key: float(bar[key]) for key in ("o", "h", "l", "c", "v")},
                    }
                )
    if not records:
        raise DataIntegrityError("intraday provider returned no bars")
    payload = {
        "schema_version": SCHEMA,
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "all",
        "symbols": list(symbols),
        "start": str(start),
        "end": str(end),
        "collected_at": moment.astimezone(dt.UTC).isoformat(),
        "minute_records": records,
        "timestamp_semantics": "interval start; available after one minute",
        "volume_basis": "vendor all-adjusted volume",
    }
    _validate(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(
            {"payload": payload, "payload_sha256": digest_json(payload)},
            handle,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")
    return payload


def load_intraday(path: Path) -> dict[str, Any]:
    """Verify receipt checksum and contract before interpreting minutes."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        set(envelope) != {"payload", "payload_sha256"}
        or digest_json(envelope["payload"]) != envelope["payload_sha256"]
    ):
        raise DataIntegrityError("intraday receipt checksum mismatch")
    payload: dict[str, Any] = envelope["payload"]
    _validate(payload)
    return payload


def _grid(
    payload: dict[str, Any], timeframe: str, as_of: dt.datetime
) -> tuple[list[tuple[dt.datetime, dt.datetime]], dict[str, int]]:
    if timeframe not in {"15m", "1h"} or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise DataIntegrityError("intraday aggregation requires 15m/1h and an aware cutoff")
    width = 15 if timeframe == "15m" else 60
    start, end = dt.date.fromisoformat(payload["start"]), dt.date.fromisoformat(payload["end"])
    schedule = xcals.get_calendar(
        "XNYS", start=str(start - dt.timedelta(days=1)), end=str(end + dt.timedelta(days=1))
    ).schedule.loc[str(start) : str(end)]
    grid = []
    omitted = {}
    cutoff = min(as_of, _stamp(payload["collected_at"]))
    for day, row in schedule.iterrows():
        opening, close = row["open"].to_pydatetime(), row["close"].to_pydatetime()
        minutes = int((close - opening).total_seconds() // 60)
        omitted[str(day.date())] = minutes % width
        for offset in range(0, minutes - width + 1, width):
            first = opening + dt.timedelta(minutes=offset)
            last = first + dt.timedelta(minutes=width)
            if last <= cutoff:
                grid.append((first, last))
    return grid, omitted


def aggregation_metadata(
    payload: dict[str, Any], timeframe: str, as_of: dt.datetime
) -> dict[str, Any]:
    grid, omitted = _grid(payload, timeframe, as_of)
    return {
        "timeframe": timeframe,
        "interval_count": len(grid),
        "timestamp_semantics": "interval end / available at",
        "calendar": "XNYS",
        "alignment": "session open",
        "omitted_final_minutes_per_session": omitted,
        "missing_minute_policy": "whole interval unavailable; preserve interval row",
        "volume_basis": "vendor all-adjusted volume",
        "raw_execution_prices_available": False,
    }


def aggregate_intraday(payload: dict[str, Any], timeframe: str, as_of: dt.datetime) -> MarketPanel:
    """Aggregate complete regular-session intervals without bridging gaps.

    Missing any constituent minute makes that asset's entire interval unavailable.
    The interval remains on the shared calendar. Hourly bars start at 09:30 local;
    the last 30 minutes of normal and early-close sessions are explicitly omitted.
    Sessions are UTC interval-end datetimes for causal numerical feature kernels.
    """
    _validate(payload)
    grid, _ = _grid(payload, timeframe, as_of)
    if not grid:
        raise DataIntegrityError("no completed intraday intervals")
    symbols = tuple(payload["symbols"])
    lookup = {(bar["symbol"], _stamp(bar["t"])): bar for bar in payload["minute_records"]}
    values = np.full((len(grid), len(symbols), 5), np.nan)
    for row, (first, last) in enumerate(grid):
        stamps = [
            first + dt.timedelta(minutes=i)
            for i in range(int((last - first).total_seconds() // 60))
        ]
        for column, symbol in enumerate(symbols):
            if any((symbol, stamp) not in lookup for stamp in stamps):
                continue
            bars = [lookup[(symbol, stamp)] for stamp in stamps]
            values[row, column] = [
                bars[0]["o"],
                max(bar["h"] for bar in bars),
                min(bar["l"] for bar in bars),
                bars[-1]["c"],
                sum(bar["v"] for bar in bars),
            ]
    shape = values.shape[:2]
    arrays = [values[:, :, i].copy() for i in range(5)]
    raw, vwaps, window_volume = np.full(shape, np.nan), np.full(shape, np.nan), np.zeros(shape)
    for array in [*arrays, raw, vwaps, window_volume]:
        array.flags.writeable = False
    return MarketPanel(
        sessions=cast(tuple[SessionDate, ...], tuple(last for _, last in grid)),
        symbols=tuple(Symbol(symbol) for symbol in symbols),
        adjusted_opens=arrays[0],
        adjusted_highs=arrays[1],
        adjusted_lows=arrays[2],
        adjusted_closes=arrays[3],
        session_volumes=arrays[4],
        raw_closes=raw,
        window_vwaps=vwaps,
        window_volumes=window_volume,
        source=f"alpaca:sip:all:shadow-intraday:{timeframe}",
    )
