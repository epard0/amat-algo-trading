import numpy as np
import pytest
from autoresearch.crypto_adx_leverage import replay
from autoresearch.crypto_guarded_replay import replay_guarded
from autoresearch.crypto_fast_breakout import prepare
from test_crypto_adx_leverage import fixture


def run(parts,leverage='1',guard=.35,funding=None):
    f,inst,meta,costs,plans=parts
    data={s:prepare(frame,(funding or {}).get(s,{})) for s,frame in f.items()}
    return replay_guarded(data,plans,inst,meta,costs,('2026-01-01','2026-01-02'),leverage,guard_fraction=guard)


def test_disabled_guard_is_exact_original_kernel():
    parts=fixture();f,inst,meta,costs,plans=parts;data={s:prepare(frame,{}) for s,frame in f.items()}
    before,bp,bl,_,_=replay(data,plans,inst,meta,costs,('2026-01-01','2026-01-02'),'max')
    after,ap,al,_,_=run(parts,'max',None)
    np.testing.assert_equal(bp.equity.to_numpy(),ap.equity.to_numpy());assert before['pnl']==after['pnl'];assert bl==al


def test_guard_triggers_between_boundaries_freezes_exits_and_never_resumes():
    parts=fixture()
    for frame in parts[0].values():
        for col in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close'):
            frame.loc[frame.index[100]:frame.index[199],col]=60
    stats,path,ledger,_,_=run(parts)
    assert stats['guard_triggered'] and stats['first_halt_cause']=='loss_guard'
    assert stats['liquidations']==0
    assert stats['guard_timestamp_ms']==int(parts[0]['KXDOGEPERP'].index[100].timestamp()*1000)
    assert stats['guard_equity']<6500
    # Both normal exit signals at minute2000 are suppressed, despite price recovery.
    assert all(r['reason']=='entry' or r['reason']=='terminal' for r in ledger)
    assert all(float(p['count'])>0 for p in stats['positions_before_terminal'].values())
    assert path.iloc[150].equity<6500
    assert path.iloc[250].equity>6500


def test_guard_is_initial_equity_not_peak_drawdown():
    parts=fixture()
    for frame in parts[0].values():
        for col in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close'):
            frame.loc[frame.index[100]:frame.index[199],col]=200
    stats,_,_,_,_=run(parts)
    assert stats['max_drawdown']<-.35
    assert not stats['guard_triggered']


def test_funding_continues_after_guard_and_reconciles():
    parts=fixture();funding={}
    for s,frame in parts[0].items():
        for col in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close'):
            frame.loc[frame.index[100]:,col]=60
        when=int(frame.index[480].timestamp()*1000)+47;funding[s]={when//3600000:(when,'.001')}
    stats,_,_,reductions,_=run(parts,funding=funding)
    assert stats['funding_after_guard']<0
    assert stats['funding_after_guard']==pytest.approx(stats['funding_pnl'])
    assert sum(r['net_pnl'] for r in reductions)==pytest.approx(stats['pnl'])


def test_maintenance_liquidation_can_follow_guard():
    parts=fixture()
    for frame in parts[0].values():
        for col in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close'):
            frame.loc[frame.index[100]:,col]=70
            frame.loc[frame.index[200]:,col]=60
    stats,_,ledger,_,_=run(parts,'2')
    assert stats['guard_triggered'] and stats['first_halt_cause']=='loss_guard'
    assert stats['liquidations']==2
    assert any(r['reason']=='liquidation' for r in ledger)


def test_funding_credit_is_applied_before_the_boundary_guard_check():
    parts=fixture();funding={}
    # Choose a funding-boundary price below6500 before a positive funding credit.
    for s,frame in parts[0].items():
        for col in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close'):
            frame.loc[frame.index[480]:frame.index[480],col]=63.8
        when=int(frame.index[480].timestamp()*1000)+47;funding[s]={when//3600000:(when,'-.01')}
    stats,_,_,_,_=run(parts,funding=funding)
    assert stats['funding_pnl']>0
    assert not stats['guard_triggered']


def test_training_selection_excludes_guard_halts_even_if_holdings_recover():
    from autoresearch.crypto_guard_grid_study import select_training
    b=dict(window=20,leverage='1',pnl=100,max_drawdown=-.2,guard_triggered=False,liquidations=0,margin_rejections=0,entries=6,capacity_supported=True)
    rows=[b,dict(b,leverage='2',pnl=200),dict(b,window=5,leverage='max',pnl=1000,guard_triggered=True),dict(b,window=10,pnl=500,liquidations=1)]
    selection=select_training(rows)
    assert selection['winner']['leverage']=='2'
    assert selection['unconstrained_pnl_leader']['window']==5


def test_intraminute_low_is_not_itself_an_observed_loss_guard_trigger():
    parts=fixture()
    for f in parts[0].values():f.loc[f.index[100],'mark_low']=60
    stats,_,_,_,_=run(parts)
    assert not stats['guard_triggered']
    assert stats['liquidations']==0
