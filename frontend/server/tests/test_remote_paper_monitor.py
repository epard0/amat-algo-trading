import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from server import paper_session, remote_paper
from server.app import Service

BASE=Path(__file__).resolve().parents[3]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec)
    sys.modules['paper_session']=paper_session
    spec.loader.exec_module(m)
    return m


worker=module('paper_worker_test',BASE/'frontend/deploy/paper-monitor/worker.py')
reader=module('paper_reader_test',BASE/'frontend/deploy/paper-monitor/reader.py')


def test_broker_calendar_handles_weekends_and_does_not_invent_sessions():
    now=dt.datetime(2026,10,24,12,tzinfo=dt.UTC)
    assert worker.selected_session(now,[{'date':'2026-10-23'},{'date':'2026-10-26'}])['date']=='2026-10-26'
    with pytest.raises(ValueError):worker.selected_session(now,[{'date':'2026-10-23'}])


def test_alert_events_are_deduplicated_and_resolution_keeps_original_session(tmp_path):
    now=dt.datetime.now(dt.UTC)
    one=worker.update_alerts({},['missed order'],now,'2026-10-21',tmp_path)
    two=worker.update_alerts(one,['missed order'],now,'2026-10-21',tmp_path)
    assert len(two['recent'])==1
    rollover=worker.update_alerts(two,[],now,'2026-10-22',tmp_path)
    assert rollover['active']  # A new session cannot erase an unresolved old discrepancy.
    failed=worker.update_alerts(two,[],now,'2026-10-21',tmp_path,reliable=False)
    assert failed['active']  # Missing evidence is not resolution.
    resolved=worker.update_alerts(two,[],now,'2026-10-21',tmp_path)
    assert resolved['recent'][-1]['state']=='resolved'
    assert resolved['recent'][-1]['session']=='2026-10-21'


