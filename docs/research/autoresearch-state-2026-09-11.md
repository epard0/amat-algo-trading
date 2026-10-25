# AMAT and executor: project state and research-loop verification

**Follow-up:** this is the pre-implementation audit. The subsequent
[strategy-development engine](strategy-development-engine.md) implements the
isolated experiment loop, bounded feature/config proposals, durable keep/discard
feedback and proposer failover. Deployment and admission remain separate work.

Audited 2026-09-11 against workspace HEAD `7fd145a` plus the existing uncommitted
work. This audit repaired local review/task-gate defects and validated both
projects. It did not deploy services, submit orders, run paid model inference,
approve research tasks, change strategy parameters, or create commits.

## Finding

The bounded **historical review cycle works offline** after the repairs below.
The proposed **autonomous research loop is not implemented end to end**.
Running the review timer repeatedly would eventually return cached reviews of
the same four comparisons; it would not generate or evaluate new strategies.

| Component | Verified state | Consequence |
|---|---|---|
| `amat` strategy and plan engine | Full test gate passes; real v6 plan is exercised by executor integration tests | Strategy-to-plan contract is working on the available snapshot |
| `portfolio_team` | Imports artifacts, checks evidence, runs bounded reviews, resumes budget deferrals, caches roles, exports reports and proposed tasks | A functioning review stage, with no trading authority |
| Experiment worker | No complete registered adapter; execution requests return blocked/nonzero | No automatic candidate/control runs or feedback into the catalog |
| Provider failover | `llmrouter` contracts, health tracking and transports exist; no chain implementation/config or `pmteam --provider chain` integration | A provider outage still stops a real review; transport tests do not establish failover |
| Research scheduler | `systemctl --user` reports no timers; `amat-research.timer` and `.service` not found | The example schedule is not installed in this user manager |
| Executor checkout | Config-digest allowlist, disable switch, size limits and delayed retries are present and tested | These protections exist in source |
| Running `amat-executor` | Docker reports healthy; installed code has no `RiskLimits` and no `phase_retry_scheduled`; required risk-limit environment variables absent | The active deployment predates the checkout's protections |

The container has `DRY_RUN=false` but pins `paper-api.alpaca.markets`. It therefore
can submit paper orders. Its health check runs `executor account`, which does not
prove that a research loop works or that a new release is installed. Its AMAT
bind mount points to this workspace's `amat` directory, read-only inside the
container. A host-side research writer could still change what that mount sees.
The intended external-worktree isolation remains necessary before autonomous
strategy/config editing is enabled.

## Repairs made

1. Restored the enforced review/process boundary. The unfinished
   `run_registered_task` used `subprocess` inside `portfolio_team`, inherited the
   host environment, ran only the candidate, and marked a zero process exit as
   a completed matched experiment. It also passed measured start dates as load
   dates, which would consume warm-up again. The compatibility entry point now
   returns `blocked: adapter_not_registered`; it launches no process. Existing
   task planning and approval workflows remain available.
2. Bound task approvals to the review ID, queue digest and exact proposed task.
   Dispatch previously checked only the queue digest, so an approval copied to
   another task's filename was accepted. Queue loading now also checks the
   persisted decision's review/evidence identity.
3. Made repeated approval idempotent. It reuses the existing approval and
   timestamp; it does not generate differing immutable bytes on every call.
4. Made blocked/failed execution return a nonzero CLI status. Scheduling scripts
   can now distinguish an unavailable adapter from a completed experiment.
5. Added typed queue export to completed and cached cycles, alongside the chief's
   backlog. Repeated exports are deterministic and do not repeat review attempts.
6. Corrected executor retry tests to advance the clock and respect the 240-second
   polling cap and 300-second retry interval. Added tests proving retries do not
   run early, do not starve another due phase, and clear pending wakeups after the
   submission window closes. The existing uncommitted daemon implementation was
   retained.
7. Resolved the eight AMAT type errors in the incomplete worker/CLI and formatted
   six previously failing files. Those six formatting changes alter no behavior.

## Verification

Both projects passed their complete local gates:

```text
amat:     1,533 passed, 1 existing skip
executor:   160 passed, no skips

uv run --locked pytest -q
uv run --locked mypy src
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
```

The single AMAT skip is the existing `__init__.py` dependency-layer exclusion.
The live-provider test directory is intentionally excluded from the default
suite; no current provider quota, generation success or model availability claim
is made here.

The executor tests include a real `amat plan --offline` subprocess using
`amat_v6.toml` and the available `alpaca-sip-2026-08-11` snapshot, then plan parsing,
submission to a fake broker, report generation, quantity rounding and stable
order IDs. This verifies the actual brain/infrastructure artifact boundary
without broker side effects. It does not certify current fills or market-data
availability.

A separate smoke run copied the six real catalog run artifacts and their
configuration files into a fresh temporary project/registry. Five cycles at
`max_reviews=1` produced:

| Cycle | Reviews dispatched | Outcome |
|---|---:|---|
| 1 | 1 | One complete; remaining comparisons deferred by cycle limit |
| 2 | 1 | One cached, one complete; two deferred |
| 3 | 1 | Two cached, one complete; one deferred |
| 4 | 1 | Three cached, one complete; catalog fully reviewed |
| 5 | 0 | Four cached; no new attempts |

There were exactly 16 scripted role attempts, zero remote model calls and zero
broker calls. Queue sizes were 8, 8, 8 and 9 tasks: 33 proposed tasks in total.
The v12/v6 sample-alignment check failed as intended; all four decisions remained
`request_evidence`. This test starts without cached decisions, unlike the
previous cached-only smoke run. It tests orchestration of historical evidence,
not new backtest execution or improved performance.

Machine-readable results:
[loop-validation.json](../../amat/outputs/governance/audits/2026-09-11-loop-validation.json).
Existing budget/resume tests also pass: zero-call budget deferral consumes no
attempt, daily rollover permits resume, completed specialists survive a chief
deferral, reservation writes are atomic, and real failures remain distinct from
budget pauses.

## Remaining work before claiming an autonomous loop

1. Prepare and deploy the current executor with explicitly configured digests
   and risk limits through the documented state-preserving upgrade procedure.
   Verify the installed module, startup authorization event and a dry refusal
   rehearsal. Docker health alone is insufficient evidence.
2. Complete the provider chain and wire it into the review/proposer coordinator,
   with per-attempt budget accounting and cooldown-aware recovery. Verify a
   bounded real provider call and offline failover scenarios.
3. Implement the deterministic experiment harness in an isolated checkout:
   immutable candidate/control inputs, matched measured windows, supported engine
   selection, compute limits, durable outcomes, full output artifacts and catalog
   registration. `study` currently supports only `v1`; adapters must select the
   engine appropriate to the experiment rather than treating every operation as
   the same command.
4. Implement the proposer, fixed scoring/validation policy, experiment ledger,
   keep/discard rules and bounded feedback into the next cycle. Existing historical
   aggregate comparisons cannot satisfy the proposed promotion gates.
5. Install and validate scheduling after the above integration is ready. Keep
   research scheduling separate from executor order scheduling and preserve the
   explicit strategy release/admission boundary.

These are missing capabilities and deployment work, not failures that can be
fixed by repeatedly invoking `pmteam cycle` or increasing its budget.
