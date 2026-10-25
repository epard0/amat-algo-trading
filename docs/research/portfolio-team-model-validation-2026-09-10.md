# Portfolio-team model integration: validation record

Date: 2026-09-10. Scope: AMAT research review tooling only. No executor
configuration, orders, positions, strategy allocations, or deployment was changed.
This is not a new backtest or a certification of the running trading container.

## Delivered

The `pmteam` CLI now supports an explicit Gemini Interactions provider, exact
request/response capture, local schema/reference validation, evidence identity
constraints, persistent token reservations, deadlines, bounded attempts, quota
error handling, and offline replay. Three independent first-pass reviewers feed
a chief PM; request dispatch defaults to one at a time for provider quota safety.
The model has no broker, shell, web tools, or execution authority.

The ML iteration workflow drove the baseline comparison and separation of
engineering acceptance from claims about model quality or trading performance.
See [operations](portfolio-team-operations.md) for commands and limits.

## Measured live results

The identity-constrained single-reviewer run completed both development cases
using `gemini-3.6-flash` under protocol `model-committee/6`:

| Development case | Expected references covered | Calls | Reported input/output tokens | Call time |
|---|---:|---:|---:|---:|
| v11 / matched v10 evidence gaps | 6/6 | 1 | 34,272 / 1,217 | 28.32 s |
| v12 / unmatched v6 control | 3/3 | 1 | 9,697 / 861 | 4.78 s |

The deterministic checks cover the same 6/6 and 3/3 references without model
calls. These are two known development examples. Reference coverage does not
measure semantic accuracy, novel fault discovery, false-positive rate, investment
quality, or profitability. Expected labels were withheld from model requests, but
the underlying deterministic checks were supplied to reviewers intentionally.

Sources: [evaluation snapshot](../../amat/outputs/governance/evaluations/d64cf435445510f0cc4a6e85d59d32a2bfdd8b0aa7facf7523cd19be7794ac8c/report.md),
[matched single review](../../amat/outputs/governance/19e27cd3204fac6273fe58e7fa43e376570f7e3ef8bc7e7fa6b360962d1e02fa/report.md),
[unmatched single review](../../amat/outputs/governance/d8e8e6d08cda89f82866cdd736400321d52ed1e1a8e3bfce0c338689db337bec/report.md).

## Failures retained, not hidden

Earlier API integration attempts encountered schema and endpoint incompatibility.
The adapter moved to Interactions; the SDK boundary now has regression tests.
Protocol 5 also produced evidence-identity mismatches: strict validation refused
those responses and exhausted role attempts without manufacturing a decision.
Protocol 6 constrained identity in the generation schema; the two single reviews
then passed on their first attempts.

The protocol-6 full committees did not complete: the provider throttled calls.
The provider subsequently reported HTTP 429 and a free-tier quota metric limit
of 20 requests for this model. Protocol 7 adds serialized-by-default dispatch,
explicit handling of the Interactions error hierarchy, and a bounded rate-limit
retry delay. These changes do not grant additional provider quota. A successful
live four-agent review has **not** been established in this validation record.
No model fallback, quota increase, or strategy approval was used to bypass this.
The [final protocol-7 evaluation](../../amat/outputs/governance/evaluations/e25382cb01c433aa58eb8b9fd09398c2b4843a0a082d973d21ede570f96f9ce8/report.md)
also remained incomplete under the quota limit; its CLI exited with status 1.

Failed model calls and unknown usage remain in the registry. Unknown usage is
charged conservatively against the internal reservation limits, not asserted to
be actual billed usage. Incomplete evaluations export their failures and return
a nonzero CLI status.

## Engineering verification

- Full AMAT suite: 1,464 passed, one existing dependency-layer skip.
- Portfolio-team targeted tests: 36 passed, including the final retry-delay guard.
- Strict typing: no issues in 106 source files.
- Scoped lint and formatting checks passed.
- All four earlier fake-provider reviews and both identity-constrained real
  single reviews replayed successfully offline against their immutable exports.
- The [scripted evaluation](../../amat/outputs/governance/evaluations/9c3e368e22d1a9cae1835f838f3d04801585f42e76eb1415750ba2da289e8efc/report.md)
  completed both single-reviewer and committee paths without model calls.
- Tests cover real-provider request wiring through a mocked SDK, usage accounting,
  secret scrubbing, rate-limit classification, schema rejection, evidence identity,
  bounded dispatch, deadlines/cancellation, durable budgets, caching, label
  exclusion, and incomplete-evaluation exit status.

Mocked transport success is not presented as live committee success.

## What this means for strategy selection

The saved matched v11/v10 artifacts show annualized return increasing from 3.29%
to 3.71%, while reported Sharpe falls from 0.6959 to 0.6652 and maximum drawdown
deepens from -8.11% to -9.92%. The review correctly treats that as a risk/return
trade-off, not a proven upgrade. The saved v12/v6 histories have different dates
and session counts, so the report suppresses their performance deltas.

Before giving the team admission authority, attach matched daily equity/return
series and trade-ledger evidence, validate accounting and execution capability,
run registered ablations with genuinely untouched evaluation, and build an
adjudicated held-out reviewer corpus. The present data does not prove that a
committee outperforms a single reviewer or the deterministic checks.
