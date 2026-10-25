# P13 historical sensitivity results and verification

Completed October 1, 2026 in the isolated `codex/p13-continuous-history`
checkout. The artifact name retains the September 30 start date.

All **76 registered paths** cover **2,698 sessions, January 4, 2016 through
September 25, 2026**: 16 byte-identical foundation primary paths plus 24 annual
recipe-adoption delay controls and 36 component-removal controls. Four fresh
primary validation replays reproduce every CSV, Parquet and JSONL hash exactly.
The final batch completed successfully in 223.27 seconds with six spawned workers.

The baseline has the highest total return at each of 2/5/10/20 bps among all
registered paths. This is a retrospective comparison, not a new parameter
selection or a prospective performance claim. It supplies no promotion authority.

## Observed results

All rows below use 2 bps one-way costs. Post-selection measures the continuous
account from its actual December 29, 2023 closing equity through September 25,
2026. Drawdown in the last column uses that same period. Full history includes
cash warm-up and baseline prehistory.

| Path | Full-history return | Post-selection return | Post-selection max drawdown |
|---|---:|---:|---:|
| baseline-2bps | 253.59% | 54.50% | -15.01% |
| balanced-2bps | 189.68% | 26.58% | -14.46% |
| balanced-no_mtf-2bps | 173.71% | 19.60% | -17.50% |
| balanced-no_exits-2bps | 195.70% | 29.21% | -17.74% |
| balanced-neither-2bps | 204.26% | 32.95% | -18.30% |
| return-2bps | 219.46% | 39.59% | -14.44% |
| return-no_mtf-2bps | 199.46% | 30.85% | -17.40% |
| return-no_exits-2bps | 225.80% | 42.36% | -17.73% |
| return-neither-2bps | 232.75% | 45.40% | -18.21% |
| defensive-2bps | 196.76% | 29.67% | -13.67% |
| defensive-no_mtf-2bps | 174.09% | 19.76% | -16.55% |
| defensive-no_exits-2bps | 192.22% | 27.69% | -17.75% |
| defensive-neither-2bps | 182.19% | 23.30% | -18.35% |

Removing MTF alone lowers post-selection return in all 12 policy/cost comparisons.
The effect ranges from −0.89 to −9.91 percentage points. Removing exits raises
balanced and return-profile returns by approximately 2.62–2.82 points, but lowers
defensive returns by 1.81–2.15 points. At 2 bps, all three no-exit profiles have
roughly −17.7% post-selection drawdowns, deeper than their full counterparts.
Removing both components behaves differently again: the retained selected
core/risk settings matter, and the component effects do not add linearly.

Five-session adoption delays improve all three profiles at all four costs in
this history. One-session delays improve the return profile but reduce balanced
and defensive returns. Only two annual switches follow initial adoption, so this
is insufficient evidence to choose an optimal delay. These controls delay annual
recipe adoption, not the latency of every daily signal.

The strongest measured nonbaseline return happens to be `return-neither` at each
cost: post-selection returns 45.40%, 43.00%, 38.97%, and 31.77%, respectively,
versus baseline 54.50%, 52.42%, 49.57%, and 46.64%. This descriptive ranking is
not a selected deployment configuration. `Neither` preserves the selected
core/risk knobs and is not equivalent to the baseline.

## Associated evidence

The HTML and Markdown reports show all 76 paths, primary equity curves and
conditional component/delay contrasts. Machine-readable exports retain 228
scope comparisons, 836 annual rows covering every year 2016–2026, and lagged
market-regime summaries with explicit observation counts. Disconnected regime
observations are descriptive; they are not independent investable accounts.
Each path retains CSV, Parquet and exact account/execution JSONL, including cash,
positions, pending instructions, entry bases, embargoes, fills and missed fills.
Transition diagnostics are a separate provenance-bound companion export.

The companion analysis verifies 648 complete transition windows (216 each at
one/five/twenty sessions), 228 scope turnover rows, and 36 paired component
comparisons. No transition window overlaps another activation. Removing MTF
reduces turnover in all 12 policy/cost pairs by 13.94–23.96%, while lowering
returns. At 2 bps, post-selection cumulative turnover is 41.02 for baseline,
65.56 balanced, 66.80 return and 61.05 defensive; observed total costs are
$2,354.40, $3,336.06, $3,700.95 and $3,171.69 respectively. Turnover sums each
fill's absolute raw notional divided by prior-session equity. These are observed
costs on each portfolio path, not pure incremental costs caused by switching.


- [Readable report](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-sensitivity/study/report.html)
- [All comparisons](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-sensitivity/study/comparison.csv)
- [Annual results](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-sensitivity/study/annual.csv)
- [Regime results](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-sensitivity/study/regimes.csv)
- [Transition analysis](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-sensitivity/transition-analysis/README.md)
- [Implementation contract](p13-sensitivity.md)

## Verification and operational notes

49 sensitivity/report/transition tests and 109 related regression tests pass
(158 distinct scoped cases). Type checks pass for the two new runtime modules;
all seven owned runtime/script/test files pass lint and format checks.
The new multiprocessing test initializes native Polars I/O in the parent,
serializes actual portfolio inputs, and executes two real spawned replays.

A resume test disabled the numerical replay functions: all 76 paths were verified
with zero numerical evaluations, an identical summary, and unchanged hashes and
modification times for all 320 per-run files. The 182 foundation runtime files,
202 foundation evidence files, 2,533 P11 files and 141 P12 files are unchanged.
The two additional runtime modules have their own new source registration.

An initial shell launcher could not find `/usr/bin/time`; it never started a
study. A subsequent fork-based attempt stalled in all six workers after native
threads had been initialized. That attempt, its registration and exact source
are preserved under `interrupted-fork-attempt`; none of its unfinished controls
enter the results. Fresh spawned workers with explicit input initialization
resolved the stall. The final study was registered and replayed afresh.

The Docker-mounted host runner and exit implementation are unchanged. A read-only
check observed `amat-executor` healthy, `DRY_RUN=true`, and
`EXECUTOR_DISABLED=true`. No container configuration was changed.

## Remaining scope

Native 15m data starts January 2, 2019. Hourly views require four complete native
15m bars under the existing session-open convention. Earlier unavailable views
remain explicit rather than truncating the account. September 28 raw/daily and
native receipts still lack the verified raw opening-window VWAP/volume required
to extend this snapshot; September 25 is the latest coherent verified endpoint
of this registered study, not a claim about the current market date.

The next implementation stage is a coherent market-scenario generator: aligned
cross-asset raw execution prices/VWAP, adjusted OHLCV, volumes, corporate actions,
and native 15m/hourly observations, followed by recomputing features and replaying
each complete calendar. Validate generator invariants and benchmark storage/runtime
before the proposed 544-run smoke matrix and 17,408-run frozen-mechanism campaign.
A broader adaptive search needs separate past-only refitting and selection-aware
validation. Method-family coverage and research-to-paper-executor parity remain
required before changing the paper mechanism. Synthetic runs completed here: zero.
