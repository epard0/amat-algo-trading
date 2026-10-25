"""Read-only, source-bound paper-session reconciliation and dashboard projections."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
from collections import defaultdict
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = "amat/outputs/paper-monitor/2026-10-21"
EXPECTED = "4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8"


def reconcile(observation: dict, now: dt.datetime, opening: dt.datetime, closing: dt.datetime) -> dict:
    """Absence is pending before its deadline; failures never become zero counts."""
    amat = observation.get("amat", {})
    if amat.get("error"):
        return {"status": "unavailable", "issues": [amat["error"]], "checks_complete": False}
    issues = []
    required=amat.get('required_execution')
    if required:
        if amat.get('execution_mode') != required.get('execution_mode') or amat.get('whole_share_buys') is not True:
            issues.append('Native whole-share paper execution is not enabled')
        if any(o.get('side')=='buy' and Decimal(o['quantity']) != Decimal(o['quantity']).to_integral_value() for o in amat.get('orders',[])):
            issues.append('A native paper purchase used fractional shares')
    if amat.get("paper_host") != "paper-api.alpaca.markets":
        issues.append("Paper endpoint could not be verified")
    if amat.get("config_sha256") != EXPECTED:
        issues.append("Running strategy differs from the verified default")
    if amat.get("dry_run") or amat.get("disabled"):
        issues.append("Paper executor is not enabled")
    if amat.get("account_identity_sha256") != "456f5aadcae1fd37dc30135dffc16ba53d8d68119dd21b4937243c4207dc7c91":
        issues.append("Paper account identity differs from deployment receipt")
    plan = amat.get("plan")
    deadline = opening + dt.timedelta(minutes=5)
    if not plan:
        if now >= opening - dt.timedelta(minutes=5):
            issues.append("Session plan is missing")
        return {"status": "review" if issues else "waiting", "issues": issues,
                "checks_complete": False, "planned": None, "missed": None, "duplicates": None}
    if plan.get("execution_session") != opening.date().isoformat() or plan.get("as_of", "9999") >= opening.date().isoformat():
        issues.append("Plan session or decision cutoff is incorrect")
    if amat.get('previous_exchange_session') and plan.get('as_of') != amat['previous_exchange_session']:
        issues.append('Plan does not use the immediately preceding exchange session')
    expected_plan=(required or {}).get('expected_plans',{}).get(opening.date().isoformat())
    if expected_plan and plan.get('sha256') != expected_plan:
        issues.append('Session plan differs from its verified frozen authorization')
    if required and amat.get('batch') and amat['batch'].get('policy_id') != required.get('policy_id'):
        issues.append('Execution batch policy differs from the authorized whole-share policy')
    if not amat.get("health", {}).get("healthy", False):
        issues.append("Executor health is not verified")
    if plan["config_sha256"] != EXPECTED:
        issues.append("Session plan uses a different strategy")
    if not amat.get("orders_complete"):
        issues.append("Broker order coverage is incomplete")
        return {"status": "unavailable", "issues": issues, "checks_complete": False}
    orders = amat.get("orders", [])
    groups = defaultdict(list)
    for order in orders:
        groups[order["client_ref"]].append(order)
    duplicates = sum(len({o["order_ref"] for o in group}) > 1 for group in groups.values())
    if duplicates:
        issues.append("Multiple broker orders share an intent identity")
    missed = partial = overfilled = filled = withheld = residual = precision_adjusted = 0
    expected = {x["client_ref"] for x in plan["intents"]}
    for intent in plan["intents"]:
        matched = {o["order_ref"]: o for o in groups.get(intent["client_ref"], [])}
        qty = sum((Decimal(o["filled_quantity"]) for o in matched.values()), Decimal(0))
        target = Decimal(intent["quantity"])
        if intent.get("remaining_intent") is not None and Decimal(intent['remaining_intent']) > 0:
            residual += 1
        if intent.get("withheld") and not matched:
            withheld += 1
            continue
        send_target = Decimal(intent.get('submitted_quantity') or intent['quantity'])
        # Legacy wire quantities are truncated to nine places. Accept only an
        # exact, single broker order proving that conversion, never arbitrary
        # undersizing or a merely near-complete partial fill.
        if not intent.get('submitted_quantity') and len(matched) == 1:
            order = next(iter(matched.values()))
            wire_target = target.quantize(Decimal('1e-9'), rounding=ROUND_DOWN)
            if (target != wire_target and Decimal(order.get('quantity', '-1')) == wire_target
                    and order.get('status') == 'filled' and qty == wire_target):
                send_target = wire_target
                precision_adjusted += 1
        if qty > target + Decimal("1e-12"):
            overfilled += 1
        if qty >= send_target - Decimal("1e-12"):
            filled += 1
        elif qty > 0:
            partial += 1
        elif now >= deadline:
            missed += 1
    unmatched = sum(o["client_ref"] not in expected for o in orders)
    if expected_plan and not expected and orders:
        issues.append('Unexpected broker orders during the verified hold session')
    if overfilled:
        issues.append("Cumulative fills exceed planned quantity")
    if now >= deadline and (missed or partial):
        issues.append("Unfilled or partial intents remain after the opening window")
    if unmatched:
        issues.append("Other orders need attribution; they are not assumed duplicates")
    return dict(status="review" if issues else "verified" if now >= deadline else "pending",
                issues=issues, checks_complete=now >= deadline, planned=len(expected), filled=filled,
                missed=missed if now >= deadline else None, partial=partial, duplicates=duplicates,
                overfilled=overfilled, withheld=withheld, residual_intents=residual, precision_adjusted=precision_adjusted, unmatched=unmatched, quality=amat.get("quality"),
                phase="after_close" if now >= closing else "session" if now >= opening else "before_open")


def published(root: Path) -> dict:
    folder = root / ROOT
    try:
        pointer = folder / "latest.json"
        if pointer.is_symlink() or pointer.stat().st_size > 4096:
            raise ValueError("Invalid pointer")
        p = json.loads(pointer.read_text())
        if Path(p["file"]).name != p["file"]:
            raise ValueError("Invalid publication path")
        path = folder / p["file"]
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ValueError("Invalid publication")
        value = json.loads(path.read_text())
        h = hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if h != p["sha256"] or value.get("schema") != "paper-session/1" or value.get("execution_authority") != "none":
            raise ValueError("Unverified paper observation")
        age = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(value["observed_at"])).total_seconds()
        return {**value, "stale": (value.get("status") != "complete" and age > 600) or age < -60}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "message": "Paper session evidence is not available yet."}


def project(observation: dict, now: dt.datetime, previous: dict | None, target_session: str) -> tuple[dict, dict]:
    """Project source observations without broker calls or mutable caller state."""
    zone=ZoneInfo("America/Detroit")
    calendar=observation.get('amat',{}).get('calendar',{})
    session = calendar.get('date') or target_session
    if session != target_session:raise ValueError('Unexpected broker session')
    opening=dt.datetime.fromisoformat(session+'T'+calendar.get('open','09:30:00')).replace(tzinfo=zone)
    closing=dt.datetime.fromisoformat(session+'T'+calendar.get('close','16:00:00')).replace(tzinfo=zone)
    local=now.astimezone(zone)
    old = json.loads(json.dumps(previous)) if previous else {'samples':[]}
    # Only valid pre-open observations can establish a session baseline.
    perps_time=observation.get('perps',{}).get('observed_at')
    baseline_fresh=perps_time and 0<=(now-dt.datetime.fromisoformat(perps_time)).total_seconds()<=300
    if local<opening and baseline_fresh and not any(observation.get(k,{}).get('error') for k in ['amat','perps']):
        old['baseline']={'observed_at':now.isoformat(),'amat':observation['amat'],'perps':observation['perps']}
    baseline=old.get('baseline');in_session=local>=opening
    if in_session and (not baseline or (opening-dt.datetime.fromisoformat(baseline['observed_at'])).total_seconds()>1800):
        baseline=None
    audit=reconcile(observation,local,opening,closing)
    bots=[]
    for key,label in [('amat','AMAT'),('perps','Perps')]:
        v=observation.get(key,{});b=baseline.get(key,{}) if baseline else {};equity=float(v['equity']) if v.get('equity') is not None else None
        start=float(b['equity']) if b.get('equity') is not None else None
        fees=(float(v['fees'])-float(b['fees'])) if in_session and key=='perps' and start is not None else None
        funding=(float(v['funding'])-float(b['funding'])) if in_session and key=='perps' and start is not None else None
        stale=False
        if key=='perps' and v.get('observed_at'):
            stale=(now-dt.datetime.fromisoformat(v['observed_at'])).total_seconds()>300
        if stale:
            fees=funding=None
        bots.append(dict(id=key,name=label,equity=equity,pnl=equity-start if equity is not None and start is not None and in_session and not stale else None,
            net_return=equity/start-1 if equity is not None and start and in_session and not stale else None,fees=fees,funding=funding,
            known_net_cost=fees-funding if fees is not None and funding is not None else None,
            fees_basis='Native simulated fees' if key=='perps' else 'Not supplied by broker order feed',
            decision_markout_bps=v.get('quality',{}).get('decision_to_fill_slippage_bps') if v.get('quality') else None,
            stale=stale,error=v.get('error'),source_time=v.get('observed_at',now.isoformat())))
    perps_fresh=not bots[1]['stale'] and not bots[1]['error']
    if local>=closing and not old.get('closing_metrics'):
        if local<=closing+dt.timedelta(minutes=5) and baseline and perps_fresh and not bots[0]['error']:
            old['closing_metrics']=bots
            old['closing_observed_at']=now.isoformat()
        elif local>closing+dt.timedelta(minutes=5):
            for bot in bots:
                bot['pnl']=bot['net_return']=bot['fees']=bot['funding']=bot['known_net_cost']=None
    if old.get('closing_metrics'):
        bots=old['closing_metrics']
    sample={'at':now.isoformat(),**{x['id']:x['net_return'] for x in bots}}
    if in_session and (local<closing or (old.get('closing_metrics') and not old.get('closing_sample_recorded'))):old['samples']=[*old.get('samples',[]),sample][-600:]
    if old.get('closing_metrics'):old['closing_sample_recorded']=True
    status='waiting' if local<opening else 'complete' if local>=closing+dt.timedelta(minutes=25) else 'monitoring'
    publication=dict(schema='paper-session/1',execution_authority='none',status=status,observed_at=now.isoformat(),
        diagnostics=dict(amat={k:observation.get('amat',{}).get(k) for k in ['execution_mode','whole_share_buys','config_sha256','required_execution','batch','portfolio','position_profit','position_profit_error']},perps={k:observation.get('perps',{}).get(k) for k in ['decision','state','observed_at','data_flow','fills']}),
        target_session=session,timezone='America/Detroit',opening=opening.isoformat(),closing=closing.isoformat(),
        performance_end=old.get('closing_observed_at'),baseline_at=baseline['observed_at'] if baseline else None,baseline_usable=in_session and baseline is not None,
        audit=audit,bots=bots,perps_fresh=perps_fresh,samples=old.get('samples',[]),
        limitations=['AMAT markout compares fills with the prior decision close and includes overnight movement; it is not opening-VWAP slippage or a fee.',
        'AMAT broker fees and perps spread/impact are unavailable in these source feeds; unknown costs are not shown as zero.',
        'Both bots use the same pre-open observation baseline. Equity changes are not adjusted for deposits or withdrawals.',
        'Closing performance freezes at the first successful close observation within five minutes; a missed close leaves final session figures unavailable.',
        'Sources can have different mark timestamps within the freshness limit. Observations are sampled; drawdowns and brief outages between checks may be missed. Stale perps data is marked explicitly.'])
    return publication, old
