# P11 diversified walk-forward parameter research

P10's training-selected finalists failed to beat the baseline in validation. P11
repeats the search in rolling windows, limits the dominance of any one method
family in the shortlist, and reports three distinct performance preferences.
It reuses the actual offline portfolio runner and P10's parameter domains.

## Registered historical folds

| Fold | Training window | Validation year | Outer reporting period |
|---|---|---|---|
| outer-2024 | 2020–2022 | 2023 | 2024 |
| outer-2025 | 2021–2023 | 2024 | 2025 |
| outer-2026 | 2022–2024 | 2025 | 2026 through September 25 |

The exact exchange-session boundaries are in `p11-folds.json`. Measurement starts
with the return after its anchor session. Features and portfolio state warm up
from earlier retained history. Outer intervals must not overlap. Each window gets
640 fresh global proposals, 128 training-only local proposals and an exact baseline:
769 proposals per fold, 2,307 overall. Seeds and protocol are frozen before evaluation.
P10's selected recipes, outcomes and eligibility masks do not seed this search.
Every eighth global proposal explicitly disables the MTF overlay, giving 80
portfolio-only global proposals per fold with other parameters still sampled.

## Feature and family contracts

All 55 feature variants and 15m/1h/1d/1w/1mo views remain available. A new shared
feature bank stores raw causal values without P10's eligibility masks. Every fold
freezes eligibility using only finite daily decisions in its own training window.
Missing values contribute zero without redistributing their coefficient. Weekly
and monthly source observations remain distinct from repeated carried daily values.

Method families separate the retained GP expression, trend/breakout, momentum,
volatility, liquidity and candle features. A recipe with zero MTF effect belongs
to the portfolio-only family, regardless of its unused feature label. The exact
mapping is implemented and validated by `method_family`.

Each eligible family supplies a union of training candidates: best consistency
score, highest annualized return, and lowest drawdown from its return/drawdown
Pareto frontier. A candidate is dominated only when another has no lower return,
no larger drawdown, and a strict improvement in at least one. Ties use recipe hashes.
The baseline is always retained. A family with no eligible recipes contributes
none; missing families and their coverage remain visible.

## Three frozen validation policies

- **Balanced:** highest consistency score, using the P10 four-slice Sharpe and
  drawdown objective.
- **Return:** highest annualized return within the bounded portfolio search
  domain. It has no additional baseline-drawdown gate. Extra return may come with
  extra risk; this policy does not claim dominance over the baseline.
- **Defensive:** smallest drawdown among eligible recipes whose validation
  annualized return meets or exceeds the baseline's on identical dates and costs.

All policies are registered before validation. Validation selections are persisted
before their union and baseline are evaluated on the outer period. Outer results
cannot choose a new profile or recipe. Pareto frontiers and parameter frequencies
are descriptive diagnostics, not another optimization round.

## Accounting and interpretation

Every recipe replays the full preceding history with its own holdings, entry
bases, embargo state and capital; returns are then measured in the named window.
These are conditional warm-state counterfactuals. Reports compare each fold with
its matching baseline. They do not concatenate selected returns into a supposedly
executed switching portfolio; changing between recipes would require explicit
holdings transitions, turnover and costs.

Costs remain 2 bps one way, with the existing corporate-action, execution-lag and
liquidity accounting. Stops/profits retain daily-close trigger and next-window
execution semantics. The analysis is offline and grants no promotion or execution
authority. The suspended P7 campaign and historical rejections remain unchanged.

Training windows overlap, and all periods were accessed in prior research. Three
annual outer folds provide descriptive repeatability, not independent replications,
selection-adjusted significance or pristine prospective evidence. Parameter
stability excludes unused feature/exit placeholders; a repeated baseline selection
is not evidence that its stored inactive MTF weights are optimal.

## Commands and reproducibility

From the `amat` directory:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --locked autoresearch patterns walkforward-parameters \
  --artifact outputs/genetic-patterns/2026-09-29-p9/artifact.json \
  --snapshot data/snapshots/alpaca-sip-shadow-2026-09-29-reconciled \
  --native-data data/research/genetic-patterns-p10-2026-09-29/native-15m.json \
  --folds docs/genetic-patterns/p11-folds.json \
  --global-trials 640 --local-trials 128 --workers 6 --seed 20260929 \
  --output outputs/my-walkforward-study
```

Source files, dependency versions, data, feature contracts, folds and domains bind
resume identity. Completed receipts are immutable and checksummed. A changed
context needs a new output directory. Unexpected failures abort; ineligible or
invalid evaluations remain recorded. Existing P10 artifacts remain untouched.

Fresh GP evolution, fitted routers/entry models, learned exits, different universes
and joint synthetic-price replay still need their own nested fitting and data
contracts. P11 measures repeatability of the current feature/portfolio parameter
space; it does not silently incorporate those other model families.

This remains a bounded search over a much larger continuous space. Family
diversification reduces shortlist concentration; it does not equalize all family
sampling budgets, establish an optimal Pareto frontier, or correct historical
selection effects. Failure to beat the baseline applies to this registered
search, not to every possible MTF configuration.

The comparisons evaluate complete recipes with joint portfolio, exit and MTF
changes. Outperformance of a selected recipe does not isolate an incremental
MTF benefit; matched component-removal controls are needed for that attribution.
