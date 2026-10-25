"""Immutable native vendor aggregates for coverage research, not inference.

Native timestamps are retained without assuming session-open alignment. In
particular, a vendor hourly bucket may straddle the XNYS regular-session open.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import exchange_calendars as xcals  # type: ignore[import-untyped]

from amat.data.alpaca import AlpacaMarketData
from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json

SCHEMA = "native-intraday-data/1"
TIMEFRAMES = {"15m": ("15Min", 15), "1h": ("1Hour", 60)}
TIMESTAMP_SEMANTICS = "interval start; available after interval duration"


def _stamp(value: str) -> dt.datetime:
    moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise DataIntegrityError("native intraday timestamps must be timezone-aware")
    return moment.astimezone(dt.UTC)


def _collection_range(start: dt.date, end: dt.date, moment: dt.datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None or start > end:
        raise DataIntegrityError("native collection requires aware clock and ordered dates")
    if end > moment.astimezone(dt.UTC).date():
        raise DataIntegrityError("native collection cannot request future dates")
    schedule = xcals.get_calendar(
        "XNYS", start=str(start - dt.timedelta(days=1)), end=str(end + dt.timedelta(days=1))
    ).schedule.loc[str(start) : str(end)]
    if schedule.empty or schedule.iloc[-1]["close"].to_pydatetime() > moment:
        raise DataIntegrityError("native collection requires closed exchange sessions")


def _universe(symbols: Any) -> None:
    if (
        not isinstance(symbols, (list, tuple))
        or not symbols
        or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
        or len(set(symbols)) != len(symbols)
    ):
        raise DataIntegrityError("native universe must be ordered, nonempty and unique")


def validate_native_intraday(payload: dict[str, Any]) -> None:
    try:
        timeframe = payload["timeframe"]
        if (
            payload.get("schema_version") != SCHEMA
            or payload.get("provider") != "alpaca"
            or payload.get("feed") != "sip"
            or payload.get("adjustment") != "all"
            or timeframe not in TIMEFRAMES
            or payload.get("provider_timeframe") != TIMEFRAMES[timeframe][0]
            or payload.get("alignment") != "unknown"
            or payload.get("timestamp_semantics") != TIMESTAMP_SEMANTICS
        ):
            raise DataIntegrityError("unsupported native intraday data contract")
        symbols = payload["symbols"]
        _universe(symbols)
        start = dt.date.fromisoformat(payload["start"])
        end = dt.date.fromisoformat(payload["end"])
        collected = _stamp(payload["collected_at"])
        _collection_range(start, end, collected)
        records = payload["native_records"]
        if not isinstance(records, list) or not records:
            raise DataIntegrityError("native provider returned no bars")
        width = dt.timedelta(minutes=TIMEFRAMES[timeframe][1])
        seen: set[tuple[str, dt.datetime]] = set()
        for bar in records:
            stamp = _stamp(bar["t"])
            key = (bar["symbol"], stamp)
            if key in seen or key[0] not in symbols or not start <= stamp.date() <= end:
                raise DataIntegrityError("duplicate or unexpected native interval")
            if stamp.second or stamp.microsecond or stamp + width > collected:
                raise DataIntegrityError("unaligned or unclosed native interval")
            seen.add(key)
            o, h, low, c, v = (float(bar[field]) for field in ("o", "h", "l", "c", "v"))
            if (
                not all(math.isfinite(value) for value in (o, h, low, c, v))
                or min(o, h, low, c) <= 0
                or v < 0
                or h < max(o, c, low)
                or low > min(o, c, h)
            ):
                raise DataIntegrityError("invalid native OHLCV")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DataIntegrityError("malformed native intraday receipt") from exc


def fetch_native_intraday(
    output: Path,
    symbols: tuple[str, ...],
    start: dt.date,
    end: dt.date,
    *,
    timeframe: str,
    client: AlpacaMarketData | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Fetch closed native SIP aggregates into a new checksum-bound receipt.

    All provider bars, including extended-hours bars, retain their timestamps.
    Missing symbols/intervals remain absent: no completeness or clock alignment
    is inferred. This receipt cannot be consumed as a minute receipt.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if timeframe not in TIMEFRAMES:
        raise DataIntegrityError("native timeframe must be 15m or 1h")
    _universe(symbols)
    moment = now or dt.datetime.now(dt.UTC)
    _collection_range(start, end, moment)
    api = client or AlpacaMarketData(feed="sip")
    records = []
    for page in api._paginate(
        "/v2/stocks/bars",
        {
            "symbols": ",".join(symbols),
            "timeframe": TIMEFRAMES[timeframe][0],
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
                records.append(
                    {"symbol": symbol, **{key: bar[key] for key in ("t", "o", "h", "l", "c", "v")}}
                )
    payload = {
        "schema_version": SCHEMA,
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "all",
        "timeframe": timeframe,
        "provider_timeframe": TIMEFRAMES[timeframe][0],
        "symbols": list(symbols),
        "start": str(start),
        "end": str(end),
        "collected_at": moment.astimezone(dt.UTC).isoformat(),
        "native_records": records,
        "alignment": "unknown",
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "volume_basis": "vendor all-adjusted volume",
        "authority": "coverage research only; not an inference input",
    }
    validate_native_intraday(payload)
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


def load_native_intraday(path: Path) -> dict[str, Any]:
    """Verify checksum and native contract without resampling timestamps."""
    try:
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"payload", "payload_sha256"}
            or not isinstance(envelope["payload"], dict)
            or digest_json(envelope["payload"]) != envelope["payload_sha256"]
        ):
            raise DataIntegrityError("native receipt checksum mismatch")
        payload: dict[str, Any] = envelope["payload"]
        validate_native_intraday(payload)
        return payload
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DataIntegrityError("malformed native intraday receipt") from exc
