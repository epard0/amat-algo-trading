# Operations UI audit — October 20, 2026 (America/Detroit)

The built dashboard is served at http://127.0.0.1:8765/.

## Navigation and scope

The sidebar now contains six tabs instead of eleven:

| Tab | Retained functionality |
| --- | --- |
| Markets | Historical candles, symbol and resolution controls, positions/orders/runs, archived account and exposure metrics |
| Performance | Eighteen saved research studies, period/strategy/cost comparisons, chart controls and paper-session observations |
| Kalshi perps | Remote paper strategy/account performance, with a link to engine management |
| Tasks | One typed launcher, reviewed dispatch, all task monitoring, cancellation, structured output and registered evidence |
| Operations | Local health/runtime/events, expandable historical data sources, workspace preferences |
| Remote | Live VPS observations, eight diagnostic panels, both paper engines' lifecycle controls |

Saved Portfolio/Execution/Research/News/Data/Settings selections migrate to the appropriate retained tab. Alt+1–6 selects the six tabs; unsupported Alt+7 no longer produces an undefined screen.

Removed the duplicate Simple view, native CLI discovery catalog, empty News screen with disabled ingestion, duplicated position/metric views, browser-driven workflow shortcuts, and local-only submission controls without an enabled resume path. Native automated research remains available through Tasks. Engine start/stop/restart/status controls are centralized in Remote. Removed the disconnected alternative view/chart components, unused metadata fetch and obsolete view styles.

## Functional fixes

- Header refresh and Alt+R refresh the active candles/performance/paper-session/perps/remote/task observations, in addition to shared dashboard data.
- Automated research gets a fresh default workspace name; a repeated launch no longer fails merely because `terminal-research` already exists. Operators can still enter a custom name.
- Task preview opens an accessible review dialog. Validation failures persist beside the form; dispatch failures remain visible in the dialog.
- Cancellation is displayed only for native research jobs the cancellation endpoint accepts.
- The brand link returns to Markets, and Reset layout resets symbol, selected data source, resolution, bar count and filter.
- Missing minute history displays its actual actionable reason. Pending/error responses cannot leave another symbol's candles under the new symbol heading.
- Structured values and logs remain formatted; no raw JSON output blocks are shown.

## Executed checks

Production TypeScript/Vite build passed. `uv run --project frontend pytest frontend/server/tests frontend/tests -q` passed **93 tests**. The only warning was the existing Starlette/httpx deprecation.

The audit dispatched real jobs through the typed browser launcher and checked their persisted results. Successful capability executions cover all eleven exposed research/analysis actions:

`amat.config.validate`, `amat.config.show`, `amat.config.universe`, `amat.status`, `amat.active`, `amat.backtest.synthetic`, `amat.scan`, `amat.backtest`, `amat.walkforward`, `amat.montecarlo`, `amat.research.launch`.

The synthetic test used 300 sessions. Monte Carlo used 100 paths over 2019–2024 and completed successfully. A fresh one-iteration automated research run completed successfully: `66225e21-4892-40ab-bde6-81a13484647b`. Both Cancel job and Cancel run stopped real research workers; two cancellation records are included in the evidence file. An API restart encountered during the Monte Carlo run did not terminate the independent worker.

Each Remote lifecycle button was exercised through review and dispatch:

| Action | Persisted job | Result |
| --- | --- | --- |
| Kalshi stop | `47f71ebb-701e-4345-b9ac-d4da343ba54a` | Succeeded; container exited |
| Kalshi start | `896d2f76-73dc-4e1b-863a-ecc215bb63af` | Succeeded; container started |
| Kalshi restart | `7b0f83e0-d581-448e-b423-e153906e95e8` | Succeeded |
| Kalshi status | `4c6ca933-e0f5-446d-bd3c-2a1470f09bdd` | Succeeded; paper mode, venue writes disabled |
| Alpaca stop | `7f0b60df-24de-4b3a-aeb4-b54c930375c0` | Succeeded; container exited |
| Alpaca start | `1b19b2ae-176f-4770-ae38-87366a557f62` | Succeeded; container started |
| Alpaca restart | `8d4fa741-20b6-4b60-9a29-acc981c3cf89` | Succeeded |
| Alpaca status | `8bebb3a7-f908-443b-a458-e5f837ab898a` | Succeeded; ready for scheduled paper orders |

Final observation: SSH transport connected; both containers running and healthy; Alpaca order readiness true. Existing images, durable paper account state and restart policies were retained. This proves controlled container lifecycle/recovery, not recovery from arbitrary host/provider failures.

The UI audit also exercised:

- 60/120/250-bar windows, all three resolutions, symbol changes, volume toggle, invalid-symbol feedback, positions/orders/runs and observation drawers.
- All 18 performance studies (HTTP 200, available data), rendered strategy/month controls, 16 period options, three retained validation strategies, benchmark toggle and all three cost settings.
- Task validation errors, review visibility at desktop/mobile widths, edit/close/Escape behavior, rejected-CSRF recovery, run selection/details, refresh, activity search/error filter and evidence pagination.
- Six structured evidence previews and a real PDF link click/download: HTTP 200, application/pdf, `%PDF-` signature, no download failure.
- Thirteen data-source selections, snapshot details, event-console preference, layout reset, operator guide, brand navigation, six keyboard shortcuts and saved-tab migration.
- All eight VPS diagnostic selectors, automatic checks toggle, Check now, activity pagination and activity record disclosure.
- All six tabs at 390px width with document width exactly 390px; no horizontal overflow. The final additional-control pass reported no uncaught browser errors.

## Repeatable checks and evidence

- `tests/browser-smoke.cjs`: read-only UI audit using an installed Playwright runtime; calls `tests/audit-ui.browser.js`.
- `tests/task-controls.browser.js`: focused task-launch/session/validation regression; executes a real configuration-validation job.
- `tests/audit-actions.browser.js`: explicitly invoked integration run for all eleven analysis actions, with bounded synthetic/Monte Carlo inputs.
- `tests/audit-performance.browser.js`: saved study and comparison controls.
- `tests/audit-remote.browser.js`: explicitly invoked paper lifecycle audit; briefly stops/restarts both VPS containers. Use only during an authorized operations audit.
- `docs/operations-audit-2026-10-20.json`: compact persisted job IDs/statuses and final remote observations, without credentials.
- `docs/audit-tasks.png`, `docs/audit-operations.png`, `docs/audit-remote.png`: verified desktop views.

The `.browser.js` files are async functions accepting a Playwright page, suitable for the browser tool's filename runner. Do not run the task or remote integration scripts as an unattended read-only smoke check.
