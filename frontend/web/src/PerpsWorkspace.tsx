import ReadableTable from "./ReadableTable";
import { summary } from "./StructuredData";
import { useEffect, useState } from "react";
type Obj = Record<string, any>;
type Props = {
  capabilities: Obj[];
  manage: () => void;
  request: (path: string, body?: Obj, csrf?: string) => Promise<Obj>;
};
const value = summary;
export default function PerpsWorkspace({
  capabilities,
  manage,
  request,
}: Props) {
  const [report, setReport] = useState<Obj>({});
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = () =>
      request("/perps")
        .then((r) => {
          if (active) {
            setReport(r);
            setError("");
          }
        })
        .catch((e) => {
          if (active) setError(String(e));
        });
    load();
    window.addEventListener("amat:refresh", load);
    const timer = window.setInterval(() => {
      if (!document.hidden) load();
    }, 15000);
    return () => {
      window.removeEventListener("amat:refresh", load);
      active = false;
      clearInterval(timer);
    };
  }, [request]);
  const remote = capabilities.some((c) => c.id === "perps.start");
  const accounts: Obj[] = report.accounts || [];
  const money = (v: any) =>
    v != null && Number.isFinite(Number(v))
      ? Number(v).toLocaleString(undefined, {
          style: "currency",
          currency: "USD",
        })
      : "—";
  const pct = (v: any) =>
    v != null && Number.isFinite(Number(v))
      ? `${(Number(v) * 100).toFixed(2)}%`
      : "—";
  const stale =
    report.stale ||
    (report.observed_at &&
      Date.now() - new Date(report.observed_at).getTime() > 120000);
  return (
    <div className="workspace-reveal">
      <div className="workspace-intro">
        <div>
          <span className="eyebrow">
            KALSHI PERPETUALS · {remote ? "VPS PAPER" : "LOCAL PAPER"}
          </span>
          <h2>Strategy operations</h2>
          <p>
            {remote
              ? "Remote paper execution with durable account state and native strategy gates."
              : "Native strategy gates, isolated accounts, supervised comparison."}
          </p>
        </div>
        <span className="environment">PAPER ONLY</span>
      </div>
      <div className="source-line">
        {report.source || "Native paper comparison"} ·{" "}
        {report.observed_at
          ? new Date(report.observed_at).toLocaleString()
          : "No observation"}{" "}
        ·{" "}
        {stale
          ? "Stale observation"
          : report.state || report.status || "Loading"}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {report.reason && (
        <p className="error" role="alert">
          {report.reason}
        </p>
      )}
      {remote && (
        <div className="report-metrics">
          <div>
            <span>Remote container</span>
            <strong>{report.deployment?.status || "Unobserved"}</strong>
            <small>{report.deployment?.health || "Health unobserved"}</small>
          </div>
          <div>
            <span>Runner state</span>
            <strong>{stale ? "Stale" : report.state || "Waiting"}</strong>
            <small>{report.error_type || "Paper mode · no venue orders"}</small>
          </div>
          <div>
            <span>Recovery</span>
            <strong>{report.deployment?.restart_count ?? "—"} restarts</strong>
            <small>{report.deployment?.restart_policy || "—"}</small>
          </div>
          <div>
            <span>Memory limit</span>
            <strong>
              {report.deployment?.memory_limit_bytes
                ? `${Math.round(report.deployment.memory_limit_bytes / 1024 ** 2)} MiB`
                : "—"}
            </strong>
            <small>Durable Docker volume</small>
          </div>
        </div>
      )}
      <section className="panel">
        <div className="panel-title">
          Forward paper performance · $10,000 initial capital per account
        </div>
        {accounts.length > 0 ? (
          <div className="table-scroll">
            <ReadableTable>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>State</th>
                  <th>Equity</th>
                  <th>Net P&amp;L</th>
                  <th>Return</th>
                  <th>Max drawdown</th>
                  <th>Fees</th>
                  <th>Leverage</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((r: Obj) => (
                  <tr key={r.strategy}>
                    <td>{r.strategy}</td>
                    <td>{value(r.state)}</td>
                    <td>{money(r.equity)}</td>
                    <td>{money(r.net_pnl)}</td>
                    <td>{pct(r.net_return)}</td>
                    <td>{pct(r.max_drawdown)}</td>
                    <td>{money(r.fees)}</td>
                    <td>
                      {r.effective_leverage != null
                        ? `${Number(r.effective_leverage).toFixed(2)}×`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
        ) : (
          <p className="pad muted">
            {report.reason || "Loading native paper performance…"}
          </p>
        )}
        <p className="pad muted">
          {report.leverage_policy?.replaceAll("_", " ")} · Forward simulated
          fills. Historical strategy qualification remains separate.
        </p>
      </section>
      <button onClick={manage}>Manage remote engine</button>
    </div>
  );
}
