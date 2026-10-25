import numpy as np
import pytest
from pydantic import ValidationError

from autoresearch.funded import trade_day
from autoresearch.funded_batches import Policy, fallback, score, unseen_proposals


def test_model_cannot_raise_risk_or_add_code():
    with pytest.raises(ValidationError):
        Policy(risk=1000)
    with pytest.raises(ValidationError):
        Policy(code='submit_order()')


def test_failure_penalty_and_worst_scenario_selection():
    assert score([{'mean_pnl':500,'failed':1,'windows':2},
                  {'mean_pnl':100,'failed':0,'windows':2}])==-500


def test_fallback_changes_one_axis_with_valid_bounds():
    p=Policy()
    for batch in range(1,21):
        for q in fallback(p,batch):
            assert sum(p.model_dump()[k]!=q.model_dump()[k] for k in p.model_dump())<=1


def test_exhausted_neighborhood_produces_new_trials():
    p=Policy()
    seen={p.digest(),*(q.digest() for i in range(1,8) for q in fallback(p,i))}
    proposals=unseen_proposals(p,13,seen,[p])
    assert len(proposals)==3
    assert len({q.digest() for q in proposals})==3
    assert all(q.digest() not in seen for q in proposals)


def test_batch_of_five_contains_five_unique_new_trials():
    p=Policy()
    seen={p.digest()}
    for batch in range(1,21):
        policies=unseen_proposals(p,batch,seen,[],count=5)
        assert len(policies)==5
        assert all(q.digest() not in seen for q in policies)
        seen.update(q.digest() for q in policies)
    assert len(seen)==101


def test_narrower_stop_can_make_contract_affordable():
    b=np.tile([20000.,20001.,19999.,20000.,1000.],(390,1))
    b[16:,:4]+=3
    cost={'commission_side':1.,'slip_ticks':1}
    a=trade_day(b,{'opening_minutes':15,'risk':10},cost,50000,48000,'eod')
    c=trade_day(b,{'opening_minutes':15,'risk':10,'stop_fraction':.25},cost,50000,48000,'eod')
    assert a['contracts']==0
    assert c['contracts']>0
