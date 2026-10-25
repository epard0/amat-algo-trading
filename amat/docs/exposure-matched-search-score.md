# Exposure-matched search score

New `strategy-development/6` sessions rank candidates by their incremental
risk-adjusted performance against the frozen original baseline. The completed
100-strategy run demonstrated the problem: its highest old-score candidate had
negative exposure-matched Sharpe improvement. This change aligns the objective
with the existing incremental-value gate rather than optimizing a second,
absolute-performance target.

## Contract

`score = matched Sharpe difference − 0.01 × max(0, candidate complexity − baseline complexity)`

The score version is `exposure-matched-incremental/1`. The baseline scores zero.
Complexity uses the existing count of features, signal terms, interactions,
pattern conditions and router allowance. Simpler candidates do not earn a bonus
that could turn negative incremental performance positive. The 0.01 weight and
0.05 improvement margin are retained policy constants, not fitted to this batch.

Exposure matching reuses the exact existing control simulation, normal 2 bps
costs and frozen tolerance. The more exposed strategy is scaled down. Missing,
nonfinite, inconsistent or unresolved matches produce no score and an explicit
failure gate. No absolute-score fallback is permitted.

All candidates use the same original baseline reference, even when branching
from a discarded parent. Frontier ranking and the incumbent score comparison
consume this objective. The existing quality, stress, stability, accounting,
scenario and incumbent uncertainty checks continue to govern retention. The old
fold-based score is recorded as `absolute_performance_score` for diagnosis only.
Yearly fold measurements and quality cutoffs remain intact.

Reports display the matched gain and complexity deduction. Structured results,
CSV exports, failure analyses, proposer context and LLM review evidence expose
the score version and its components, including reference ID and exposure error.

## Evidence limits and compatibility

Matching is retrospective and matches average gross exposure within tolerance;
it does not equalize every day's exposure, sector mix, volatility or tail risk.
It is not a tradeable benchmark or independent evidence of future alpha. The
incumbent uncertainty check is still a separate requirement. Higher raw returns
can remain unattractive when explained by greater exposure.

Existing version 5 programs preserve their canonical payload and scoring
semantics. Their frozen launchers remain the supported way to resume them.
Start a new session with the normal `./research 5` command to use version 6.
Rollback consists of resuming a prior frozen session or reverting this change
before initializing another session; never rewrite historical decisions.

## Verification

Unit coverage checks negative incremental value despite a high absolute score,
complexity, baseline zero, missing and invalid evidence, retention of risk gates,
and legacy contract compatibility. The frontier regression includes zero and
negative scores. The real research integration test verifies baseline zero,
matched-score arithmetic, report output and resume behavior against actual
backtests. Historical reranking is recorded separately and does not replay
promotion or claim a new adaptive-search outcome.

## Recorded 100-strategy comparison

The [comparison report](exposure-score-comparison-2026-09-12/report.md) and
[all 100 ranking rows](exposure-score-comparison-2026-09-12/comparison.csv) use
verified original control measurements. A separate JSON manifest records 14,231
source hashes checked before and after calculation. The historical ledger,
program, backtest outputs and recorded decisions remain unchanged.

| Experiment | Old rank | New rank | Matched Sharpe gain | Complexity deduction | New score |
|---|---:|---:|---:|---:|---:|
| 73: former score leader | 1 | 13 | -0.085375 | 0.030 | -0.115375 |
| 15: new score leader | 14 | 1 | +0.043700 | 0.000 | +0.043700 |
| 2: passed historical quality gates | 30 | 2 | +0.002894 | 0.010 | -0.007106 |
| 101: passed historical quality gates | 45 | 3 | +0.016030 | 0.030 | -0.013970 |

One candidate has a positive new score; none clears the initial 0.05 margin.
Experiment 15 also retains its recorded control-runner warning. The comparison
supports objective alignment, not an improvement in achieved strategy returns.
A different search objective would change subsequent hypotheses; this is not a
counterfactual replay of an entire adaptive batch.

Reproduce into a new directory outside the historical session:

```bash
uv run --locked python scripts/compare_research_scores.py SESSION NEW_OUTPUT_DIRECTORY
```

## Implementation checks completed

- Full regression suite: **1,740 passed, 1 existing skip** in 439 seconds.
- After the final reporting and LLM-label clarification: **41 focused tests passed**,
  including the historical-comparison integrity test and score metadata in all
  failure/proposer/reviewer evidence paths.
- Lint and formatting passed; strict type checking passed across 144 source files.
- Fresh extended-robustness verification session:
  `/home/supasuge/Documents/amat-research/incremental-score-verification-20260912`.
  The baseline scores zero. Candidate 1 has matched gain `0.0028936334`, a `0.01`
  complexity deduction and score `-0.0071063666`; quality checks pass but the
  improvement margin fails. Candidate 2 has matched gain `-0.1303030323`, the same
  deduction and score `-0.1403030323`; the incremental-value gate also fails.
  Both are discarded, with no crashes or escalation. This verification session
  froze the scoring implementation before the final explanation-only refinements;
  those refinements are covered by the focused checks above.

The fresh session exited successfully with two completed candidates, zero failed
attempts and no escalation. Both candidate LLM reviews completed; the baseline
review is explicitly unavailable. Its score arithmetic, three-row CSV export,
HTML labels and 489 frozen/evaluation artifacts were verified. The session's
`score-verification.json` records those checks. The final report is at
`/home/supasuge/Documents/amat-research/incremental-score-verification-20260912/report.html`.
