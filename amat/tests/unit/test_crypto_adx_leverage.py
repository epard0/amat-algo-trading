import numpy as np
import pandas as pd
import pytest
from autoresearch.crypto_fast_breakout import prepare
from autoresearch.crypto_adx_leverage import replay,cost_bps
from kalshi_perps.domain import Instrument


def fixture(n=2880):
    idx=pd.date_range('2026-01-01',periods=n,freq='1min',tz='UTC');frames={};instruments={};metadata={};curves={};plans={}
    for s in ('KXDOGEPERP','KXSOLPERP'):
        p=np.full(n,100.);f=pd.DataFrame({k:p.copy() for k in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close')},index=idx);f['tradable']=1
        frames[s]=f;instruments[s]=Instrument(s,1,1,.01,1,1,'active')
        # Construct via parser so all fields have native Decimal types.
        instruments[s]=Instrument.parse(dict(ticker=s,contract_size='1',underlying_multiplier='1',tick_size='.01',fractional_trading_enabled=False,market_version=1,status='active',asset_class='Crypto'))
        metadata[s]=dict(long={'1000000':'4'},short={'1000000':'4'},initial_margin_multiplier='1.3')
        curves[s]={side:[dict(notional=200000,full_coverage_fraction=1,median_bps=0,p95_bps=2)] for side in ('bids','asks')}
        w=np.zeros(n);w[:2000]=.98;why=np.full(n,7);plans[s]=(w,why)
    return frames,instruments,metadata,curves,plans


def run(parts,lev):
    f,inst,meta,costs,plans=parts
    return replay({s:prepare(frame,{}) for s,frame in f.items()},plans,inst,meta,costs,('2026-01-01','2026-01-02'),lev)


def test_flat_market_fees_and_integer_contracts_reconcile():
    parts=fixture();stats,path,ledger,red,closed=run(parts,'1')
    assert stats['liquidations']==0
    assert stats['pnl']==pytest.approx(-stats['fees'])
    assert sum(r['net_pnl'] for r in red)==pytest.approx(stats['pnl'])
    assert all(float(fill['count']).is_integer() for fill in ledger)
    assert stats['take_profit_events']==2


def test_max_uses_published_initial_margin_curve_and_reserve():
    parts=fixture();stats,_,ledger,_,_=run(parts,'max')
    first=[x for x in ledger if x['timestamp_ms']==int(parts[0]['KXDOGEPERP'].index[0].timestamp()*1000)+6000]
    notional=sum(float(x['count'])*float(x['price']) for x in first)
    assert 2.98<notional/10000<3.02  # .98*4/1.3, integer rounded.
    assert stats['margin_rejections']==0


def test_intraminute_margin_breach_halts_pooled_account():
    parts=fixture()
    for f in parts[0].values():f.loc[f.index[100],'mark_low']=60
    stats,path,ledger,_,_=run(parts,'max')
    assert stats['halted'] and stats['liquidations']==2
    assert len(path.iloc[100:].equity.unique())==1
    assert not any(float(x['count'])>0 for x in ledger if x['timestamp_ms']>int(parts[0]['KXDOGEPERP'].index[100].timestamp()*1000))


def test_daily_resize_never_buys_back_partial():
    parts=fixture();n=2880
    for s in parts[4]:
        w=np.full(n,.98);w[100:]=.49;why=np.full(n,2);parts[4][s]=(w,why)
    _,_,ledger,_,_=run(parts,'max')
    after=[r for r in ledger if r['timestamp_ms']>=int(parts[0]['KXDOGEPERP'].index[100].timestamp()*1000)]
    assert all(float(r['count'])<0 for r in after)


def test_costs_use_next_supported_bucket_and_refuse_extrapolation():
    parts=fixture();curve=parts[3]
    assert cost_bps(curve,'KXDOGEPERP','asks',1000,'p95')==2
    assert cost_bps(curve,'KXDOGEPERP','asks',1000,'double_p95_slippage')==4
    with pytest.raises(ValueError,match='outside observed'):cost_bps(curve,'KXDOGEPERP','asks',200001,'median')


def test_second_asset_entry_rebalances_first_and_preserves_gross_target():
    parts=fixture()
    w=parts[4]['KXDOGEPERP'][0];w[:100]=0
    stats,_,ledger,_,_=run(parts,'1')
    assert stats['max_effective_leverage']<1.01
    assert stats['margin_rejections']==0
    assert any(r['symbol']=='KXSOLPERP' and float(r['count'])<0 and r['reason']=='rebalance' for r in ledger)


def test_historical_funding_is_charged_once_and_allocated_to_reductions():
    parts=fixture();frames,inst,meta,costs,plans=parts
    data={}
    for s,f in frames.items():
        when=int(f.index[480].timestamp()*1000)+47
        data[s]=prepare(f,{when//3600000:(when,'.001')})
    stats,_,ledger,red,_=replay(data,plans,inst,meta,costs,('2026-01-01','2026-01-02'),'1')
    first=[r for r in ledger if r['timestamp_ms']==int(frames['KXDOGEPERP'].index[0].timestamp()*1000)+6000]
    expected=-sum(float(r['count'])*100*.001 for r in first)
    assert stats['funding_pnl']==pytest.approx(expected)
    assert sum(r['net_pnl'] for r in red)==pytest.approx(stats['pnl'])
