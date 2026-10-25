import CampaignComparison from "./CampaignComparison";
import ReadableTable from "./ReadableTable";
import { useEffect, useState } from "react";

type Obj = Record<string, any>;
const date = (value: unknown) => {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) return "Not available";
  return new Date(value).toLocaleString("en-US", { timeZone: "America/Detroit", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
};
const label = (value: unknown) => typeof value === "string" || typeof value === "number" ? String(value) : "Not available";
const percent = (value: unknown) => value == null || !Number.isFinite(Number(value)) ? "Not measured" : `${(Number(value) * 100).toFixed(2)}%`;

export default function ResearchCampaignPanel({ request }: { request: (path: string) => Promise<Obj> }) {
  const [data, setData] = useState<Obj | null>(null);
  const [failed, setFailed] = useState(false);
  const [selectedCost, setSelectedCost] = useState("6");
  useEffect(() => {
    let active = true;
    const load = () => request("/research-campaign").then(value => { if (active) { setData(value); setFailed(false); } }).catch(() => { if (active) setFailed(true); });
    load();
    window.addEventListener("amat:refresh", load);
    const timer = window.setInterval(() => { if (!document.hidden) load(); }, 30000);
    return () => { active = false; window.removeEventListener("amat:refresh", load); clearInterval(timer); };
  }, [request]);
  const campaign = data?.campaign || {};
  const counts = data?.counts || { completed_experiments: campaign.experiments_completed, promotions: campaign.experiments_promoted,
    completed_runs: Array.isArray(data?.scheduler_receipts) ? data.scheduler_receipts.filter((r: Obj) => r.status === "completed").length : undefined };
  const current = data?.current_default || campaign.baseline || {};
  const results: Obj[] = Array.isArray(data?.results) ? data.results : [];
  const visibleResults = results.filter(row => row.cost_bps == null || String(row.cost_bps) === selectedCost);
  const pending = Array.isArray(data?.pending_foundations) ? data.pending_foundations : Array.isArray(campaign.pending_foundation) ? campaign.pending_foundation : [];
  const foundationNames: Obj = { sharedQQQbenchmark: "Comparable QQQ benchmark", exactcurrentdefaultattribution: "Attribute current strategy performance", simulationentrycapparity: "Match simulated entry limits to execution", livetialcorporateactioncoverage: "Verify recent dividend and split coverage", "partialrebalancewhole-sellsparity": "Verify partial rebalance sell sizing", nativecontrolreproduction: "Reproduce the current strategy", typed_numeric_gate_evaluator_and_source_bound_evidence: "Independent numerical promotion evaluator", native_broker_fill_contract_parity: "Compare historical fills with the broker execution contract", candidate_exported_plan_parity: "Reproduce new rules in native session plans", typed_numeric_gate_evaluator_integration: "Connect the numerical evaluator to promotion controls" };
  const summary = data?.last_summary || data?.final_summary || data?.current_run?.agent_report?.summary;
  const shadow = campaign.free_model_shadow;
  const status = data?.lifecycle || data?.status || "Loading";
  const statusNames: Obj = { running: "Research run in progress", starting: "Starting a research run", completed: "Run finished; validation status below", finished: "Campaign finished", engine_failed: "Research run failed", engine_unavailable: "Research engine unavailable", timed_out: "Run reached its time limit", invalid_agent_report: "Run report requires attention", awaiting_next_tick: "Waiting for the next hourly run", cycle_complete: "Validation cycle finished" };
  const evidence = data?.publication_evidence;
  return <section className="paper-session-panel" aria-label="Research campaign">
    <div className="analysis-section-title"><h2>Research campaign on this laptop</h2><span>{data?.stale || failed ? "Saved status needs attention" : statusNames[status] || label(status)}</span></div>
    {!data ? <p>{failed ? "Research status could not be read." : "Reading the laptop’s research status…"}</p> : <>
      <p>{data.transport === "local" ? data.scheduling_note || data.message || "The research schedule runs on this laptop." : "Campaign status is unavailable."}</p>
      {data.stale && <p>The saved campaign status is older than expected or could not be verified. It does not confirm that Codex is running.</p>}
      <div className="analysis-metric-row">
        <div><span>Completed experiments</span><strong>{label(counts.completed_experiments)}</strong></div>
        <div><span>Completed runs</span><strong>{label(counts.completed_runs)}</strong></div>
        <div><span>Promotions</span><strong>{label(counts.promotions)}</strong></div>
      </div>
      <dl><dt>Current verified default</dt><dd>{label(typeof current === "string" ? current : current.label || current.id || current.version)}</dd>
        <dt>Campaign started</dt><dd>{date(data.started_at || data.start_utc)}</dd><dt>Next scheduled run</dt><dd>{date(data.next_run_at || data.next_run_utc)}</dd>
        <dt>Campaign deadline</dt><dd>{date(data.deadline || data.deadline_utc)}</dd><dt>Last saved campaign update</dt><dd>{date(data.observed_at)}</dd></dl>
      {summary && <p>{label(summary)}</p>}
      {shadow && <section aria-label="Free-model shadow research">
        <h3>Free-model shadow arm</h3>
        <p>Research proposals only. The paper bots and their order authority remain separate.</p>
        <dl><dt>Requested model</dt><dd>{label(shadow.requested_model)}</dd>
          <dt>Actual served model</dt><dd>{label(shadow.served_model)}</dd>
          <dt>Latest capability check</dt><dd>{date(shadow.checked_utc)}</dd></dl>
        <div className="table-scroll"><ReadableTable><thead><tr><th>Check</th><th>Finding</th></tr></thead><tbody>
          <tr><td>Action gates</td><td>{label(shadow.action_gates_summary)}</td></tr>
          {shadow.native_adapter_summary && <tr><td>Research native adapter</td><td>{label(shadow.native_adapter_summary)}</td></tr>}
          {shadow.native_plan_summary && <tr><td>Native plan compatibility</td><td>{label(shadow.native_plan_summary)}</td></tr>}
          <tr><td>Hosted capability</td><td>{label(shadow.capability_summary)}</td></tr>
          <tr><td>Observed inference cost</td><td>{label(shadow.billing_summary)}</td></tr>
          <tr><td>Prospective performance</td><td>{label(shadow.prospective_summary)}</td></tr>
          <tr><td>Execution and promotion authority</td><td>None</td></tr>
        </tbody></ReadableTable></div>
        {Array.isArray(shadow.native_adapter_costs) && <>
          <h4>Research adapter checks by cost</h4>
          <p>Fixed synthetic proposals through native simulated fills and accounting. These modeled fees are not paper-account costs or investment returns.</p>
          <div className="table-scroll"><ReadableTable><thead><tr><th>Cost per side</th><th>Passed cases</th><th>Native comparison</th><th>One-share buy fee</th><th>Capped request: filled / remaining</th></tr></thead><tbody>
            {shadow.native_adapter_costs.map((row: Obj, i: number) => <tr key={i}><td>{label(row.cost_bps)} bps</td><td>{label(row.passed_cases)}</td><td>{label(row.parity)}</td><td>{label(row.resize_fee)}</td><td>{label(row.capped_quantities)}</td></tr>)}
          </tbody></ReadableTable></div>
        </>}
        <p>{label(shadow.limitations_summary)}</p>
      </section>}
      {campaign.promotion_enabled === false && <p>Strategy promotion is blocked while the required validation is incomplete.</p>}
      {pending.length > 0 && <details open><summary>Checks required before promotion</summary><ul>{pending.map((item: unknown, i: number) => <li key={i}>{foundationNames[String(item)] || label(item)}</li>)}</ul></details>}
      {data.results_error && <p role="alert">{label(data.results_error)}</p>}
      {results.length > 0 && <label>Trading costs per side <select aria-label="Trading costs per side" value={selectedCost} onChange={e => setSelectedCost(e.target.value)}>{[2, 6, 10].map(c => <option key={c} value={c}>{c} basis points ({(c / 100).toFixed(2)}%)</option>)}</select></label>}
      {results.length ? <div className="table-scroll"><ReadableTable><thead><tr><th>Experiment</th><th>Result</th><th>Cost per side</th><th>Net return</th><th>2020 onward</th><th>Maximum drawdown</th><th>Decision</th></tr></thead><tbody>{visibleResults.map((row, i) => <tr key={row.id || i}><td>{label(row.name || row.id)}</td><td>{label(row.status)}</td><td>{row.cost_bps == null ? "Not measured" : `${row.cost_bps} bps`}</td><td>{percent(row.net_return)}</td><td>{percent(row.forward_return)}</td><td>{percent(row.max_drawdown)}</td><td>{label(row.display_reason || row.reason || row.decision)}</td></tr>)}</tbody></ReadableTable></div> : <p>No completed strategy measurements have been published. Engineering checks do not count as investment improvements.</p>}
      {data.comparison && <CampaignComparison value={data.comparison} cost={selectedCost} />}
      <p>Only improvements that pass the independent validation checks can replace the default. The remote paper-session monitor and both paper bots remain separate.</p>
      <details><summary>Saved evidence</summary><p>Campaign: {label(data.campaign_id)}</p><p>Default identity: {label(current.sha256 || current.profile_sha256)}</p>{evidence && <p>Publication: {label(evidence.file)} · {label(evidence.sha256)}</p>}
        {data.current_run?.run_id && <p>Run: {label(data.current_run.run_id)} · {label(data.current_run.status)}</p>}
        {Array.isArray(data.evidence) && data.evidence.map((item: unknown, i: number) => <p key={i}>{typeof item === "object" && item !== null ? JSON.stringify(item) : label(item)}</p>)}
      </details>
    </>}
  </section>;
}
