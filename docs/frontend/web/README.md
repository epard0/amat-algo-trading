# AMAT terminal Web UI

React 19, TypeScript and Vite client for the local FastAPI gateway. From this directory:

```sh
npm ci
npm run dev
npm run build
```

Vite binds to loopback and proxies `/api` to `http://127.0.0.1:8765`. Set `AMAT_API_URL` to use another loopback API port. Production assets are in `dist/`; see [the terminal operating guide](../README.md) for the local gateway launcher. The Web UI and gateway run on the workstation. Remote engines use the existing pinned SSH interface.

## Working surfaces

The six desks are **Markets**, **Performance**, **Kalshi perps**, **Tasks**, **Operations**, and **Remote**.

- Markets shows dated account observations, registered historical candles, positions, orders and runs. Historical observations retain source dates; a refresh does not make them live prices.
- Performance shows saved strategy comparisons and paper-session monitoring, preserving evidence periods, costs, provenance and qualification limits.
- Kalshi perps shows native paper accounts and observed performance.
- Tasks provides a guided browser CLI: search by task name or command, select with arrows/Enter, configure typed inputs, inspect the automatic inline preview, then choose **Run action**. Before selection it says **No action selected**. Registered capabilities are supported; arbitrary shell commands are not accepted. The selected run's durable results and subsequent SSE output appear in the monitor. Uncertain dispatches reuse their idempotency key. Workspace-wide Registered evidence is removed; compact reports appear only when their association with the selected run is established.
- Operations shows local health/runtime, readable event history and per-snapshot disclosures. Snapshot comparisons summarize metadata: coverage dates, symbols, row counts, provider and feed. They do not compare every price value. Technical IDs are available through optional details.
- Remote shows cached, dated host/container/engine observations. New automatic diagnostics belong to the local API lifecycle and continue independently of browser visibility while the API runs. They stop scheduling when the API closes. Existing native watchdogs and VPS paper-session collection have separate lifecycles.

The client obtains a same-origin local session before API reads and SSE. Mutations retain CSRF, exact-input/session/revision-bound expiring previews and durable idempotency. Native safety checks and worker ownership remain authoritative. Command success does not establish an order fill. Output is rendered as escaped content.

`Alt+1` through `Alt+6` switch desks, `Alt+R` refreshes, and Escape closes details. Searchable task/run/snapshot pickers support arrows, Enter and Escape. Small finite choices may use native selectors. Layout respects reduced-motion preferences.

## Shared presentation

- `ReadableTable.tsx` is the semantic table primitive. It derives each cell's label from its heading and preserves all values as labeled records when width is constrained. Use meaningful headings and optional disclosures. Do not rely on horizontal scrolling, clipping or tiny type.
- `StructuredData.tsx` presents unknown objects as labeled fields and object arrays as bounded expandable records, retaining full fields inside. `OutputView` handles structured records and ordinary progress messages.
- `TaskPicker.tsx` exports `TaskPicker` and `PickerOption` for searchable catalogs with labels, descriptions, groups and disabled states.
- `eventPresentation.ts` shares event titles, useful status messages and dated time formatting.
- `TaskConsole.tsx` owns task configuration, automatic inline preview and Run action. `OperationsViews.tsx` owns event history and snapshot summaries.
- `styles.css` defines semantic charcoal/teal tokens and system sans-serif text. The previous computed hierarchy increases by one CSS pixel. Ordinary tables use the full 14px body scale; financial values use tabular numerals, commands use monospace.

When adding a table, verify its own `scrollWidth <= clientWidth` and document width. Include long names, missing values, expanded details and narrow containing panels. Row-count changes do not establish changes to market-data values.

## Verification

From the workspace root:

```sh
npm run build --prefix frontend/web
uv run --project frontend pytest frontend/server/tests frontend/tests -q
```

Browser scripts require installed Playwright/Chromium and the web project's installed esbuild. Set `NODE_PATH` to the available Playwright installation if necessary. The desktop `load_workspace_dependencies` tool locates bundled dependencies.

Isolated fixtures make no API, provider, broker, notification or remote calls:

```sh
node frontend/tests/readable-data.cjs
node frontend/tests/task-terminal.cjs
node frontend/tests/task-output.cjs
```

They cover responsive data at 1440/1024/768/390px and 125% zoom, the actual conditional perps account table, field disclosures, keyboard selection, disabled tasks, preview/dispatch regressions and durable/SSE output isolation. Each script records its exact assertions.

Read-only checks against the running local UI:

```sh
AMAT_TERMINAL_URL=http://127.0.0.1:5173/ node frontend/tests/ui-surfaces.cjs
AMAT_TERMINAL_URL=http://127.0.0.1:5173/ node frontend/tests/table-layout.cjs
```

`ui-surfaces.cjs` exercises Markets positions/orders/runs and operational desks at four widths. `table-layout.cjs` additionally iterates every saved Performance comparison and expands observational disclosures, checking cell labels, migration, per-wrapper overflow and document overflow. Neither dispatches actions. Conditional live tables may be absent when observations are unavailable; fixtures cover those cases explicitly and do not establish live engine health.

Actual October 23 screenshots are under [`../docs/ui-ops-2026-10-23/`](../../../frontend/docs/ui-ops-2026-10-23). Tasks screenshots show a preview and previously retained run observations; capture did not execute a new task.
