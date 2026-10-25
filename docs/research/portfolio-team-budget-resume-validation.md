# Budget-aware research review continuation

Date: 2026-09-10. Scope: AMAT portfolio-team coordinator and review automation.
The live Astra analysis is still pending; no limits were raised and no clock or
historical accounting entries were changed to make a request fit.

## Real registry check

Command exercised:

```bash
uv run --locked pmteam review v11-versus-broad-control \
  --provider openai --model gpt-6-astra --single-reviewer
```

The command exited nonzero with `deferred_budget:daily_output`, not an investment
verdict or a provider failure. The resulting review ID is
`ee5ba68add8350309b1f5eb0bd5be203aa925dc288d650252a1158f7f5cfc9f5`.

| Measurement | Observed |
|---|---:|
| Shared daily output allowance | 100,000 |
| Already charged/reserved | 99,014 |
| Available | 986 |
| Required Astra output reservation | 8,192 |
| Attempts consumed by this review | 0 |
| New model calls | 0 |
| Historical model calls retained | 37 |

Status is `deferred_budget`; no decision was created. Preflight reports both
attempts available and `2026-09-11T00:00:00+00:00` as the next applicable daily
reset. That is an application-budget boundary, not assurance of provider quota.
Existing unknown-usage reservations remain charged conservatively; these counts
are not claimed to be actual billed tokens.

## Implemented behavior

- Read-only preflight of the next uncached role's application budget and attempts.
- Atomic attempt/request/reservation writes, rolled back together on failure.
- No attempts or reservations consumed by a pre-dispatch budget deferral.
- Separate persistent deferral state, eligible for reconsideration by later cycles.
- Cached specialist assessments retained when the chief has to pause.
- Zero-call deferrals do not exhaust the cycle's dispatch allowance.
- Real provider/validation failures still require operator attention; a concurrent
  failure cannot be hidden by another reviewer's budget deferral.
- Evaluation reports distinguish unscored budget deferrals from failures.

No prompt/model identity was changed. Existing saved reviews still replay without
additional provider calls. Earlier failed reviews and their attempt history were
not reset or retroactively relabeled.

## Verification scope

Isolated tests exercise UTC rollover, repeated deferrals, automatic cycle resume,
cached-chief handoff, rollback after an injected SQLite insert failure, impossible
per-review/request limits, mixed failure/deferral precedence and evaluation labels.
The injected clock and SQLite trigger exist only in temporary test registries.

The scheduler remains disabled, and no executor/broker settings were modified.
See [the workflow guide](research-team-workflow.md) for preflight and review commands.

Final checks: **1,521 passed, one existing skip** in the full AMAT suite;
**56 portfolio-team tests passed**; strict typing passed for **113 source files**.
Scoped lint, formatting and diff whitespace checks passed. The saved Gemini
review replayed unchanged after the coordinator/storage changes.
