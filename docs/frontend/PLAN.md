# AMAT Terminal implementation plan and GUI review

October 8, 2026, America/Detroit. Original planning baseline. October 9 implementation update: the initial WebUI, API, historical market adapter, independent jobs, engine adapters and optional Electron shell now exist. See [implementation status](IMPLEMENTATION.md) and [verification evidence](tests/VERIFICATION.md) for delivered scope; advanced workbench coverage remains a roadmap.

## Review of the current plan

The September 30 `docs/hedge-fund-workbench/PLAN.md` is a strong functional baseline: it preserves paper-only execution, covers research and configuration, separates workers from HTTP, and specifies truthful freshness, durable events, layouts and evidence. Retain those requirements and its capability registry. Its 88-command inventory is a historical baseline, not current verified coverage; regenerate it against the October working tree.

| Finding | Required improvement |
|---|---|
| Operations delivery comes late in P5 | Bring read-only account, orders, health and event console into the first integrated release. Keep executor mutations after verified control contracts. |
| General analytical shell leaves trading details open | Specify a watchlist/chart/order-intent/positions layout, dense tables and persistent environment context. |
| Event stream lacks explicit action-effect linkage | Add action/correlation/causation IDs and separate request, engine start, trigger, broker acknowledgment and eventual fill. |
| September baseline predates October VPS safeguards | Integrate heartbeat, local readiness, STOP_SUBMISSIONS, durable receipts, retry exhaustion, notification state and unknown-order recovery. |
| Financial metric definitions are not fully assigned | Add definitions, units, source ownership, sample windows and unavailable behavior before implementing headline numbers. |
| Folder proposal is only documentation | Establish top-level `frontend/` for web, optional desktop, API and adapters; do not build inside AMAT's empty package file. |
| Upstream reuse is unspecified | Pin actual Fincept source and hashes, distinguish Qt reference code from web ports, preserve licensing and provenance. |
| Container health could imply trading readiness | Display API, process, feed, reconciliation and order readiness independently. |

This update supersedes the earlier `workbench/` folder recommendation and delivery priority. It supplements, rather than replaces, the complete research/backtest/configuration coverage requirements. No existing strategy, execution cap or trading mode is changed by this plan.

## Architecture and application boundary

Use one React/TypeScript/Vite application served with a local FastAPI gateway. The API delegates typed actions to a durable Python supervisor and narrow adapters. Existing AMAT runtime computes strategies/metrics; the executor remains the sole broker submission owner. Observe an explicitly selected local deployment or a remote authenticated gateway on the VPS; do not assume local `executor/state` is the Docker volume. Record deployment identity, account binding, source root and revision in every read model.

WebUI is the first delivery. Electron is an optional wrapper after browser acceptance, using the identical web bundle/API contracts. Keep Node integration off, context isolation and renderer sandbox on, strict CSP, minimal allowlisted preload IPC and restricted navigation. Credentials remain server-side. Closing the window does not stop the executor or existing supervised jobs. Desktop process ownership must distinguish an application-owned API from an attached remote service. Browser support must not depend on Electron APIs.

Do not import AMAT into executor or put web dependencies in its standard-library package. No direct browser-to-broker action, browser shell execution, duplicate scheduler, or second strategy/risk calculation path. API reads indexes and approved snapshots; only source owners write authoritative engine state.

## Trading interface and screen composition

Visual thesis: a dark, precise workstation with restrained orange action accents, thin dividers, aligned tabular figures, and semantic gain/loss colors. Use Fincept's copied tokens and compact status/watchlist organization as porting references. Content plan: persistent header/navigation, resizable trading canvas, right inspector and bottom event tray. Interaction thesis: linked symbol selection, synchronized chart/trade inspection, brief update highlights with stable row selection. Respect reduced motion; never animate away an incident.

Default desktop layout at 1440×900:

```text
┌ AMAT TERMINAL | PAPER / DRY RUN | deployment/account | session/clock | search ┐
│ Markets Execution Portfolio Research Data Operations Settings | saved desk │
├──────────────┬─────────────────────────────────────┬───────────────────────┤
│ Watchlists   │ Selected symbol: price, change,     │ Plan / intent         │
│ Symbol Last  │ bid/ask, feed delay, session        │ version/digest        │
│ Δ% Volume    │ Candles + volume + order markers   │ target/current weight │
│ freshness    │ timeframes, crosshair, indicators  │ quantity/notional     │
│              │                                     │ readiness / preview   │
├──────────────┴─────────────────────────────────────┴───────────────────────┤
│ Account strip: equity | cash | exposure | P&L | drawdown | observations     │
│ Positions / Orders / Fills / Plans / Research jobs / Incidents              │
├───────────────────────────────────────────────────────────────────────────┤
│ Live events: time · source · action · event · state · latency · details     │
└ API | executor progress | market feed | reconciliation | disk | stream lag ┘
```

