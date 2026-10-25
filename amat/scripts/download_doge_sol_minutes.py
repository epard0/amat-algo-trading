"""Verified real one-minute trade and mark data, January2024–September2026."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import time
import urllib.request
import zipfile

import pandas as pd
import polars as pl
from download_crypto_perpetual_data import COLUMNS

ROOT = Path('data/research/doge-sol-minute-2024-2026-09')


def get(url):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception:
            if attempt==5:
                raise
            time.sleep(min(8, 2**attempt))


def fetch(task):
    symbol, kind, stamp, frequency = task
    name = f'{symbol}-1m-{stamp}.zip'
    folder = ROOT/'raw'/symbol/(kind+'-'+frequency)
    folder.mkdir(parents=True, exist_ok=True)
    url = f'https://data.binance.vision/data/futures/um/{frequency}/{kind}/{symbol}/1m/{name}'
    path, check = folder/name, folder/(name+'.CHECKSUM')
    blob = path.read_bytes() if path.exists() else get(url)
    checksum = check.read_bytes() if check.exists() else get(url+'.CHECKSUM')
    digest = hashlib.sha256(blob).hexdigest()
    if digest != checksum.decode().split()[0]:
        raise ValueError('archive checksum mismatch')
    path.write_bytes(blob)
    check.write_bytes(checksum)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        content = z.read(z.namelist()[0])
    # Archives include a header in newer years.
    header = content.split(b'\n', 1)[0].startswith(b'open_time')
    f = pl.read_csv(io.BytesIO(content), has_header=header, new_columns=COLUMNS,
                    schema_overrides={c: pl.Float64 for c in COLUMNS})
    f = f.with_columns(pl.col('open_time_ms').cast(pl.Int64), pl.col('close_time_ms').cast(pl.Int64))
    return f, dict(url=url, rows=len(f), sha256=digest, checksum_verified=True)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    months = [f'{y}-{m:02}' for y in range(2024, 2027) for m in range(1, 13) if (y, m)<=(2026, 9)]
    records, datasets = [], []
    grid = pd.date_range('2024-01-01', '2026-10-01', freq='1min', inclusive='left', tz='UTC')
    for symbol in ('DOGEUSDT', 'SOLUSDT'):
        for kind in ('klines', 'markPriceKlines'):
            tasks = [(symbol, kind, m, 'monthly') for m in months]
            with ThreadPoolExecutor(max_workers=6) as pool:
                received = list(pool.map(fetch, tasks))
            f = pl.concat([r[0] for r in received]).sort('open_time_ms')
            records.extend(r[1] for r in received)
            missing = grid.difference(pd.to_datetime(f['open_time_ms'].to_numpy(), unit='ms', utc=True))
            for day in missing.floor('D').unique():
                extra, record = fetch((symbol, kind, str(day.date()), 'daily'))
                f = pl.concat([f, extra]).unique('open_time_ms').sort('open_time_ms')
                records.append(record)
            actual = pd.to_datetime(f['open_time_ms'].to_numpy(), unit='ms', utc=True)
            missing = grid.difference(actual)
            if actual.duplicated().any() or len(actual.difference(grid)) or (len(missing) and kind!='markPriceKlines'):
                raise ValueError('trade grid incomplete or duplicate/off-grid marks')
            if len(missing)>10:
                raise ValueError('too many unrecoverable mark minutes')
            path = ROOT/f'{symbol}-{kind}.parquet'
            f.write_parquet(path)
            datasets.append(dict(symbol=symbol, kind=kind, file=path.name, rows=len(f),
                missing_minutes=[str(t) for t in missing],
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            print('downloaded', symbol, kind, len(f), flush=True)
    manifest = dict(start='2024-01-01', end_exclusive='2026-10-01', interval='1m',
        source='Binance USD-M perpetual archives; not historical Kalshi data',
        retrieved_at=dt.datetime.now(dt.UTC).isoformat(), datasets=datasets, archives=records)
    (ROOT/'manifest.json').write_text(json.dumps(manifest, indent=2))
    print('COMPLETE', len(records), 'verified archives', flush=True)


if __name__=='__main__':
    main()
