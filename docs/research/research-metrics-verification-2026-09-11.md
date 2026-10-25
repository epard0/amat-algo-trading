# Research recording verification

Validated the simplified research workflow and metrics extension with the full
suite: **1,714 passed, 1 skipped**, in 200.18 seconds. Ruff passed and mypy checked
142 source files successfully. Regression coverage includes metric arithmetic,
tied expected-shortfall quantiles, undefined risk forecasts, artifact tampering,
CSV precision and formula handling, interrupted phases, safe live status reads,
distinct strategy counting, failed attempts, and incumbent escalation.

## Fresh run

Executed a frozen session with one built-in candidate plus its baseline and the
extended robustness suite:

```bash
/home/supasuge/Documents/amat-research/metrics-recording-final-20260911/research.sh loop --iterations 1
```

Program SHA-256:
`60aefb6e5cf076ed3562e9858cdc38f6579f885730bcc8e081dc42e5abae8087`.
The process returned zero with one completed candidate, no failed measurements,
and no escalation. The candidate failed the incumbent improvement requirement.

| Recorded evidence | Count |
|---|---:|
| Strategies, including baseline | 2 |
| Daily account records, primary and stressed | 5,036 |
| Executed fills | 6,245 |
| Missed fills | 34 |
| Yearly results | 10 |
| Paired scenario rows | 44 |
| Provider attempts | 5 |
| Completed phase timings | 6 |
| Lifecycle events | 28 |

All 236 referenced measurement hashes and all 11 analysis export hashes matched.
CSV row counts matched the manifest. Every completed phase and provider attempt
had a nonnegative duration. Primary daily records contained 2,516 defined post-
allocation volatility forecasts and two undefined anchors recorded as null;
binding constraints were preserved as lists. The new risk diagnostics appeared
in both experiment results and all five LLM review requests.

The baseline LLM review was unavailable after provider failures; this was recorded
as unavailable. The candidate review succeeded through OpenRouter and explained
why the result was rejected. Four provider attempts failed; three had unknown
usage. Recorded known usage was 9,341 input and 3,166 output tokens, separately
from 56,015 budget-charged tokens. These are token counts, not monetary costs.

`./research status` was exercised while the session was reviewing its baseline.
It displayed the running phase and current usage without interrupting the writer.
The process subsequently completed normally. Analysis was refreshed after exit
to capture the inactive writer state in the saved report.

## Existing five-strategy session

Rebuilt exports using `./research analyze` for
`/home/supasuge/Documents/amat-research/simple-five-strategies-20260911` without
rerunning backtests. Verified all 772 measurement hashes, all 11 export hashes,
and CSV row counts: six strategies including baseline, 15,108 daily records,
15,413 fills, 239 missed fills, 30 yearly results, 132 paired scenario rows, and
12 provider records. Its five distinct candidates and original decisions remained
intact. Historical phase timings and unrecorded risk fields remain unavailable.

Both `./research status` and `./research view --no-open` still resolve the latest
session to this five-strategy report. See [the batch verification](simple-research-verification-2026-09-11.md)
for measured performance and proposal fallback details, and
[the metrics guide](research-metrics.md) for definitions and file schemas.
