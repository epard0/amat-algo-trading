"""Fixed read-only remote observation: no broker POST or lifecycle operations."""
import json
import subprocess
import os
import datetime as dt

SESSION = dt.date.fromisoformat(os.getenv("AMAT_MONITOR_SESSION", "2026-10-21")).isoformat()

AMAT = r'''
import datetime as dt,hashlib,json,pathlib
from executor.config import ExecutorSettings,PAPER_TRADING_HOST
from executor.broker import AlpacaPaperClient
from executor.plan_source import parse_plan
from executor.submit import client_order_id_for
from executor.report.metrics import compute_execution_quality
from executor.health import readiness_status
s=ExecutorSettings.from_environment();assert PAPER_TRADING_HOST=='paper-api.alpaca.markets'
c=AlpacaPaperClient(s.trading_key_id,s.trading_secret_key);a=c.get_account()

from decimal import Decimal
position_profit=None;position_profit_error=None
try:
    rows=c._get('/v2/positions')
    if not isinstance(rows,list):raise ValueError('Position response is not a list')
    def number(row,key):
        value=row.get(key)
        if value is None:return None
        value=Decimal(str(value))
        if not value.is_finite():raise ValueError('Non-finite position field')
        return str(value)
    positions=[dict(symbol=str(r['symbol']),side=r.get('side'),**{k:number(r,k) for k in ['qty','market_value','cost_basis','avg_entry_price','current_price','unrealized_pl','unrealized_intraday_pl']}) for r in rows]
    def total(key):
        return str(sum((Decimal(r[key]) for r in positions),Decimal(0))) if all(r[key] is not None for r in positions) else None
    position_profit=dict(observed_at=dt.datetime.now(dt.UTC).isoformat(),positions=positions,unrealized_pl=total('unrealized_pl'),unrealized_intraday_pl=total('unrealized_intraday_pl'),market_value=total('market_value'),cost_basis=total('cost_basis'),cash=str(a.cash),equity=str(a.equity),last_equity=str(a.last_equity),equity_change_since_previous_close=str(a.equity-a.last_equity) if a.last_equity else None)
except Exception as exc:
    position_profit_error=type(exc).__name__

ref=lambda x:hashlib.sha256(x.encode()).hexdigest()[:20]
day=dt.date.fromisoformat('__SESSION__')
calendar=c.get_calendar(day-dt.timedelta(days=14),day)
sessions=[x for x in calendar if x.date==day];assert len(sessions)==1
session=sessions[0];previous_session=max(x.date for x in calendar if x.date<day)
path=s.plans_dir/'__SESSION__.json';plan=parse_plan(json.loads(path.read_text())) if path.exists() else None
from zoneinfo import ZoneInfo
start=dt.datetime.combine(day,dt.time.min,ZoneInfo("America/Detroit")).astimezone(dt.UTC)
orders=c.list_orders(after=start) if dt.datetime.now(dt.UTC)>=start else ()
intents=[] if plan is None else [dict(client_ref=ref(client_order_id_for(plan,i)),symbol=i.symbol,side=i.side,quantity=str(i.quantity)) for i in plan.intents]
quality=compute_execution_quality(plan,orders).as_dict() if plan is not None else None
portfolio_path=s.state_root/'portfolio-monitor.json'
portfolio=json.loads(portfolio_path.read_text()) if portfolio_path.exists() else {}
portfolio={k:portfolio.get(k) for k in ['status','observed_at','last_reconciled_at','cash_difference','position_differences','cash_tolerance','exact_match']}
batch_summary=None
batch_path=s.state_root/'batches/__SESSION__.json'
if batch_path.exists():
    from executor.batch import load_batch, digest as batch_digest
    batch=load_batch(batch_path)
    assert plan is not None and batch['config_sha256']==plan.config_sha256 and batch['execution_session']=='__SESSION__'
    assert batch['plan_sha256']==batch_digest(plan.raw) and batch['account_id']==batch_digest(a.account_number)
    intents=[dict(client_ref=ref((r.get('order') or {}).get('client_order_id') or r['intent_id']),
        symbol=r['symbol'],side=r['side'],quantity=r['original_authorized_quantity'],
        submitted_quantity=(r.get('order') or {}).get('quantity'),withheld=r.get('withheld'),
        remaining_intent=r['remaining_intent_quantity'],cumulative_contract_fill=r['cumulative_filled_quantity']) for r in batch['intents']]
    batch_summary={k:batch.get(k) for k in ['status','pending_cash_commitments','expires_at','policy_id']}
    from decimal import Decimal
    adverse=Decimal(0);reference=Decimal(0)
    for r in batch['intents']:
        key=(r.get('order') or {}).get('client_order_id')
        for o in orders:
            if o.client_order_id==key and o.filled_avg_price is not None:
                price=Decimal(r['reference_price']);reference+=price*o.filled_quantity
                adverse+=(o.filled_avg_price-price)*o.filled_quantity*(1 if r['side']=='buy' else -1)
    quality={'decision_to_fill_slippage_bps':float(adverse/reference*10000) if reference else None,'source':'verified batch to broker fills; prior decision close'}
print(json.dumps(dict(paper_host=PAPER_TRADING_HOST,dry_run=s.dry_run,disabled=s.limits.disabled,
 whole_share_buys=getattr(s,'whole_share_buys',False),
 previous_exchange_session=str(previous_session),
 config_sha256=hashlib.sha256(s.strategy_config.read_bytes()).hexdigest(),equity=str(a.equity),
 account_identity_sha256=hashlib.sha256((s.state_root/"account-identity.json").read_bytes()).hexdigest(),
 calendar=dict(date=str(session.date),open=str(session.open_local),close=str(session.close_local)),
 health=readiness_status(s),orders_complete=True,execution_mode=s.execution_mode,batch=batch_summary,
 portfolio=portfolio,position_profit=position_profit,position_profit_error=position_profit_error,
 plan=None if plan is None else dict(config_sha256=plan.config_sha256,strategy=plan.strategy_name,
 sha256=hashlib.sha256(json.dumps(plan.raw,sort_keys=True,allow_nan=False).encode()).hexdigest(),
 as_of=str(plan.as_of_session),execution_session=str(plan.earliest_execution_session),
 intents=intents),
 orders=[dict(order_ref=ref(o.id),client_ref=ref(o.client_order_id),symbol=o.symbol,side=o.side,
 quantity=str(o.quantity),filled_quantity=str(o.filled_quantity),price=str(o.filled_avg_price) if o.filled_avg_price is not None else None,status=o.status) for o in orders],
 quality=quality)))
'''
PERPS = r'''
import pathlib,json
v=json.loads(pathlib.Path('/var/lib/kalshi-perps/adx-forward/status.json').read_text())
import sqlite3
db=sqlite3.connect('file:/var/lib/kalshi-perps/paper-doge-sol-adx14.sqlite?mode=ro',uri=True)
decision=None
for (body,) in db.execute('SELECT result FROM events ORDER BY rowid DESC LIMIT 30'):
    result=json.loads(body)
    if result.get('decision'):
        d=result['decision'];decision={k:d.get(k) for k in ['as_of_ms','ready','weights','reasons','exit_policy','policy_hash']};break
db.close()
assert v['mode']=='paper' and v['live_enabled'] is False
m=v['metrics'];print(json.dumps(dict(mode=v['mode'],state=v['state'],observed_at=v['updated_at'],strategy=v['strategy'],decision=decision,data_flow=v.get('data_flow'),
 error='Perps paper runner halted' if m.get('halted') else v.get('error_type'),
 **{k:m.get(k) for k in ['equity','fees','funding','net_pnl','net_return','fills','max_drawdown']})))
'''
result = {}
for key, container, code in [('amat','amat-executor',AMAT),('perps','kalshi-perps',PERPS)]:
    try:
        p = subprocess.run(['docker','exec',container,'python','-c',code.replace('__SESSION__',SESSION)],capture_output=True,text=True,timeout=35)
        if p.returncode:
            raise RuntimeError('Remote observation failed: '+p.stderr.splitlines()[-1][:180])
        result[key] = json.loads(p.stdout)
    except Exception as exc:
        result[key] = {'error':str(exc)}
print(json.dumps(result))
