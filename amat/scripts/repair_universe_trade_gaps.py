"""Complete five proxy histories from checksum-verified daily trade archives."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import shutil
import time
import urllib.request
import zipfile
import pandas as pd
from download_crypto_perpetual_data import COLUMNS


def main():
    old=Path('data/research/kalshi-universe-proxies-2021-2026-09');root=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09');root.mkdir(parents=True,exist_ok=True)
    symbols=('LTCUSDT','NEARUSDT','SOLUSDT','XRPUSDT','ZECUSDT')
    manifest=json.loads((old/'manifest.json').read_text());grid=pd.date_range('2021-01-01','2026-10-01',freq='1h',inclusive='left',tz='UTC')
    tasks=[];sources={}
    for s in symbols:
        f=pd.read_csv(old/f'{s}-klines.csv');sources[s]=f
        missing=grid.difference(pd.to_datetime(f.open_time_ms,unit='ms',utc=True))
        tasks += [(s,str(d.date())) for d in missing.floor('D').unique()]
    def fetch(task):
        s,date=task;name=f'{s}-1h-{date}.zip';url=f'https://data.binance.vision/data/futures/um/daily/klines/{s}/1h/{name}'
        def get(u):
            for i in range(6):
                try:
                    with urllib.request.urlopen(u,timeout=30) as r:return r.read()
                except Exception:
                    if i==5:raise
                    time.sleep(min(8,2**i))
        blob=get(url);check=get(url+'.CHECKSUM');digest=hashlib.sha256(blob).hexdigest()
        if digest!=check.decode().split()[0]:raise ValueError('daily trade checksum failed')
        folder=root/'raw'/s;folder.mkdir(parents=True,exist_ok=True);(folder/name).write_bytes(blob);(folder/(name+'.CHECKSUM')).write_bytes(check)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:content=z.read(z.namelist()[0])
        f=pd.read_csv(io.BytesIO(content),header=None,names=COLUMNS)
        if str(f.iloc[0,0])=='open_time':f=f.iloc[1:]
        return s,f.apply(pd.to_numeric,errors='raise'),dict(url=url,sha256=digest,rows=len(f),checksum_verified=True)
    with ThreadPoolExecutor(max_workers=5) as pool:received=list(pool.map(fetch,tasks))
    records=[]
    for record in manifest['datasets']:
        if record['symbol'] not in symbols:continue
        r=dict(record);s=r['symbol'];path=root/r['file']
        if r['kind']=='klines':
            f=pd.concat([sources[s]]+[f for sym,f,_ in received if sym==s],ignore_index=True).drop_duplicates('open_time_ms').sort_values('open_time_ms');f.to_csv(path,index=False)
            r.update(rows=len(f),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        else:shutil.copy2(old/r['file'],path)
        records.append(r)
    coverage=[r for r in json.loads((old/'universe-coverage.json').read_text()) if r['symbol'] in symbols]
    (root/'universe-coverage.json').write_text(json.dumps(coverage,indent=2))
    (root/'manifest.json').write_text(json.dumps(dict(source='Binance proxy supplement; verified daily trade gaps repaired',datasets=records,daily_trade_supplements=[r for *_,r in received],parent_manifest_sha256=hashlib.sha256((old/'manifest.json').read_bytes()).hexdigest()),indent=2))
    print('repaired',len(received),'daily trade archives for',len(symbols),'assets')

if __name__=='__main__':main()
