# P11 diversified walk-forward verification — 2026-09-29

Two implementation subagents added the search engine and concrete portfolio
harness; a third performed a read-only review. Parent integration added the CLI,
registered the three folds, ran the extended study and retained the evidence.

## Engineering verification

**600 genetic-pattern and exit-rule regression tests passed in 36.53 seconds.**
This includes 22 new search-engine cases, 13 harness cases and one new CLI route.
MyPy passed 37 genetic source modules. Ruff lint and formatting passed all 76
genetic source/test files. The unrelated full application and executor suites
were not rerun. No new dependencies were installed.

The new modules are `walkforward_search.py` and `walkforward_study.py`. The CLI
adds `autoresearch patterns walkforward-parameters`. Methodology and reproduction
commands are in [walkforward-parameters.md](walkforward-parameters.md).

Tests cover deterministic resume and changed-spec refusal, family diversification,
Pareto ties/dominance, profile selection boundaries, reserved portfolio-only
sampling, batch failure isolation, fold ordering/overlap, training-only feature
eligibility, cache recovery and tampering, worker isolation, and active-parameter
stability with missing-fold disclosure.

Review corrections also added final runtime/data identity checks, verified all
five feature matrices before sidecar recovery, and excluded unused MTF/exit fields
from parameter stability. The final runtime was frozen before the extended run.

## Inputs and scope

P11 reuses P10's verified native15m receipt and the reconciled daily snapshot.
The receipt contains 1,041,746 records across 11 assets from January 2019 through
September 25, 2026. Native hourly views require four observed15m components on the
session-open grid. Missing components remain missing; daily decisions only use
completed bars. No provider refetch or P10 masked-cache reuse was performed.

All **3,453 P4–P10 evidence files**, **35 external hash references** and **six reused
input references** matched their retained hashes before the study. P10 evidence,
historical rejections and the suspended P7 campaign remain preserved.

There are 55 feature variants, five timeframes, 640 global proposals and 128 local
proposals plus baseline per fold: 2,307 registered training proposals overall.
Each fold reserves 80 global proposals for portfolio-only retuning. Method-family
quotas diversify the training shortlist, and three fixed validation policies
select consistency, return and defensive profiles before outer evaluation.

## Interpretation

Outer periods are 2024, 2025 and 2026 through September 25. These are retrospective
periods already accessed in earlier research. Overlapping training windows and
shared assets do not produce independent replications or selection-adjusted
significance. The bounded search does not establish a global optimum.

Each recipe is a separately warmed full-history replay. Fold comparisons do not
represent an executed switching portfolio, and reported parameter ranges do not
include inactive placeholders. Return-profile results may trade additional
risk for return. Defensive's baseline-return floor applies in validation and is
not a guarantee for the later outer period. Outer results select no new profile.

Fixed exposure controls replay the baseline at risk scalars 0.25, 0.5, 0.75 and
1.0. These describe the effect of allocation scaling; they do not prove matched
realized volatility. All results retain the existing daily-close exit triggers,
next-window execution, costs, corporate actions and liquidity accounting.

There are no live orders, campaign resets, new prospective observations or
promotion permissions associated with this study.

## Measured results

Completed **2,307 training evaluations** in 32.9 minutes, with 0 invalid, 1 ineligible and 0 exact duplicate recipes. All three policy comparisons are reported; this table does not choose a policy after seeing outer results.

No profile consistently improved on the baseline: balanced and return profiles won one of three outer return comparisons; defensive won none and matched baseline in 2025. These are descriptive outcomes, not a new policy selection.

| Fold | Profile | Annualized return | Drawdown magnitude | Return difference vs baseline |
|---|---|---:|---:|---:|
| outer-2024 | baseline | 10.08% | 7.53% | +0.00 pp |
| outer-2024 | balanced | -2.51% | 3.36% | -12.58 pp |
| outer-2024 | return | 5.61% | 6.15% | -4.47 pp |
| outer-2024 | defensive | -2.51% | 3.36% | -12.58 pp |
| outer-2025 | baseline | 27.08% | 10.22% | +0.00 pp |
| outer-2025 | balanced | 29.37% | 10.78% | +2.29 pp |
| outer-2025 | return | 29.37% | 10.78% | +2.29 pp |
| outer-2025 | defensive | 27.08% | 10.22% | +0.00 pp |
| outer-2026 | baseline | 14.87% | 15.01% | +0.00 pp |
| outer-2026 | balanced | 8.01% | 15.49% | -6.86 pp |
| outer-2026 | return | 8.01% | 15.49% | -6.86 pp |
| outer-2026 | defensive | 8.01% | 15.49% | -6.86 pp |

Completed-search resume left **2,445 JSON receipts unchanged** and requested zero evaluations. All 178 runtime hashes match the registered context. The source/configuration/test archive contains 222 files. `git diff --check` passed.

All **686 outer baseline daily returns** match the retained P10 series exactly.

The retained evidence is in `outputs/genetic-patterns/2026-09-29-p11/`. Start with `performance-report.md`, `results-summary.json`, `study/summary.json`, `resume-verification.json` and `evidence-manifest.json`. Every fold retains its training shortlist, validation policy selections, trials and fixed exposure controls. Feature arrays include training eligibility and source-bar statistics for each fold.
