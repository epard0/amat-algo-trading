# AMAT Terminal

For current cross-system installation, API contracts and contribution guidance, start with the [workspace documentation](../README.md#table-of-contents). Dated reports and deployment observations below require their original local evidence and do not describe a fresh clone.

A local React workstation and FastAPI gateway for research, recorded evidence and remote paper-engine operations. The browser UI/API stays on the workstation. The VPS runs `amat-executor`, `kalshi-perps` and the main-account `kalshi-perps-trade-alerts` watcher; the optional shadow account is retired with its state retained.

## Run locally

From the workspace root:

```sh
uv sync --project frontend --locked
npm ci --prefix frontend/web
frontend/.venv/bin/python frontend/scripts/doctor.py
frontend/.venv/bin/python frontend/scripts/start.py
```

Open http://127.0.0.1:5173. Development uses the API at `127.0.0.1:8765`. For the built application:

```sh
npm run build --prefix frontend/web
TERMINAL_PERPS_TARGET=remote frontend/.venv/bin/python frontend/scripts/start.py --production
```

Open http://127.0.0.1:8765. The installed local user service in `deploy/amat-terminal.service` also selects remote engines. Starting/stopping the gateway does not start/stop the trading daemons. Accepted independent jobs persist through browser/API restarts.

`TERMINAL_STORAGE` selects local SQLite/events/jobs storage (default `frontend/.runtime`). `TERMINAL_EXECUTOR_STATE` selects local executor evidence; it is separate from the authoritative remote observations. Configure the existing private SSH interface as described in [remote diagnostics](docs/REMOTE.md). Credentials are not returned to the browser.

## Working surfaces

- **Markets:** dated account/holdings observations, historical candle data and quality flags, positions, orders and runs. A refresh does not turn archived data into live prices.
- **Performance:** saved research comparisons, period/cost selectors and retained supporting results. Table records wrap or become labeled records at narrower widths; technical detail stays expandable.
- **Kalshi perps:** native paper-account observations, recorded performance and engine task shortcuts. Paper experiments do not establish a profitable or live-qualified strategy.
- **Tasks:** a guided browser CLI for executable registered capabilities. Search by task name or command, select using arrows/Enter, configure inputs, review the automatically populated description/target/effects, then use **Run action**. Before selection the UI says **No action selected**. Long configuration lists are searchable. Boolean settings use checkboxes.
- **Operations:** local workspace health/runtime, readable event history and per-snapshot expandable summaries. Snapshot comparisons cover dates, symbols, row counts and provider/feed metadata; they do not compare individual market-data values.
- **Remote:** cached, timestamped host/container resources, native process/data health, readiness, backup freshness, issues and optional diagnostic output. Cached display refreshes do not initiate another SSH collection.

The task interface keeps the backend's exact-input/session/revision-bound, expiring preview and durable idempotency controls. It shows validation directly under the selection, without a separate review modal. If a dispatch response is lost, settings remain locked while **Run action** reconnects using the same request identity. **Prepare another run** explicitly starts a new request after acceptance.

The run monitor shows queued/running/completed/failed/cancelled/unknown states and escaped/redacted output while work is active. Durable output can be recovered after reload, and selected-run state does not reuse another run's output. Completed output is bounded by the worker capture policy. Reports/downloads appear compactly only when association with the selected run is known; workspace-wide registered evidence is not listed on Tasks.

Alt+1 through Alt+6 switch desks; Alt+R refreshes observations; Escape closes details or a picker. Fields and summaries retain keyboard focus indicators. Table text uses sans-serif/tabular numerals; commands retain monospace. Main body text is 14px, with the previous hierarchy raised by one pixel and ordinary tables standardized at 14px.

## Automatic checks and small-server behavior

With `TERMINAL_PERPS_TARGET=remote`, the local API owns a bounded background scheduler: lightweight remote checks about every 60 seconds plus jitter, and extended diagnostics about every five minutes when host headroom allows. Checks continue when the browser is closed and stop scheduling when the API shuts down. No new VPS health-check timer is installed. Existing native watchdogs, trade alerts, backups and the independent paper-session collector keep their separate lifecycles.

Checks use the existing pinned key-only SSH transport and named helper operations. Process responsiveness, fresh trading data, reconciliation/readiness, transport health and resource pressure remain distinct. An alarm does not start/restart a daemon or submit an order. Failed observations keep dated last-success measurements with stale labels and bounded backoff.

The runtime removes duplicate history working copies and replays ledger events through bounded SQLite ordering without trimming fixed-origin strategy history or deleting account events. Exact strategy input and some returned accounting detail still grow with history; this is a measured reduction, not a constant-memory guarantee.

Completed inactive collector detail older than seven days is compressed with checksums/manifests and compatible readers. Final summaries and compressed detail remain; no automatic archive-expiry policy is added. Active sessions, account databases, receipts, deduplication, interlocks, equity history and checkpoints are excluded. Maintenance defers under insufficient host resources. See [archive contract](deploy/paper-monitor/ARCHIVES.md) and [backup/restore contract](../executor/scripts/BACKUPS.md).

## Verify

```sh
frontend/.venv/bin/python -m pytest frontend/server/tests frontend/tests -q
npm run build --prefix frontend/web
git diff --check
```

With the installed Playwright module available on `NODE_PATH`:

```sh
node frontend/tests/readable-data.cjs
node frontend/tests/task-terminal.cjs
node frontend/tests/task-output.cjs
node frontend/tests/table-layout.cjs
node frontend/tests/ui-surfaces.cjs
```

The first three use isolated in-memory fixtures and do not dispatch real engine/provider/notification actions. The table/surface checks inspect the running local UI; set `AMAT_TERMINAL_URL` to its local URL. `browser-smoke.cjs` is read-only. `task-controls.browser.js` explicitly runs an inert local configuration validation; broad action/lifecycle audits are separate manual scripts and are not ordinary smoke tests.

Run native executor/Kalshi suites from their package directories when changing their code. Preserve state and current deployed source/configuration identity during selective releases. See the root [repository instructions](../AGENTS.md), [approved plan](../docs/ui-remote-operations-plan-2026-10-23.md) and dated implementation report for release-specific evidence.

## Architecture

`web/` contains React/TypeScript and shared readable-table, picker, event and task components. `server/` contains authenticated local adapters, capability/preview/job contracts, SSE, independent worker supervision and cached remote monitoring. `deploy/` contains the local service template and separately installed remote paper-session collector. `scripts/` contains launch, diagnostics and off-host backup tools; `contracts/` distinguishes discovered native CLI inventory from supported executable adapters. Unsupported commands do not become executable merely because they are discoverable.

The optional Electron shell uses the same local application, with a sandboxed isolated renderer. Native Electron packaging remains a separate verification scope. The Qt reference under `vendor/fincept` is retained reference material, not bundled application code.