- Watchlist width approximately 220 px; right inspector 300 px; remaining width belongs to chart. Panes resize with keyboard alternatives and persist by named layout. Use 12–14 px readable data text, 28–32 px rows and explicit density settings.
- Price header includes symbol, exchange, currency, current quote versus previous close, absolute/% change, bid/ask sizes where supplied, OHLC, volume and exact as-of time. Delayed and historical data are unmistakable.
- Chart supports candles, volume, timeframe selection, synchronized crosshair, zoom/reset, sessions/timezone, optional supported overlays and source-backed trade/plan markers. Daily bars are not presented as tick-live data. Corporate-action adjustment convention is visible.
- Right panel is the executor's approved-plan intent inspector first. It shows current/target/delta allocation, sizing, cash constraints, expiry, digest and blockers. Manual trading tickets are a separate backend capability, not assumed from a familiar trading layout.
- Positions table: symbol, quantity, cost basis, mark/as-of, market value, weight, unrealized P&L/$/%, realized P&L if supplied, strategy ownership/evidence. Orders: client/broker IDs, symbol, side/type, requested/filled/remaining, prices, broker status, registry status, submit/last-update times and session.
- Optional depth/time-and-sales panels require actual entitlement and source data. Do not substitute candles or simulated depth. Quote changes do not imply trades.
- Keep PAPER, DRY_RUN, disabled state and selected deployment visible in every desk, modal and detached window. A large green READY badge must identify its exact scope.
- Market desk, portfolio desk, execution desk, research desk and operations desk reuse components. Save layouts, filters and selected entities separately from frozen strategy evidence. At 1024 px collapse the inspector; narrow views prioritize monitoring.
- No confirmation for routine reads or bounded authorized research. Order-capable actions use an explicit consequence preview and server recheck. Show existing policy requirements without adding recurring approval requirements to the unattended daemon.

## Metric and presentation contract

All values include unit/currency, method/version, source reference, as-of time, period and availability. Calculations run in the owning Python metric layer. Drilldown exposes samples, denominators and exclusions. JavaScript formatting/plot sampling never changes official metrics.

| Metric group | Display and definition |
|---|---|
| Equity, cash, buying power | Broker-observed account strip; buying power is not spendable cash or an allocation instruction. |
| Daily P&L | Dollar and percentage against an explicit prior-session equity baseline, with external cash flows treated by a documented method; unavailable if baseline/flow history is insufficient. |
| Realized/unrealized P&L | Separate from account return; broker/source accounting basis visible, dividends/fees not silently merged into trade profit. |
| Gross/net exposure and concentration | Market-value exposure / equity; cash excluded, price age and maximum-position weight visible. Current long-only scope remains explicit. |
| Equity and drawdown | Equity curve plus drawdown `(equity / running_peak) - 1`, with cash-flow-adjusted performance series identified separately from raw account equity. |
| Returns, CAGR, volatility | Explicit net/gross basis, period, annualization convention and minimum observations; suppress misleading annualization of short/gapped samples. |
| Sharpe and Sortino | Method/version and risk-free/MAR assumptions visible. Existing CAGR/volatility Sharpe must not be silently renamed conventional arithmetic excess-return Sharpe. New definitions require backend parity tests. |
| Win rate, profit factor, expectancy | Completed-trade count and trade matching policy, partial fills, fees and open trades explicit; zero losses means undefined/infinite under a declared convention, never an arbitrary large value. |
| Fill quality | Fill rate denominator, weighted fill price, signal-to-submit and submit-to-ack times; slippage against a defined observable reference. Paper fills carry a simulation limitation label. |
| Strategy/research | Benchmark excess performance, turnover, costs, folds/partitions, trial count, budget, robustness and failed gates; independent paper performance remains separate. |
| Runtime | CPU/memory/disk, queue age, job progress, provider latency/error/rate limits, token usage/cost where known, feed lag and event ingestion lag. Unknown cost stays unknown. |

Metrics not currently produced are backlog entries with methodology and fixtures, not frontend guesses. Display unavailable with a reason and offer source details.

## Monitoring and healthchecks

| Observation | Authoritative source / display rule |
|---|---|
| Daemon progress | `heartbeat.json`, `executor health`; current native maximum progress age is 1800 seconds. Show age and native result; UI warning threshold may be stricter and labeled. |
| Local order readiness | `executor health --ready`, config digest, DRY_RUN, EXECUTOR_DISABLED, interlock and reconciliation reasons. Local readiness does not establish broker/network readiness. |
| Orders | `order-registry.json`; preserve broker status separately from pending_market_closed or observation_unavailable. |
| Cash/share reconciliation | `portfolio-monitor.json`, baseline/checkpoint evidence; last attempt and last success independent. Current documented staleness is twelve minutes; verify parser/code in Phase 0. |
| Incidents/deployment | `deployment-registry.sqlite3`, `operations.json`; paper authorization, observed fills and incident watch remain separate facets. |
| Submission recovery | Durable pre-POST receipts, uncertain outcomes, retry budgets and singleton locks; show unresolved original identities. |
| Reports and alerts | Durable report presence versus Telegram/Mailgun/SMTP configuration/delivery state; missing notifications are warnings, not invented order gates. |
| Infrastructure | API/supervisor process health, disk/inodes, memory, queue backlog, source file errors, stream connection and adapter failures. Probe health without holding trading locks. |
| Whole-host outage | Independent external observer/heartbeat receiver required; workstation disconnect alone cannot diagnose a VPS failure. |

