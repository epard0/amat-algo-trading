# Local HTTP API and task integration

The FastAPI application in [server/app.py](../frontend/server/app.py) is the workstation's loopback gateway. Its implemented routes and generated `/openapi.json` are authoritative. [The original API contract](frontend/api/CONTRACT.md) includes roadmap requirements; do not treat every target endpoint or normalized event name there as implemented.

Start the [local server](installation.md) and inspect [OpenAPI](http://127.0.0.1:8765/openapi.json). Do not expose this API as a public unauthenticated service or bypass local Host/origin/session checks with a reverse proxy.

## Implemented interface groups

| Routes under `/api/v1` | Purpose |
| --- | --- |
| `GET /session` | Establish local session cookie and obtain CSRF token |
| `GET /health/live`, `/health/ready`, `/workspace` | Gateway availability, stores/adapters and workspace metadata |
| `GET /capabilities`, `/workflows` | Registered supported capability schemas and workflows |
| `POST /actions/preview`, `/actions` | Exact-input preview, then durable idempotent dispatch |
| `GET /jobs`, `/runs`, `/jobs/{job_id}`, `/actions/{job_id}` | Persisted lifecycle and bounded output |
| `POST /jobs/{job_id}/cancel` | Supported cooperative cancellation; no implied broker cancellation |
| `GET /events/history`, `/events/stream` | Durable history and SSE reconnect |
| `GET /remote`, `/remote/health`, `/remote/engines` | Cached registered diagnostics and engine observations |
| `POST /remote/run`, `/remote/health/refresh` | Request bounded registered diagnostics / wake shared collector |
| `GET /market/snapshots`, `/market/symbols`, `/market/bars`, `/market/quotes`, `/market/depth` | Recorded/provider market observations with coverage/quality |
| `GET /dashboard`, `/metrics`, `/analysis/performance`, `/analysis/screen`, `/analysis/account-history` | Derived views and research/account metrics |
| `GET /perps`, `/paper-session`, `/research-campaign`, `/news` | Native paper/research/news views |
| `GET /artifacts`, `/artifacts/{artifact_id}`, `/artifacts/{artifact_id}/download` | Bounded registered evidence access |

`GET /deployments/{deployment}/{section}` adapts supported native deployment sections. Consult the running schema and capability catalog for actual schemas/availability, rather than guessing a generic broker endpoint.

## Safe integration sequence

1. Fetch `/session` with the same client cookie jar used for later requests. Retain `terminal_session` and the returned `csrf_token`.
2. Fetch `/capabilities`; choose a supported registered capability and its declared inputs/effects. Discoverable CLI inventory can be unsupported.
3. Send `{capability, inputs}` to `/actions/preview`, with the local origin and `X-CSRF-Token` required by the application. The preview validates without executing.
4. Inspect effects and use the returned preview ID before its 120-second expiry. Dispatch the exact same inputs with `preview_id` and a new `idempotency_key` to `/actions`.
5. Retain returned action/job identity. If a response is lost, reuse the same key and exact input; do not invent another request to “retry” a possibly accepted action.
6. Read job state and SSE output; reconnect to that job after reload. Report unknown outcomes as unknown until native reconciliation resolves them.

A preview is bound to session, exact action/input, revision and expiry. A stale preview or key reused for different input is refused. The gateway persists acceptance before independent worker dispatch. Worker success is command completion; order acknowledgments/fills remain separate native facts.

No API accepts arbitrary browser-provided shell commands, executables, environment dictionaries, SSH hosts or unregistered paths. Extra diagnostic command names can be registered only in trusted private operator configuration. Keep output bounded, escaped and redacted before persistence/display.

## Events and observations

SSE reconnect uses durable event identity/cursors. A client must handle resynchronization and stale observations, not assume that each delivery is unique or each old record remains current. Record source measurement time separately from UI refresh time. A cached GET never starts another remote health probe. Monitoring scheduling follows API startup/shutdown, while accepted task workers and native watchdogs have independent lifecycles.

For concrete schemas and guard behavior, see `frontend/server/tests` and [testing](testing.md). Use isolated TestClient fixtures for mutations; installation smoke checks must not dispatch engine, notification or broker actions.
