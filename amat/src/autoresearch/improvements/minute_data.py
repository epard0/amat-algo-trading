"""Verified raw minute partition audit and resumable read-only extension."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl

from amat.data.alpaca import AlpacaMarketData


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_archive(root: Path, symbols: tuple[str, ...]) -> dict[str, Any]:
    """Audit real partition bounds without claiming complete session coverage."""
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("adjustment") != "raw" or manifest.get("bar_minutes") != 1:
        raise ValueError("execution archive must be raw one-minute data")
    rows = []
    for name, expected in manifest["partitions"].items():
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or sha(path) != expected:
            raise ValueError("minute partition identity mismatch")
        symbol = name.split("-")[0]
        frame = pl.scan_parquet(path)
        summary = (
            frame.select(
                pl.len().alias("rows"),
                pl.col("t").min().alias("first"),
                pl.col("t").max().alias("last"),
                pl.col("t").n_unique().alias("unique_timestamps"),
            )
            .collect()
            .to_dicts()[0]
        )
        rows.append({"symbol": symbol, "partition": name, **summary})
    available = sorted({r["symbol"] for r in rows})
    return {
        "schema": "minute-archive-audit/1",
        "archive": str(root.resolve()),
        "requested_symbols": symbols,
        "available_symbols": available,
        "missing_symbols": sorted(set(symbols) - set(available)),
        "partitions": rows,
        "coverage_claim": "partition bounds only; exchange-grid audit required",
    }


def extend_archive(  # noqa: PLR0912, PLR0915 - checkpointed partition ingestion
    source: Path,
    destination: Path,
    symbols: tuple[str, ...],
    start: dt.date,
    end: dt.date,
    *,
    client: AlpacaMarketData | None = None,
) -> dict[str, Any]:
    """Reuse verified complete years; fetch missing intervals into new partitions.

    End is inclusive and must precede today's exchange date. Each completed
    request is checkpointed so a provider rejection does not discard earlier work.
    """
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    today = dt.datetime.now(dt.UTC).astimezone(ZoneInfo("America/New_York")).date()
    if start > end or end >= today:
        raise ValueError("minute extension requires past completed sessions")
    audit_archive(source, symbols)
    original = json.loads((source / "manifest.json").read_text())
    destination.mkdir(parents=True, exist_ok=True)
    spec = {
        "symbols": list(symbols),
        "start": str(start),
        "end": str(end),
        "source_manifest_sha256": sha(source / "manifest.json"),
    }
    spec_path = destination / "request.json"
    if spec_path.exists() and json.loads(spec_path.read_text()) != spec:
        raise ValueError("extension request changed; use a new output directory")
    spec_path.write_text(json.dumps(spec, indent=2))
    api = client or AlpacaMarketData(feed="sip")
    partitions = {}
    source_end = dt.date.fromisoformat(original["end"])
    for symbol in symbols:
        for year in range(start.year, end.year + 1):
            first, last = max(start, dt.date(year, 1, 1)), min(end, dt.date(year, 12, 31))
            old_name = f"{symbol}-{year}.parquet"
            if old_name in original["partitions"]:
                # Reuse only data whose requested interval lies inside this study.
                meta = json.loads((source / old_name).with_suffix(".json").read_text())
                request = meta["request"]
                old_first = dt.date.fromisoformat(request["start"])
                old_last = dt.date.fromisoformat(request["end"])
                if old_first == first and old_last <= last:
                    import shutil  # noqa: PLC0415

                    target = destination / old_name
                    if not target.exists():
                        shutil.copyfile(source / old_name, target)
                    if sha(target) != original["partitions"][old_name]:
                        raise ValueError("copied raw partition changed")
                    partitions[old_name] = sha(target)
                    first = old_last + dt.timedelta(days=1)
            if first > last:
                continue
            # Do not use a broad manifest end as evidence of an individual partition.
            name = f"{symbol}-{first}-{last}.parquet"
            target = destination / name
            receipt = target.with_suffix(".json")
            request = {
                "symbol": symbol,
                "start": str(first),
                "end": str(last),
                "feed": "sip",
                "adjustment": "raw",
                "timeframe": "1Min",
            }
            if receipt.exists():
                saved = json.loads(receipt.read_text())
                if saved["request"] != request or sha(target) != saved["sha256"]:
                    raise ValueError("resumed minute partition changed")
            else:
                records = []
                for page in api._paginate(
                    "/v2/stocks/bars",
                    {
                        "symbols": symbol,
                        "timeframe": "1Min",
                        "feed": "sip",
                        "adjustment": "raw",
                        "start": f"{first}T00:00:00Z",
                        "end": f"{last}T23:59:59Z",
                        "limit": 5000,
                        "sort": "asc",
                    },
                ):
                    records.extend(page.get("bars", {}).get(symbol, []))
                if not records:
                    raise ValueError(f"no raw minute records for {symbol} {first}..{last}")
                frame = pl.DataFrame(records).sort("t")
                if frame["t"].n_unique() != len(frame):
                    raise ValueError("duplicate minute timestamps")
                temporary = target.with_suffix(".partial")
                frame.write_parquet(temporary)
                temporary.replace(target)
                receipt.write_text(
                    json.dumps(
                        {"request": request, "sha256": sha(target), "rows": len(frame)}, indent=2
                    )
                )
            partitions[name] = sha(target)
    manifest = {
        "schema": "intraday-data/1",
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "raw",
        "bar_minutes": 1,
        "symbols": list(symbols),
        "start": str(start),
        "end": str(end),
        "partitions": partitions,
        "timestamp_semantics": "bar start; available after one minute plus latency",
        "source_end_not_used_for_partition_coverage": str(source_end),
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def audit_exchange_grid(
    root: Path, symbols: tuple[str, ...], start: dt.date, end: dt.date, output: Path
) -> dict[str, Any]:
    """Analyze completeness without feeding future-day validity into inference."""
    import exchange_calendars as xcals  # type: ignore[import-untyped] # noqa: PLC0415

    bounds = audit_archive(root, symbols)
    calendar = xcals.get_calendar(
        "XNYS", start=str(start - dt.timedelta(days=2)), end=str(end + dt.timedelta(days=2))
    )
    schedule = calendar.schedule.loc[str(start) : str(end)]
    sessions = pl.DataFrame(
        [
            {
                "date": date.date(),
                "open_at": row["open"].to_pydatetime(),
                "close_at": row["close"].to_pydatetime(),
                "expected_minutes": int((row["close"] - row["open"]).total_seconds() / 60),
            }
            for date, row in schedule.iterrows()
        ]
    )
    manifest = json.loads((root / "manifest.json").read_text())
    groups = []
    duplicates = 0
    invalid = 0
    raw_rows = 0
    for year in range(start.year, end.year + 1):
        scans = []
        for name in manifest["partitions"]:
            symbol = name.split("-")[0]
            if symbol in symbols and f"-{year}" in name:
                scans.append(
                    pl.scan_parquet(root / name).select(
                        "t", "o", "h", "l", "c", "v", pl.lit(symbol).alias("symbol")
                    )
                )
        if not scans:
            continue
        frame = (
            pl.concat(scans)
            .filter(pl.col("t").str.slice(0, 10).is_between(pl.lit(str(start)), pl.lit(str(end))))
            .collect()
            .with_columns(pl.col("t").str.to_datetime(time_zone="UTC").alias("stamp"))
        )
        raw_rows += len(frame)
        duplicates += frame.select(pl.struct("symbol", "stamp").is_duplicated().sum()).item()
        invalid += frame.filter(
            ~pl.all_horizontal(pl.col("o", "h", "l", "c", "v").is_finite())
            | (pl.min_horizontal("o", "h", "l", "c") <= 0)
            | (pl.col("v") < 0)
            | (pl.col("l") > pl.min_horizontal("o", "c"))
            | (pl.col("h") < pl.max_horizontal("o", "c"))
        ).height
        frame = frame.with_columns(
            pl.col("stamp").dt.convert_time_zone("America/New_York").dt.date().alias("date")
        ).join(sessions, on="date", how="inner")
        regular = frame.filter(
            (pl.col("stamp") >= pl.col("open_at")) & (pl.col("stamp") < pl.col("close_at"))
        )
        groups.append(
            regular.group_by("date", "symbol").agg(
                pl.col("stamp").n_unique().alias("observed_minutes"),
                (pl.col("v") == 0).sum().alias("zero_volume_minutes"),
            )
        )
    expected = sessions.select("date", "expected_minutes").join(
        pl.DataFrame({"symbol": symbols}), how="cross"
    )
    if groups:
        observed = (
            pl.concat(groups)
            .group_by("date", "symbol")
            .agg(pl.col("observed_minutes").sum(), pl.col("zero_volume_minutes").sum())
        )
        expected = expected.join(observed, on=["date", "symbol"], how="left")
    else:
        expected = expected.with_columns(
            pl.lit(0).alias("observed_minutes"), pl.lit(0).alias("zero_volume_minutes")
        )
    expected = expected.fill_null(0).with_columns(
        (pl.col("expected_minutes") - pl.col("observed_minutes")).alias("missing_minutes")
    )
    output.mkdir(parents=True, exist_ok=True)
    expected.sort("date", "symbol").write_csv(output / "minute-coverage.csv")
    total = int(expected["expected_minutes"].sum())
    actual = int(expected["observed_minutes"].sum())
    result = {
        "schema": "minute-exchange-grid-audit/1",
        "archive": str(root.resolve()),
        "sessions": len(sessions),
        "symbols": symbols,
        "raw_rows": raw_rows,
        "expected_regular_asset_minutes": total,
        "observed_regular_asset_minutes": actual,
        "missing_regular_asset_minutes": total - actual,
        "coverage": actual / total if total else None,
        "duplicate_keys": duplicates,
        "invalid_ohlcv_rows": invalid,
        "complete_asset_days": expected.filter(pl.col("missing_minutes") == 0).height,
        "zero_volume_regular_minutes": expected["zero_volume_minutes"].sum(),
        "inference_policy": (
            "diagnostic audit only; event-time eligibility never uses future completeness"
        ),
        "missing_symbols": bounds["missing_symbols"],
    }
    (output / "minute-grid-audit.json").write_text(json.dumps(result, indent=2))
    if duplicates or invalid:
        raise ValueError("raw minute quality audit failed; retain diagnostic evidence")
    return result
