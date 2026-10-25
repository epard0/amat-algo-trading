import { StructuredData, OutputView } from "./StructuredData";
import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Server, Terminal } from "lucide-react";
type Obj = Record<string, any>;
type Props = {
  capabilities: Obj[];
  choose: (id: string) => void;
  request: (path: string, body?: Obj, csrf?: string) => Promise<Obj>;
};
const date = (v: any) => v ? new Date(v).toLocaleString() : "Never observed";
const text = (v: any) => v == null ? "—" : String(v);
const bytes = (v: any) => typeof v === "number" && Number.isFinite(v)
  ? `${(v / 1024 ** 3).toFixed(2)} GiB` : "—";
export default function RemoteWorkspace({ request, capabilities, choose }: Props) {
  const [remote, setRemote] = useState<Obj>({});
  const [health, setHealth] = useState<Obj>({});
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("logs");
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const observe = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const [current, healthStatus] = await Promise.all([request("/remote"), request("/remote/health")]);
      setRemote(current); setHealth(healthStatus); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { busy.current = false; }
  }, [request]);
  const check = useCallback(async (diagnostics = false) => {
    setPending(true);
    try {
      const session = await request("/session");
      await request(diagnostics ? "/remote/run" : "/remote/health/refresh", diagnostics ? { checks: ["status", "performance", "logs"] } : {}, session.csrf_token);
      await observe();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setPending(false); }
  }, [request, observe]);
  useEffect(() => {
    void observe();
    const refresh = () => { void observe(); };
    window.addEventListener("amat:refresh", refresh);
    // Cached reads only. The local API owns collection even when this desk is closed.
    const timer = window.setInterval(refresh, 5000);
    return () => { clearInterval(timer); window.removeEventListener("amat:refresh", refresh); };
  }, [observe]);
  const engines = health.observation || {};
  const infra = engines.infrastructure || {};
  const host = infra.host || {};
  const schedule = health.scheduler || {};
  const run = remote.last_run;
  const completed = run?.status === "running" ? remote.last_completed_run : run;
  const results: Obj[] = completed?.results || [];
  const active = results.find((r) => r.command === selected);
  const readiness = engines.executor?.readiness || {};
  const containers: [string, Obj][] = Object.entries(infra.containers || {});
  return <div className="remote-workspace workspace-reveal">
    <div className="workspace-intro">
      <div><span className="eyebrow">REMOTE HEALTH</span><h2><Server size={22} /> {remote.host || "VPS"}</h2>
        <p>{remote.username || "SSH"} · Port {remote.port || "—"} · Paper engines</p></div>
      <div className="button-row"><button className="primary" disabled={pending || !remote.configured || !schedule.running} onClick={() => void check()}>
        <RefreshCw size={14} className={pending ? "spin" : ""} />{pending ? "Requested…" : "Check now"}
      </button></div>
    </div>
    {(error || remote.reason || engines.reason) && <p className="error" role="alert">{error || engines.reason || remote.reason}</p>}
    <p className="source-line" role="status">{engines.stale ? "Stale / unavailable" : engines.transport === "connected" ? "Recent remote observation" : "Waiting for remote observation"} · {date(engines.refreshed_at)}
      {engines.age_seconds != null ? ` · ${Math.round(engines.age_seconds)}s ago` : ""}</p>
    <section className="panel"><div className="panel-title">Automatic monitoring</div>
      <div className="pad"><p>{schedule.running ? "Running with the local API" : "Automatic checks paused"} · Checks every {schedule.interval_seconds || 60}s, independently of the selected tab.</p>
        <p className="muted">Next check in {text(schedule.next_check_in_seconds)}s · Extended diagnostics every {Math.round((schedule.heavy_interval_seconds || 300) / 60)} min when resources allow. {schedule.heavy_status}</p>
        <small>Checks observe existing processes. Native watchdogs, trade alerts, backups and paper-session collection retain their own lifecycles.</small>
      </div>
      {health.issues?.length > 0 && <ul className="pad">{health.issues.map((issue: string) => <li key={issue}>{issue}</li>)}</ul>}
    </section>
    <div className="report-metrics">
      <div><span>Memory available</span><strong>{bytes(host.memory_available_bytes)}</strong><small>of {bytes(host.memory_total_bytes)} physical RAM</small></div>
      <div><span>Swap used</span><strong>{bytes(host.swap_used_bytes)}</strong><small>of {bytes(host.swap_total_bytes)}</small></div>
      <div><span>Disk available</span><strong>{bytes(host.disk_free_bytes)}</strong><small>of {bytes(host.disk_total_bytes)}</small></div>
      <div><span>CPU load · 1 / 5 / 15m</span><strong>{host.load_1_5_15?.map((v: number) => v.toFixed(2)).join(" / ") || "—"}</strong><small>{text(host.cpu_count)} CPU · {host.uptime_seconds ? `${(host.uptime_seconds / 86400).toFixed(1)} days uptime` : "Uptime unavailable"}</small></div>
    </div>
    <section className="panel"><div className="panel-title">Application processes and resources{engines.stale ? " · Last known observations" : ""}</div>
      {containers.length ? containers.map(([name, container]) => <details className="structured-record" key={name}>
        <summary><strong>{name}</strong><span>{container.running ? container.health || container.status : container.status} · {container.resources?.MemUsage || "Memory unobserved"} · {container.resources?.CPUPerc || "—"} CPU</span></summary>
        <StructuredData value={{ Status: container.status, Health: container.health, "Memory use / limit": container.resources?.MemUsage, "CPU use": container.resources?.CPUPerc, "Process count": container.resources?.PIDs, Restarts: container.restart_count, "Out of memory termination": container.oom_killed, Image: container.image }} />
      </details>) : <p className="empty">Container measurements have not been collected.</p>}
    </section>
    <div className="remote-readiness"><strong>Executor order readiness</strong><span>{(engines.stale || engines.executor?.stale) ? "Last known readiness · stale / unavailable" : readiness.ready_for_orders === true ? "Ready for scheduled paper orders" : readiness.order_readiness_issues?.join(" · ") || "Not observed"}</span>
      {readiness.notification_warnings?.length > 0 && <small>{readiness.notification_warnings.join(" · ")}</small>}</div>
    <section className="panel remote-engines"><div className="panel-title">VPS trading engines</div>
      {[
        { key: "executor", title: "AMAT · Alpaca paper", prefix: "vps.executor." },
        { key: "perps", title: "Kalshi perps · Paper", prefix: "perps." },
      ].map(({ key, title, prefix }) => <div className="remote-engine-row" key={key}><div><h3>{title}</h3>
        <p>{engines[key]?.deployment?.status || engines[key]?.status || "Unobserved"} · {(engines.stale || engines[key]?.stale) ? "Last known observation · stale" : engines[key]?.health?.responsive === false ? "Progress stale" : engines[key]?.health?.healthy === true ? "Native health good" : engines[key]?.state || "Health unobserved"}</p></div>
        <div className="button-row">{capabilities.filter(c => c.supported && c.target === "remote" && c.id.startsWith(prefix)).map(c => <button key={c.id} aria-label={c.title || c.id} onClick={() => choose(c.id)}>{c.id.split(".").at(-1)}</button>)}</div>
      </div>)}
      <p className="pad muted">Select a control to review its effects before Run action. Stopping preserves account state and accepted orders.</p>
    </section>
    <section className="panel"><div className="panel-title">Backups and session collector</div><div className="pad">
      {Object.entries(infra.backups || {}).map(([kind, value]) => { const backup = value as Obj; return <p key={kind}><strong>{kind === "state" ? "Executor" : "Kalshi"} backup</strong> · {backup.status || "Unobserved"} · {backup.age_seconds != null ? `${(backup.age_seconds / 3600).toFixed(1)} hours old` : "Age unavailable"}</p>; })}
      <p>Paper-session collector · {infra.paper_monitor?.status || "Unobserved"} · {infra.paper_monitor?.age_seconds != null ? `${Math.round(infra.paper_monitor.age_seconds)}s since last success` : "No timestamp"}</p>
      <details><summary>Host capacity and application inventory</summary><StructuredData value={{ "Free inodes": host.inodes_free, "Total inodes": host.inodes_total, "Unexpected containers": infra.unexpected_containers, "Unexpected TCP listeners": infra.unexpected_listeners, "Unexpected UI services": infra.unexpected_services, "Unexpected UI processes": infra.unexpected_ui_processes, "Memory pressure": host.memory_pressure, "Listener observation available": infra.listener_observation_available, "Service observation available": infra.service_observation_available }} /></details>
    </div></section>
    <div className="remote-detail-grid"><section className="panel"><div className="panel-title">Extended diagnostics</div>
      <div className="pad"><button disabled={pending || remote.running || !remote.configured} onClick={() => void check(true)}>{remote.running ? "Diagnostics running…" : "Run extended diagnostics"}</button><p className="muted">Last completed {date(completed?.finished_at)}</p></div>
      <div className="check-list">{["status", "performance", "logs"].map(name => { const r = results.find(r => r.command === name); return <button key={name} className={selected === name ? "selected" : ""} onClick={() => setSelected(name)}><span>{name === "logs" ? "Recent activity" : name}</span><span className={r?.status === "passed" ? "good-text" : "muted"}>{r?.status?.replaceAll("_", " ") || "Waiting"}</span></button>; })}</div>
    </section><section className="panel remote-output"><div className="panel-title"><span><Terminal size={14} /> {selected === "logs" ? "Recent activity" : selected}</span><small>{date(active?.observed_at)}</small></div>
      {active ? <><div className="source-line">{active.status?.replaceAll("_", " ")} · {text(active.duration_seconds)}s{active.truncated ? " · Output truncated" : ""}</div><StructuredData value={active.data ?? active.stdout ?? "No output reported"} />{active.stderr && <div className="diagnostic-warning"><strong>Diagnostic messages</strong><OutputView output={active.stderr} /></div>}</> : <div className="empty">Extended measurements appear after an automatic or requested diagnostic run.</div>}
    </section></div>
  </div>;
}
