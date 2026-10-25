# Terminal API and event contract

Design contract with an initial implementation delivered October 9. The running API /openapi.json and /api/v1/capabilities describe actual implemented endpoints and adapters; some broader requirements below remain roadmap items. Prefix `/api/v1`. FastAPI owns validation and authentication; the browser never receives provider credentials. OpenAPI and Pydantic schemas generate TypeScript clients during foundation delivery. All IDs are namespaced by deployment or registered workspace root.

## Read endpoints

| Method/path | Response and ownership |
|---|---|
| GET `/workspace` | Registered roots, deployment, engine versions, source digests, supported capabilities. |
| GET `/health/live` | API process availability only. |
| GET `/health/ready` | API stores/adapters ready; separate from permission to submit. |
| GET `/deployments/{id}/health` | Native executor health/readiness, heartbeat age, interlocks, explicit reasons. |
| GET `/deployments/{id}/account` | Cash, equity, buying power, restrictions, positions; source and observation times. |
| GET `/deployments/{id}/orders` | Broker status, registry status, requested/filled/remaining quantities, intent and plan references. |
| GET `/deployments/{id}/operations` | Reconciliation, incidents, retry/receipt state, qualifying sessions, notification configuration. |
| GET `/deployments/{id}/plans/{plan_id}` | Immutable archived plan, validity, digest, intended allocation and blockers. |
| GET `/market/quotes`, `/market/bars`, `/market/depth` | Registered provider data, entitlement, delay, session and timestamps; unavailable depth stays unavailable. |
| GET `/metrics` | Versioned metric definitions and values with period, units, source, sample count and status. |
| GET `/jobs`, `/jobs/{id}`, `/runs`, `/artifacts/{id}` | Durable jobs and bounded registered evidence access. |
| GET `/events/history` | Filtered paginated durable event history; opaque cursor. |
| GET `/events/stream` | SSE; Last-Event-ID, bounded queues, heartbeat and explicit resync after retention gaps. |

Pagination has stable cursor/sort semantics. Chart queries require range and resolution and enforce row/point limits. Money and order quantities use decimal strings. Unknown/missing measurements use null plus reason, never zero. Error envelopes contain code, message, field errors, retryability and correlation ID, without command environments or credentials.

## Command endpoints

`POST /actions/preview` validates a capability and returns an expiring preview ID bound to deployment, exact input hash, configuration/plan digest, resource revision and operator. It produces no broker order. `POST /actions` persists an action and its idempotency key, returns `202 {action_id, job_id, status_url}`, and dispatches through an allowlisted adapter. `GET /actions/{id}` reports durable status and observed effects. Repeating a key with different inputs returns 409; the same input returns the original action. Stale previews/revisions return 409. Mutations require local session authentication, origin/Host checks and CSRF protection.

| Capability | Adapter / initial availability |
|---|---|
| `executor.status`, `executor.health`, `executor.check` | Existing executor CLI, bounded timeout and captured redacted output; check never submits. |
| `executor.report` | Existing report command; surface file writes and possible notification delivery as effects. |
| `executor.reconcile_submissions` | Existing reconciliation command; observes original broker identities, never blindly retries orders. |
| `executor.submit` | Existing submit boundary only; revalidate all safeguards immediately before dispatch; paper only. |
| `executor.stop_submissions` | New executor-owned control adapter required for durable STOP_SUBMISSIONS; initial capability unsupported. |
| `executor.resume_submissions` | New executor-owned adapter and recovery semantics required; cannot clear unresolved unknown orders. |
| `amat.backtest`, `amat.research.launch` | Allowlisted AMAT command schemas, correct project runtime and durable supervisor. |
| `amat.job.cancel` | Advertise only for jobs with a verified cooperative cancellation adapter. |

Phase 0 captures exact current CLI arguments and effective policy from the parser. No action accepts shell strings, arbitrary executables, unregistered paths or environment dictionaries. Report runs use existing singleton locks. No generic broker cancel/replace/flatten endpoint is offered until executor-owned support exists. A UI cancel request never implies that a broker order was canceled.

