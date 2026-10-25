import { OutputView, summary } from "./StructuredData";
import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { TaskPicker } from "./TaskPicker";
import { eventTitle, eventMessage } from "./eventPresentation";
type Obj = Record<string, any>;
const active = (s: string) =>
  ["queued", "started", "cancel_requested"].includes(s);
const time = (v: any) => (v ? new Date(v).toLocaleString() : "—");
const text = summary;
export default function ResearchWorkspace({
  latestJobId,
  catalog,
  jobs,
  events,
  artifacts,
  runtime,
  stream,
  updated,
  request,
  inspect,
  openArtifact,
  cancel,
  refresh,
}: {
  latestJobId: string;
  catalog: Obj[];
  jobs: Obj[];
  events: Obj[];
  artifacts: Obj[];
  runtime: Obj;
  stream: string;
  updated: string;
  request: (path: string) => Promise<Obj>;
  inspect: (v: Obj) => void;
  openArtifact: (v: Obj) => void;
  cancel: (id: string) => void;
  refresh: () => void;
}) {
  const [chosen, choose] = useState("");

  useEffect(() => {
    if (latestJobId) choose(latestJobId);
  }, [latestJobId]);
  const [detail, setDetail] = useState<Obj | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [clock, tick] = useState(Date.now());
  const research = jobs;
  const id = chosen || research[0]?.id || "";
  const job = research.find((j) => j.id === id);
  const related = events.filter(
    (e) => id && (e.action_id === id || e.correlation_id === id),
  );
  const visible = related.filter(
    (e) =>
      e.kind !== "action.output" &&
      (!onlyErrors ||
        e.severity === "error" ||
        /fail|refus|error/.test(e.kind || "")) &&
      JSON.stringify(e).toLowerCase().includes(query.toLowerCase()),
  );
  useEffect(() => {
    const timer = window.setInterval(() => tick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    setDetail(null);
    setError("");
    if (!id) return;
    let live = true;
    const load = () =>
      request("/jobs/" + encodeURIComponent(id))
        .then((d) => {
          if (live) {
            setDetail(d);
            setError("");
          }
        })
        .catch((e) => {
          if (live) setError(String(e));
        });
    load();
    window.addEventListener("amat:refresh", load);
    const timer = window.setInterval(() => {
      if (!document.hidden && active(job?.status)) load();
    }, 3000);
    return () => {
      window.removeEventListener("amat:refresh", load);
      live = false;
      clearInterval(timer);
    };
  }, [id, request, job?.status]);
  const boundDetail = detail?.id === id ? detail : null;
  const current = job?.finished_at && !boundDetail?.finished_at ? job : boundDetail || job;
  const baseOutput = boundDetail?.live_output || "";
  const liveTail = related.filter(event => event.kind === "action.output" && Number(event.cursor || event.event_id || 0) > Number(boundDetail?.live_output_cursor || 0))
    .sort((a, b) => Number(a.cursor || a.event_id) - Number(b.cursor || b.event_id))
    .map(event => event.payload?.text || "").join("");
  const shownOutput = current?.output || (baseOutput + liveTail).slice(0, 128000);
  const elapsed = current?.started_at
    ? Math.max(
        0,
        Math.floor(
          ((current.finished_at
            ? new Date(current.finished_at).getTime()
            : clock) -
            new Date(current.started_at).getTime()) /
            1000,
        ),
      )
    : null;
  const status = current?.status;
  const failed = ["failed", "refused", "cancelled", "outcome_unknown"].includes(
    status,
  );
  return (
    <div className="research-monitor">
      <section className="panel">
        <div className="panel-title">
          <span>
            <Activity size={14} /> RUN OVERVIEW
          </span>
          <button onClick={refresh}>
            <RefreshCw size={12} /> Refresh
          </button>
        </div>
        <div className="research-vitals" aria-live="polite">
          <div>
            <span>Event stream</span>
            <strong className={stream === "Live" ? "research-live" : ""}>
              {stream}
            </strong>
          </div>
          <div>
            <span>Task queue</span>
            <strong>
              {research.filter((j) => active(j.status)).length} active
            </strong>
          </div>
          <div>
            <span>Gateway memory</span>
            <strong>
              {runtime.process_resident_bytes == null
                ? "—"
                : (runtime.process_resident_bytes / 1048576).toFixed(1) + " MB"}
            </strong>
          </div>
          <div>
            <span>Host load · 1 min</span>
            <strong>
              {runtime.host_load_1_5_15_minutes?.[0]?.toFixed(2) ?? "—"}
            </strong>
          </div>
        </div>
        <p className="muted pad">
          Local gateway observations · Updated {time(updated)} · Worker CPU,
          model latency and token usage are not reported by this gateway.
        </p>
        <div className="research-run-picker">
          <TaskPicker label="Monitor run" value={id} onChange={choose} placeholder="Search previous runs…"
            options={research.map(j => ({ value: j.id, label: `${catalog.find(c => (c.capability || c.id) === j.capability)?.title || j.capability} · ${j.status}`, description: time(j.requested_at || j.started_at) }))} />
          {current && (
            <button onClick={() => inspect(current)}>Run details</button>
          )}
          {current &&
            active(status) &&
            current.capability?.startsWith("amat.") && (
              <button onClick={() => cancel(id)}>Cancel run</button>
            )}
        </div>
        {error && (
          <p role="alert" className="pad">
            Run refresh failed: {error}
          </p>
        )}
        {current ? (
          <>
            <ol className="research-lifecycle" aria-label="Run lifecycle">
              {[
                "Accepted",
                "Queued",
                "Running",
                failed ? status : "Succeeded",
              ].map((stage, i) => {
                const position = active(status)
                  ? status === "queued"
                    ? 1
                    : 2
                  : 3;
                return (
                  <li
                    key={i}
                    className={`${i === 0 || i === position || (i === 1 && status !== "refused") || (i === 2 && current.started_at) ? "reached" : ""} ${i === position ? "current" : ""} ${i === 3 && failed ? "issue" : ""}`}
                  >
                    <span>{i + 1}</span>
                    {stage}
                  </li>
                );
              })}
            </ol>
            <div className="research-run-meta">
              <span>
                Elapsed:{" "}
                {elapsed === null
                  ? "Awaiting worker"
                  : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`}
              </span>
              <span>{related.length} retained events</span>
              <span>Requested {time(current.requested_at)}</span>
            </div>
            {current.reason && (
              <p role="status" className="pad">
                {text(current.reason)}
              </p>
            )}
          </>
        ) : (
          <p className="pad muted">
            Choose a task and use Run action to begin monitoring.
            Existing runs appear here automatically.
          </p>
        )}
      </section>
      <section className="panel research-results-panel task-transcript" aria-label="Task output">
        <div className="panel-title">
          TERMINAL OUTPUT{" "}
          <span className="muted">Native values · updates with the run</span>
        </div>
        <div className="research-measured">
          {boundDetail?.live_output_truncated && <p className="muted">Live output reached the display bound; the completed run retains its final captured output.</p>}
          {current?.exit_code != null && <p>Exit code: {current.exit_code}</p>}
          <OutputView
            output={
              shownOutput || "Waiting for worker output…"
            }
          />
        </div>
      </section>
      <section className="panel">
        <div className="panel-title">
          RUN ACTIVITY <span className="muted">{visible.length} events</span>
        </div>
        <div className="research-feed-tools">
          <input
            aria-label="Search task activity"
            placeholder="Search output, agent, tool or event…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={onlyErrors}
              onChange={(e) => setOnlyErrors(e.target.checked)}
            />{" "}
            Errors only
          </label>
        </div>
        <div className="research-feed">
          {visible.length ? (
            visible.slice(0, 150).map((e, i) => (
              <button
                className={`research-event ${e.severity === "error" ? "issue" : ""}`}
                key={e.event_id || i}
                onClick={() => inspect(e)}
              >
                <time>{time(e.observed_at || e.occurred_at)}</time>
                <span className="research-event-node" />
                <span>
                  <strong>{eventTitle(e)}</strong>
                  <span className="research-event-summary">
                    {eventMessage(e)}
                  </span>
                </span>
              </button>
            ))
          ) : (
            <p className="muted pad">
              {id
                ? "No matching events in the retained stream. Run output is available below."
                : "Waiting for a task run."}
            </p>
          )}
        </div>
      </section>
      {artifacts.some(a => id && (a.job_id === id || a.action_id === id || a.path?.split("/").includes(id))) && <details className="panel">
        <summary>Reports and downloads for this run</summary>
        <div className="research-evidence">{artifacts.filter(a => id && (a.job_id === id || a.action_id === id || a.path?.split("/").includes(id))).map(a => <button key={a.id} onClick={() => openArtifact(a)}>{a.name || a.title || "Report"}</button>)}</div>
      </details>}
    </div>
  );
}
