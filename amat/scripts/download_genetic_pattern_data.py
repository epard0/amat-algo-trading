"""Download a bounded video-replication dataset; never edits AMAT snapshots.

Run from workspace root with Python 3.12+. Standard library only.
Archives and provider checksums are retained; manifest lists gaps without filling them.
"""

import concurrent.futures
import csv
import datetime as dt
import hashlib
import io
import json
import math
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data/research/genetic-patterns-2026-09-27"
BASE = "https://data.binance.vision/data/spot/monthly/klines"


def download(symbol: str, year: int, month: int) -> tuple[dict[str, object], list[list[str]]]:
    name = f"{symbol}-1h-{year}-{month:02d}.zip"
    url = f"{BASE}/{symbol}/1h/{name}"
    path = ROOT / "raw" / name
    checksum = path.with_suffix(".zip.CHECKSUM")
    if not checksum.exists():
        checksum.write_bytes(urllib.request.urlopen(url + ".CHECKSUM", timeout=45).read())  # noqa: S310 - fixed HTTPS provider
    expected = checksum.read_text().split()[0]
    if not path.exists():
        payload = urllib.request.urlopen(url, timeout=45).read()  # noqa: S310 - fixed HTTPS provider
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError(f"Provider checksum mismatch: {name}")
        path.write_bytes(payload)
    payload = path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ValueError(f"Cached checksum mismatch: {name}")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if names != [name.removesuffix(".zip") + ".csv"]:
            raise ValueError(f"Unexpected archive members: {name}")
        rows = list(csv.reader(io.StringIO(archive.read(names[0]).decode())))
    return {
        "url": url,
        "path": str(path.relative_to(ROOT)),
        "sha256": actual,
        "bytes": len(payload),
        "rows": len(rows),
    }, rows


def main() -> None:
    (ROOT / "raw").mkdir(parents=True, exist_ok=True)
    tasks = [
        (symbol, year, month)
        for symbol in ("BTCUSDT", "ETHUSDT")
        for year in range(2018, 2024)
        for month in range(1, 13)
    ]
    records, grouped = [], {"BTCUSDT": [], "ETHUSDT": []}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(download, *task): task for task in tasks}
        for future in concurrent.futures.as_completed(futures):
            record, rows = future.result()
            records.append(record)
            grouped[futures[future][0]].extend(rows)
    summaries = []
    for symbol, rows in grouped.items():
        rows.sort(key=lambda row: int(row[0]))
        stamps = [int(row[0]) for row in rows]
        if len(stamps) != len(set(stamps)):
            raise ValueError(f"Duplicate timestamps: {symbol}")
        irregular_closes = []
        for row in rows:
            o, h, lo, c, volume = map(float, row[1:6])
            if not all(map(math.isfinite, (o, h, lo, c, volume))):
                raise ValueError("Nonfinite prices or volume")
            if not (0 < lo <= min(o, c) <= max(o, c) <= h and volume >= 0):
                raise ValueError("Invalid OHLCV")
            if int(row[6]) != int(row[0]) + 3_600_000 - 1:
                irregular_closes.append({"open_time_ms": int(row[0]), "close_time_ms": int(row[6])})
        start = int(dt.datetime(2018, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
        end = int(dt.datetime(2024, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
        expected = set(range(start, end, 3_600_000))
        off_grid = sorted(set(stamps) - expected)
        missing = sorted(expected - set(stamps))
        dest = ROOT / f"{symbol}-1h-2018-2023.csv"
        with dest.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "open_time_ms",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "close_time_ms",
                    "quote_volume",
                    "trades",
                    "taker_buy_volume",
                    "taker_buy_quote_volume",
                    "ignore",
                ]
            )
            writer.writerows(rows)
        summaries.append(
            {
                "symbol": symbol,
                "file": dest.name,
                "rows": len(rows),
                "expected_hours": len(expected),
                "missing_hours": len(missing),
                "missing_open_times_ms": missing,
                "off_grid_open_times_ms": off_grid,
                "duplicate_timestamps": 0,
                "invalid_ohlcv_rows": 0,
                "irregular_close_rows": irregular_closes,
                "eligibility": "requires gap and irregular-close masking",
                "zero_volume_rows": sum(float(r[5]) == 0 for r in rows),
                "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "retrieved_at": dt.datetime.now(dt.UTC).isoformat(),
        "purpose": "method replication only; not AMAT trading universe",
        "provider": "Binance public spot archives",
        "timestamp_unit": "milliseconds",
        "interval": "1h",
        "timezone": "UTC",
        "gap_policy": "no imputation",
        "start_inclusive": "2018-01-01",
        "end_exclusive": "2024-01-01",
        "limitations": "OHLCV only; no quotes, funding, borrow or execution prices",
        "files": sorted(records, key=lambda row: row["path"]),
        "datasets": summaries,
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"archives": len(records), "datasets": summaries}, indent=2))


if __name__ == "__main__":
    main()
