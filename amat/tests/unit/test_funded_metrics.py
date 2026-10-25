from autoresearch.funded_metrics import PROFILES, diagnostics


def path(values):
    cash=50000.
    rows=[]
    for x in values:
        cash+=x
        rows.append({'pnl':x,'equity':cash,'floor':48000.,'contracts':1,'status':'observed'})
    return {'daily':rows}


def test_apex_boundary_is_strict_topstep_boundary_inclusive():
    p=path([250,250])
    assert diagnostics(p,PROFILES['apex_50k_eod_pa'])['consistency_satisfied'] is False
    assert diagnostics(p,PROFILES['topstep_50k_combine'])['consistency_satisfied'] is True


def test_nonprofitable_consistency_is_unknown():
    r=diagnostics(path([250,-300]),PROFILES['apex_50k_eod_pa'])
    assert r['consistency_fraction'] is None
    assert r['actual_payout_eligibility'] is None
    assert r['qualifying_days_against_profile']==1
    assert r['maximum_eod_drawdown']==300


def test_tradeify_does_not_use_wrong_commission_basis():
    r=diagnostics(path([200,300]),PROFILES['tradeify_select_evaluation'])
    assert r['consistency_satisfied'] is None


def test_topstep_target_adjusts_after_outlier_day():
    r=diagnostics(path([2200,100]),PROFILES['topstep_50k_combine'])
    assert r['adjusted_evaluation_target']==4000