Proposed cadence: source file/event discovery 1–2 seconds, lightweight API heartbeat 5 seconds, host resources 10 seconds, expensive diagnostics on demand or budgeted intervals. Preserve executor's actual broker polling cadence (documented approximately four minutes plus processing). Faster UI refresh cannot fabricate faster broker observations. Missing, corrupt, future-dated and stale data retain explicit failure states. Avoid invoking heavy `executor check` on every repaint.

The event tray shows action initiation and resulting events as separate rows; filter by severity, source, symbol, action, plan, job and time. Include autoscroll/freeze, unseen count, search, JSON details and redacted export. Rows have source time and observed time; correlation drilldown shows causal chains. A dry run ends in simulated intent, a broker acknowledgment is not a fill, and rejected or ambiguous actions remain visible. See the API contract for the durable envelope and replay protocol.

## Delivery backlog and acceptance gates

Estimates are provisional engineer-weeks and overlap the earlier workbench plan; they are not additive estimates for the same work.

| Phase | Delivery | Gate | Effort |
|---|---|---|---|
| T0 Contracts and audit | Refresh capability/schema inventory, select authoritative deployment, capture sanitized fixtures, metric definitions, reference license decision and dependency pins. | Every action classified read/write/order-capable; current native event mapping and missing instrumentation documented. | 1–2 |
| T1 Foundation | Web shell, registered-root API, auth, persisted layouts, client types, read-only source adapters and health. | Restart restores layout; actual executor source shown; missing/corrupt sources fail truthfully. | 2–3 |
| T2 Trading monitor | Watchlists, candles, account/positions/orders/plans, incidents, durable event ingestion/replay and correlated timeline. | Recorded partial fills and live read-only observations render correctly; no broker mutation; disconnect/rotation gaps visible. | 2–3 |
| T3 AMAT actions | Supervisor, bounded research/backtest/plan previews, artifact inspection, metrics parity and job cancellation where supported. | Browser closure does not kill jobs; duplicate action cannot duplicate a run; canonical CLI parity verified. | 2–4 |
| T4 Executor controls | Preview/action adapter, native initiation receipts, stop/recovery support, report/reconcile and guarded submit. | Unknown POST outcome never auto-resubmits; stale plans, races, locks and paper boundary pass fault tests. | 2–4 |
| T5 Full workbench | Complete prior plan's advanced research/data/pattern/review/configuration coverage, accessibility, performance, backup/restore; optional Electron. | Coverage denominators regenerated; all supported journeys pass; desktop uses same service boundary. | Reestimate against remaining original backlog |

First useful release is T2: a genuine trading operations monitor with real observations. T3 adds practical engine actions; T4 adds executor control only after backend gates pass. Complete advanced coverage remains the original 15–26 engineer-week planning envelope subject to refresh, rather than pretending a terminal shell finishes it.

Create implementation tickets for: metric registry; source snapshot/watermark contract; native executor event mapping; remote deployment identity; health panel; trading layout; order table; event console; source rotation archive; job adapter; preview/idempotency store; stop/recovery adapter; browser fault suite; Electron packaging. Each ticket names source owner, dependencies, schemas and acceptance evidence. Unsupported controls remain explicitly unsupported until completed.

## Verification and definition of done

- Contract tests: decimal precision, timestamp/timezone/DST, source identity, exact CLI argument allowlists, schema drift, secret redaction and availability states.
- Event replay tests: process crash before/after acceptance, duplicate event, partial NDJSON, two-generation rotation, missing historical action IDs, retention gap, snapshot/stream race and slow clients.
- Executor boundary tests: unchanged paper host, no AMAT imports, singleton state ownership, dry-run/disabled/interlock gates, account binding, digest/expiry, receipt recovery and no blind order retries. Use existing engine suites when their code changes.
- Browser journeys: switch deployment without state mixing, select symbol across linked panes, inspect partial order/closed market, follow action → child effect, disconnect/reconnect, start bounded research and restore after closing the browser.
- Financial parity: reference artifacts for units, return/flow treatment, short sample suppression, missing prices, fees, zero-loss trades and the two Sharpe conventions.
- Performance targets: shell usable within two seconds after API readiness, adapter-to-render p95 under two seconds excluding source sampling; 100,000-row paginated/virtual tables; bounded event buffers and time-range chart data. Benchmark on a declared host.
- Release evidence: actual screenshots, fixture provenance, test results, API version and source hashes; no fixture presented as live production data. Backup/restore covers both application state and independently owned source proof chains.

The October 8 change established planning and pinned reference code. The October 9 delivery adds the runnable terminal and API; consult IMPLEMENTATION.md for actual supported actions and remaining work rather than interpreting every roadmap item as finished.
