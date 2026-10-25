import datetime as dt
import hashlib
import json
from copy import deepcopy
from zoneinfo import ZoneInfo

from server.paper_session import EXPECTED, ROOT, published, reconcile

OPEN = dt.datetime(2026,10,21,9,30,tzinfo=ZoneInfo('America/Detroit'))
CLOSE = OPEN.replace(hour=16,minute=0)


def observation():
    return {'amat': {'paper_host':'paper-api.alpaca.markets','config_sha256':EXPECTED,
        'account_identity_sha256':'456f5aadcae1fd37dc30135dffc16ba53d8d68119dd21b4937243c4207dc7c91',
        'dry_run':False,'disabled':False,'health':{'healthy':True},'orders_complete':True,
        'plan':{'config_sha256':EXPECTED,'execution_session':'2026-10-21','as_of':'2026-10-20',
                'intents':[{'client_ref':'intent','quantity':'10'}]},'orders':[]}}


def test_missing_plan_waits_until_due_and_never_claims_success():
    v=observation();v['amat']['plan']=None
    early=reconcile(v,OPEN-dt.timedelta(hours=1),OPEN,CLOSE)
    assert early['status']=='waiting' and early['missed'] is None
    late=reconcile(v,OPEN,OPEN,CLOSE)
    assert late['status']=='review' and not late['checks_complete']


def test_no_order_is_pending_then_missed_after_window():
    assert reconcile(observation(),OPEN,OPEN,CLOSE)['missed'] is None
    result=reconcile(observation(),OPEN+dt.timedelta(minutes=6),OPEN,CLOSE)
    assert result['missed']==1 and result['status']=='review'


def test_cumulative_duplicate_and_overfill_not_hidden_by_same_symbol():
    v=observation();v['amat']['orders']=[{'order_ref':k,'client_ref':'intent','filled_quantity':'6'} for k in ['a','b']]
    result=reconcile(v,CLOSE,OPEN,CLOSE)
    assert result['duplicates']==1 and result['overfilled']==1
    v['amat']['orders']=[{'order_ref':'a','client_ref':'intent','filled_quantity':'6'}]*2
    result=reconcile(v,CLOSE,OPEN,CLOSE)
    assert result['duplicates']==0 and result['overfilled']==0 and result['partial']==1


def test_incomplete_query_and_transport_error_are_not_zero_orders():
    v=observation();v['amat']['orders_complete']=False
    assert reconcile(v,CLOSE,OPEN,CLOSE)['status']=='unavailable'
    assert reconcile({'amat':{'error':'query capped'}},CLOSE,OPEN,CLOSE)['checks_complete'] is False


def test_empty_intent_plan_can_be_a_valid_no_trade_day():
    v=observation();v['amat']['plan']['intents']=[]
    result=reconcile(v,CLOSE,OPEN,CLOSE)
    assert result['status']=='verified' and result['missed']==0


def test_wrong_hash_account_cutoff_and_nonpaper_endpoint_are_flagged():
    for field,value in [('config_sha256','wrong'),('account_identity_sha256','wrong'),('paper_host','unknown')]:
        v=observation();v['amat'][field]=value
        assert reconcile(v,CLOSE,OPEN,CLOSE)['status']=='review'
    v=observation();v['amat']['plan']['as_of']='2026-10-21'
    assert any('cutoff' in s for s in reconcile(v,CLOSE,OPEN,CLOSE)['issues'])


def test_unattributed_orders_are_review_cases_not_assumed_duplicates():
    v=observation();v['amat']['orders']=[{'order_ref':'other','client_ref':'other','filled_quantity':'1'}]
    result=reconcile(v,CLOSE,OPEN,CLOSE)
    assert result['unmatched']==1 and result['duplicates']==0


def test_publication_rejects_tampering_paths_and_execution_authority(tmp_path):
    folder=tmp_path/ROOT;folder.mkdir(parents=True)
    def save(value,name='observed.json'):
        (folder/'observed.json').write_text(json.dumps(value))
        (folder/'latest.json').write_text(json.dumps({'file':name,'sha256':hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()}))
    value={'schema':'paper-session/1','execution_authority':'none','observed_at':dt.datetime.now(dt.UTC).isoformat(),'status':'waiting'}
    save(value);assert published(tmp_path)['status']=='waiting'
    value['execution_authority']='submit';save(value);assert published(tmp_path)['status']=='unavailable'
    value['execution_authority']='none';save(value,'../observed.json');assert published(tmp_path)['status']=='unavailable'
    save(value);(folder/'observed.json').write_text('{}');assert published(tmp_path)['status']=='unavailable'


