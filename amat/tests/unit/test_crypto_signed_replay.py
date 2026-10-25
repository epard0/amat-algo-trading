import numpy as np
import pandas as pd
import pytest
from autoresearch.crypto_fast_breakout import prepare
from autoresearch.crypto_guarded_replay import replay_guarded
from autoresearch.crypto_signed_replay import replay_signed
from autoresearch.crypto_signed_adx import signed_plan, fresh_signed_plan
from test_crypto_adx_leverage import fixture


def run(parts,plans=None,lev='1',funding=None):
    f,inst,meta,costs,original=parts
    data={s:prepare(frame,(funding or {}).get(s,{})) for s,frame in f.items()}
    return replay_signed(data,plans or original,inst,meta,costs,('2026-01-01','2026-01-02'),lev)


def test_long_only_exact_guarded_kernel_parity():
    parts=fixture();f,inst,meta,costs,plans=parts
    data={s:prepare(frame,{}) for s,frame in f.items()}
    for lev in ('1','2','max'):
        a,ap,al,ar,ac=replay_guarded(data,plans,inst,meta,costs,('2026-01-01','2026-01-02'),lev)
        b,bp,bl,br,bc=run(parts,lev=lev)
        assert a==b and al==bl and ar==br and ac==bc
        np.testing.assert_equal(ap.equity.to_numpy(),bp.equity.to_numpy())


def test_short_profits_from_falls_receives_positive_funding_and_reconciles():
    parts=fixture();plans={s:(-w,why) for s,(w,why) in parts[-1].items()};funding={}
    for s,f in parts[0].items():
        for col in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close'):
            f.loc[f.index[100]:,col]=90
        when=int(f.index[480].timestamp()*1000)+47;funding[s]={when//3600000:(when,'.001')}
    stats,_,ledger,reductions,_=run(parts,plans,funding=funding)
    assert stats['pnl']>0 and stats['funding_pnl']>0
    assert float(ledger[0]['count'])<0
    assert sum(r['net_pnl'] for r in reductions)==pytest.approx(stats['pnl'])
    assert stats['liquidations']==0


def test_short_liquidates_on_high_not_low():
    parts=fixture();plans={s:(-w,why) for s,(w,why) in parts[-1].items()}
    for f in parts[0].values():f.loc[f.index[100],'mark_high']=200
    stats,_,ledger,_,_=run(parts,plans,lev='2')
    assert stats['liquidations']==2
    assert all(float(r['price'])>=200 for r in ledger if r['reason']=='liquidation')


@pytest.mark.parametrize('asset',['DOGE','SOL'])
def test_short_signal_is_symmetric_default_engine(asset):
    idx=pd.date_range('2025-01-01',periods=24*90,freq='1h',tz='UTC')
    c=100+np.sin(np.arange(len(idx))/30)*5-np.arange(len(idx))*.02
    f=pd.DataFrame(dict(open=c,high=c+.01,low=c-.01,close=c,tradable=1),index=idx)
    mirrored=f.copy()
    for name,other in [('open','open'),('close','close'),('high','low'),('low','high')]:mirrored[name]=1000-f[other]
    short,why=signed_plan(f,asset,'short');long,lwhy=signed_plan(mirrored,asset,'long')
    np.testing.assert_allclose(short,-long,equal_nan=True);np.testing.assert_equal(why,lwhy)
    assert np.any(short<0)


def test_fresh_start_skips_a_carried_short():
    index=pd.date_range('2026-01-01',periods=6,freq='1D',tz='UTC')
    w=np.array([-.98,-.98,-.98,0,-.98,0]);why=np.zeros(6)
    result,_=fresh_signed_plan(w,why,index,('2026-01-02','2026-01-06'))
    np.testing.assert_equal(result,[ -.98,0,0,0,-.98,0])


def test_short_partial_does_not_buy_back_and_negative_funding_is_paid():
    parts=fixture();plans={};funding={}
    for s,(w,why) in parts[-1].items():
        w=-w.copy();why=why.copy();w[500:2000]=-.49;why[500:2000]=2;plans[s]=(w,why)
        f=parts[0][s];when=int(f.index[480].timestamp()*1000)+47
        funding[s]={when//3600000:(when,'-.001')}
    stats,_,ledger,reductions,_=run(parts,plans,funding=funding)
    assert stats['funding_pnl']<0
    counts={s:0. for s in parts[0]};previous={s:None for s in parts[0]}
    for row in ledger:
        s=row['symbol'];q=counts[s];counts[s]+=float(row['count'])
        if row['reason']=='partial_tp':
            assert q<0 and float(row['count'])>0 and abs(counts[s])<abs(q)
            previous[s]=abs(counts[s])
        elif previous[s] is not None:
            assert abs(counts[s])<=previous[s];previous[s]=abs(counts[s])
    assert stats['take_profit_events']>=2


def test_short_uses_published_short_margin_curve():
    parts=fixture();plans={s:(-w,why) for s,(w,why) in parts[-1].items()}
    for m in parts[2].values():m['short']={k:'1.1' for k in m['short']}
    stats,_,ledger,_,_=run(parts,plans,lev='max')
    assert stats['max_effective_leverage']<1.2
    assert stats['margin_rejections']==0


def test_short_guard_freezes_covers_without_claiming_a_loss_cap():
    parts=fixture();plans={s:(-w,why) for s,(w,why) in parts[-1].items()}
    for f in parts[0].values():
        for col in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close'):
            f.loc[f.index[100]:f.index[199],col]=140
    stats,_,ledger,_,_=run(parts,plans)
    assert stats['guard_triggered']
    assert all(row['reason'] in ('entry','terminal') for row in ledger)


def test_guarded_short_closes_before_missing_mark_even_after_flat_signal():
    parts=fixture();plans={}
    for s,(w,why) in parts[-1].items():
        w=-w.copy();w[250:]=0;plans[s]=(w,why)
        f=parts[0][s]
        for col in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close'):
            f.loc[f.index[100]:f.index[199],col]=140
        f['mark_available']=True;f.loc[f.index[400],'mark_available']=False
    stats,_,ledger,_,_=run(parts,plans)
    assert stats['guard_triggered'] and stats['blackout_closes_after_guard']==2
    closes=[r for r in ledger if r['reason']=='blackout']
    assert len(closes)==2
    assert all(r['timestamp_ms']==int(parts[0]['KXDOGEPERP'].index[399].timestamp()*1000) for r in closes)


def test_signed_configuration_cannot_enable_execution(tmp_path):
    import json
    from autoresearch.crypto_signed_api import load_config
    config=dict(scope='research_only',directions={'KXDOGEPERP':'both','KXSOLPERP':'long'},leverage='max',loss_guard_fraction=.35)
    p=tmp_path/'config.json';p.write_text(json.dumps(config));assert load_config(p)==config
    config['scope']='paper';p.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='research only'):load_config(p)
