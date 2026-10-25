"""Repair missing monthly mark candles using separately checksum-verified daily archives."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile
import pandas as pd
from download_crypto_perpetual_data import COLUMNS


def main():
    root=Path('data/research/crypto-perpetual-2020-2026-09')
    manifest=json.loads((root/'manifest.json').read_text())
    original=(root/'manifest.json').read_bytes()
    (root/'monthly-manifest.json').write_bytes(original)
    grid=pd.date_range('2020-01-01','2026-10-01',freq='1h',inclusive='left',tz='UTC')
    tasks=[]; source={}
    for symbol in ('BTCUSDT','ETHUSDT'):
        f=pd.read_csv(root/f'{symbol}-markPriceKlines.csv'); source[symbol]=f
        missing=grid.difference(pd.to_datetime(f.open_time_ms,unit='ms',utc=True))
        tasks += [(symbol,str(day.date())) for day in missing.floor('D').unique()]
    def fetch(task):
        symbol,date=task; name=f'{symbol}-1h-{date}.zip'
        url=f'https://data.binance.vision/data/futures/um/daily/markPriceKlines/{symbol}/1h/{name}'
        with urllib.request.urlopen(url,timeout=30) as r: blob=r.read()
        with urllib.request.urlopen(url+'.CHECKSUM',timeout=30) as r: check=r.read()
        digest=hashlib.sha256(blob).hexdigest()
        if digest!=check.decode().split()[0]:raise ValueError('daily checksum mismatch')
        folder=root/'raw'/symbol/'mark-daily';folder.mkdir(parents=True,exist_ok=True)
        (folder/name).write_bytes(blob);(folder/(name+'.CHECKSUM')).write_bytes(check)
        with zipfile.ZipFile(io.BytesIO(blob)) as z: content=z.read(z.namelist()[0])
        f=pd.read_csv(io.BytesIO(content),header=None,names=COLUMNS)
        if str(f.iloc[0,0])=='open_time':f=f.iloc[1:]
        f=f.apply(pd.to_numeric,errors='raise')
        return symbol,f,dict(url=url,rows=len(f),sha256=digest,checksum_verified=True)
    with ThreadPoolExecutor(max_workers=6) as pool:received=list(pool.map(fetch,tasks))
    for symbol,f in source.items():
        merged=pd.concat([f]+[df for s,df,_ in received if s==symbol],ignore_index=True).sort_values('open_time_ms')
        # Daily supplements only fill absent timestamps; existing monthly prices retain priority.
        merged=merged.drop_duplicates('open_time_ms',keep='first')
        path=root/f'{symbol}-markPriceKlines.csv';merged.to_csv(path,index=False)
        record=next(r for r in manifest['datasets'] if r['symbol']==symbol and r['kind']=='markPriceKlines')
        record.update(rows=len(merged),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest['daily_mark_supplements']=[r for *_,r in received]
    manifest['monthly_manifest_sha256']=hashlib.sha256(original).hexdigest()
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('repaired from',len(received),'daily mark archives')

if __name__=='__main__':main()
