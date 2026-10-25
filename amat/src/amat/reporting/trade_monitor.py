"""Read-only trade diagnostics shared through JSON with the paper executor.

An event is an instruction (or a named counterfactual opportunity), not a
closed position. Markouts measure the asset path following that event. They
must never be described as realized portfolio profit or independent trials.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "trade-monitor/1"
HORIZONS = (1, 5, 20, 60)
MIN_EVENTS = 30
MIN_DATES = 10
NOTES = [
    "Events are instructions, not closed round trips. Scale-ins and partial exits remain separate.",
    "Positive side-signed markout means price rose after a buy or fell after a sell. "
    "A sell markout measures exit timing, not the return of a short position.",
    "Horizons count exchange sessions: 1 is the execution-session close; 5 is its fourth "
    "following close. Excursions use closes only and exclude pre-fill intraday prices.",
    "Markouts are adjusted price proxies before fees, spread and impact; they are not "
    "realized trade P&L. Each fill is normalized using its execution-session raw/adjusted close.",
    "Decision-to-fill gap includes market movement after the decision close. It is not "
    "a quote-based slippage or market-impact estimate.",
    "Raw signal scores are not probabilities or expected returns. Forecast errors are "
    "computed only for explicit predictions, with the model's original label and maturity.",
    "Recent/prior comparisons use distinct execution dates. Events on the same date and "
    "overlapping horizons are dependent. Sample flags are descriptive, not significance tests.",
    "Backtests, paper observations, cost cases and counterfactual opportunities stay in "
    "separate cohorts. Historical diagnostics support hypotheses for new held-out tests.",
]


def finite(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def event_id(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:24]


def _average(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _sample(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dates = len({row["execution_session"] for row in rows})
    return {
        "events": len(rows),
        "dates": dates,
        "sample_status": "descriptive_sample"
        if len(rows) >= MIN_EVENTS and dates >= MIN_DATES
        else "insufficient_sample",
    }


def _outcome_summary(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    key = str(horizon)
    matured = [
        row for row in rows if row.get("outcomes", {}).get(key, {}).get("status") == "matured"
    ]
    values = [float(row["outcomes"][key]["signed_markout_bps"]) for row in matured]
    # Equal date weight prevents a ten-symbol rebalance receiving ten times
    # the influence of a one-symbol rebalance in the headline average.
    by_date: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(matured, values, strict=True):
        by_date[row["execution_session"]].append(value)
    return {
        **_sample(matured),
        "horizon_sessions": horizon,
        "not_matured_or_unavailable": len(rows) - len(matured),
        "mean_signed_markout_bps": _average(values),
        "date_weighted_mean_bps": _average([statistics.fmean(v) for v in by_date.values()]),
        "median_bps": statistics.median(values) if values else None,
        "positive_markout_rate": _average([float(value > 0) for value in values]),
        "worst_bps": min(values) if values else None,
    }


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Cohort metrics with explicit denominators and no pooled model verdict."""
    cohorts: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    identity = ("source", "run_id", "model_id", "event_kind", "side")
    for row in events:
        cohort = tuple(str(row.get(key, "unknown")) for key in identity)
        for dimension in ("all", "symbol", "regime", "reason"):
            value = str(row.get(dimension, "unknown")) if dimension != "all" else "all"
            cohorts[(*cohort, dimension, value)].append(row)
    summaries = []
    for key, rows in sorted(cohorts.items()):
        dates = sorted({row["execution_session"] for row in rows})
        recent_dates = set(dates[-MIN_EVENTS:])
        prior_dates = set(dates[-2 * MIN_EVENTS : -MIN_EVENTS])
        recent = [row for row in rows if row["execution_session"] in recent_dates]
        prior = [row for row in rows if row["execution_session"] in prior_dates]
        gaps = [
            gap for row in rows if (gap := finite(row.get("decision_to_fill_gap_bps"))) is not None
        ]
        summaries.append(
            {
                **dict(zip((*identity, "dimension", "value"), key, strict=True)),
                **_sample(rows),
                "execution_attention_events": sum(bool(row.get("alerts")) for row in rows),
                "forecast_available_events": sum(bool(row.get("forecasts")) for row in rows),
                "gap_events": len(gaps),
                "mean_decision_to_fill_gap_bps": _average(gaps),
                "outcomes": [_outcome_summary(rows, h) for h in HORIZONS],
                "recent_30_dates": _outcome_summary(recent, 5),
                "prior_30_dates": _outcome_summary(prior, 5),
            }
        )
    forecasts: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        for forecast in row.get("forecasts", []):
            if forecast["status"] != "matured":
                continue
            key = (
                *(str(row.get(k, "unknown")) for k in identity),
                forecast["head"],
                str(forecast["horizon_sessions"]),
                forecast["basis"],
            )
            forecasts[key].append({**forecast, "execution_session": row["execution_session"]})
    forecast_summaries = []
    for key, rows in sorted(forecasts.items()):
        errors = [float(row["error_bps"]) for row in rows]
        forecast_summaries.append(
            {
                **dict(zip((*identity, "head", "horizon_sessions", "basis"), key, strict=True)),
                **_sample(rows),
                "mean_prediction_bps": _average([float(r["prediction_bps"]) for r in rows]),
                "mean_actual_bps": _average([float(r["actual_bps"]) for r in rows]),
                "bias_actual_minus_prediction_bps": _average(errors),
                "mae_bps": statistics.fmean(abs(e) for e in errors),
                "rmse_bps": math.sqrt(statistics.fmean(e * e for e in errors)),
                "fraction_actual_below_prediction": statistics.fmean(e < 0 for e in errors),
            }
        )
    return {
        "events": len(events),
        "attention_events": sum(bool(row.get("alerts")) for row in events),
        "events_without_forecasts": sum(not row.get("forecasts") for row in events),
        "cohorts": summaries,
        "forecasts": forecast_summaries,
    }


