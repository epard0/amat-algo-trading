# P12 matched component and execution-cost verification

P12 freezes the union of P11 validation selections and baseline for three outer
folds. It compares full, no-MTF, no-exits, neither, daily-only and no-intraday
recipes at 2/5/10/20 bps one way. Other portfolio parameters remain fixed.
Baseline is replayed at every cost. Shared policy selections are aliases, not
independent evidence. No new optimization occurs.

Implementation: `src/autoresearch/genetic/component_study.py`, with CLI
`autoresearch patterns compare-components`. Methodology and commands are in
[component-comparison.md](component-comparison.md).

## Verification contract

Before outcomes, freeze all jobs and runtime/data/cache identities. Preserve the
original P11 numerical source and dependency versions; record the additive CLI
change. Verify feature array checksums, embedded/sidecar metadata equality,
original fold eligibility, and unchanged retained timeframe coefficients.

For all unchanged 2-bps recipes, require exactly matching dates, daily returns,
fills, warnings, annualized return and drawdown against P11. Apply every cost
scenario inside the same portfolio engine, including warm-up history. Resume
must reuse immutable receipts and request no replays after completion.

Independent review found no remaining issues after output-overlap guards,
metadata validation, inactive-parameter deduplication and identity checks were
completed. Tests cover coefficient preservation, paired costs, exact parity,
source/data refusal, and idempotent resume. Full unrelated application and
executor suites are not rerun for this additive offline research change.

## P7 clarification

P7 completed reconciliation and artifact refresh. The campaign remains latched
suspended after its verified IEF prevalence alert. Its artifact also requires
its original runtime or an explicit parity-verified migration. One retained
backfill is not prospective evidence. See [current P7 status](p7-current-status.md).
P12 neither changes that ledger nor grants execution or promotion authority.

## Measured verification

Completed **108 unique replays**, 108 labeled comparisons and **7 exact P11 parity checks** in 96.15 seconds.

**624 tests passed** in 36.17 seconds. MyPy passed for 38 source files; Ruff and formatting passed for 78 files. All 179 runtime source hashes matched registration. The source archive contains 224 files.

Completed resume preserved 110 JSON receipts and invoked zero evaluation callbacks. P4–P11 evidence verification passed before and after execution. `git diff --check` passed.

| Fold | Profile aliases | Setting | Annualized return | Drawdown magnitude |
|---|---|---|---:|---:|
| outer-2024 | baseline | full | 10.08% | 7.53% |
| outer-2024 | balanced, defensive | full | -2.51% | 3.36% |
| outer-2024 | balanced, defensive | no_mtf | -1.71% | 3.05% |
| outer-2024 | balanced, defensive | no_exits | -2.51% | 3.36% |
| outer-2024 | balanced, defensive | neither | -1.72% | 3.05% |
| outer-2024 | return | full | 5.61% | 6.15% |
| outer-2024 | return | no_mtf | 5.45% | 6.23% |
| outer-2024 | return | no_exits | 5.50% | 6.26% |
| outer-2024 | return | neither | 5.37% | 6.23% |
| outer-2025 | baseline, defensive | full | 27.08% | 10.22% |
| outer-2025 | balanced, return | full | 29.37% | 10.78% |
| outer-2025 | balanced, return | no_mtf | 31.40% | 8.07% |
| outer-2025 | balanced, return | no_exits | 29.05% | 10.79% |
| outer-2025 | balanced, return | neither | 37.94% | 8.08% |
| outer-2026 | baseline | full | 14.87% | 15.01% |
| outer-2026 | balanced, defensive, return | full | 8.01% | 15.49% |
| outer-2026 | balanced, defensive, return | no_mtf | -4.52% | 18.62% |
| outer-2026 | balanced, defensive, return | no_exits | 6.76% | 17.73% |
| outer-2026 | balanced, defensive, return | neither | 0.62% | 18.36% |

These are annualized returns; 2026 ends September 25. Full control tables and cost comparisons are in the retained `performance-report.md` and `results-summary.json`. Results are conditional on historical selection and separate warm-state trajectories; they do not select a new winner or establish a switching portfolio.