def test_budgeted_whole_share_fill_preserves_unspent_authorization():
    v=observation();v['amat']['plan']['intents'][0].update(submitted_quantity='8',remaining_intent='2')
    v['amat']['orders']=[{'order_ref':'a','client_ref':'intent','filled_quantity':'8'}]
    result=reconcile(v,CLOSE,OPEN,CLOSE)
    assert result['filled']==1 and result['partial']==0 and result['overfilled']==0
    assert result['residual_intents']==1


def test_policy_withholding_is_not_a_missing_broker_order():
    v=observation();v['amat']['plan']['intents'][0]['withheld']='cash reserve'
    result=reconcile(v,CLOSE,OPEN,CLOSE)
    assert result['withheld']==1 and result['missed']==0


def test_legacy_wire_precision_requires_exact_terminal_broker_proof():
    v=observation();v['amat']['plan']['intents'][0]['quantity']='23.73519534508552379053351538'
    o={'order_ref':'a','client_ref':'intent','quantity':'23.735195345','filled_quantity':'23.735195345','status':'filled'}
    v['amat']['orders']=[o]
    result=reconcile(v,CLOSE,OPEN,CLOSE)
    assert result['filled']==1 and result['partial']==0 and result['precision_adjusted']==1
    for changes in [{'quantity':'23.735195344','filled_quantity':'23.735195344'}, {'status':'partially_filled'}, {'filled_quantity':'23.7'}]:
        v['amat']['orders']=[dict(o,**changes)]
        assert reconcile(v,CLOSE,OPEN,CLOSE)['partial']==1


def test_required_native_mode_flags_fractional_purchases_without_relabeling_legacy():
    v=observation();v['amat']['execution_mode']='legacy';v['amat']['whole_share_buys']=False
    assert not any('Native' in s for s in reconcile(v,CLOSE,OPEN,CLOSE)['issues'])
    v['amat']['required_execution']={'execution_mode':'staged-v1'}
    assert any('Native' in s for s in reconcile(v,CLOSE,OPEN,CLOSE)['issues'])
    v['amat'].update(execution_mode='staged-v1',whole_share_buys=True)
    v['amat']['orders']=[{'order_ref':'a','client_ref':'intent','quantity':'9.5','filled_quantity':'9.5','side':'buy'}]
    assert any('fractional' in s for s in reconcile(v,CLOSE,OPEN,CLOSE)['issues'])
    v['amat']['orders'][0]['side']='sell'
    assert not any('fractional' in s for s in reconcile(v,CLOSE,OPEN,CLOSE)['issues'])


def test_verified_future_plan_digest_is_bound_without_rejecting_valid_hold():
    v=observation();a=v['amat'];a['execution_mode']='staged-v1';a['whole_share_buys']=True
    a['required_execution']={'execution_mode':'staged-v1','expected_plans':{'2026-10-21':'frozen'}}
    a['previous_exchange_session']='2026-10-20';a['plan']['sha256']='frozen';a['plan']['intents']=[]
    assert reconcile(v,CLOSE,OPEN,CLOSE)['status']=='verified'
    a['plan']['sha256']='changed'
    assert any('frozen authorization' in s for s in reconcile(v,CLOSE,OPEN,CLOSE)['issues'])
    a['plan']['as_of']='2026-10-19'
    assert any('immediately preceding' in s for s in reconcile(v,CLOSE,OPEN,CLOSE)['issues'])


def test_verified_hold_plan_flags_every_broker_order_and_duplicate_identities():
    v=observation();a=v['amat'];a.update(execution_mode='staged-v1',whole_share_buys=True)
    a['required_execution']={'execution_mode':'staged-v1','expected_plans':{'2026-10-21':'hold'}}
    a['plan'].update(sha256='hold',intents=[])
    assert reconcile(v,CLOSE,OPEN,CLOSE)['status']=='verified'
    a['orders']=[{'order_ref':key,'client_ref':'unexpected','quantity':'1','filled_quantity':'0','side':'buy'} for key in ['a','b']]
    result=reconcile(v,CLOSE,OPEN,CLOSE)
    assert result['duplicates']==1 and result['unmatched']==2
    assert 'Unexpected broker orders during the verified hold session' in result['issues']
