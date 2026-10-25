# P13 frozen-policy synthetic replay

This stage integrates the coherent market generator with the five-timeframe
symbolic interpreter and continuous account simulator. The registered smoke
matrix is **17 scenario families × 2 seeds × 4 policies × 4 execution costs =
544 complete-calendar paths**. This is a frozen-mechanism experiment; it does not
run an adaptive search, refit historical eligibility masks, or authorize paper
deployment.

## Full-calendar and pairing contract

Each path spans all 2,698 verified sessions, January 4, 2016 through September 25,
2026. The first 1,006 sessions retain observed data and account state. The generated
suffix begins January 2, 2020, with calibration restricted to the preceding
observations. Every policy/cost cell for one world consumes exactly the same world
receipt and numerical market arrays. The original seed-zero worlds stay immutable;
seed-one worlds use the generator's archived runtime and their own registration.

The four policies are baseline, balanced, return, and defensive. Costs are 2, 5,
10, and 20 basis points one way. Baseline controls the account until the original
P11 activation dates. The historical recipes and their training eligibility masks
remain frozen. Their feature values are recomputed from each synthetic world:
no observed suffix feature array is imported. Recipes selected from historical
data are retrospective interventions on synthetic worlds, not independent
out-of-sample tests of the original selection procedure.

The active selected feature methods are the retained GP expression,
`illiquidity:21`, `momentum:63`, and `variance_ratio:21`. The nine frozen annual
policy contexts include reward-to-risk settings from 1.2482 to 7.4273 and stop
fractions from 0.05 to 0.1881. These are the original selected settings; this
matrix does not sweep every intervening ratio. The complete 27-terminal registry,
new GP populations, learned entry/router/exit models, and alternative allocation
methods are not claimed as independently tested by this stage. The retained
`frozen-method-and-exit-coverage.csv` identifies the actual method, exit mode,
weights, activation, and recipe hash for every context.

The explicit research adapter binds daily, 15-minute, hourly, weekly, and monthly
views to their original synthetic provenance. It checks registered input hashes,
world receipts, immutable numerical arrays, axes, calendars, and source identifiers.
Patterns retain their actual timeframes. Closed-view alignment and the original
neutral-contribution rule apply to unavailable views; early intraday history is
not fabricated. Only the original symbolic numerical interpreter is extracted for
reuse. An AST comparison proves that extraction leaves numerical operations and
the normal observed-source guard unchanged.

## Evidence and reporting

Each completed path retains sessions in CSV and Parquet, exact account and
execution state in JSONL, and a checksum receipt. World-specific feature arrays,
availability counts, pattern hashes, and policy schedules remain available for
inspection. Resume validates these artifacts and recomputes reported metrics from
the exact ledger; cached results cannot silently bypass missing schedules or
changed feature files. Failed jobs retain their identity and reason without
redrawing their seed. This smoke controller stops on a failure and does not publish
a successful campaign summary. Before the extended campaign, unaffected cells
should be allowed to finish and failed cells must appear explicitly in a final
summary with the full registered denominator. The normal CLI verifies the
controller before producing the report; standalone report generation currently
assumes that verification has succeeded and does not independently rederive
metric scalars from every exact account ledger.

Report all four scopes from the same continuous account:

- Full history, including initial cash and indicator warmup.
- Tradable history, anchored at the final warmup closing equity.
- Post-selection history, anchored at the December 29, 2023 closing equity.
- Generated suffix, anchored at the December 31, 2019 closing equity.

Annual slices inherit the previous year's closing equity. The final year is
partial. Prior-close regime labels describe disconnected subsets of sessions;
they are attribution summaries rather than independently tradable accounts.
Comparisons pair the same world and execution cost against baseline. Two seeds
and deliberately chosen stresses cannot establish future scenario probabilities
or a statistically validated optimum.

## Next stage and paper dependency

The fixed crash, gap, and liquidity events in this generator occur early in the
2020 suffix, before the P11 policies first activate in 2024. They exercise baseline
shock handling and subsequent carried account state; they do **not** directly
test the selected pattern policies during those particular shocks. Persistent
bull, bear, distributional, and volatility scenarios continue through the active
period. The per-world gallery records exact event dates and adoption coverage.
Before merely adding more seeds, introduce registered event timing across the
2024, 2025, and 2026 policy contexts, with common event dates for matched policy
comparisons and enough remaining sessions to observe recovery. Keep all earlier
account history. This requires a new generator contract and a separate matrix;
do not rewrite these frozen worlds or interpret their early events as active-GP
crisis exposure.

Use measured smoke-run runtime and storage to size the separately registered
17,408-run extension (17 families × 64 seeds × 4 policies × 4 costs). Expanding
the TP/exit and method parameter space requires its own registered candidate
matrix and, for adaptive optimization, past-only training and validation inside
each world. Monte Carlo scenario sampling and parameter search remain distinct
axes of that experiment. All candidate paths must preserve this full-calendar
account contract.

For that adaptive stage, separate development, selection, and evaluation seeds
before seeing results; seeds used to debug or choose settings cannot become an
untouched evaluation set. Reserve a past-only fit boundary for every learned
component and retain every attempted candidate, including invalid and failed
trials. Compare global random starts with local parameter perturbations under the
same trial budget. Report stability across nearby settings, costs, and regimes
alongside return, drawdown, turnover, and fill capacity. A selected numerical
maximum alone is insufficient evidence of a useful live mechanism.

The Docker paper mechanism remains unchanged. Research/executor parity and the
paper integration audit remain prerequisites for any later deployment decision.
