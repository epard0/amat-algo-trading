# Local terminal, workers and remote helpers

`frontend/` owns a local React/TypeScript UI and FastAPI gateway. The workstation hosts both on loopback. Remote engines retain native accounts and safety boundaries.

| Area | Purpose |
| --- | --- |
| `web/` | React/Vite six-desk workstation, charts, readable tables, accessible selectors, task preview and output |
| `server/app.py` | Local HTTP API, session/CSRF/origin checks, previews, jobs, events and registered artifacts |
| `server/catalog.py`, adapters | Discover native CLI inventory and expose only supported executable capabilities |
| `server/supervision.py` and worker code | Persist acceptance, supervise independent jobs and recover bounded output |
| `server/remote.py`, health scheduler, VPS adapters | Cached engine observations and bounded named remote operations |
| `scripts/` | Local launch/doctor, diagnostics and off-host backup tools |
| `deploy/amat-terminal.service` | Optional local user service with loopback binding |
| `deploy/paper-monitor/` | Separately installed paper-session evidence collector and archive contract |
| `desktop/` | Optional sandboxed Electron shell using the same local app |
| `contracts/`, `api/` | Inventory/adapter distinctions and original API design contracts |
| `vendor/fincept/` | Reference/provenance material; not the running terminal |

## Lifecycle

The launcher starts local API and development UI, or serves the built app in production mode. API startup owns automatic remote health scheduling when remote mode is selected. Browser visibility has no effect on collection. Cached reads do not launch SSH work. Stale observations retain their original times. Shutdown stops scheduling; independent accepted task workers and existing native services have separate lifecycles.

Tasks select typed registered inputs, preview effects and dispatch once under exact-input/session/revision/expiry and idempotency controls. A lost dispatch response reconnects to the same request identity. Output is bounded, redacted and escaped. Only evidence known to belong to the selected job is shown as a job result.

## Remote deployment helpers

The local SSH adapter loads mode-600 configuration, verifies pinned host keys and bounds connection/command/output work. Remote helper operations are named contracts; the browser cannot send shell commands or credential dictionaries. Health collection is read-only and reports transport, process responsiveness, data freshness, readiness/reconciliation, backups and resource pressure separately.

The separately installed paper-session collector has an enforced top-level `manifest.json["files"]` hash map. Updating any installed helper requires updating each changed/new file's hash before collector startup, while keeping unchanged attestations. Auxiliary release metadata does not replace that map. See [archive contract](../../frontend/deploy/paper-monitor/ARCHIVES.md).

## UI contribution expectations

Run the UI build and API/launcher regressions. Check each record/table wrapper as well as document overflow at desktop, tablet and mobile widths. Keep meaningful labels and values readable; searchable selectors must work by keyboard. Preserve focus, escaping/redaction and selected-run identity. Browser smoke actions must be inert, such as local config validation. Native Electron packaging is a separate test scope.

See [installation](../installation.md), [HTTP API](../http-api.md), [native README](../../frontend/README.md), and [remote diagnostics](../../frontend/docs/REMOTE.md).
