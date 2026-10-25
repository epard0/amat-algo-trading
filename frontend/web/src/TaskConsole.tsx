import { useEffect, useRef, useState } from "react";
import { Terminal } from "lucide-react";
import { TaskPicker } from "./TaskPicker";
import { StructuredData } from "./StructuredData";

type Obj = Record<string, any>;
const title = (key: string) => key.replace(/[_.-]+/g, " ").replace(/\b\w/g, c => c.toUpperCase());

/** Guided CLI: rendering a preview never executes a command. */
export default function TaskConsole({ capabilities, capability, inputs, select, edit, request, accepted }: {
  capabilities: Obj[]; capability: string; inputs: string;
  select: (id: string) => void; edit: (inputs: string) => void;
  request: (path: string, body?: Obj) => Promise<Obj>;
  accepted: (result: Obj) => void;
}) {
  const selected = capabilities.find(c => (c.capability || c.id) === capability);
  const [preview, setPreview] = useState<Obj | null>(null);
  const [error, setError] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [runError, setRunError] = useState("");
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [completed, setCompleted] = useState("");
  const [revision, refreshPreview] = useState(0);
  const flight = useRef(false);
  const idem = useRef({ signature: "", key: "" });
  const signature = JSON.stringify([capability, inputs]);
  let values: Obj = {};
  try { values = JSON.parse(inputs); } catch { /* Preview reports invalid JSON. */ }
  useEffect(() => { setRunError(""); }, [signature]);
  useEffect(() => {
    let current = true;
    setPreview(null); setError(""); setPending(Boolean(selected));
    if (!selected) return;
    if (selected.supported === false || selected.available === false) {
      setError(selected.reason || "This task is unavailable."); setPending(false); return;
    }
    const timer = window.setTimeout(async () => {
      try {
        const parsed = JSON.parse(inputs);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("Inputs must be an object.");
        const response = await request("/actions/preview", { capability, inputs: parsed });
        if (current) setPreview({ ...response, signature });
      } catch (cause) {
        if (current) setError(cause instanceof Error ? cause.message : String(cause));
      } finally { if (current) setPending(false); }
    }, 350);
    return () => { current = false; clearTimeout(timer); };
  }, [signature, revision, request, selected]);
  useEffect(() => {
    if (!preview) return;
    const delay = Math.max(1000, new Date(preview.expires_at).getTime() - Date.now() - 10000);
    const timer = window.setTimeout(() => refreshPreview(n => n + 1), delay);
    return () => clearTimeout(timer);
  }, [preview]);
  const valid = preview?.signature === signature && new Date(preview.expires_at).getTime() > Date.now() && !pending;
  async function run() {
    if (flight.current || !valid || completed === signature) return;
    flight.current = true; setBusy(true); setRunError("");
    // Keep this key through network errors: retrying an uncertain HTTP response
    // must reconnect to the accepted job, rather than create another job.
    if (idem.current.signature !== signature) idem.current = { signature, key: crypto.randomUUID() };
    try {
      const result = await request("/actions", { capability, inputs: JSON.parse(inputs), preview_id: preview!.preview_id, idempotency_key: idem.current.key });
      setUncertain(false); setCompleted(signature); accepted(result);
    } catch (cause) {
      const status = (cause as { status?: number })?.status;
      setUncertain(!status || status >= 500);
      setRunError(cause instanceof Error ? cause.message : String(cause));
      refreshPreview(n => n + 1);
    } finally { flight.current = false; setBusy(false); }
  }
  return <section className="panel action-panel task-cli" aria-label="Task terminal">
    <div className="panel-title"><span><Terminal size={16} /> TASK TERMINAL</span><span className="muted">Search · configure · run</span></div>
    <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      <div className="form">
        <TaskPicker disabled={busy || uncertain} label="Choose a task" value={capability} onChange={select} placeholder="Type a task name or command…"
          options={capabilities.map(c => ({ value: c.capability || c.id, label: c.title || title(c.capability || c.id), group: c.category || "Other", description: c.supported === false || c.available === false ? c.reason || "Unavailable" : c.summary, disabled: uncertain || c.supported === false || c.available === false }))} />
        <div className="task-preview" aria-label="Selected task" aria-live="polite">
          {!selected ? <p className="muted">No action selected</p> : <>
            <div className="workflow-head"><strong>{selected.title || title(capability)}</strong>{selected.risk_label && <span className="risk">{selected.risk_label}</span>}</div>
            <p>{selected.summary}</p>
            <p className="muted">Target: {selected.target === "remote" || /^(perps|vps)\./.test(capability) ? "Remote VPS" : "Local workspace"}</p>
            {selected.effects?.length > 0 && <ul>{selected.effects.map((effect: string) => <li key={effect}>{effect}</li>)}</ul>}
          </>}
        </div>
        {selected && Object.entries(selected.inputs || {}).map(([key, raw]) => {
          const schema = raw as Obj;
          const update = (value: any) => edit(JSON.stringify({ ...values, [key]: value }));
          if (schema.type === "select" && schema.options?.length > 8) return <TaskPicker disabled={busy || uncertain} key={key} label={title(key)} value={values[key] ?? schema.default ?? ""} onChange={update} placeholder={`Search ${title(key).toLowerCase()}…`} options={schema.options.map((option: string) => ({ value: option, label: title(String(option)), disabled: uncertain }))} />;
          return <label key={key} className={schema.type === "boolean" ? "boolean-field" : ""}>
            {schema.type === "boolean" ? <><input disabled={uncertain} type="checkbox" checked={values[key] ?? schema.default ?? false} onChange={e => update(e.target.checked)} />{title(key)}</> : <>
              {title(key)}
              {schema.type === "select" ? <select disabled={uncertain} aria-label={title(key)} value={values[key] ?? schema.default ?? ""} onChange={e => update(e.target.value)}>{(schema.options || []).map((v: string) => <option key={v} value={v}>{title(String(v))}</option>)}</select>
                : <input disabled={uncertain} aria-label={title(key)} type={schema.type === "integer" ? "number" : "text"} min={schema.minimum} max={schema.maximum} value={values[key] ?? schema.default ?? ""} onChange={e => update(schema.type === "integer" && e.target.value !== "" ? Number(e.target.value) : e.target.value)} />}
            </>}
            {schema.description && <small className="muted">{schema.description}</small>}
          </label>;
        })}
        {selected && <div className="task-prompt"><code className="cli-command">&gt; {capability} {Object.entries(values).filter(([, v]) => v !== "").map(([k, v]) => `--${k.replace(/_/g, "-")} ${JSON.stringify(v)}`).join(" ")}</code><span className="muted">{pending ? "Checking inputs…" : valid ? "Ready to run · inputs validated" : "Waiting for valid inputs"}</span></div>}
        <div id="action-feedback" aria-live="polite">{(runError || error) && <p role="alert" className="action-error">{runError || error}</p>}</div>
        {uncertain && <p role="status">The request outcome is unconfirmed. Run action again to reconnect using the same request; task settings stay locked until it is resolved.</p>}
        {completed === signature ? <div role="status"><p>Action accepted. Follow progress and output below.</p><button onClick={() => { setCompleted(""); idem.current = { signature: "", key: "" }; refreshPreview(n => n + 1); }}>Prepare another run</button></div>
          : <button className="primary" disabled={!valid || busy || !selected} onClick={run}>{busy ? "Starting action…" : "Run action"}</button>}
        {preview && <details><summary>Validated inputs and preview expiry</summary><StructuredData value={{ inputs: preview.inputs, expires_at: preview.expires_at }} /></details>}
      </div>
    </fieldset>
  </section>;
}
