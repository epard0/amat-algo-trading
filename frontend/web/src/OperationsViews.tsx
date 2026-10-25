import { useState } from "react";
import { eventMessage, eventTime, eventTitle } from "./eventPresentation";
import { TaskPicker } from "./TaskPicker";
import { StructuredData } from "./StructuredData";
type Obj = Record<string, any>;
const count = (value: any) => typeof value === "number" ? value.toLocaleString() : "Not reported";

export function EventHistory({ events, inspect }: { events: Obj[]; inspect: (event: Obj) => void }) {
  const [limit, setLimit] = useState(30);
  return <section className="panel event-history" aria-label="Event history">
    <div className="panel-title">EVENT HISTORY <span className="muted">Newest first · {events.length} retained</span></div>
    {!events.length && <p className="muted pad">No matching events.</p>}
    {events.slice(0, limit).map((event, index) => <button key={event.event_id || index} className="event-history-row" onClick={() => inspect(event)}>
      <time>{eventTime(event.occurred_at || event.observed_at)}</time>
      <span className={`severity ${event.severity || "info"}`}>{event.severity || "info"}</span>
      <span className="event-message"><strong>{eventTitle(event)}</strong><span>{eventMessage(event)}</span><small className="muted">{event.source || "Local gateway"}</small></span>
    </button>)}
    {limit < events.length && <button onClick={() => setLimit(n => n + 30)}>Show 30 more events</button>}
  </section>;
}

export function snapshotDifferences(current: Obj, prior: Obj | undefined): string[] {
  if (!prior) return ["No earlier comparable snapshot is available."];
  const changes: string[] = [];
  for (const [key, label] of [["first_session", "Coverage starts"], ["last_session", "Coverage ends"], ["provider", "Provider"], ["feed", "Feed"]]) {
    if (current[key] !== prior[key]) changes.push(`${label}: ${prior[key] || "not reported"} → ${current[key] || "not reported"}.`);
  }
  if (typeof current.row_count === "number" && typeof prior.row_count === "number" && current.row_count !== prior.row_count) changes.push(`Row count: ${(current.row_count - prior.row_count).toLocaleString(undefined, { signDisplay: "always" })} (${count(prior.row_count)} → ${count(current.row_count)}).`);
  if (Array.isArray(current.symbols) && Array.isArray(prior.symbols)) {
    const added = current.symbols.filter((symbol: string) => !prior.symbols.includes(symbol));
    const removed = prior.symbols.filter((symbol: string) => !current.symbols.includes(symbol));
    if (added.length) changes.push(`Symbols added: ${added.join(", ")}.`);
    if (removed.length) changes.push(`Symbols removed: ${removed.join(", ")}.`);
  }
  return changes.length ? changes : ["Coverage, row count, provider/feed and reported symbols are unchanged. Market-data values were not compared."];
}
function SnapshotRecord({ snapshot, snapshots, index, inspect }: { snapshot: Obj; snapshots: Obj[]; index: number; inspect: (snapshot: Obj) => void }) {
  const previous = snapshots.slice(index + 1).find(other => other.provider === snapshot.provider && other.feed === snapshot.feed);
  const [baseline, setBaseline] = useState(previous?.id || "");
  const prior = snapshots.find(other => other.id === baseline);
  return <details className="snapshot-record">
    <summary className="snapshot-summary"><strong>{snapshot.id}</strong><span>{snapshot.first_session || "Unknown start"} – {snapshot.last_session || "Unknown end"}</span><span>{Array.isArray(snapshot.symbols) ? count(snapshot.symbols.length) : "Unknown"} symbols · {count(snapshot.row_count)} rows</span></summary>
    <p>Historical data{snapshot.provider ? ` from ${snapshot.provider}` : ""}{snapshot.feed ? ` · ${snapshot.feed}` : ""}. {snapshot.price_basis || "Price basis not reported."}</p>
    <TaskPicker label={`Compare ${snapshot.id} with`} value={baseline} onChange={setBaseline} placeholder="Select a comparison snapshot…" options={snapshots.filter(other => other.id !== snapshot.id).map(other => ({ value: other.id, label: other.id, description: `${other.first_session || "?"} – ${other.last_session || "?"} · ${count(other.row_count)} rows` }))} />
    <ul className="snapshot-delta">{snapshotDifferences(snapshot, prior).map(change => <li key={change}>{change}</li>)}</ul>
    {prior && <p className="muted">Metadata comparison only; row-count changes do not identify changed prices.</p>}
    <details><summary>Coverage and provenance</summary><StructuredData value={{ symbols: snapshot.symbols, source: snapshot.source, manifest_as_of: snapshot.manifest_as_of, manifest_last_session: snapshot.manifest_last_session }} /></details>
    <button onClick={() => inspect(snapshot)}>Technical details</button>
  </details>;
}
export function SnapshotList({ snapshots, inspect }: { snapshots: Obj[]; inspect: (snapshot: Obj) => void }) {
  return <section className="snapshot-list" aria-label="Snapshot summaries">{snapshots.length ? snapshots.map((snapshot, index) => <SnapshotRecord key={snapshot.id} snapshot={snapshot} snapshots={snapshots} index={index} inspect={inspect} />) : <p className="muted">No readable historical snapshots are available.</p>}</section>;
}
