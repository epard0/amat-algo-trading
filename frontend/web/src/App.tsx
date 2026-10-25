import { StructuredData, summary } from "./StructuredData";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  ChevronRight,
  CircleHelp,
  Database,
  FlaskConical,
  Globe2,
  LayoutDashboard,
  ListFilter,
  OctagonX,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Terminal,
  X,
  Zap,
} from "lucide-react";
import ResearchWorkspace from "./ResearchWorkspace";
import TaskConsole from "./TaskConsole";
import ReadableTable from "./ReadableTable";
import { EventHistory, SnapshotList } from "./OperationsViews";
import { eventTitle, eventMessage, eventTime } from "./eventPresentation";
import RemoteWorkspace from "./RemoteWorkspace";
import PerpsWorkspace from "./PerpsWorkspace";
import { PerformanceCharts } from "./AnalysisCharts";
type Obj = Record<string, any>;
const desks = [
  "Markets",
  "Performance",
  "Kalshi perps",
  "Tasks",
  "Operations",
  "Remote",
];
const icons = [BarChart3, LayoutDashboard, Zap, FlaskConical, Activity, Globe2];
const deskAliases: Record<string, string> = {
  Portfolio: "Markets",
  Execution: "Tasks",
  Research: "Tasks",
  News: "Tasks",
  Data: "Operations",
  Settings: "Operations",
};
const api = "/api/v1";
function defaultInputs(cap: Obj | undefined): Obj {
  const values = Object.fromEntries(
    Object.entries(cap?.inputs || {}).map(([key, value]) => {
      const schema = value as Obj;
      return [
        key,
        schema.default ??
          schema.options?.[0] ??
          (schema.type === "boolean" ? false : ""),
      ];
    }),
  );
  if ((cap?.id || cap?.capability) === "amat.research.launch")
    values.name = `research-${new Date().toISOString().replace(/[^0-9]/g, "")}-${crypto.randomUUID().slice(0, 6)}`;
  return values;
}
function list(value: any): Obj[] {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") {
    for (const k of [
      "items",
      "positions",
      "orders",
      "events",
      "runs",
      "artifacts",
      "values",
      "checks",
      "quotes",
      "bars",
    ])
      if (Array.isArray(value[k])) return value[k];
  }
  return [];
}
function show(v: any): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return summary(v);
  return String(v);
}
function label(s: string) {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
function timestamp(v: any) {
  if (!v) return "—";
  const d = new Date(v);
  return isNaN(d.getTime())
    ? show(v)
    : d.toLocaleTimeString([], { hour12: false });
}
let sessionPromise: Promise<Obj> | null = null;
function sessionReady() {
  if (!sessionPromise)
    sessionPromise = fetch(api + "/session", { credentials: "same-origin" })
      .then(async (r) => {
        if (!r.ok) throw new Error("Local session unavailable");
        return r.json();
      })
      .catch((e) => {
        sessionPromise = null;
        throw e;
      });
  return sessionPromise;
}
async function request(
  path: string,
  body?: Obj,
  csrf?: string,
  retryAuth = true,
): Promise<Obj> {
  if (path === "/session") return sessionReady();
  const session = await sessionReady();
  const r = await fetch(api + path, {
    credentials: "same-origin",
    headers: body
      ? {
          "Content-Type": "application/json",
          "X-CSRF-Token": session.csrf_token || csrf || "",
        }
      : {},
    ...(body ? { method: "POST", body: JSON.stringify(body) } : {}),
  });
  let data: Obj;
  try {
    data = await r.json();
  } catch {
    throw new Error(`HTTP ${r.status}: gateway returned a non-JSON response`);
  }
  if (!r.ok) {
    if (
      retryAuth &&
      (r.status === 401 ||
        (r.status === 403 && data.detail === "CSRF token required"))
    ) {
      sessionPromise = null;
      await sessionReady();
      return request(path, body, undefined, false);
    }
    if (r.status === 401) sessionPromise = null;
    const failure = new Error(
      typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail
              .map(
                (e: Obj) =>
                  `${(e.loc || []).filter((k: string) => k !== "body").join(" / ")}: ${e.msg}`,
              )
              .join("; ")
          : data.detail?.message || data.message || `HTTP ${r.status}`,
    );
    Object.assign(failure, { status: r.status });
    throw failure;
  }
  return data;
}
function money(v: any, currency = "USD") {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return show(v);
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return show(v);
  }
}
function Empty({
  title = "No observations available",
  detail = "Connect a configured source to populate this view.",
}: {
  title?: string;
  detail?: string;
}) {
  return (
    <div className="empty">
      <Database size={24} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}
function Table({
  rows,
  columns,
  onSelect,
  empty,
}: {
  rows: Obj[];
  columns?: string[];
  onSelect?: (r: Obj) => void;
  empty?: { title: string; detail: string };
}) {
  const available = new Set(rows.flatMap(Object.keys));
  const priorities = available.has("capability")
    ? ["capability", "status", "requested_at", "finished_at", "exit_code"]
    : available.has("side")
      ? ["symbol", "ticker", "side", "qty", "quantity", "status", "filled_qty"]
      : ["symbol", "ticker", "qty", "quantity", "market_value", "unrealized_pl", "price", "change_percent", "status"];
  const preferred = priorities.filter(key => available.has(key));
  const keys = columns || (preferred.length ? preferred : [...available].filter(key =>
    !["payload", "metadata", "raw"].includes(key) && !/(^|_)(id|hash|digest|path)$/.test(key)
  )).slice(0, 5);
  return rows.length ? (
    <div className="table-scroll">
      <ReadableTable>
        <thead>
          <tr>
            {keys.map((k) => (
              <th key={k}>{label(k)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={r.id || r.event_id || i}
              onClick={() => onSelect?.(r)}
              tabIndex={onSelect ? 0 : undefined}
              onKeyDown={(e) => e.key === "Enter" && onSelect?.(r)}
            >
              {keys.map((k) => (
                <td key={k} title={show(r[k])}>
                  {(["price", "market_value", "unrealized_pl"].includes(k))
                    ? money(r[k])
                    : k === "change_percent" &&
                        r[k] !== null &&
                        r[k] !== undefined
                      ? Number(r[k]).toFixed(2) + "%"
                      : /_at$/.test(k) ? eventTime(r[k]) : show(r[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </ReadableTable>
    </div>
  ) : (
    <Empty {...empty} />
  );
}
function Facts({ data }: { data: any }) {
  if (data == null) return <Empty />;
  return <StructuredData value={data} />;
}
function Chart({ bars }: { bars: Obj[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [volume, setVolume] = useState(true);
  const valid = bars.filter(
    (b) => Number.isFinite(Number(b.close)) && b.close !== null,
  );
  if (valid.length < 2)
    return (
      <Empty
        title="Price history unavailable"
        detail="Enter a symbol with available historical bars. No synthetic prices are shown."
      />
    );
  const values = valid.flatMap((b) => [
    Number(b.low ?? b.close),
    Number(b.high ?? b.close),
  ]);
  const min = Math.min(...values),
    max = Math.max(...values),
    range = max - min || 1;
  const y = (v: number) => 220 - ((v - min) / range) * 195;
  const width = 1000 / valid.length;
  const maxVolume = Math.max(...valid.map((b) => Number(b.volume) || 0), 1);
  const selected = valid[hover ?? valid.length - 1];
  return (
    <div className="chart">
      <div className="chart-toolbar">
        <span>
          {show(selected.time ?? selected.session_date ?? selected.date)} · O{" "}
          {show(selected.open)} H {show(selected.high)} L {show(selected.low)} C{" "}
          {show(selected.close)}
        </span>
        <button onClick={() => setVolume(!volume)} aria-pressed={volume}>
          Volume {volume ? "on" : "off"}
        </button>
      </div>
      <div className="chart-axis">
        <span>{max.toFixed(2)}</span>
        <span>{((max + min) / 2).toFixed(2)}</span>
        <span>{min.toFixed(2)}</span>
      </div>
      <svg
        viewBox="0 0 1000 280"
        role="img"
        aria-label="Historical OHLC candlesticks and volume"
        preserveAspectRatio="none"
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          setHover(
            Math.max(
              0,
              Math.min(
                valid.length - 1,
                Math.floor(
                  ((e.clientX - rect.left) / rect.width) * valid.length,
                ),
              ),
            ),
          );
        }}
        onMouseLeave={() => setHover(null)}
      >
        {[25, 90, 155, 220].map((y) => (
          <line key={y} x1="0" x2="1000" y1={y} y2={y} stroke="#252b32" />
        ))}
        {valid.map((b, i) => {
          const open = Number(b.open ?? b.close),
            close = Number(b.close);
          const color = close >= open ? "#80b4a3" : "#c47e78";
          return (
            <g key={i}>
              <line
                x1={(i + 0.5) * width}
                x2={(i + 0.5) * width}
                y1={y(Number(b.high ?? close))}
                y2={y(Number(b.low ?? close))}
                stroke={color}
              />
              <rect
                x={(i + 0.15) * width}
                y={Math.min(y(open), y(close))}
                width={Math.max(width * 0.7, 1)}
                height={Math.max(Math.abs(y(open) - y(close)), 1)}
                fill={color}
              />
              {volume && (
                <rect
                  x={i * width}
                  y={280 - ((Number(b.volume) || 0) / maxVolume) * 45}
                  width={Math.max(width * 0.8, 1)}
                  height={((Number(b.volume) || 0) / maxVolume) * 45}
                  fill={color}
                  opacity=".35"
                />
              )}
            </g>
          );
        })}
        {hover !== null && (
          <line
            x1={(hover + 0.5) * width}
            x2={(hover + 0.5) * width}
            y1="0"
            y2="280"
            stroke="#a5afbc"
            strokeDasharray="3 4"
          />
        )}
      </svg>
      <div className="chart-caption">
        <span>
          {show(valid[0]?.time ?? valid[0]?.session_date ?? valid[0]?.date)}
        </span>
        <span>{valid.length} historical bars</span>
        <span>
          {show(
            valid.at(-1)?.time ??
              valid.at(-1)?.session_date ??
              valid.at(-1)?.date,
          )}
        </span>
      </div>
    </div>
  );
}
export default function App() {
  const [desk, setDesk] = useState(() => {
    const saved = localStorage.getItem("amat.desk") || "Markets";
    return deskAliases[saved] || (desks.includes(saved) ? saved : "Markets");
  });
  const [data, setData] = useState<Obj>({});
  const [workspace, setWorkspace] = useState<Obj>({});
  const [events, setEvents] = useState<Obj[]>([]);
  const [connected, setConnected] = useState(false);
  const [stream, setStream] = useState("Connecting");
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState("");
  const [filter, setFilter] = useState(
    () => localStorage.getItem("amat.filter") || "",
  );
  const [tab, setTab] = useState("Positions");
  const [selection, setSelection] = useState<Obj | null>(null);
  const [capability, setCapability] = useState("");
  const [inputs, setInputs] = useState("{}");
  const [result, setResult] = useState<Obj | null>(null);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [symbol, setSymbol] = useState(
    () => localStorage.getItem("amat.symbol") || "SPY",
  );
  const [bars, setBars] = useState<Obj[]>([]);
  const [barError, setBarError] = useState("");
  const [barLoading, setBarLoading] = useState(false);
  const [barRevision, setBarRevision] = useState(0);
  useEffect(() => {
    const refresh = () => {
      if (desk === "Markets") setBarRevision((n) => n + 1);
    };
    window.addEventListener("amat:refresh", refresh);
    return () => window.removeEventListener("amat:refresh", refresh);
  }, [desk]);
  const [interval, setIntervalValue] = useState("250");
  const [resolution, setResolution] = useState("5m");
  const [snapshots, setSnapshots] = useState<Obj[]>([]);
  const [artifactContent, setArtifactContent] = useState<Obj | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [barMeta, setBarMeta] = useState<Obj>({});
  const [notice, setNotice] = useState("");
  async function refresh() {
    try {
      const [d, w] = await Promise.all([
        request("/dashboard"),
        request("/workspace"),
      ]);
      setData(d);
      setWorkspace(w);
      setEvents((old) => mergeEvents([...list(d.events), ...old]));
      setConnected(true);
      setError("");
      setUpdated(new Date().toISOString());
    } catch (e) {
      setConnected(false);
      setError(String(e instanceof Error ? e.message : e));
    }
  }
  function refreshAll() {
    refresh();
    window.dispatchEvent(new Event("amat:refresh"));
  }
  function mergeEvents(es: Obj[]) {
    const seen = new Set();
    return es
      .filter((e) => {
        const key = e.event_id || JSON.stringify(e);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) =>
        String(b.observed_at || b.occurred_at).localeCompare(
          String(a.observed_at || a.occurred_at),
        ),
      )
      .slice(0, 500);
  }
  useEffect(() => {
    let disposed = false;
    let s: EventSource | undefined;
    let eventRefresh: number | undefined;
    const t = window.setInterval(() => {
      if (!disposed) refresh();
    }, 15000);
    request("/session")
      .then(() => {
        if (disposed) return;
        refresh();
        request("/market/snapshots")
          .then((d) => setSnapshots(list(d)))
          .catch(() => {});
        s = new EventSource(api + "/events/stream");
        s.onopen = () => setStream("Live");
        s.onerror = () => {
          setStream("Reconnecting");
          sessionPromise = null;
          sessionReady().catch(() => {});
        };
        const receive = (e: MessageEvent) => {
          try {
            const item = JSON.parse(e.data);
            if (item.kind === "resync_required") {
              refresh();
              return;
            }
            setEvents((old) => mergeEvents([item, ...old]));
            if (
              /^action\.(requested|started|succeeded|failed|refused|cancelled|outcome_unknown)$/.test(
                item.kind || "",
              )
            ) {
              clearTimeout(eventRefresh);
              eventRefresh = window.setTimeout(refresh, 200);
            }
          } catch {
            /* heartbeat */
          }
        };
        s.onmessage = receive;
        s.addEventListener("terminal", receive as EventListener);
        s.addEventListener("resync", () => refresh());
      })
      .catch((e) => setError(String(e)));
    return () => {
      disposed = true;
      clearInterval(t);
      clearTimeout(eventRefresh);
      s?.close();
    };
  }, []);
  useEffect(() => {
    if (desk === "Markets") refresh();
    localStorage.setItem("amat.desk", desk);
  }, [desk]);
  useEffect(() => {
    localStorage.setItem("amat.filter", filter);
  }, [filter]);
  useEffect(() => {
    if (!symbol) {
      setBars([]);
      setBarMeta({});
      return;
    }
    let active = true;
    setBarLoading(true);
    setBarError("");
    setBars([]);
    request(
      `/market/bars?symbol=${encodeURIComponent(symbol)}&resolution=${resolution}&limit=${interval}${snapshot ? "&snapshot=" + encodeURIComponent(snapshot) : ""}`,
    )
      .then((d) => {
        if (active) {
          setBars(list(d));
          setBarMeta(d);
        }
      })
      .catch((e) => {
        if (active) {
          setBars([]);
          setBarMeta({});
          setBarError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (active) setBarLoading(false);
      });
    return () => {
      active = false;
    };
  }, [symbol, interval, snapshot, resolution, barRevision]);
  useEffect(() => {
    localStorage.setItem("amat.symbol", symbol);
  }, [symbol]);
  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelection(null);
      }
      if (e.altKey && e.key.toLowerCase() === "r") {
        e.preventDefault();
        refreshAll();
      }
      if (e.altKey && /^[1-6]$/.test(e.key)) {
        e.preventDefault();
        setDesk(desks[Number(e.key) - 1]);
      }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, []);
  const capabilities = useMemo(() => {
    const c = workspace.capabilities || [];
    return Array.isArray(c)
      ? c.map((v: any) =>
          typeof v === "string" ? { capability: v, available: true } : v,
        )
      : Object.entries(c).map(([k, v]) => ({
          capability: k,
          ...(typeof v === "object" ? (v as Obj) : { available: v }),
        }));
  }, [workspace]);
  const positions = list(data.account?.positions || data.positions);
  const orders = list(data.orders);
  const rows =
    tab === "Positions"
      ? positions
      : tab === "Orders"
        ? orders
        : list(data.runs);
  const filtered = (r: Obj[]) =>
    r.filter((x) =>
      JSON.stringify(x).toLowerCase().includes(filter.toLowerCase()),
    );
  function openAction(cap: string, overrides: Obj = {}) {
    const c = capabilities.find((a: any) => (a.capability || a.id) === cap);
    setCapability(cap);
    setInputs(
      JSON.stringify(
        {
          ...defaultInputs(c),
          ...overrides,
        },
        null,
        2,
      ),
    );
    setResult(null);
    if (desk !== "Tasks") setDesk("Tasks");
  }
  async function cancelJob(id: string) {
    try {
      const session = await request("/session");
      await request(
        "/jobs/" + encodeURIComponent(id) + "/cancel",
        {},
        session.csrf_token,
      );
      setNotice("Cancel requested; the job stops within a moment.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  useEffect(() => {
    if (!selection?.id || !["queued", "started"].includes(selection.status))
      return;
    const t = window.setInterval(() => {
      request("/jobs/" + encodeURIComponent(selection.id))
        .then((j) => {
          setSelection((cur) => (cur?.id === j.id ? j : cur));
          if (!["queued", "started"].includes(j.status)) refresh();
        })
        .catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [selection?.id, selection?.status]);
  const metricEntries = Object.entries(
    data.account?.account || data.account || {},
  ).filter(
    ([k, v]) =>
      [
        "equity",
        "cash",
        "buying_power",
        "portfolio_value",
        "unrealized_pnl",
        "realized_pnl",
      ].includes(k) && typeof v !== "object",
  );
  return (
    <div className="terminal">
      <aside className="rail">
        <a
          className="brand"
          href="#"
          aria-label="AMAT terminal"
          onClick={(e) => {
            e.preventDefault();
            setDesk("Markets");
          }}
        >
          <span className="brand-mark">A</span>
          <b>
            AMAT<span>TERMINAL</span>
          </b>
        </a>
        <div className="rail-label">WORKSPACE</div>
        <nav>
          {desks.map((name, i) => {
            const Icon = icons[i];
            return (
              <button
                className={desk === name ? "active" : ""}
                key={name}
                aria-label={name}
                onClick={() => {
                  setDesk(name);
                  setSelection(null);
                }}
              >
                <Icon size={17} />
                <span>{name}</span>
                {desk === name && <ChevronRight size={14} />}
              </button>
            );
          })}
        </nav>
        <div className="rail-bottom">
          <div className="deployment">
            <span className={`dot ${connected ? "good" : ""}`} />
            <div>
              <b>Local workspace</b>
              <small>
                {connected ? "Gateway connected" : "Gateway unavailable"}
              </small>
            </div>
          </div>
          <button
            className="help"
            onClick={() =>
              setSelection({
                title: "Terminal shortcuts",
                refresh:
                  "Data refreshes every 15 seconds. Use the refresh button for an immediate snapshot.",
                events:
                  "Events reconnect automatically; click a row to inspect its source and correlation.",
                actions:
                  "Preview a task before dispatching it. Remote contains paper engine lifecycle controls; stopping a container preserves its durable state.",
              })
            }
          >
            <CircleHelp size={16} /> Operator guide
          </button>
        </div>
      </aside>
      <div className="shell">
        <header>
          <div className="breadcrumb">
            WORKSPACE <ChevronRight size={12} />{" "}
            <strong>{desk.toUpperCase()}</strong>
          </div>
          <div className="header-right">
            <span className="environment">OBSERVATION & CONTROL</span>
            <span className={`connection ${connected ? "good-text" : ""}`}>
              <Radio size={13} />
              {connected ? "Connected" : "Offline"}
            </span>
            <button
              className="icon-button"
              onClick={refreshAll}
              aria-label="Refresh data"
            >
              <RefreshCw size={16} />
            </button>
          </div>
        </header>
        <div className="titlebar">
          <div>
            <div className="eyebrow">AMAT / LOCAL TERMINAL</div>
            <h1>{desk === "Markets" ? "Market overview" : desk}</h1>
          </div>
          <div className="title-actions">
            <span className="muted">Snapshot {timestamp(updated)}</span>

          </div>
        </div>
        {error && (
          <div role="alert" className="error">
            <ShieldCheck size={15} />
            <span>{error}</span>
            <button onClick={() => setError("")} aria-label="Dismiss error">
              <X size={14} />
            </button>
          </div>
        )}
        {notice && (
          <div role="status" className="notice">
            <ShieldCheck size={15} />
            <span>{notice}</span>
            <button onClick={() => setNotice("")} aria-label="Dismiss notice">
              <X size={14} />
            </button>
          </div>
        )}
        {["Markets", "Portfolio"].includes(desk) && (
          <>
            <div className="observation-banner">
              <span className="historical-badge">
                {String(data.account?.status || "UNOBSERVED").toUpperCase()}{" "}
                ACCOUNT
              </span>
              <span>
                {data.account?.source || "Awaiting source"} ·{" "}
                {data.account?.observed_at
                  ? new Date(data.account.observed_at).toLocaleString()
                  : "No observation timestamp"}
              </span>
              <span className="account-age">
                {data.account?.observed_at
                  ? Math.max(
                      0,
                      Math.floor(
                        (Date.now() -
                          new Date(data.account.observed_at).getTime()) /
                          86400000,
                      ),
                    ) + " days old"
                  : "Age unknown"}
              </span>
              <span className="muted">
                {data.account?.reason ||
                  "Live broker values have not been observed."}
              </span>
            </div>
            <div className="metrics-strip">
              {(metricEntries.length
                ? metricEntries
                : [
                    ["equity", null],
                    ["cash", null],
                    ["buying_power", null],
                    ["unrealized_pnl", null],
                  ]
              ).map(([k, v]) => (
                <div key={k}>
                  <span>{label(String(k))}</span>
                  <strong>
                    {money(v, data.account?.account?.currency || "USD")}
                  </strong>
                  <small>
                    {v === null
                      ? "Awaiting account observation"
                      : (data.account?.account?.currency || "USD") +
                        " · " +
                        (data.account?.status || "observed")}
                  </small>
                </div>
              ))}
              <div className="health-summary">
                <span>Executor readiness</span>
                <strong className="status-value">
                  {data.health?.ready_for_orders === true
                    ? "Ready"
                    : data.health?.ready_for_orders === false
                      ? "Blocked"
                      : "Unverified"}
                </strong>
                <small>Independent of API connection</small>
              </div>
            </div>
          </>
        )}
        <main>
          {desk === "Markets" ? (
            <>
              <div className="market-grid">
                <section className="panel price-panel">
                  <div className="panel-title">
                    <span>
                      {symbol || "PRICE HISTORY"}{" "}
                      <span className="muted">/ Observed data</span>
                    </span>
                    <div className="segmented">
                      {["60", "120", "250"].map((t) => (
                        <button
                          className={interval === t ? "selected" : ""}
                          key={t}
                          onClick={() => setIntervalValue(t)}
                        >
                          {t} bars
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="chart-symbol">
                    <select
                      aria-label="Bar resolution"
                      className="resolution-selector"
                      value={resolution}
                      onChange={(e) => setResolution(e.target.value)}
                    >
                      <option value="5m">5 minutes</option>
                      <option value="1d">Daily</option>
                      <option value="1w">Weekly</option>
                    </select>
                    <input
                      list="market-symbols"
                      aria-label="Chart symbol"
                      placeholder="Enter symbol, e.g. SPY"
                      value={symbol}
                      onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                    />
                    <span className="muted">
                      HISTORICAL · {barMeta.as_of || "snapshot"}
                    </span>
                  </div>
                  {barMeta.quality_status === "review_required" && (
                    <button
                      className="quality-notice"
                      onClick={() =>
                        setSelection({
                          quality_status: barMeta.quality_status,
                          quality_scope: barMeta.quality_scope,
                          warnings: barMeta.quality_warnings,
                        })
                      }
                    >
                      <ShieldCheck size={12} />
                      <span>
                        Source data needs review ·{" "}
                        {list(barMeta.quality_warnings).length} anomalous bars
                      </span>
                      <ChevronRight size={12} />
                    </button>
                  )}
                  <datalist id="market-symbols">
                    {positions.map((p) => (
                      <option key={p.symbol} value={p.symbol} />
                    ))}
                  </datalist>
                  {barLoading ? (
                    <p className="pad muted" role="status">
                      Loading historical bars…
                    </p>
                  ) : barError ? (
                    <Empty title="History unavailable" detail={barError} />
                  ) : (
                    <Chart bars={bars} />
                  )}
                </section>
              </div>
              <section className="panel holdings">
                <div className="panel-title">
                  <div className="tabs">
                    {["Positions", "Orders", "Runs"].map((t) => (
                      <button
                        className={tab === t ? "selected" : ""}
                        key={t}
                        onClick={() => setTab(t)}
                      >
                        {t}{" "}
                        <span>
                          {t === "Positions"
                            ? positions.length
                            : t === "Orders"
                              ? orders.length
                              : list(data.runs).length}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
                <Table rows={filtered(rows)} onSelect={setSelection} />
              </section>
            </>
          ) : null}
          {desk === "Remote" && (
            <RemoteWorkspace
              request={request}
              capabilities={capabilities}
              choose={openAction}
            />
          )}
          {desk === "Performance" && (
            <div className="performance-workspace workspace-reveal">
              <PerformanceCharts request={request} />
            </div>
          )}
          {desk === "Kalshi perps" && (
            <PerpsWorkspace
              request={request}
              capabilities={capabilities}
              manage={() => setDesk("Remote")}
            />
          )}
          {desk === "Markets" && (
            <>
              <div className="section-heading">
                <h2>Account & exposure</h2>
                <span className="muted">
                  Account currency and source observation shown above
                </span>
              </div>
              <section className="panel">
                <Facts data={data.account} />
              </section>
              <section className="panel">
                <div className="panel-title">METRICS</div>
                <Facts data={data.metrics} />
              </section>
            </>
          )}
          {desk === "Tasks" && (
            <div
              className={`execution-grid ${desk === "Tasks" ? "research-layout" : ""}`}
            >
              <TaskConsole capabilities={capabilities} capability={capability} inputs={inputs}
                select={(id) => openAction(id)} edit={setInputs} request={request}
                accepted={(response) => { setResult(response); refresh(); }} />
              <ResearchWorkspace
                latestJobId={result?.job_id || ""}
                catalog={capabilities}
                jobs={list(data.jobs || data.runs)}
                events={events}
                artifacts={list(data.artifacts)}
                runtime={data.health?.runtime || {}}
                stream={stream}
                updated={updated}
                request={request}
                inspect={setSelection}
                cancel={cancelJob}
                refresh={refreshAll}
                openArtifact={(r) => {
                  setSelection(r);
                  setArtifactContent(null);
                  request("/artifacts/" + encodeURIComponent(r.id))
                    .then(setArtifactContent)
                    .catch((e) => setError(String(e)));
                }}
              />
            </div>
          )}
          {desk === "Operations" && (
            <>
              <div className="workspace-intro operations-intro">
                <div>
                  <span className="eyebrow">LOCAL WORKSPACE</span>
                  <h2>Workspace health & runtime</h2>
                  <p>
                    These observations describe the local gateway and executor.
                    VPS engine health and controls are available in Remote.
                  </p>
                </div>
                <button onClick={() => setDesk("Remote")}>
                  <Globe2 size={14} /> View remote engines
                </button>
              </div>
              <div className="operations-health">
                <section className="panel">
                  <div className="panel-title">
                    <span>
                      <Activity size={14} /> HEALTH & READINESS
                    </span>
                  </div>
                  <Facts
                    data={
                      data.health
                        ? Object.fromEntries(
                            Object.entries(data.health).filter(
                              ([key]) => key !== "runtime",
                            ),
                          )
                        : null
                    }
                  />
                </section>

              </div>
              <section className="panel">
                <div className="panel-title">
                  LOCAL GATEWAY RUNTIME{" "}
                  <span className="muted">
                    Process measurements · separate from executor readiness
                  </span>
                </div>
                <Facts data={data.health?.runtime} />
              </section>
              <EventHistory events={filtered(events)} inspect={setSelection} />
            </>
          )}
          {desk === "Operations" && (
            <details className="panel operations-data">
              <summary>Historical data sources & snapshots</summary>
              <section className="panel">
                <div className="panel-title">
                  MARKET SNAPSHOTS{" "}
                  <select
                    aria-label="Market snapshot"
                    value={snapshot}
                    onChange={(e) => setSnapshot(e.target.value)}
                  >
                    <option value="">Latest available</option>
                    {snapshots.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.last_session || "Undated"} · {s.symbols?.length ?? "?"} symbols · {s.id}
                      </option>
                    ))}
                  </select>
                </div>
                <SnapshotList snapshots={snapshots} inspect={setSelection} />
              </section>
            </details>
          )}
          {desk === "Operations" && (
            <details className="panel settings">
              <summary>Workspace preferences</summary>
              <div className="panel-title">
                <span>
                  <Settings2 size={14} /> TERMINAL PREFERENCES
                </span>
              </div>
              <div className="setting-row">
                <div>
                  <h3>Persistent event console</h3>
                  <p>Keep action initiation and resulting events in view.</p>
                </div>
                <input
                  aria-label="Show event console"
                  type="checkbox"
                  checked={consoleOpen}
                  onChange={(e) => setConsoleOpen(e.target.checked)}
                />
              </div>
              <div className="setting-row">
                <div>
                  <h3>Saved workspace</h3>
                  <p>
                    Desk, selected symbol, and table filter are saved in this
                    browser. Alt+1–6 switches tabs, Alt+R refreshes, Escape
                    closes details.
                  </p>
                </div>
                <button
                  onClick={() => {
                    setDesk("Markets");
                    setSymbol("SPY");
                    setSnapshot("");
                    setResolution("5m");
                    setIntervalValue("250");
                    setFilter("");
                  }}
                >
                  Reset layout
                </button>
              </div>
              <div className="setting-row">
                <div>
                  <h3>Connection</h3>
                  <p>Same-origin API · {api} · refresh every 15 seconds</p>
                </div>
                <span>{connected ? "Connected" : "Unavailable"}</span>
              </div>
            </details>
          )}
        </main>
        <section className={`event-console ${consoleOpen ? "" : "collapsed"}`}>
          <div className="console-heading">
            <button onClick={() => setConsoleOpen(!consoleOpen)}>
              <Terminal size={14} /> LIVE ACTIVITY{" "}
              <span className="event-count">{events.length}</span>
            </button>
            <div>
              <label className="event-filter">
                <ListFilter size={13} />
                <input
                  aria-label="Filter events and tables"
                  placeholder="Filter events…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
              </label>
              <span className={stream === "Live" ? "good-text" : "muted"}>
                <span className="dot" />
                {stream}
              </span>
            </div>
          </div>
          {consoleOpen && (
            <div className="event-rows">
              {filtered(events)
                .slice(0, 70)
                .map((e, i) => (
                  <button
                    className="event-row"
                    key={e.event_id || i}
                    onClick={() => setSelection(e)}
                  >
                    <time>{timestamp(e.occurred_at || e.observed_at)}</time>
                    <span className={`severity ${e.severity || "info"}`}>
                      {(e.severity || "info").toUpperCase()}
                    </span>
                    <span className="event-source">
                      {e.source || "gateway"}
                    </span>
                    <strong>{eventTitle(e)}</strong>
                    <span className="event-detail">
                      {eventMessage(e)}
                    </span>
                  </button>
                ))}
              {!events.length && (
                <div className="console-empty">
                  Waiting for durable source events. Action requests and their
                  observed effects will appear separately.
                </div>
              )}
            </div>
          )}
        </section>
        <footer>
          <span>
            <span className={`dot ${connected ? "good" : ""}`} />{" "}
            {connected ? "API ONLINE" : "API OFFLINE"}
          </span>
          <span>EVENT STREAM {stream.toUpperCase()}</span>
          <span className="footer-end">
            AMAT ENGINE / EXECUTOR{" "}
            <span className="muted">· Local workspace</span>
          </span>
        </footer>
      </div>
      {selection && (
        <div className="drawer-backdrop" onClick={() => setSelection(null)}>
          <aside className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="panel-title">
              <span>OBSERVATION DETAILS</span>
              <button
                aria-label="Close details"
                onClick={() => setSelection(null)}
              >
                <X size={18} />
              </button>
            </div>
            {["queued", "started"].includes(selection.status) &&
              /^(amat|news)\./.test(selection.capability || "") && (
                <button
                  className="danger"
                  onClick={() => cancelJob(selection.id)}
                >
                  <OctagonX size={13} /> Cancel job
                </button>
              )}
            <Facts data={selection} />
            {artifactContent &&
              selection.id === artifactContent.artifact?.id && (
                <div className="artifact-content">
                  <h3>Artifact contents</h3>
                  {artifactContent.download_url && (
                    <a
                      className="download-link"
                      href={artifactContent.download_url}
                      download
                      target="_blank"
                      rel="noreferrer"
                    >
                      Download report / evidence <ArrowUpRight size={13} />
                    </a>
                  )}
                  <StructuredData value={artifactContent.content} />
                </div>
              )}
          </aside>
        </div>
      )}
    </div>
  );
}
