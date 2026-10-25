# Flexible strategy research and failure analysis

New sessions also freeze compatible cross-session development evidence for the
proposer. See [research tools](research-tools.md) for retrieval, verification,
charts and the matched follow-up worker. The full human-readable failure library
remains broader than the strictly filtered proposer memory.

Run a complete batch from either the workspace root or `amat/`:

```bash
./research 100
./research status
./research view
./research failures
./research failures --all
```

`failures` opens the latest session's failure library. `--all` reads original ledgers across sibling research sessions, including older
program versions, and verifies recorded artifact/review hashes. It reports active
writers, damaged evidence and other coverage gaps instead of silently omitting them.
Export-only sessions can also be collected when their manifest matches.
Aggregation keeps session and program identifiers; results from different frozen
programs are not pooled as comparable performance evidence.

## Creator flexibility

The built-in researcher now interleaves fresh families with refinement, ablation,
combination, pattern, routing and policy experiments. Fresh exploration rotates
through 18 feature families, multiple lookbacks, normalization methods, polarity,
causal lags and holding periods. It returns to fresh exploration every third
post-screening attempt and when frontier mutations are exhausted. Every recipe
still passes the same schema, warm-up and protected-configuration checks.

Continuous signal interactions support `product`, `difference`, `minimum`,
`maximum`, and `gate`. Inputs are tanh-normalized terms. Minimum expresses
agreement, maximum an alternative, and gate multiplies the left term by
`(right + 1) / 2`. Pure interaction rules can set direct term weights to zero;
removing their final interaction produces a valid baseline ablation. Missing
inputs propagate as unavailable. Lookbacks can extend to 252 sessions subject
to the fixed warm-up budget; holding periods can extend to 63 sessions.

An optional research brief guides LLM proposals:

```bash
./research 10 --brief "Explore simple trend pullbacks and participation confirmation, including longer holds."
```

The brief is limited to 3,000 UTF-8 bytes and frozen with the session. It cannot
change the evaluation contract. Built-in fallback uses its documented search
schedule; it does not claim to interpret a free-text brief.

## Durable failure library

Every discarded strategy and crashed attempt remains in the SQLite experiment
ledger. Reports export `analysis/failures.json` and `analysis/failures.html`,
including the proposal, ancestry, measurement hashes, LLM review availability,
and seven evidence-based standpoints:

1. Implementation: contract, data or runtime error versus completed evaluation.
2. Objective: actual score, incumbent, required margin, gates and complexity.
3. Risk: drawdown, exposure and available tail/underwater diagnostics.
4. Execution: stressed costs, turnover evidence, fills and missed fills.
5. Market conditions: losing years, weaker paired scenarios and stability checks.
6. Signal: coverage, associations and exposure-matched controls.
7. Uncertainty: passed, failed, skipped or unrecorded statistical checks.

Each standpoint includes its observations and a next test. This deterministic
analysis remains available when an LLM is unavailable. LLM commentary receives
these observations, including runtime failures, and must distinguish observed
reasons for rejection from unproven economic explanations. Crashes require an
inconclusive performance verdict. Records do not assert that every standpoint
failed or that historical association proves causality.

The proposer receives aggregate failure counts plus recent failed-recipe
findings from the current session, within its context budget. Full recipes and
all failures remain in the library even when prompt history is trimmed.

## Batch and budget behavior

A requested count means distinct completed strategy evaluations; the baseline
is additional. Crashes and duplicates remain recorded and do not fill a slot.
The simple launcher allows at most three attempts per requested completion,
with a matching stall budget. Insufficient search, data or provider resources
produce an explicit partial result rather than a false completion.

The default daily model budget remains 300,000 charged tokens. For larger runs,
`--max-model-tokens-per-day` can raise it explicitly. Unknown provider usage may
be charged conservatively; token accounting is not a dollar estimate. A missing
LLM review is marked unavailable. Promotion still requires beating the retained
incumbent under the frozen quality and uncertainty gates.
