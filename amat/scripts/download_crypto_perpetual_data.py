"""Checksum-pinned Binance USD-M perpetual trade/mark candles and funding."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

import pandas as pd

ROOT = Path('data/research/crypto-perpetual-2020-2026-09')
COLUMNS = ['open_time_ms','open','high','low','close','volume','close_time_ms',
           'quote_volume','trades','taker_buy_volume','taker_buy_quote_volume','ignore']


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    raw=ROOT/'raw'; raw.mkdir(exist_ok=True)
    months=[(y,m) for y in range(2020,2027) for m in range(1,13) if (y,m)<=(2026,9)]
    tasks=[(s,k,y,m) for s in ('BTCUSDT','ETHUSDT') for k in ('klines','markPriceKlines','fundingRate') for y,m in months]
    def get(url):
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url,timeout=45) as response: return response.read()
            except Exception:
                if attempt==2: raise
    def fetch(task):
        symbol,kind,y,m=task
        suffix='fundingRate' if kind=='fundingRate' else '1h'
        name=f'{symbol}-{suffix}-{y}-{m:02}.zip'
        folder=raw/symbol/kind; folder.mkdir(parents=True,exist_ok=True)
        url=f'https://data.binance.vision/data/futures/um/monthly/{kind}/{symbol}/'+('' if kind=='fundingRate' else '1h/')+name
        path=folder/name; check=folder/(name+'.CHECKSUM')
        blob=path.read_bytes() if path.exists() else get(url)
        checksum=check.read_bytes() if check.exists() else get(url+'.CHECKSUM')
        digest=hashlib.sha256(blob).hexdigest()
        if digest!=checksum.decode().split()[0]: raise ValueError(f'checksum mismatch {url}')
        path.write_bytes(blob); check.write_bytes(checksum)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            content=z.read(z.namelist()[0])
        if kind=='fundingRate':
            frame=pd.read_csv(io.BytesIO(content))
        else:
            frame=pd.read_csv(io.BytesIO(content),header=None,names=COLUMNS)
            if str(frame.iloc[0,0]) in ('open_time','open_time_ms'):
                frame=frame.iloc[1:]
            frame=frame.apply(pd.to_numeric,errors='raise')
            for column in ('open_time_ms','close_time_ms'):
                frame[column]=frame[column].astype('int64')
        return symbol,kind,frame,dict(url=url,sha256=digest,rows=len(frame),checksum_verified=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        received=list(pool.map(fetch,tasks))
    datasets=[]
    for symbol in ('BTCUSDT','ETHUSDT'):
        for kind in ('klines','markPriceKlines','fundingRate'):
            frame=pd.concat([f for s,k,f,_ in received if (s,k)==(symbol,kind)],ignore_index=True)
            time='calc_time' if kind=='fundingRate' else 'open_time_ms'
            frame=frame.sort_values(time)
            if frame[time].duplicated().any(): raise ValueError(f'duplicate {symbol} {kind}')
            path=ROOT/f'{symbol}-{kind}.csv'; frame.to_csv(path,index=False)
            datasets.append(dict(symbol=symbol,kind=kind,file=path.name,rows=len(frame),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    manifest=dict(provider='Binance USD-M perpetual public archives',interval='1h',
        start='2020-01-01',end_exclusive='2026-10-01',timezone='UTC',timestamp_unit='milliseconds',
        retrieved_at=dt.datetime.now(dt.UTC).isoformat(),files=[r for *_,r in received],datasets=datasets)
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(dict(archives=len(received),datasets=datasets),indent=2))

if __name__=='__main__': main()
