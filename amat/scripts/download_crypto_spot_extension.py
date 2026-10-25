"""Download checksum-verified Binance hourly archives through September 2026."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import datetime as dt
import hashlib
import io
import json
import urllib.request
import zipfile
import pandas as pd


def main():
    root=Path('data/research/crypto-spot-2018-2026-09'); root.mkdir(parents=True,exist_ok=False)
    raw=root/'raw'; raw.mkdir()
    original=Path('data/research/genetic-patterns-2026-09-27')
    manifest=json.loads((original/'manifest.json').read_text())
    tasks=[(s,y,m) for s in ('BTCUSDT','ETHUSDT') for y in (2024,2025,2026) for m in range(1,13) if (y,m)<=(2026,9)]
    def fetch(task):
        symbol,y,m=task; name=f'{symbol}-1h-{y}-{m:02}.zip'
        url=f'https://data.binance.vision/data/spot/monthly/klines/{symbol}/1h/{name}'
        def get(u):
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(u,timeout=40) as response: return response.read()
                except Exception:
                    if attempt==2: raise
        blob=get(url); checksum=get(url+'.CHECKSUM')
        sha=hashlib.sha256(blob).hexdigest()
        if checksum.decode().split()[0]!=sha: raise ValueError(f'checksum mismatch {name}')
        (raw/name).write_bytes(blob); (raw/(name+'.CHECKSUM')).write_bytes(checksum)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            df=pd.read_csv(z.open(z.namelist()[0]),header=None,names=['open_time_ms','open','high','low','close','volume','close_time_ms','quote_volume','trades','taker_buy_volume','taker_buy_quote_volume','ignore'])
        for col in ('open_time_ms','close_time_ms'):
            micro=df[col]>10**14
            df.loc[micro,col]=df.loc[micro,col]//1000
        return symbol,df,dict(url=url,sha256=sha,rows=len(df),checksum_verified=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        received=list(pool.map(fetch,tasks))
    datasets=[]
    for record in manifest['datasets']:
        symbol=record['symbol']; path=original/record['file']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']: raise ValueError('old hash mismatch')
        merged=pd.concat([pd.read_csv(path)]+[df for s,df,_ in received if s==symbol]).sort_values('open_time_ms')
        if merged.open_time_ms.duplicated().any(): raise ValueError('duplicate timestamps')
        output=root/f'{symbol}-1h-2018-2026-09.csv'; merged.to_csv(output,index=False)
        datasets.append(dict(symbol=symbol,file=output.name,rows=len(merged),sha256=hashlib.sha256(output.read_bytes()).hexdigest()))
    result=dict(interval='1h',timestamp_unit='milliseconds',timezone='UTC',start_inclusive='2018-01-01',end_exclusive='2026-10-01',
        retrieved_at=dt.datetime.now(dt.UTC).isoformat(),provider='Binance public spot archives',datasets=datasets,files=[r for _,_,r in received],
        original_manifest_sha256=hashlib.sha256((original/'manifest.json').read_bytes()).hexdigest(),
        normalization='2025+ microseconds floor-divided by 1000; raw ZIPs and provider CHECKSUM files retained')
    (root/'manifest.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(directory=str(root),archives=len(received),datasets=datasets),indent=2))

if __name__=='__main__': main()
