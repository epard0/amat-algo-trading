import csv
import datetime as dt

import numpy as np
import pytest

from autoresearch.funded import evaluate, initialize, load_sessions, run, trade_day


def bars():
    b = np.tile([20000., 20001., 19999., 20000., 1000.], (390, 1))
    b[16:, :4] += 3
    return b


def test_unaffordable_contract_abstains():
    result = trade_day(bars(), {"opening_minutes":15,"risk":1}, {"slip_ticks":1,"commission_side":1}, 50000,48000,"eod")
    assert result["contracts"] == 0
    assert result["pnl"] == 0


def test_next_open_and_tick_costs():
    b = bars()
    result = trade_day(b,{"opening_minutes":15,"risk":10},{"slip_ticks":1,"commission_side":1},50000,48000,"eod")
    assert result["contracts"] == 0  # 4.25 point risk + exit slip + fees = $11
    result = trade_day(b,{"opening_minutes":15,"risk":12},{"slip_ticks":1,"commission_side":1},50000,48000,"eod")
    assert result["contracts"] == 1
    assert result["entry"] == 20003.25
    assert result["pnl"] == -3  # round trip two ticks ($1) and $2 commissions


def test_gap_overshoot_causes_failure():
    b = bars()
    b[18:, :4] -= 100
    result = trade_day(b,{"opening_minutes":15,"risk":50},{"slip_ticks":1,"commission_side":1},50000,49900,"eod")
    assert result["failed"]
    assert result["reason"] == "floor_gap"


def test_intraday_trails_unrealized_eod_does_not():
    b = bars()
    b[18,1] = 21000
    c, cost = {"opening_minutes":15,"risk":50}, {"slip_ticks":1,"commission_side":1}
    intra = trade_day(b,c,cost,50000,48000,"intraday")
    eod = trade_day(b,c,cost,50000,48000,"eod")
    assert intra["failed"]
    assert intra["floor"] > 48000
    assert not eod["failed"]
    assert eod["floor"] == 48000


def test_windows_reset_accounts_and_omit_truncated_starts():
    start=dt.date(2026,1,5)
    sessions=[(str(start+dt.timedelta(days=i)),None) for i in range(45)]
    result=evaluate(sessions,{"opening_minutes":15,"risk":50},{"slip_ticks":1,"commission_side":1},"eod")
    assert result["windows"] == 16
    assert result["failed"] == result["passed"] == 0
    assert all(x["pnl"] == 0 for x in result["outcomes"])


def test_frozen_protocol_cannot_be_overwritten(tmp_path):
    initialize(tmp_path)
    with pytest.raises(ValueError,match="frozen"):
        initialize(tmp_path)


def test_run_rejects_changed_profit_target(tmp_path):
    initialize(tmp_path)
    path=tmp_path/'protocol.json'
    path.write_text(path.read_text().replace('"target": 3000','"target": 300'))
    with pytest.raises(ValueError,match="Frozen protocol changed"):
        run(tmp_path)


def test_missing_data_is_not_success(tmp_path):
    initialize(tmp_path)
    r=run(tmp_path)
    assert r['status']=='blocked_data'
    assert r['promotion']=='DISABLED'


def test_loader_rejects_duplicate_and_off_tick(tmp_path):
    p=tmp_path/'bars.csv'
    def save(prices):
        with p.open('w',newline='') as f:
            w=csv.writer(f)
            w.writerow(['datetime','open','high','low','close','volume'])
            w.writerows(prices)
    row=['2026-01-20T14:30:00',20000,20001,19999,20000,10]
    save([row,row])
    with pytest.raises(ValueError,match='duplicate'):
        load_sessions(p,'UTC')
    row[1]=20000.1
    save([row])
    with pytest.raises(ValueError,match='tick size'):
        load_sessions(p,'UTC')
