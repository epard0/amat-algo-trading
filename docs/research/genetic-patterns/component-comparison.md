# P12 matched components and execution-cost stress

P12 diagnoses P11's frozen outer-period selections. It does not search again,
select a new winner, or change the suspended P7 campaign. The same completed
15m, session-open hourly, daily, weekly and monthly inputs feed the existing
daily portfolio runner. Exit decisions retain daily-close trigger and
next-execution-window semantics.

## Frozen comparison

For each P11 fold, retain the union of its three validation-selected recipes and
the original baseline. Preserve all policy aliases when policies selected the
same recipe. Compare each at 2, 5, 10 and 20 basis points of one-way transaction
cost, including the baseline at every cost. These are declared cost scenarios,
not measured account-specific fees or a liquidity model.

Each selected recipe has six controls:

| Control | Change from selected recipe |
|---|---|
| full | None |
| no_mtf | Zero the complete feature-overlay effect |
| no_exits | Disable stop/profit exit overlay |
| neither | Disable feature and exit overlays together |
| daily_only | Remove 15m, hourly, weekly and monthly contributions |
| no_intraday | Remove 15m and hourly contributions |

All other portfolio parameters stay fixed. Removing timeframes preserves the
original coefficients of retained views: removed weight is not redistributed.
The implementation adjusts the remaining total effect to compensate for the
existing evaluator's normalization. Missing or training-ineligible inputs still
contribute zero. A baseline selection or an already-disabled component can yield
an exact no-op; identical resulting recipes are replayed once per fold/cost and
retained under all applicable comparison labels.

The full 2-bps replay must reproduce the retained P11 outer metrics and daily
returns. Baseline cost stress uses the same execution accounting as every
selected recipe. Recompute each whole historical trajectory at its declared
cost; do not subtract a flat fee from reported final returns.

## Interpretation

Full minus no-MTF measures the contribution of the feature overlay conditional
on that recipe's other settings and exits. Full minus no-exits measures the exit
overlay conditional on its feature settings. Full minus neither measures the
joint contribution. Differences need not add: portfolio constraints, rebalances,
exit embargoes and costs can interact. Daily-only and no-intraday are component
removal checks, not separately optimized strategies.

These are retrospective counterfactual comparisons on already accessed periods.
They inherit P11's per-fold training eligibility, separate warm-up trajectories,
and selected-recipe bias. They neither establish independent significance nor
produce a realized portfolio switching between fold selections. Cost stress
also changes the warm-up holdings/capital path. Results are diagnostic evidence,
not a new promotion or execution authorization.

## Reproduction

Run from `amat/` after installing the locked project dependencies:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --locked autoresearch patterns compare-components \
  --artifact outputs/genetic-patterns/2026-09-29-p9/artifact.json \
  --snapshot data/snapshots/alpaca-sip-shadow-2026-09-29-reconciled \
  --native-data data/research/genetic-patterns-p10-2026-09-29/native-15m.json \
  --p11-study outputs/genetic-patterns/2026-09-29-p11/study \
  --costs 2 5 10 20 --workers 6 \
  --output outputs/my-component-study
```

The runner registers source/data identities, input receipts and the complete job
list before evaluation. Its separate output directory contains immutable,
checksummed resumable receipts. Original P11 selections and feature caches are
read-only inputs. P11 numerical source files and dependencies must still match;
the additive CLI change is separately recorded. Embedded feature metadata must
match its sidecar. A changed runtime or input refuses resume.

The next distinct engineering step is a portfolio transition simulation that
carries holdings, capital, entry bases and outstanding exit/embargo state across
annual recipe changes and explicitly charges switching costs. Concatenating
these separately warmed fold returns is not that simulation.
