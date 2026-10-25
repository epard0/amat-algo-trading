# Terminal integration verification

## October 23, 2026 UI and remote operations release

The current production UI is served locally at `http://127.0.0.1:8765/` by `amat-terminal.service`. The Web UI/API are absent from the VPS. The [implementation report](../../operations/ui-remote-2026-10-23/REPORT.md) records source/release identities, remote continuity, restore checks, measurements, screenshots, and limitations. The October 9 receipt below remains historical.

| Final gate | Result |
|---|---|
| Frontend API, workers, launcher and integration tests | 147 passed |
| Complete Kalshi package suite, invoked from `kalshi-perps/` | 91 passed |
| Complete executor suite | 396 passed |
| TypeScript and Vite production build | Passed; 1,592 modules |
| Changed Python Ruff checks and backup shell syntax | Passed |
| `readable-data.cjs` | Passed: four widths, 125% zoom, actual perps component with fixtures, labels/disclosures, keyboard search including parent rerenders and disabled controls |
| `task-terminal.cjs` | Passed: preview races/validation, no dispatch until Run, uncertain-response idempotent retry, locked inputs, snapshot metadata differences |
| `task-output.cjs` | Passed: durable output prefix/cursor, live SSE output, selected-run isolation |
| `table-layout.cjs` | Passed: all 18 Performance selections at 1440/1024/768/390px, 8–35 rendered tables per selection, expanded details, wrapper and document overflow |
| `ui-surfaces.cjs` | Passed against production: Markets views, perps, Operations, Tasks and Remote at four widths; populated fixtures cover otherwise empty conditional account tables |
| `browser-smoke.cjs` | Passed against production: six desks, historical bars/timeframes/snapshots, mobile document width, no page errors |
| `task-controls.browser.js` | Passed against production: automatic inline preview, CSRF renewal, one inert configuration-validation task, successful status/output, responsive layouts |

The actual inert browser job was `d856d432-b10c-4be9-b1e1-fde61a194e5e`. It validated configuration; it did not submit an order or send a notification. Mocked actions cover preview races and uncertain-response recovery. Browser fixtures are labeled separately from real engine observations.

New remote regressions exercise API-lifetime scheduling, single-flight/backoff/shutdown, cached reads, missing/stale/malformed observations, read-only command boundaries, archive interruption/checksum/path safety, bounded transfer and committed-WAL SQLite restoration. Runtime parity regressions cover economic event ordering, late funding, partial take profits, restart behavior, exact seeded history, default-only alert delivery and preserved retired-account cursors.

The integrated frontend suite exposed a worker timing assertion with approximately a one-second polling allowance under host load. Its test now uses a bounded 20-second monotonic deadline; the complete 147-test rerun passed. Invoking Kalshi tests from the workspace root exposed an existing relative `configs/demo.toml` assumption; the package-directory invocation passed all 91 tests.

Final remote checks found the three intended application containers healthy, no OOM/restarts, advancing ordinary perps observations, preserved account/configuration/volume identity, verified collector source attestation and successful minute collection. Fresh backup restores passed SQLite integrity checks for three active-state databases and three retired-shadow databases. The separate collector retains an October 22 `Session plan is missing` audit alert; successful current health collection does not resolve that historical finding.

## Historical October 9 verification

Verified October 9, 2026 on Linux, Python 3.13.12, Node 22.23.3, npm 12.2.0.

- `frontend/.venv/bin/python frontend/scripts/doctor.py` passes with installed frontend dependencies.
- `python3 -m unittest discover -s frontend/tests -p test_launcher.py`: 2 tests pass.
- `npm run build` in `frontend/web`: TypeScript and Vite production build pass.
- Optional desktop package: `node --check frontend/desktop/main.cjs` passes; package lock audit reports zero vulnerabilities. Native Electron launch and platform packaging were not tested.
- Live browser: fresh cookies establish session, API connects, SSE connects, no uncaught JavaScript exceptions, all seven desks navigate, and 1600px/1024px layouts avoid horizontal overflow.
- Historical source observations render with their dates and explicit historical status. Eleven watchlist symbols and 250 SPY daily OHLC/volume bars render from registered snapshots. No generated prices or broker mutation were used.
- Event filter `plan_built` selects one native source row; selecting the row opens the inspector.
- Actual browser action: selected `amat.config.validate` with `amat_v6.toml`, reviewed preview and dispatched. Job `8b74bdd0-4f1d-46ee-a846-41c0a07094c6` succeeded. Distinct durable events `action.requested`, `action.started`, two `action.output` entries, and `action.succeeded` arrived in the live console. This command validates a local configuration and does not place orders.
- Production launcher `--production --api-port 8766` served the built terminal, authenticated API session and historical candles correctly. The temporary production test process was stopped. Development API/WebUI were retained for review.

The screenshot at `../docs/terminal-desktop.png` captures actual local archived state plus the observed validation action. It is not a live broker account screenshot. Source SPY OHLC contains an extreme wick; the final interface visibly flags anomalous source bars and permits quality detail inspection without silently correcting prices. Final production static browser verification on port 8765 reports zero console errors and zero warnings, with the source-quality notice and historical candles visible.

`browser-smoke.cjs` is a reusable read-only Playwright smoke script for a running terminal. Supply an installed `playwright` module and `AMAT_TERMINAL_URL` to run it outside the integrated browser tool. It does not submit actions.

Additional actual offline API/independent-worker integration: `amat.backtest.synthetic` using v1 (300 sessions, seed 7), and `amat.backtest` v1 for 2019–2020 all succeeded. The historical job produced a PDF and native run evidence under temporary application storage. No provider/broker network actions were required. The v6 configuration is not compatible with the generic synthetic runner; its initial native refusal was preserved, and the synthetic default was corrected to v1.

Final application test suite: 21 tests passed, covering API/CSRF/idempotency, worker recovery, source/event/metric contracts, market quality checks, typed engine actions, output streaming redaction and launcher checks. Dedicated Python lint and final production web build passed. The test runner reports a Starlette/httpx deprecation warning; the final browser reports zero errors and zero warnings.
