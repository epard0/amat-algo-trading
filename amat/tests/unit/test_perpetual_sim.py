from decimal import Decimal as D
import pytest
from autoresearch.perpetual_sim import ContractSpec, PerpOrder, PerpetualSimulator


def account(**kwargs):
    return PerpetualSimulator(ContractSpec('SIM-BTC', multiplier='0.01',
        fee_rate='0', slippage_rate='0', **kwargs), '10000')


def trade(a, side, count, price='100', ident='entry', reduce=False, tif='immediate_or_cancel', ts=1):
    return a.submit(PerpOrder(ident, 'SIM-BTC', side, count, price,
                             time_in_force=tif, reduce_only=reduce),
                    bid=price, ask=price, mark=price, timestamp_ms=ts)


@pytest.mark.parametrize('side,expected', [('bid', '10'), ('ask', '-10')])
def test_signed_pnl(side, expected):
    a = account(); trade(a, side, '100')
    a.mark_to_market('110', 2)
    assert a.unrealized_pnl == D(expected)
    assert a.equity == D('10000')+D(expected)


def test_partial_close_and_reversal_realize_only_closed_quantity():
    a=account(); trade(a,'bid','100')
    trade(a,'ask','40','110','partial',ts=2)
    assert a.realized_pnl == D('4') and a.average_entry == 100
    trade(a,'ask','100','120','reverse',ts=3)
    assert a.realized_pnl == D('16')
    assert a.contracts == -40 and a.average_entry == 120
    trade(a,'bid','40','100','close',ts=4)
    assert a.contracts == 0 and a.realized_pnl == 24 and a.cash == 10024


@pytest.mark.parametrize('side,sign', [('bid',-1),('ask',1)])
def test_funding_sign_and_idempotence(side,sign):
    a=account(); trade(a,side,'100')
    payment=a.apply_funding(event_id='8h',rate='.001',mark='100',timestamp_ms=2)
    assert payment == sign*D('.1')
    before=a.cash
    assert a.apply_funding(event_id='8h',rate='.001',mark='100',timestamp_ms=2)==payment
    assert a.cash==before
    with pytest.raises(ValueError,match='reused'):
        a.apply_funding(event_id='8h',rate='.002',mark='100',timestamp_ms=2)


def test_multiplier_scales_pnl_not_leverage():
    a=account(); b=PerpetualSimulator(ContractSpec('SIM-BTC',multiplier='.02',fee_rate=0,slippage_rate=0),'10000')
    trade(a,'bid',100); trade(b,'bid',100)
    a.mark_to_market(110,2); b.mark_to_market(110,2)
    assert b.unrealized_pnl == 2*a.unrealized_pnl
    c=account(max_leverage=5); trade(c,'bid',100); c.mark_to_market(110,2)
    assert c.unrealized_pnl == a.unrealized_pnl


def test_reduce_only_cannot_open_or_reverse():
    a=account()
    with pytest.raises(ValueError,match='reduce_only'): trade(a,'ask',1,reduce=True)
    trade(a,'bid',10)
    result=trade(a,'ask',100,ident='reduce',reduce=True)
    assert result['fill_count']=='10' and result['canceled_count']=='90'
    assert a.contracts == 0


def test_fok_does_not_partially_reduce():
    a=account(); trade(a,'bid',10)
    with pytest.raises(ValueError,match='FOK'):
        trade(a,'ask',100,ident='reduce',reduce=True,tif='fill_or_kill')
    assert a.contracts==10


def test_rejected_margin_does_not_change_holdings_or_cash():
    a=account(); before=a.cash
    with pytest.raises(ValueError,match='margin'): trade(a,'bid','30000')
    assert a.cash==before and a.contracts==0


def test_order_idempotency_and_conflicts():
    a=account(); first=trade(a,'bid',10)
    assert trade(a,'bid',10)==first and a.contracts==10
    with pytest.raises(ValueError,match='reused'): trade(a,'bid',20)


def test_limit_rejects_adverse_execution_and_tick_rounding():
    spec=ContractSpec('SIM-BTC',multiplier=1,fee_rate='.001',slippage_rate='.0005')
    a=PerpetualSimulator(spec,10000)
    result=trade(a,'bid',1,'100')
    assert result['status']=='canceled' and a.contracts==0
    result=a.submit(PerpOrder('cross','SIM-BTC','bid',1,'101'),bid=100,ask=100,mark=100,timestamp_ms=2)
    assert result['average_fill_price']=='100.05'
    assert a.fees==D('.10005')
    assert a.equity==D('10000')-D('.05')-D('.10005')


def test_liquidation_keeps_gap_deficit_visible():
    a=PerpetualSimulator(ContractSpec('SIM-BTC',multiplier=1,fee_rate=0,slippage_rate=0,liquidation_fee_rate=0),10000)
    trade(a,'bid',150,'100')
    a.mark_to_market('10',2)
    assert a.halted and a.contracts==0 and a.equity==D('-3500')
    assert a.position()['deficit']=='3500'


def test_negative_funding_reverses_payments_and_can_liquidate():
    a=PerpetualSimulator(ContractSpec('SIM-BTC',multiplier=1,max_leverage=10,maintenance_rate='.05',fee_rate=0,slippage_rate=0),1000)
    trade(a,'ask',90,'100')
    payment=a.apply_funding(event_id='adverse',rate='-.1',mark=100,timestamp_ms=2)
    assert payment==-900 and a.halted and a.contracts==0


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity',True])
def test_nonfinite_and_boolean_inputs_rejected(value):
    with pytest.raises(ValueError): ContractSpec('SIM-BTC',multiplier=value)


def test_invalid_timestamps_and_increments():
    a=account(); trade(a,'bid',10,ts=10)
    with pytest.raises(ValueError,match='timestamps'): a.mark_to_market(100,9)
    with pytest.raises(ValueError,match='increments'): trade(a,'bid','.5',ident='bad',ts=11)


def test_payload_matches_margin_order_field_conventions():
    p=PerpOrder('id','SIM-BTC','ask','10','100').kalshi_payload()
    assert p['side']=='ask' and p['count']=='10' and p['price']=='100'
    assert p['time_in_force']=='immediate_or_cancel'
    assert p['self_trade_prevention_type']=='taker_at_cross'
    assert 'yes_price' not in p and 'action' not in p


def test_paper_api_payload_roundtrip():
    from autoresearch.perpetual_sim import KalshiStylePaperAPI
    a=account(); api=KalshiStylePaperAPI(a)
    p=PerpOrder('roundtrip','SIM-BTC','ask',10,100).kalshi_payload()
    response=api.create_order(p,bid=100,ask=100,mark=100,timestamp_ms=1)
    assert response['fill_count']=='10' and a.contracts==-10
    assert api.get_positions()['execution_authority']=='simulation_only'
    with pytest.raises(ValueError,match='fields'):
        api.create_order(p|{'yes_price':50},bid=100,ask=100,mark=100,timestamp_ms=2)
