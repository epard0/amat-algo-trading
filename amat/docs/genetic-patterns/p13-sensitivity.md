# P13 historical timing and component controls

This stage extends the verified continuous portfolio foundation to the 76-path
matrix defined before these controls were measured. It introduces two new
research modules in the isolated `codex/p13-continuous-history` worktree. The
existing 182 foundation runtime source files remain unchanged.

## Registered comparison

- 16 primary paths: baseline, balanced, return and defensive at 2/5/10/20 bps.
- 24 timing paths: the three selected policies at each cost, adopting each annual
  recipe one or five observed sessions later than the primary activation.
- 36 component paths: the three policies at each cost, consistently removing
  MTF contributions, exit rules, or both from every selected annual recipe.

No component/delay cross-product, new parameter search, or outcome-dependent
change of recipe is introduced. All runs cover the same complete verified
2016-01-04 through 2026-09-25 calendar. Cash-only warm-up, baseline prehistory,
missing native intraday coverage and actual portfolio state remain intact.

Delay means later adoption of each annual recipe, not permanently shifting every
signal or changing the execution window. The old recipe controls decisions until
the new activation; pending orders still settle at their original execution dates.
Observed sessions govern offsets, including the January 9, 2025 exchange closure.

Component controls leave all other selected parameters unchanged. In particular,
`neither` is not the baseline: selected trend/volatility/rebalance/target-risk
settings and scalar changes remain. Effects are paired conditional contrasts;
MTF and exits can interact and their removal effects need not add linearly.

## Evidence lifecycle

The new study verifies the foundation registration, all original module hashes,
dependencies, selected data inputs, receipts and data files. Four primary runs
at 2 bps are replayed through the new orchestration and must reproduce the exact
CSV, Parquet and JSONL hashes before the new controls can proceed. These are
validation replays, not additional registered parameter trials.

The original 16 primary datasets are copied byte-for-byte. Their new wrapper
receipts bind both the new registration and their original receipt/registration
identities; the original receipt is retained beside each copy. Historical
foundation receipts and their strict resume gates are not rewritten. Its archived
runtime remains the reproducible environment for resuming that old registration.

The 60 additional paths use the same runner, accounting, action-exception policy,
resolved corporate actions, feature eligibility, baseline inputs and execution
contracts. Every new path retains every source session. Successful receipt
publication follows complete data export and accounting reconciliation; failed
attempts have explicit failure records. Separate workers own distinct output
paths and preserve registered run identities even when later recipes coincide.

Resume checks registration/job identities, hashes and copied-primary provenance.
It also recomputes stored metrics from exact account-state JSONL. The full matrix,
activation schedule and source calendar have independent registration hashes.

## Reporting

The report retains full-calendar, tradable-history and post-selection metrics.
Annual tables cover every available year; the first year starts at initial
source equity and later years start at the actual preceding close. The final
2026 period is explicitly partial, ending September 25.

Each contrast uses both the same-policy full zero-delay path and the same-cost
baseline. Regime comparisons use previous-close labels, explicit observation
counts and descriptive paired session means; disconnected regime observations
are not treated as one tradable CAGR or drawdown path.

All registered paths appear in the report and machine-readable exports. Full
account-state traces retain holdings, pending instructions, fills, missed fills,
entry bases and embargoes. One/five/twenty-session transition diagnostics describe
actual costs and turnover around activation. They are transition-window costs,
not automatically the incremental cost caused by switching.

## Boundaries

This is retrospective diagnostic evidence. The baseline data extension remains
blocked by missing observed raw opening-window VWAP/volume for September 28.
Native 15m coverage starts in January 2019; session-open hourly views follow the
existing four-complete-bars contract. No earlier intraday history is fabricated.

Synthetic-market validation, full adaptive refitting, wider method-family
coverage, and research-to-executor parity remain later stages. This study cannot
authorize candidate promotion or changing Docker paper execution settings.

## Worker execution

The final controller uses fresh spawned processes and an explicit serialized-state
initializer. Forking after native data-library threads start can leave workers
blocked; the interrupted fork attempt is retained separately and excluded from
final results. A real two-worker replay test verifies native Parquet exports after
parent-side data-library initialization.
