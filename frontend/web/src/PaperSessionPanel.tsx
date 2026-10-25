import ReadableTable from "./ReadableTable";
import { useEffect, useState } from "react";
type Obj = Record<string, any>;
const money = (x: any) =>
  x == null
    ? "Unavailable"
    : Number(x).toLocaleString(undefined, {
        style: "currency",
        currency: "USD",
      });
const pct = (x: any) =>
  x == null ? "Pending" : `${(Number(x) * 100).toFixed(2)}%`;
const time = (x: string) =>
  new Date(x).toLocaleString("en-US", {
    timeZone: "America/Detroit",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
export default function PaperSessionPanel({
  request,
}: {
  request: (path: string) => Promise<Obj>;
}) {
  const [data, setData] = useState<Obj | null>(null);
  const [error, setError] = useState(false);
  const [session, setSession] = useState("");
  useEffect(() => {
    let active = true;
    const load = () =>
      request(`/paper-session${session ? `?session=${encodeURIComponent(session)}` : ""}`)
        .then((v) => {
          if (active) {
            setData(v);
            setError(false);
          }
        })
        .catch(() => {
          if (active) setError(true);
        });
    load();
    window.addEventListener("amat:refresh", load);
    const id = window.setInterval(() => {
      if (!document.hidden) load();
    }, 30000);
    return () => {
      active = false;
      window.removeEventListener("amat:refresh", load);
      clearInterval(id);
    };
  }, [request, session]);
  if (!data)
    return (
      <section className="paper-session-panel">
        <h2>Paper trading session</h2>
        <p>
          {error
            ? "Session observations are unavailable."
            : "Loading the next paper-session checks…"}
        </p>
      </section>
    );
  if (data.status === "unavailable")
    return (
      <section className="paper-session-panel">
        <h2>Paper trading session</h2>
        <p>{data.message}</p>
      </section>
    );
  const audit = data.audit || {};
  const repairedHistoricalGap = (a: Obj) => data.plan_preflight?.status === 'verified_hold_plan' && a.session === '2026-10-22' && a.message === 'Session plan is missing';
  const historicalAlertsOnly = data.alerts?.length > 0 && data.alerts.every(repairedHistoricalGap);
  const samples: Obj[] = data.samples || [];
  const values = samples
    .flatMap((s) => [s.amat, s.perps])
    .filter((x) => x != null)
    .map(Number);
  const low = Math.min(0, ...values),
    high = Math.max(0, ...values),
    span = Math.max(high - low, 0.001);
  const y = (v: number) => 170 - ((v - low) / span) * 140;
  const start = samples.length ? Date.parse(samples[0].at) : 0;
  const duration = samples.length
    ? Math.max(1, Date.parse(samples[samples.length - 1].at) - start)
    : 1;
  const points = (key: string) =>
    samples.map((s, i) =>
      s[key] == null
        ? null
        : {
            i,
            x: 45 + ((Date.parse(s.at) - start) / duration) * 700,
            y: y(Number(s[key])),
          },
    );
  const segments = (key: string) => {
    const groups: string[][] = [[]];
    for (const p of points(key)) {
      if (p) groups[groups.length - 1].push(`${p.x},${p.y}`);
      else if (groups[groups.length - 1].length) groups.push([]);
    }
    return groups.filter((g) => g.length);
  };
  return (
    <section
      className="paper-session-panel"
      aria-label="Paper session verification"
    >
      <div className="analysis-section-title">
        <div>
          <h2>Remote paper trading monitor</h2>
          <p>{time(data.opening)}–{time(data.closing)} Detroit · AMAT and perps</p>
        </div>
        <span className="analysis-data-badge">Paper funds</span>
      </div>
      {data.remote_sessions && <div className="analysis-controls"><label>Session <select aria-label="Paper monitoring session" value={session} onChange={e=>setSession(e.target.value)}><option value="">Latest session</option>{data.remote_sessions.map((date:string)=><option key={date} value={date}>{date}</option>)}</select></label><span>Collected every minute on the VPS · continues while this computer is asleep</span></div>}
      {data.transport === "unavailable" && <p role="alert">The dashboard cannot reach the VPS. Saved remote measurements are shown as stale; no local account is substituted.</p>}
      {data.alerts?.length>0 && <div className="paper-session-alerts" role={historicalAlertsOnly ? "note" : "alert"}><h3>{historicalAlertsOnly ? 'Historical planning incident — cause repaired' : 'Needs attention'}</h3><ul>{data.alerts.map((a:Obj)=><li key={a.id}>{a.session ? `${a.session} · ` : ""}{a.message}{repairedHistoricalGap(a) ? ' — historical gap preserved; October 23 plan verified' : ''}</li>)}</ul></div>}
      <p className="source-line">
        Last checked {time(data.observed_at)} ·{" "}
        {data.stale || error
          ? "Observation is stale or unavailable"
          : data.status === "waiting"
            ? "Waiting for the session"
            : data.status === "complete"
              ? "Monitoring window ended"
              : "Session monitoring active"}
      </p>
      {data.session_watch && <p className="source-line">Remote watch: {data.session_watch.session} · Expected AMAT orders: {data.session_watch.expected_amat_orders} (hold plan) · Perps signals, fills, fees and funding checked every minute. {data.target_session !== data.session_watch.session || data.status === 'waiting' ? 'Session results will appear after the open.' : data.status === 'complete' ? 'Session performance is saved; order and signal checks continue.' : 'Session performance and execution checks are updating.'}</p>}
      {data.plan_preflight && <div className="paper-session-alerts"><h3>October 23 plan readiness</h3><p><strong>{data.plan_preflight.status === 'verified_hold_plan' ? 'Verified: hold existing positions' : 'Needs review'}</strong> · Prepared {time(data.plan_preflight.verified_at)}</p><p>{data.plan_preflight.summary}</p><div className="analysis-table-wrap"><ReadableTable aria-label="Next session plan readiness"><thead><tr><th>Check</th><th>Result</th></tr></thead><tbody><tr><td>Fresh completed data</td><td>{data.plan_preflight.data_session} · {data.plan_preflight.symbols} assets · SIP feed</td></tr><tr><td>Execution session</td><td>{data.plan_preflight.execution_session} · whole-share purchases enabled</td></tr><tr><td>Account reconciliation</td><td>Exact match · cash difference {money(data.plan_preflight.cash_difference)}</td></tr><tr><td>Planned orders</td><td>{data.plan_preflight.intent_count} · valid hold decision</td></tr><tr><td>Native decision comparison</td><td>{data.plan_preflight.native_decision_parity ? 'Matched' : 'Needs review'}</td></tr></tbody></ReadableTable></div><details><summary>Why October 22 was missed and what changed</summary><p>{data.plan_preflight.root_cause}</p><p>{data.plan_preflight.data_repair}</p><p>{data.plan_preflight.limitations}</p></details></div>}
      {data.diagnostics?.amat?.position_profit && <div className="paper-session-alerts"><h3>AMAT profit: positions versus today</h3><p>Open-position profit is measured from each position’s entry cost. Session change below measures account equity from the 9:29 a.m. baseline. These are different periods and can differ substantially.</p><div className="analysis-table-wrap"><ReadableTable aria-label="AMAT broker profit reconciliation"><thead><tr><th>Broker measure</th><th>Amount</th><th>Meaning</th></tr></thead><tbody><tr><td>Open-position unrealized profit</td><td>{money(data.diagnostics.amat.position_profit.unrealized_pl)}</td><td>Sum of Alpaca position profit since entry</td></tr><tr><td>Open-position profit today</td><td>{money(data.diagnostics.amat.position_profit.unrealized_intraday_pl)}</td><td>Alpaca intraday position profit</td></tr><tr><td>Account equity change since previous close</td><td>{money(data.diagnostics.amat.position_profit.equity_change_since_previous_close)}</td><td>Includes overnight change; not adjusted for transfers</td></tr></tbody></ReadableTable></div><details><summary>Open positions and entry costs</summary><ReadableTable aria-label="AMAT open position profit"><thead><tr><th>Asset</th><th>Side</th><th>Entry cost basis</th><th>Market value</th><th>Profit since entry</th></tr></thead><tbody>{data.diagnostics.amat.position_profit.positions.map((p:Obj)=><tr key={p.symbol}><td>{p.symbol}</td><td>{p.side || 'Unavailable'}</td><td>{money(p.cost_basis)}</td><td>{money(p.market_value)}</td><td>{money(p.unrealized_pl)}</td></tr>)}</tbody></ReadableTable></details><small>Broker snapshot {time(data.diagnostics.amat.position_profit.observed_at)}. This snapshot continues updating after the session performance below freezes at the close. Costs and realized gains are separate.</small></div>}
      {data.diagnostics?.amat?.position_profit_error && <p role="alert">Open-position profit is unavailable from the latest broker read; session equity change is a separate measure.</p>}
      {data.session_findings && <div className="paper-session-alerts"><h3>{data.session_findings.final ? 'Completed session review' : 'Interim session review'}</h3><p>Evidence through {time(data.session_findings.through)} · Reviewed {time(data.session_findings.reviewed_at)}</p><p>{data.session_findings.summary}</p><p>{data.session_findings.perps}</p><p>{data.session_findings.limitations}</p><small>Live measurements below continue updating independently of this dated review.</small></div>}
      {data.session_review && <div className="paper-session-alerts"><h3>Session review</h3><p>{data.session_review.summary}</p><p>{data.session_review.execution_note}</p><p>Historical alerts remain in the record. The reviewed order counts below correct the precision-only alerts.</p></div>}
      {data.operational_review && <div className="paper-session-alerts"><h3>Execution upgrade and perps investigation</h3><p>AMAT is enabled in paper mode with whole-share purchases and the current best strategy. Next monitored session: {data.operational_review.next_session}.</p><p>Perps is waiting for a new breakout signal. The reviewed decisions requested no exposure in either asset; no orders were submitted.</p><details><summary>Verification details and limits</summary><p>{data.operational_review.amat}</p><p>{data.operational_review.parity}</p><p>{data.operational_review.perps}</p><p>{data.operational_review.limitation}</p>{data.operational_review.prior_alert && <p>{data.operational_review.prior_alert}</p>}</details><small>Reviewed {time(data.operational_review.reviewed_at)}</small></div>}
      {data.diagnostics && <div className="analysis-table-wrap"><ReadableTable aria-label="Execution and signal diagnostics"><thead><tr><th>Bot</th><th>Current observed policy</th><th>Latest evidence</th></tr></thead><tbody><tr><td>AMAT</td><td>{data.diagnostics.amat?.execution_mode === 'staged-v1' ? 'Durable staged execution' : data.diagnostics.amat?.execution_mode === 'legacy' ? 'Legacy execution' : 'Unavailable'} · {data.diagnostics.amat?.whole_share_buys ? 'whole-share purchases' : 'fractional purchases'}</td><td>{data.diagnostics.amat?.config_sha256 && data.diagnostics.amat.config_sha256 === data.operational_review?.activation?.after?.strategy_hash ? 'Matches current best' : 'See strategy check below'}</td></tr>{Object.entries(data.diagnostics.perps?.decision?.exit_policy || {}).map(([asset,p]:[string,any])=><tr key={asset}><td>{asset}</td><td>{Number(p.level)>0 ? `Active signal: ${p.level}` : 'No entry signal'} · {p.reason}</td><td>Completed hour {new Date(data.diagnostics.perps.decision.as_of_ms).toLocaleString('en-US',{timeZone:'America/Detroit'})}</td></tr>)}</tbody></ReadableTable></div>}
      {data.operational_review?.entry_conditions && <details><summary>Why perps is waiting: breakout conditions</summary><p>Snapshot from the investigation; prices are USD per contract. Reference highs move as new bars complete.</p><div className="analysis-table-wrap"><ReadableTable aria-label="Perps breakout conditions"><thead><tr><th>Asset</th><th>Four-hour close</th><th>Prior 20-day high</th><th>Rise to reference high</th><th>Entry breakout</th></tr></thead><tbody>{data.operational_review.entry_conditions.conditions.map((r:Obj)=><tr key={r.ticker}><td>{r.ticker}</td><td>{Number(r.close).toFixed(3)}</td><td>{Number(r.prior_20day_high).toFixed(3)}</td><td>{Number(r.distance_below_entry_pct).toFixed(2)}%</td><td>{r.breakout ? 'Yes' : 'No'}</td></tr>)}</tbody></ReadableTable></div></details>}
      <div className="analysis-metric-row">
        {(data.bots || []).map((b: Obj) => (
          <div key={b.id}>
            <span>
              {b.name} paper equity{b.stale ? " · stale" : ""}
            </span>
            <strong>{money(b.equity)}</strong>
            <small>
              Session change from 9:29 a.m. {b.pnl == null ? "pending" : money(b.pnl)} ·{" "}
              {pct(b.net_return)}
            </small>
          </div>
        ))}
        <div>
          <span>Default strategy check</span>
          <strong>
            {audit.issues?.some((s: string) => s.includes("strategy"))
              ? "Needs review"
              : audit.checks_complete
                ? "Verified"
                : audit.planned != null && !audit.issues?.length
                  ? "Plan ready"
                : "Awaiting session plan"}
          </strong>
          <small>All groups + volatility switching</small>
        </div>
      </div>
      <h3>AMAT orders and fills</h3>
      <div className="analysis-table-wrap">
        <ReadableTable aria-label="Paper order checks">
          <thead>
            <tr>
              <th>Planned orders</th>
              <th>Filled</th>
              <th>Partial</th>
              <th>Unfilled after opening</th>
              <th>Duplicate orders</th>
              <th>Overfilled</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              {[
                audit.planned,
                audit.filled,
                audit.partial,
                audit.missed,
                audit.duplicates,
                audit.overfilled,
              ].map((n, i) => (
                <td key={i}>{n == null ? "Pending" : n}</td>
              ))}
            </tr>
          </tbody>
        </ReadableTable>
      </div>
      {audit.issues?.length > 0 ? (
        <ul>
          {audit.issues.map((s: string) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      ) : (
        <p>
          {audit.checks_complete
            ? data.status === "complete" ? "All observed orders reconciled. Session monitoring is complete." : "No discrepancy detected in the observed orders. Final reconciliation follows the close."
            : audit.planned === 0
              ? "Hold plan ready: no orders are expected. Monitoring will flag unexpected orders throughout the session."
              : "Order checks are pending. No-trade decisions will be checked against the actual plan."}
        </p>
      )}
      {(audit.withheld > 0 || audit.residual_intents > 0) && (
        <p>
          {audit.withheld || 0} intents withheld by policy ·{" "}
          {audit.residual_intents || 0} retain unspent authorization. These are
          reported separately from missing orders.
        </p>
      )}
      {!data.perps_fresh && (
        <p role="alert">
          Perps observations are stale or unavailable; its performance is not
          verified.
        </p>
      )}
      <h3>Session performance</h3>
      {data.session_review?.orders && <details><summary>Reviewed AMAT fills</summary><div className="analysis-table-wrap"><ReadableTable aria-label="Reviewed AMAT fills"><thead><tr><th>Asset</th><th>Side</th><th>Filled shares</th><th>Average price</th><th>Status</th></tr></thead><tbody>{data.session_review.orders.map((o:Obj)=><tr key={o.order_ref}><td>{o.symbol}</td><td>{o.side}</td><td>{o.filled_quantity}</td><td>{money(o.price)}</td><td>{o.status}</td></tr>)}</tbody></ReadableTable></div></details>}
      {values.length > 0 ? (
        <>
          <div className="paper-session-legend">
            <span style={{ color: "#2b63b8" }}>● AMAT</span>
            <span style={{ color: "#32817d" }}>● Perps</span>
            <span>Change from the same pre-open observation</span>
          </div>
          <svg
            viewBox="0 0 800 210"
            role="img"
            aria-label="Paper session return chart"
          >
            <line x1="45" x2="745" y1={y(0)} y2={y(0)} stroke="#91a0b5" />
            <text x="3" y="20" fill="currentColor" fontSize="12">
              {(high * 100).toFixed(2)}%
            </text>
            <text x="3" y="185" fill="currentColor" fontSize="12">
              {(low * 100).toFixed(2)}%
            </text>
            <text x="3" y={y(0) - 5} fill="currentColor" fontSize="12">
              0%
            </text>
            {["amat", "perps"].flatMap((key, i) =>
              segments(key).map((g, j) => (
                <polyline
                  key={key + j}
                  points={g.join(" ")}
                  fill="none"
                  stroke={i ? "#32817d" : "#2b63b8"}
                  strokeWidth="3"
                />
              )),
            )}
            <text x="45" y="200" fill="currentColor" fontSize="12">
              {time(samples[0].at)}
            </text>
            <text
              x="745"
              y="200"
              textAnchor="end"
              fill="currentColor"
              fontSize="12"
            >
              {time(samples[samples.length - 1].at)}
            </text>
          </svg>
        </>
      ) : (
        <p>
          The chart starts after the open once a fresh, shared pre-open baseline
          is available. Future returns are not estimated.
        </p>
      )}
      <h3>Execution costs</h3>
      <div className="analysis-table-wrap">
        <ReadableTable aria-label="Paper execution costs">
          <thead>
            <tr>
              <th>Bot</th>
              <th>Fees during session</th>
              <th>Funding received / paid</th>
              <th>Known fees less funding</th>
              <th>Decision-close-to-fill price difference</th>
            </tr>
          </thead>
          <tbody>
            {(data.bots || []).map((b: Obj) => (
              <tr key={b.id}>
                <td>{b.name}</td>
                <td>{money(b.fees)}</td>
                <td>{money(b.funding)}</td>
                <td>{money(b.known_net_cost)}</td>
                <td>
                  {b.decision_markout_bps == null
                    ? "Unavailable"
                    : `${Number(b.decision_markout_bps).toFixed(2)} bps`}
                </td>
              </tr>
            ))}
          </tbody>
        </ReadableTable>
      </div>
      {data.alert_events?.length>0 && <details><summary>Alert history</summary><div className="analysis-table-wrap"><ReadableTable aria-label="Paper monitor alert history"><thead><tr><th>Time · Detroit</th><th>Session</th><th>Issue</th><th>State</th></tr></thead><tbody>{[...data.alert_events].reverse().map((a:Obj,i:number)=><tr key={a.id+a.at+i}><td>{time(a.at)}</td><td>{a.session}</td><td>{a.message}</td><td>{a.state}</td></tr>)}</tbody></ReadableTable></div></details>}
      <details>
        <summary>Sources and measurement limits</summary>
        <p>
          Broker-confirmed session; remote AMAT orders and plan; remote perps
          paper account. Monitoring runs every minute on the remote VPS, independently of this browser or Codex. Dashboard
          reads the saved observation every 30 seconds.
        </p>
        <ul>
          {(data.limitations || []).map((s: string) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
        <p>
          Shared baseline:{" "}
          {data.baseline_at ? time(data.baseline_at) : "not yet available"}.
          Closing observation:{" "}
          {data.performance_end ? time(data.performance_end) : "pending"}.
          Figures show account equity changes, not cash-flow-adjusted strategy
          attribution.
        </p>
      </details>
    </section>
  );
}
