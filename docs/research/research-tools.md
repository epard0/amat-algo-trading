# Evidence tools and matched research follow-ups

New daily research sessions freeze a snapshot of eligible prior experiments. The
normal `./research 5` entry point continues to work. Earlier sessions must resume
through their original frozen launchers; installing this change does not alter
their program or scoring policy.

## Proposal reliability

A structured model proposal that fails local schema, contract or novelty checks
can receive **one repair call across the entire provider chain**. The repair
contains locally authored validation guidance, never raw invalid response text.
Both calls use normal token reservations and settlement. Quota, authentication,
timeout and transport errors continue through normal failover and cooldowns.
Proposal calls request low reasoning effort to leave output space for the recipe.
The prompt supplies the editable parent recipe and fixed market/evaluation
contract instead of repeating the entire application configuration.

`proposal_result` events distinguish valid proposals from exhausted chains.
The `calls` ledger distinguishes `proposal_schema`, `proposal_contract` and
`proposal_duplicate`. Built-in fallback remains visible as a separate proposal
source; batch completion does not establish successful model proposal generation.

## Frozen cross-session memory

Initialization scans sibling daily research sessions and writes
`runtime/research-memory.json` into the new program's frozen input manifest.
Eligible records require compatible evaluation rules, complete baseline, retention gates, snapshot,
dependencies, measured anchor and evaluator implementation. The source training
period cannot extend beyond the new training period. Sessions with checkpoint
exposure, unknown/future research timestamps, active writers, missing evidence or
changed hashes are excluded. Exclusions are recorded in the memory snapshot.

Only verified development outcomes and recipes enter memory. Provider responses,
review commentary and checkpoint outcomes do not enter it. The current limit is
500 records and four relevant matches per request, further bounded by the model
context limit. Retrieval uses feature families and text relevance; it is not a
claim of causal similarity. Findings from incompatible score versions are not
pooled. Historical programs may therefore be excluded even if their failures
remain available in the human failure library.

Inspect retrieved evidence with the frozen launcher:

```bash
/path/to/session/research.sh related --query "momentum confirmation"
/path/to/session/research.sh related --candidate CANDIDATE_DIGEST
```

The proposer receives the selected evidence and is instructed to explain what
materially changes when revisiting a failed mechanism. `memory_retrieved` events
record the IDs actually supplied after context trimming. This supports informed
proposals; it does not guarantee that an LLM will follow the lesson or eliminate
adaptive overfitting. New sessions freeze their own memory rather than reading a
changing library during every proposal.

Both model proposals and built-in search reject exact candidate identities already
measured in eligible memory over the same training period. This prevents automatic
repetition of an identical experiment. Explicit reproduction remains available;
nearby or conceptually similar recipes require research judgment.

## Verified visual diagnostics

```bash
/path/to/session/research.sh charts --candidate CANDIDATE_DIGEST
```

The command creates `analysis/charts-CANDIDATE_DIGEST.html` and an adjacent JSON
file with every plotted value and the input hashes. It shows rebased equity,
drawdown and gross exposure for primary and stressed runs and, when available,
both exposure-matched controls. It verifies recorded artifacts, chronological
alignment and equity/return consistency first. The command does not rerun a
backtest or alter retention decisions. Omit the candidate to inspect the incumbent.

These are standard static diagnostic charts with downloadable data. Parameter
heatmaps, linked interactive filtering and additional research packages remain
subsequent work. The plots label exposure matching as retrospective development
analysis, not an investable benchmark.

## Execute an approved matched follow-up

The first registered operation is `matched_backtest` for a comparison exported by
the same frozen daily research program. It reproduces the reviewed candidate and
control using the existing credential-free backtest subprocess, 2/6 bps costs,
fixed data, correct load/warm-up window and common measured dates. It does not
interpret free-form objective text or invent study arguments. Other operation
types and legacy aggregate study envelopes remain unsupported.

First import/review a declared comparison and plan its follow-ups through the
existing portfolio-team workflow. Approve a concrete task:

```bash
uv run --locked pmteam --root /path/to/session plan REVIEW_ID
uv run --locked pmteam --root /path/to/session approve-task REVIEW_ID TASK_ID --by operator
/path/to/session/research.sh follow-up REVIEW_ID TASK_ID
```

The equivalent explicit installed command is
`pmteam --root /path/to/session execute-task REVIEW_ID TASK_ID --run`, provided
the installed controller matches the session. Execution checks the completed
source review, exact derived queue, approval identity, candidate hashes, source
comparison and program identity. The review registry lock is released before the
research worker acquires its lock.

The worker writes a separate matched comparison, both sets of run artifacts and a
new `catalog.toml` under `outputs/followups/`. It imports the resulting evidence
into the review registry. The receipt reports the catalog path and evidence hash;
that catalog can be passed to the next `pmteam cycle`. Completion means the
matched reproduction was recorded, not that every source-review objection is
resolved or that the strategy passed a promotion gate.

Role-level progress is durable. A retry verifies and reuses a completed side.
There are at most two execution attempts per queue/task identity. Completed jobs
return their verified receipt without launching more backtests. Incumbent
selection, trial outcomes, the executor and broker credentials are not changed.
The receipt uses `TASK_ID.matched.json`, preserving any older blocked receipt.

## Evidence boundaries

These tools improve the mechanics of research and its observability. Their effect
on strategy quality still needs a budget-matched workflow comparison. None of the
new commands turns reused development data into an untouched holdout. Research
retention and deployment remain separate decisions.
