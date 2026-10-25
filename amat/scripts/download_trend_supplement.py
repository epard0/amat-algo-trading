"""Checksummed SUI/WLD proxy history for the frozen2024training window."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,io,hashlib,zipfile
import numpy as np
import pandas as pd
from download_kalshi_universe_proxies import get
from download_crypto_perpetual_data import COLUMNS
OUT=Path('data/research/trend-supplement-prepared-2026-10-22');OUT.mkdir(exist_ok=True)
if (OUT/"audit.json").exists():raise ValueError("preserve verified output")
months=[(y,m) for y in range(2023,2027) for m in range(1,13) if (2023,10)<=(y,m)<=(2026,9)]
tasks=[(s,k,y,m) for s in ['SUI','WLD'] for k in ['klines','markPriceKlines','fundingRate'] for y,m in months]
def fetch(task):
 s,kind,y,m=task;symbol=s+'USDT';name=f'{symbol}-'+('fundingRate' if kind=='fundingRate' else '1h')+f'-{y}-{m:02}.zip';url=f'https://data.binance.vision/data/futures/um/monthly/{kind}/{symbol}/'+('' if kind=='fundingRate' else '1h/')+name
 folder=OUT/'raw'/s/kind;folder.mkdir(parents=True,exist_ok=True)
 blob=(folder/name).read_bytes() if (folder/name).exists() else get(url);check=(folder/(name+'.CHECKSUM')).read_bytes() if (folder/(name+'.CHECKSUM')).exists() else get(url+'.CHECKSUM')
 if blob is None or check is None:raise ValueError('missing archive '+name)
 assert hashlib.sha256(blob).hexdigest()==check.decode().split()[0]
 folder=OUT/'raw'/s/kind;folder.mkdir(parents=True,exist_ok=True);(folder/name).write_bytes(blob);(folder/(name+'.CHECKSUM')).write_bytes(check)
 with zipfile.ZipFile(io.BytesIO(blob)) as z:content=z.read(z.namelist()[0])
 if kind=='fundingRate':f=pd.read_csv(io.BytesIO(content))
 else:
  f=pd.read_csv(io.BytesIO(content),header=None,names=COLUMNS)
  if str(f.iloc[0,0]) in ('open_time','open_time_ms'):f=f.iloc[1:]
  f=f.apply(pd.to_numeric,errors='raise')
 return s,kind,f,dict(url=url,sha256=hashlib.sha256(blob).hexdigest(),rows=len(f),checksum_verified=True)
with ThreadPoolExecutor(max_workers=8) as pool:received=list(pool.map(fetch,tasks))
repairs=[]
index=pd.date_range('2023-10-01','2026-10-01',freq='1h',inclusive='left',tz='UTC');audit={}
for s in ['SUI','WLD']:
 combined=None;funding={}
 for kind in ['klines','markPriceKlines','fundingRate']:
  f=pd.concat([f for asset,k,f,_ in received if asset==s and k==kind],ignore_index=True);time='calc_time' if kind=='fundingRate' else 'open_time_ms';f=f.sort_values(time);assert not f[time].duplicated().any()
  if kind=='fundingRate':
   times=f.calc_time.to_numpy(dtype='int64');interval=f.funding_interval_hours.to_numpy(dtype=float);assert np.isfinite(interval).all() and (interval>0).all();assert (np.diff(times)/3600000<=np.maximum(interval[:-1],interval[1:])+.017).all();assert times[0]//3600000==int(index[0].timestamp()*1000)//3600000;assert (int(index[-1].timestamp()*1000)+3600000-times[-1])/3600000<=interval[-1];assert (times%3600000<6000).all();assert np.isfinite(f.last_funding_rate).all() and (abs(f.last_funding_rate)<1).all();funding={int(t//3600000):(int(t),str(r)) for t,r in zip(times,f.last_funding_rate)}
   continue
  missing=index.difference(pd.to_datetime(f.open_time_ms,unit='ms',utc=True))
  for day in missing.floor('D').unique():
   symbol=s+'USDT';name=f'{symbol}-1h-{day.date()}.zip';url=f'https://data.binance.vision/data/futures/um/daily/{kind}/{symbol}/1h/{name}';blob=get(url);check=get(url+'.CHECKSUM')
   if blob is None or check is None:raise ValueError('missing daily repair '+name)
   assert hashlib.sha256(blob).hexdigest()==check.decode().split()[0]
   folder=OUT/'raw'/s/(kind+'-daily');folder.mkdir(parents=True,exist_ok=True);(folder/name).write_bytes(blob);(folder/(name+'.CHECKSUM')).write_bytes(check)
   with zipfile.ZipFile(io.BytesIO(blob)) as z:extra=pd.read_csv(io.BytesIO(z.read(z.namelist()[0])),header=None,names=COLUMNS)
   if str(extra.iloc[0,0]) in ('open_time','open_time_ms'):extra=extra.iloc[1:]
   extra=extra.apply(pd.to_numeric);extra=extra[pd.to_datetime(extra.open_time_ms,unit='ms',utc=True).isin(missing)];f=pd.concat([f,extra],ignore_index=True).sort_values('open_time_ms');assert not f.open_time_ms.duplicated().any();repairs.append(dict(url=url,sha256=hashlib.sha256(blob).hexdigest(),rows=len(extra),checksum_verified=True))
  assert (f.close_time_ms.to_numpy()==f.open_time_ms.to_numpy()+3599999).all();f.index=pd.to_datetime(f.open_time_ms,unit='ms',utc=True);f=f.reindex(index);assert not f[['open','high','low','close']].isna().any().any();assert ((f.low>0)&(f.low<=f[['open','close']].min(axis=1))&(f.high>=f[['open','close']].max(axis=1))).all()
  if kind=='klines':combined=f[['open','high','low','close']].copy();combined['tradable']=(f.volume>0).astype(float)
  else:
   for col in ['open','high','low','close']:combined['mark_'+col]=f[col]
 combined.to_pickle(OUT/(s+'.pkl'));(OUT/(s+'-funding.json')).write_text(json.dumps(funding));audit[s]=dict(hours=len(index),missing_hours=0,funding_events=len(funding),start='2023-10-01',end_exclusive='2026-10-01');print(s,'verified',len(index),'hours',flush=True)
(OUT/'audit.json').write_text(json.dumps(audit,indent=2));(OUT/'manifest.json').write_text(json.dumps(dict(files=[receipt for *_,receipt in received],daily_repairs=repairs,collector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2));print('DONE',flush=True)
