"""Pin multi-year Binance proxies for current Kalshi crypto universe; no invented listings."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import time
from pathlib import Path
import urllib.error
import urllib.request
import zipfile
import pandas as pd
from download_crypto_perpetual_data import COLUMNS

ROOT=Path('data/research/kalshi-universe-proxies-2021-2026-09')
INVENTORY=Path('outputs/crypto-directional-2026-10-19/kalshi-inventory/markets.json')


def get(url):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url,timeout=40) as r:return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code==404:return None
            if attempt==5:raise
            time.sleep(min(8,2**attempt))
        except Exception:
            if attempt==5:raise
            time.sleep(min(8,2**attempt))


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    markets=json.loads(INVENTORY.read_text())['markets']
    active=[m for m in markets if m['asset_class']=='Crypto' and m['status']=='active']
    coverage=[]; eligible=[]
    def probe(m):
        asset=m['ticker'][2:-4]
        symbol=('1000SHIB' if asset=='KSHIB' else asset)+'USDT'
        if asset in ('BTC','ETH'):return dict(ticker=m['ticker'],symbol=symbol,status='existing primary dataset')
        u=f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-2021-01.zip'
        b=get(u)
        return dict(ticker=m['ticker'],symbol=symbol,status='eligible' if b else 'excluded: no January 2021 perpetual funding archive; insufficient frozen training history')
    with ThreadPoolExecutor(max_workers=8) as pool:coverage=list(pool.map(probe,active))
    (ROOT/'universe-coverage.json').write_text(json.dumps(coverage,indent=2))
    eligible=[r['symbol'] for r in coverage if r['status']=='eligible']
    months=[(y,m) for y in range(2021,2027) for m in range(1,13) if (y,m)<=(2026,9)]
    tasks=[(s,k,y,m) for s in eligible for k in ('klines','markPriceKlines','fundingRate') for y,m in months]
    def fetch(task):
        symbol,kind,y,m=task;suffix='fundingRate' if kind=='fundingRate' else '1h'
        name=f'{symbol}-{suffix}-{y}-{m:02}.zip';folder=ROOT/'raw'/symbol/kind;folder.mkdir(parents=True,exist_ok=True)
        url=f'https://data.binance.vision/data/futures/um/monthly/{kind}/{symbol}/'+('' if kind=='fundingRate' else '1h/')+name
        path=folder/name;check=folder/(name+'.CHECKSUM')
        blob=path.read_bytes() if path.exists() else get(url)
        if blob is None:return symbol,kind,None,dict(url=url,missing=True)
        checksum=check.read_bytes() if check.exists() else get(url+'.CHECKSUM')
        if checksum is None or hashlib.sha256(blob).hexdigest()!=checksum.decode().split()[0]:raise ValueError('checksum failed')
        path.write_bytes(blob);check.write_bytes(checksum)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:content=z.read(z.namelist()[0])
        if kind=='fundingRate':f=pd.read_csv(io.BytesIO(content))
        else:
            f=pd.read_csv(io.BytesIO(content),header=None,names=COLUMNS)
            if str(f.iloc[0,0]) in ('open_time','open_time_ms'):f=f.iloc[1:]
            f=f.apply(pd.to_numeric,errors='raise')
        return symbol,kind,f,dict(url=url,sha256=hashlib.sha256(blob).hexdigest(),rows=len(f),checksum_verified=True)
    with ThreadPoolExecutor(max_workers=6) as pool:received=list(pool.map(fetch,tasks))
    datasets=[]
    for symbol in eligible:
        for kind in ('klines','markPriceKlines','fundingRate'):
            pieces=[f for s,k,f,_ in received if s==symbol and k==kind and f is not None]
            f=pd.concat(pieces,ignore_index=True);time='calc_time' if kind=='fundingRate' else 'open_time_ms'
            f=f.sort_values(time)
            if f[time].duplicated().any():raise ValueError('duplicate source timestamps')
            # Supplement absent monthly mark hours with verified daily mark files.
            if kind=='markPriceKlines':
                grid=pd.date_range('2021-01-01','2026-10-01',freq='1h',inclusive='left',tz='UTC')
                missing=grid.difference(pd.to_datetime(f.open_time_ms,unit='ms',utc=True))
                extras=[]
                for day in missing.floor('D').unique():
                    name=f'{symbol}-1h-{day.date()}.zip';url=f'https://data.binance.vision/data/futures/um/daily/markPriceKlines/{symbol}/1h/{name}'
                    blob=get(url)
                    if blob is None:continue
                    checksum=get(url+'.CHECKSUM')
                    if checksum is None or hashlib.sha256(blob).hexdigest()!=checksum.decode().split()[0]:raise ValueError('daily checksum failed')
                    folder=ROOT/'raw'/symbol/'mark-daily';folder.mkdir(exist_ok=True)
                    (folder/name).write_bytes(blob);(folder/(name+'.CHECKSUM')).write_bytes(checksum)
                    with zipfile.ZipFile(io.BytesIO(blob)) as z:content=z.read(z.namelist()[0])
                    extra=pd.read_csv(io.BytesIO(content),header=None,names=COLUMNS)
                    if str(extra.iloc[0,0])=='open_time':extra=extra.iloc[1:]
                    extras.append(extra.apply(pd.to_numeric,errors='raise'))
                if extras:f=pd.concat([f]+extras,ignore_index=True).drop_duplicates('open_time_ms').sort_values('open_time_ms')
            path=ROOT/f'{symbol}-{kind}.csv';f.to_csv(path,index=False)
            datasets.append(dict(symbol=symbol,kind=kind,file=path.name,rows=len(f),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (ROOT/'manifest.json').write_text(json.dumps(dict(source='Binance perpetual proxies; not Kalshi historical fills or funding',start='2021-01-01',end_exclusive='2026-10-01',datasets=datasets,files=[r for *_,r in received],inventory_sha256=hashlib.sha256(INVENTORY.read_bytes()).hexdigest()),indent=2))
    print(json.dumps(dict(coverage=coverage,archives=len(received),datasets=len(datasets)),indent=2))

if __name__=='__main__':main()
