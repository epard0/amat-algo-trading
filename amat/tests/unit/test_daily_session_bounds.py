"""Select completed daily SIP bars without requesting future intraday time."""
import datetime as dt
from unittest.mock import Mock
import pytest
from amat.data.alpaca import AlpacaCredentials,AlpacaMarketData

@pytest.mark.parametrize('day,expected',[(dt.date(2026,10,22),'2026-10-22T04:00:00Z'),(dt.date(2026,11,5),'2026-11-05T05:00:00Z')])
def test_daily_bounds_select_inclusive_session_stamp_in_exchange_timezone(day,expected):
    client=AlpacaMarketData(AlpacaCredentials('test-key','test-secret'))
    page={'bars':{'SPY':[dict(t=expected,o=100,h=102,l=99,c=101,v=10,vw=100.5,n=3)]}}
    client._paginate=Mock(return_value=iter([page]))
    bars=client.daily_bars(['SPY'],day,day,adjustment='all')
    params=client._paginate.call_args.args[1]
    assert params['start']==params['end']==expected
    assert params['feed']=='sip' and params['adjustment']=='all' and params['timeframe']=='1Day'
    assert bars[0].session_date==day and bars[0].close==101