Action states: requested → validating → queued → started → succeeded / failed / refused / outcome_unknown. Cancellation uses cancel_requested and engine acknowledgment. The API persists acceptance before worker dispatch. A supervisor restart reconciles effects before deciding whether any retry is safe. Broker POST ambiguity becomes outcome_unknown and requires existing receipt/reconciliation handling.

## Correlated event envelope

```json
{
  "schema_version": "terminal-event/1",
  "event_id": "executor:paper-vps:receipt-123:started",
  "cursor": "opaque-persisted-cursor",
  "source": "executor",
  "deployment_id": "paper-vps",
  "action_id": "action-123",
  "correlation_id": "action-123",
  "causation_id": "request-event-123",
  "source_event_id": "receipt-123",
  "source_sequence": null,
  "occurred_at": "2026-10-08T14:00:00Z",
  "observed_at": "2026-10-08T14:00:01Z",
  "kind": "action.started",
  "severity": "info",
  "origin": "native",
  "payload": {"capability": "executor.submit", "session": "2026-10-08", "plan_id": "plan-123"}
}
```

A command yields distinct facts: `action.requested`, `action.started`, then zero or more `plan.built`, `intent.created`, `order.submission_started`, `order.acknowledged`, `order.partially_filled`, `order.filled`, `portfolio.reconciled`, ending in an observed terminal action state. Refused, failed, dry-run and unknown outcomes are first-class entries. Successful command exit does not establish a fill. An action that triggers research or an alert creates a child event with causation_id pointing to the actual triggering event. Asynchronous fills may arrive after the command completes.

Keep native journal event names/payload references. The normalized names above are a target mapping, not a claim that all current producers emit them. Add executor-native durable initiation evidence where absent; wrap CLI process start as `origin=adapter` rather than claiming engine initiation. Missing historical action IDs remain null or explicitly inferred. Never invent causal links from nearby timestamps.

Tail both journal generations with file identity/offset checkpoints, incomplete-line buffering, corruption counts and deduplication. Preserve application audit events separately from rotating source files. Snapshot and stream responses share a watermark so reconnect can replay without missing the snapshot boundary. Delivery is at least once; event IDs make reducers idempotent. If downtime exceeds source retention, expose a historical gap and resnapshot; do not claim complete recovery. Slow clients cannot block ingestion. Redact before persistence and display; source artifacts remain read-only.

## October 23 local monitoring and live output update

The local API lifecycle owns automatic remote diagnostics when `TERMINAL_PERPS_TARGET=remote`. No Web UI or new diagnostic timer is deployed on the VPS.

- `GET /api/v1/remote/health` is a cached read returning `{scheduler, observation, issues}`. Scheduler metadata includes running/lifecycle/cadence/backoff/last-attempt information. `observation` is the last bounded `remote-health/1` result with transport/stale/age metadata, per-engine observations and infrastructure resources, backup ages and unexpected application inventory.
- `POST /api/v1/remote/health/refresh` requests work from the existing single collector; it does not synchronously run arbitrary commands. Session and CSRF checks apply. A paused collector reports `accepted:false` with a reason. Cached GET requests never create another SSH probe.
- Engine observations retain original measurement times and explicit stale status if an engine is intentionally stopped or temporarily unavailable. Host/process health, trading-data health and readiness remain separate.
- `GET /api/v1/jobs/{id}` for queued/started jobs includes bounded `live_output`, `live_output_cursor` and `live_output_truncated`. The cursor identifies the last included output event. A client may append correlated `action.output` SSE chunks with a larger cursor, without duplicating the durable prefix. Final jobs retain their worker-produced `output` and `exit_code`.
- Preview requests remain inert and bind exact inputs/session/revision for 120 seconds. Expired transient previews/sessions are cleaned up; at most 2,048 live previews are retained and excess requests return 429 rather than evicting valid previews. Accepted jobs and idempotency receipts are durable and are not pruned by transient cleanup.

The guided browser CLI is a presentation layer over these existing registered actions; it adds no arbitrary shell or PTY execution endpoint. An uncertain HTTP dispatch retries using the same idempotency key. An explicit new-run action creates a new key only after the prior accepted response is known.