def document(events: list[dict[str, Any]], **metadata: Any) -> dict[str, Any]:
    ordered = sorted(events, key=lambda row: (row["execution_session"], row["event_id"]))
    ids = [(row["source"], row["run_id"], row["event_id"]) for row in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate trade event identity")
    payload = {
        "schema": SCHEMA,
        "metadata": metadata,
        "notes": NOTES,
        "summary": summarize_events(ordered),
        "events": ordered,
    }
    # Invalid metrics are a data error, never a JavaScript NaN or Infinity.
    json.dumps(payload, allow_nan=False)
    return payload


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, allow_nan=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_monitor(target: Path, payload: dict[str, Any]) -> Path:
    """Write portable JSON, a flat event CSV and an offline interactive viewer."""
    target.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    json_path = target / "trade-monitor.json"
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(data + "\n", encoding="utf-8")
    temporary.replace(json_path)
    flat = []
    for event in payload["events"]:
        row = {key: value for key, value in event.items() if key != "outcomes"}
        for horizon, outcome in event.get("outcomes", {}).items():
            row.update({f"{key}_{horizon}s": value for key, value in outcome.items()})
        flat.append(row)
    columns = sorted({key for row in flat for key in row}) or ["event_id", "source"]
    csv_path = target / "trades.csv"
    temporary = csv_path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({k: _csv_value(v) for k, v in row.items()} for row in flat)
    temporary.replace(csv_path)
    page = target / "trade-monitor.html"
    # JSON lives in a non-executable script and user strings only reach textContent.
    embedded = data.replace("<", "\\u003c").replace("&", "\\u0026")
    temporary = page.with_suffix(".html.tmp")
    temporary.write_text(_HTML.replace("__DATA__", embedded), encoding="utf-8")
    temporary.replace(page)
    return page


def combine(paths: list[Path], target: Path) -> Path:
    events = []
    sources = []
    for path in paths:
        data = path.read_bytes()
        payload = json.loads(data)
        if payload.get("schema") != SCHEMA:
            raise ValueError(f"unsupported trade monitor: {path}")
        events.extend(payload["events"])
        sources.append({"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()})
    return write_monitor(target, document(events, inputs=sources))


_HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Trade performance monitor</title>
<style>
:root{color-scheme:light;--ink:#183333;--muted:#516b69;--line:#d4e1dc;--paper:#f4f7f3}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55
system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:34px}h1{font-size:34px;
letter-spacing:-1px;margin:0}h2{margin:28px 0 12px}p{max-width:1040px}.eyebrow{color:#256e62;
font-size:12px;font-weight:750;letter-spacing:2px}.sub,small{color:var(--muted)}a{color:#256e62}
.filters,.cards{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}.cards>div{background:white;
border:1px solid var(--line);border-radius:10px;padding:16px 22px;min-width:190px;flex:1}
.cards strong{display:block;font-size:28px;font-weight:600}.filters label{font-size:12px;
color:var(--muted);display:grid;gap:5px}select,input{padding:9px;border:1px solid var(--line);
border-radius:5px;background:white;max-width:240px;color:var(--ink)}.scroll{overflow:auto;
border:1px solid var(--line);border-radius:8px;background:white}table{border-collapse:collapse;
width:100%;font-size:13px}th,td{padding:11px 14px;text-align:left;border-bottom:1px solid
var(--line);white-space:nowrap}th{background:#e7efe8}tbody tr:hover{background:#f0f6f0}
.bad{color:#a33824}.good{color:#187661}button{background:transparent;border:0;color:#256e62;
cursor:pointer;text-decoration:underline}#detail{white-space:pre-wrap;max-height:450px;
overflow:auto;background:#e7efe8;padding:20px;border-radius:8px}.notice{border-left:3px solid
#c1974e;padding:10px 18px;background:#fff8e9}details{margin-top:24px}li{margin:8px 0}
@media(max-width:700px){main{padding:20px}h1{font-size:28px}}
</style><main><div class="eyebrow">MODEL / EXECUTION / OUTCOME</div>
<h1>Trade performance monitor</h1><p class="sub">Compare the instruction, the fill and the
price path that followed. Filter to one model and source before interpreting performance.</p>
<p><a href="trades.csv">Download events CSV</a> ·
<a href="trade-monitor.json">Download complete evidence</a></p>
<div class="filters" id="filters"></div><div class="cards" id="cards"></div>
<p class="notice">A losing trade can follow the model correctly. Positive markouts describe
entry or exit timing; they do not measure realized portfolio profit. Missing forecasts and
pending outcomes are excluded from the relevant metric, with counts shown.</p>
<h2>Outcomes by cohort</h2><p class="sub">Means give equal weight to each execution date.
Recent and prior windows each contain up to 30 event dates. These are descriptive comparisons.</p>
<div class="scroll"><table><thead><tr><th>Source / case</th><th>Model</th><th>Side / kind</th>
<th>Matured / events</th><th>Dates</th><th>Mean (bps)</th><th>Recent 5s</th><th>Prior 5s</th>
<th>Sample</th></tr></thead><tbody id="cohorts"></tbody></table></div>
<h2>Trade instructions & opportunities</h2><p id="count" class="sub"></p>
<div class="scroll"><table><thead><tr><th>Date</th><th>Asset</th><th>Side / kind</th><th>Regime</th>
<th>Instruction / execution</th><th>Filled / intended</th><th>Gap (bps)</th><th>Markout (bps)</th>
<th>Favourable / adverse close (bps)</th><th>Evidence</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<pre id="detail" hidden></pre><h2>Explicit forecast checks</h2>
<p class="sub">Errors are actual minus predicted, in basis points. The model's forecast
target can differ from the close-based markout above. Inspect an event for its exact definition.</p>
<div class="scroll"><table><thead><tr><th>Source / case / side / kind</th><th>Model / head</th>
<th>Horizon</th><th>Matured / dates</th><th>Predicted</th><th>Actual</th><th>Bias</th>
<th>MAE</th><th>RMSE</th><th>Sample</th></tr></thead><tbody id="forecasts"></tbody></table></div>
<details><summary>Definitions and evidence limits</summary><ul id="notes"></ul></details>
</main><script id="data" type="application/json">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent), filters={};
const el=id=>document.getElementById(id), fmt=x=>x==null?'unavailable':Number(x).toFixed(1);
function cell(tr,value){const td=document.createElement('td');td.textContent=value;tr.append(td);
return td}function tableRow(id,values){const tr=document.createElement('tr');
values.forEach(v=>cell(tr,v));el(id).append(tr);return tr}
for(const key of ['source','run_id','model_id','symbol','side','regime','event_kind']){
const label=document.createElement('label');label.textContent=key.replaceAll('_',' ');
const select=document.createElement('select');select.add(new Option('All',''));
[...new Set(data.events.map(r=>String(r[key]??'unknown')))].sort().forEach(v=>
select.add(new Option(v,v)));label.append(select);el('filters').append(label);
filters[key]=select;select.onchange=render;}
const label=document.createElement('label');label.textContent='Outcome horizon';
const horizon=document.createElement('select');[1,5,20,60].forEach(h=>
horizon.add(new Option(h+' sessions',h)));horizon.value='5';horizon.onchange=render;
label.append(horizon);el('filters').append(label);
const searchLabel=document.createElement('label');searchLabel.textContent='Search evidence';
const search=document.createElement('input');search.placeholder='Reason, ID, alert…';
search.oninput=render;searchLabel.append(search);el('filters').append(searchLabel);
function matches(r){return Object.entries(filters).every(([k,s])=>!s.value||
String(r[k]??'unknown')===s.value)&&(!search.value||JSON.stringify(r).toLowerCase()
.includes(search.value.toLowerCase()))}
function mean(a){return a.length?a.reduce((s,x)=>s+x,0)/a.length:null}
function stats(rows,h){const ok=rows.filter(r=>r.outcomes?.[h]?.status==='matured');
const dates={};ok.forEach(r=>(dates[r.execution_session]??=[])
.push(r.outcomes[h].signed_markout_bps));
return {n:ok.length,dates:Object.keys(dates).length,mean:mean(Object.values(dates).map(mean))}}
function render(){const rows=data.events.filter(matches), h=horizon.value;
el('cards').replaceChildren();for(const [name,value] of [['Events in view',rows.length],
['Execution attention',rows.filter(r=>r.alerts?.length).length],
['Matured at '+h+' sessions',stats(rows,h).n],
['Without explicit forecasts',rows.filter(r=>!r.forecasts?.length).length]]){
const box=document.createElement('div'),strong=document.createElement('strong');
box.textContent=name;strong.textContent=value;box.append(strong);el('cards').append(box)}
el('cohorts').replaceChildren();const groups={};rows.forEach(r=>{const key=JSON.stringify(
[r.source,r.run_id,r.model_id,r.side,r.event_kind]);(groups[key]??=[]).push(r)});
for(const [k,rr] of Object.entries(groups)){const [source,run,model,side,kind]=JSON.parse(k);
const s=stats(rr,h),dates=[...new Set(rr.map(r=>r.execution_session))].sort();
const recent=new Set(dates.slice(-30)),prior=new Set(dates.slice(-60,-30));
tableRow('cohorts',[source+' / '+run,model.slice(0,16),side+' / '+kind,s.n+' / '+rr.length,
s.dates,fmt(s.mean),fmt(stats(rr.filter(r=>recent.has(r.execution_session)),5).mean),
fmt(stats(rr.filter(r=>prior.has(r.execution_session)),5).mean),
s.n>=30&&s.dates>=10?'descriptive':'insufficient']);}
el('rows').replaceChildren();el('count').textContent=rows.length?
'Showing '+Math.min(rows.length,200)+' of '+rows.length+' matching events. CSV contains all.':
'No trade events match this view. New paper observations appear after a report is captured.';
for(const r of rows.slice(-200).reverse()){const o=r.outcomes?.[h]??{};
const tr=tableRow('rows',[r.execution_session,r.symbol,r.side+' / '+r.event_kind,r.regime,
r.instruction_status+' / '+r.execution_status,(r.filled_quantity??'—')+' / '+
(r.intended_quantity??'—'),fmt(r.decision_to_fill_gap_bps),o.status==='matured'?
fmt(o.signed_markout_bps):(o.status??'unavailable'),
fmt(o.favourable_close_bps)+' / '+fmt(o.adverse_close_bps)]);
const td=cell(tr,''),b=document.createElement('button');b.textContent=r.alerts?.length?
r.alerts.join(', '):'Inspect';if(r.alerts?.length)b.className='bad';b.onclick=()=>{
el('detail').hidden=false;el('detail').textContent=JSON.stringify(r,null,2);
el('detail').scrollIntoView({behavior:'smooth',block:'nearest'})};td.append(b)}
el('forecasts').replaceChildren();const fg={};rows.forEach(r=>(r.forecasts??[]).forEach(f=>{
if(f.status!=='matured')return;const k=JSON.stringify([r.source,r.run_id,r.model_id,r.side,
r.event_kind,f.head,f.horizon_sessions,f.basis]);(fg[k]??=[]).push({...f,date:r.execution_session})}));
for(const [k,fs] of Object.entries(fg)){const [source,run,model,side,kind,head,hh]=JSON.parse(k),
dates=new Set(fs.map(f=>f.date)).size;tableRow('forecasts',[source+' / '+run+' / '+side+' / '+kind,
model.slice(0,12)+' / '+head,hh,fs.length+' / '+dates,
fmt(mean(fs.map(f=>f.prediction_bps))),fmt(mean(fs.map(f=>f.actual_bps))),
fmt(mean(fs.map(f=>f.error_bps))),fmt(mean(fs.map(f=>Math.abs(f.error_bps)))),
fmt(Math.sqrt(mean(fs.map(f=>f.error_bps**2)))),fs.length>=30&&dates>=10?'descriptive':'insufficient'])}
if(!Object.keys(fg).length)tableRow('forecasts',['No matured explicit forecasts in this view.']);}
data.notes.forEach(n=>{const li=document.createElement('li');
li.textContent=n;el('notes').append(li)});
render();</script></html>"""