def test_reader_verifies_hash_and_retained_final_without_polling_brokers(tmp_path,monkeypatch):
    monkeypatch.setattr(reader,'STORE',tmp_path);folder=tmp_path/'2026-10-21';folder.mkdir()
    value=dict(schema='paper-session/1',execution_authority='none',status='complete',target_session='2026-10-21',observed_at=dt.datetime.now(dt.UTC).isoformat(),collection_location='remote VPS')
    pointer={'file':'observation-one.json','sha256':reader.digest(value),'session':'2026-10-21'}
    (folder/'final.json').write_text(json.dumps(value));(folder/'latest.json').write_text(json.dumps(pointer));(tmp_path/'latest.json').write_text(json.dumps(pointer));(tmp_path/'health.json').write_text(json.dumps({'status':'running'}))
    assert reader.read('2026-10-21')['target_session']=='2026-10-21'
    value['target_session']='2026-10-22';(folder/'final.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):reader.read('2026-10-21')
    with pytest.raises(ValueError):reader.read('../x')


def test_remote_reader_only_accepts_registered_date_and_inert_remote_data(monkeypatch,tmp_path):
    run=Mock();monkeypatch.setattr(remote_paper,'load_config',lambda p:{'username':'operator'});monkeypatch.setattr(remote_paper,'Runner',lambda c:SimpleNamespace(run=run));monkeypatch.setattr(remote_paper,'safe_output',lambda r,c:r)
    with pytest.raises(ValueError):remote_paper.invoke(tmp_path,'2026-10-21; reboot')
    run.assert_not_called()
    run.return_value={'exit_code':0,'truncated':False,'stdout':json.dumps({'schema':'paper-session/1','execution_authority':'submit','collection_location':'remote VPS'})}
    with pytest.raises(ValueError):remote_paper.invoke(tmp_path)
    assert run.call_args.args[0]=='/home/operator/.local/bin/amat-paper-monitor-read'


def test_remote_failure_keeps_remote_cache_stale_without_local_fallback(monkeypatch,tmp_path):
    svc=Service(root=tmp_path,state=tmp_path/'state',storage=tmp_path/'storage')
    with svc.lock,svc.db:svc.db.execute("INSERT INTO observations(name,data) VALUES(?,?)",('remote.paper-session',json.dumps({'status':'monitoring','collection_location':'remote VPS','bots':[{'id':'amat','equity':123}]})))
    monitor=remote_paper.RemotePaperSession(svc);monkeypatch.setattr(remote_paper,'invoke',Mock(side_effect=RuntimeError('offline')))
    result=monitor.describe();assert result['stale'] and result['transport']=='unavailable'
    assert result['bots'][0]['equity']==123
    assert monitor.describe()==result
    assert remote_paper.invoke.call_count==1


def source(now,equity='100',fees='0',funding='0'):
    return {'amat':{'calendar':{'date':'2026-10-21','open':'09:30:00','close':'16:00:00'},'equity':equity,'error':None},'perps':{'equity':equity,'fees':fees,'funding':funding,'observed_at':now.isoformat()}}


def test_performance_closes_once_and_late_baseline_is_not_invented():
    pre=dt.datetime(2026,10,21,13,29,tzinfo=dt.UTC)
    _,state=paper_session.project(source(pre),pre,None,'2026-10-21')
    close=dt.datetime(2026,10,21,20,0,tzinfo=dt.UTC)
    result,state=paper_session.project(source(close,'110','2','1'),close,state,'2026-10-21')
    assert result['bots'][1]['known_net_cost']==1 and result['bots'][1]['pnl']==10
    late=close+dt.timedelta(minutes=25)
    final,_=paper_session.project(source(late,'130','5','0'),late,state,'2026-10-21')
    assert final['bots'][1]['equity']==110 and final['performance_end']==close.isoformat()
    missing,_=paper_session.project(source(late,'130'),late,None,'2026-10-21')
    assert missing['bots'][1]['net_return'] is None


def test_one_minute_collection_retains_entire_regular_session_curve():
    pre=dt.datetime(2026,10,21,13,29,tzinfo=dt.UTC)
    _,state=paper_session.project(source(pre),pre,None,'2026-10-21')
    for minute in range(391):
        now=pre+dt.timedelta(minutes=minute+1)
        result,state=paper_session.project(source(now),now,state,'2026-10-21')
    assert len(result['samples'])==391
    assert result['samples'][0]['at']=='2026-10-21T13:30:00+00:00'
    assert result['samples'][-1]['at']=='2026-10-21T20:00:00+00:00'


def test_failed_cycle_cannot_clone_old_order_alerts_or_clear_source_outages(tmp_path):
    now=dt.datetime.now(dt.UTC)
    old=worker.update_alerts({},['missed order'],now,'2026-10-21',tmp_path)
    rollover=worker.update_alerts(old,[],now,'2026-10-22',tmp_path)
    failed=worker.update_alerts(rollover,['Remote monitor cycle failed: RuntimeError'],now,'2026-10-22',tmp_path,reliable=False,source_available=False)
    assert len(failed['active'])==2
    recovered=worker.update_alerts(failed,[],now,'2026-10-22',tmp_path)
    assert list(recovered['active'].values())==['missed order']


def test_review_overlay_requires_exact_original_digest_and_keeps_original_audit(tmp_path,monkeypatch):
    monkeypatch.setattr(reader,'STORE',tmp_path);folder=tmp_path/'2026-10-21';folder.mkdir()
    message='Unfilled or partial intents remain after the opening window'
    value=dict(schema='paper-session/1',execution_authority='none',observed_at=dt.datetime.now(dt.UTC).isoformat(),audit={'partial':3},alerts=[{'id':'x','session':'2026-10-21','message':message}],alert_events=[])
    pointer={'file':'observation-one.json','sha256':reader.digest(value),'session':'2026-10-21'}
    (folder/pointer['file']).write_text(json.dumps(value));(folder/'latest.json').write_text(json.dumps(pointer))
    review={'source_sha256':'wrong','reviewed_at':value['observed_at'],'audit':{'partial':0,'precision_adjusted':3,'issues':[]}}
    (folder/'review.json').write_text(json.dumps(review));assert reader.read('2026-10-21')['audit']['partial']==3
    review['source_sha256']=pointer['sha256'];(folder/'review.json').write_text(json.dumps(review))
    result=reader.read('2026-10-21');assert result['audit']['partial']==0 and result['original_audit']['partial']==3
    assert result['alerts']==[] and result['alert_events'][-1]['state']=='resolved'


def test_repaired_portfolio_alert_can_clear_with_fresh_source_without_erasing_old_plan_gap(tmp_path):
    now=dt.datetime.now(dt.UTC)
    old=worker.update_alerts({},['Session plan is missing','AMAT portfolio reconciliation blocks order readiness'],now,'2026-10-22',tmp_path)
    new=worker.update_alerts(old,[],now,'2026-10-23',tmp_path,source_available=False)
    assert len(new['active'])==2
    repaired=worker.update_alerts(new,[],now,'2026-10-23',tmp_path,source_available=True)
    assert list(repaired['active'].values())==['Session plan is missing']


def test_stale_or_unready_perps_signal_produces_alert_without_crashing_collection():
    now=dt.datetime.now(dt.UTC)
    publication={'audit':{'issues':[]},'perps_fresh':True}
    for decision in [None,{'ready':False,'as_of_ms':int(now.timestamp()*1000)}, {'ready':True,'as_of_ms':int((now-dt.timedelta(hours=3)).timestamp()*1000)}]:
        result=worker.observation_issues(publication,{'perps':{'decision':decision}},now)
        assert result==['Perps strategy decision is stale or not ready']
    assert worker.observation_issues(publication,{'perps':{'decision':{'ready':True,'as_of_ms':int(now.timestamp()*1000)}}},now)==[]


def test_dated_findings_keep_their_immutable_source_when_live_pointer_advances(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, 'STORE', tmp_path)
    folder = tmp_path / '2026-10-23'
    folder.mkdir()
    old = dict(schema='paper-session/1', execution_authority='none', observed_at='2026-10-23T15:00:00+00:00')
    live = dict(old, observed_at='2026-10-23T15:01:00+00:00')
    source = dict(file='observation-old.json', sha256=reader.digest(old), session='2026-10-23')
    (folder / source['file']).write_text(json.dumps(old))
    (folder / 'observation-live.json').write_text(json.dumps(live))
    (folder / 'latest.json').write_text(json.dumps(dict(source, file='observation-live.json', sha256=reader.digest(live))))
    findings = dict(source=source, through=old['observed_at'], summary='Interim, not final')
    (folder / 'findings.json').write_text(json.dumps(findings))
    result = reader.read('2026-10-23')
    assert result['observed_at'] == live['observed_at']
    assert result['session_findings']['through'] == old['observed_at']
    (folder / source['file']).write_text(json.dumps(dict(old, execution_authority='submit')))
    with pytest.raises(ValueError):
        reader.read('2026-10-23')


def test_broker_position_profit_is_separate_from_session_equity_and_keeps_missing_unknown():
    # Execute the exact isolated GET/parse block deployed inside the paper container.
    text = (BASE/'frontend/scripts/paper-session-remote.py').read_text()
    block = text.split('from decimal import Decimal\n', 1)[1].split('ref=lambda', 1)[0]
    from decimal import Decimal
    account = SimpleNamespace(cash=Decimal('20000'), equity=Decimal('102000'), last_equity=Decimal('101500'))
    def parse(rows):
        client = SimpleNamespace(_get=lambda path: rows)
        scope = dict(c=client, a=account, dt=dt, Decimal=Decimal)
        exec(block, scope)
        return scope
    rows = [dict(symbol='SPY', side='long', qty='100', market_value='50000', cost_basis='48000', unrealized_pl='2000', unrealized_intraday_pl='500')]
    value = parse(rows)['position_profit']
    assert value['unrealized_pl'] == '2000'
    assert value['equity_change_since_previous_close'] == '500'
    assert value['unrealized_intraday_pl'] == '500'
    rows[0].pop('unrealized_pl')
    assert parse(rows)['position_profit']['unrealized_pl'] is None
    rows[0]['unrealized_pl'] = 'NaN'
    assert parse(rows)['position_profit_error'] == 'ValueError'
    assert parse([])['position_profit']['unrealized_pl'] == '0'
    short = parse([dict(symbol='QQQ', side='short', qty='-5', market_value='-450', cost_basis='-500', unrealized_pl='50', unrealized_intraday_pl='-10')])['position_profit']
    assert short['unrealized_pl'] == '50' and short['unrealized_intraday_pl'] == '-10'


def test_final_review_never_calls_missing_orders_a_verified_hold(tmp_path):
    (tmp_path/'evidence').mkdir()
    pub = dict(observed_at='2026-10-23T20:25:00+00:00', performance_end='2026-10-23T20:00:00+00:00', bots=[], audit={'status':'verified','planned':0}, diagnostics={'amat':{'required_execution':{'expected_plans':{'2026-10-23':'pinned'}}}})
    pointer = dict(session='2026-10-23', file='observation-last.json', sha256='a')
    raw = dict(amat=dict(config_sha256='current', orders_complete=True, plan={'sha256':'pinned'}, orders=[]), perps=dict(decision={'weights':{'DOGE':'0'}}, fills=0))
    path=tmp_path/'evidence/observation-last.json';path.write_text(json.dumps(raw))
    result=worker.final_findings(tmp_path,pointer,pub)
    assert result['final'] and result['summary'].startswith('Verified hold:')
    raw['amat']['orders']=[{'order_ref':'unexpected'}];path.write_text(json.dumps(raw))
    assert worker.final_findings(tmp_path,pointer,pub)['summary'].startswith('Final review requires attention')
    raw['amat']['orders']=[];raw['amat']['orders_complete']=False;path.write_text(json.dumps(raw))
    assert worker.final_findings(tmp_path,pointer,pub)['summary'].startswith('Final review requires attention')
