"""Complete checksum-pinned minute trade/mark archives for the intraday protocol."""
from concurrent.futures import ThreadPoolExecutor
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

ROOT=Path('data/research/crypto-minute-2026-q1-q3')


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    def get(url):
        for n in range(6):
            try:
                with urllib.request.urlopen(url,timeout=60) as r:return r.read()
            except Exception:
                if n==5:raise
                time.sleep(min(8,2**n))
    tasks=[(s,k,m) for s in ('BTCUSDT','ETHUSDT') for k in ('klines','markPriceKlines') for m in range(1,10)]
    def fetch(t):
        s,k,m=t;name=f'{s}-1m-2026-{m:02}.zip';folder=ROOT/'raw'/s/k;folder.mkdir(parents=True,exist_ok=True)
        url=f'https://data.binance.vision/data/futures/um/monthly/{k}/{s}/1m/{name}'
        path=folder/name;check=folder/(name+'.CHECKSUM')
        blob=path.read_bytes() if path.exists() else get(url)
        checksum=check.read_bytes() if check.exists() else get(url+'.CHECKSUM')
        digest=hashlib.sha256(blob).hexdigest()
        if digest!=checksum.decode().split()[0]:raise ValueError('minute archive checksum mismatch')
        path.write_bytes(blob);check.write_bytes(checksum)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:content=z.read(z.namelist()[0])
        f=pd.read_csv(io.BytesIO(content),header=None,names=COLUMNS)
        if str(f.iloc[0,0])=='open_time':f=f.iloc[1:]
        f=f.apply(pd.to_numeric,errors='raise')
        return s,k,f,dict(url=url,rows=len(f),sha256=digest,checksum_verified=True)
    with ThreadPoolExecutor(max_workers=4) as pool:received=list(pool.map(fetch,tasks))
    grid=pd.date_range('2026-01-01','2026-10-01',freq='1min',inclusive='left',tz='UTC')
    datasets=[];daily_supplements=[]
    for symbol in ('BTCUSDT','ETHUSDT'):
        for kind in ('klines','markPriceKlines'):
            f=pd.concat([df for s,k,df,_ in received if (s,k)==(symbol,kind)],ignore_index=True)
            missing=grid.difference(pd.to_datetime(f.open_time_ms,unit='ms',utc=True));extras=[]
            for day in missing.floor('D').unique():
                name=f'{symbol}-1m-{day.date()}.zip';url=f'https://data.binance.vision/data/futures/um/daily/{kind}/{symbol}/1m/{name}'
                blob=get(url);checksum=get(url+'.CHECKSUM');digest=hashlib.sha256(blob).hexdigest()
                if digest!=checksum.decode().split()[0]:raise ValueError('daily minute checksum failed')
                folder=ROOT/'raw'/symbol/(kind+'-daily');folder.mkdir(parents=True,exist_ok=True)
                (folder/name).write_bytes(blob);(folder/(name+'.CHECKSUM')).write_bytes(checksum)
                with zipfile.ZipFile(io.BytesIO(blob)) as z:content=z.read(z.namelist()[0])
                df=pd.read_csv(io.BytesIO(content),header=None,names=COLUMNS)
                if str(df.iloc[0,0])=='open_time':df=df.iloc[1:]
                extras.append(df.apply(pd.to_numeric,errors='raise'))
                daily_supplements.append(dict(url=url,sha256=digest,rows=len(df),checksum_verified=True))
            if extras:f=pd.concat([f]+extras,ignore_index=True).drop_duplicates('open_time_ms')
            f=f.sort_values('open_time_ms')
            path=ROOT/f'{symbol}-{kind}.parquet';pl.DataFrame({c:f[c].to_numpy() for c in f.columns}).write_parquet(path)
            datasets.append(dict(symbol=symbol,kind=kind,file=path.name,rows=len(f),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    manifest=dict(start='2026-01-01',end_exclusive='2026-10-01',interval='1m',source='Binance USD-M perpetual archives; not Kalshi historical prices',datasets=datasets,files=[r for *_,r in received],daily_supplements=daily_supplements)
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(dict(datasets=datasets,supplements=len(daily_supplements)),indent=2))

if __name__=='__main__':main()
