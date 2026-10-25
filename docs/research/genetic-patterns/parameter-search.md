# Multi-timeframe portfolio parameter research

P10 connects explicit native 15m and session-open hourly views to the bot's
existing **offline portfolio runner**, alongside daily, weekly and monthly
features. It adds broad parameter search, local refinement, separate chronological
selection stages and post-selection return resampling. It does not enable live
orders or change a suspended campaign.

## Source and decision contract

Native 15m bars retain their own receipt and semantic contract. An hourly bar
requires four consecutive native 15m components on the 09:30 exchange-session
grid. Missing components produce unavailable rows. Native clock-hour receipts
are not relabelled. Only completed regular-session bars are used.

Each timeframe's most recent completed value is aligned to the daily decision
close. Intraday values never carry from a previous session. Weekly/monthly values
carry only until the next expected period completes; missing replacement values
remain unavailable. Source markers distinguish native research panels from strict
minute-derived panels. The symbolic evaluator requires the matching contract.

The blend adds at most 0.25 score points to existing positive baseline scores.
Zero entry gates and unavailable baseline scores remain intact. Fixed normalized
weights combine the five views; an unavailable view contributes zero without
redistributing its weight. Eligibility for each feature/timeframe/asset is fixed
using training data only, requiring at least 20 finite daily decision observations.
Those repeated daily decisions are not independent weekly/monthly observations;
actual source-bar reference statistics and counts are retained separately.

The offline adapter preserves corporate actions, holdings, portfolio constraints,
selection, existing overlays, the 1% entry participation cap, costs and execution
lag. Zero-effect baseline parity is tested against the existing `Simulation`.
The new path is explicitly research-only; it grants no campaign reset or promotion.

## What the exits mean

The existing daily exit engine measures the adjusted return from the opening
position's basis. A close breaching a stop or take-profit target schedules an exit
for the next execution window. Position resizing does not reset the original
basis; the re-entry embargo prevents immediate re-entry after a breach.

A reward/risk ratio means `take_profit_fraction / stop_loss_fraction`. It is a
ratio of trigger thresholds, not a promised realized payoff. Gaps, execution lag
and costs can change the realized result. This study does not simulate intrabar
bracket orders, touch-price fills, or partial take-profit ladders. Those require
a separate execution contract; the existing minute simulator also uses different
close-trigger/next-open and position-accounting assumptions.

## Search domain

The default extended search evaluates one exact baseline, 2,048 global proposals,
and 256 local proposals. This is 2,305 training proposals before deduplication.

| Dimension | Initial declared domain |
|---|---|
| Feature method | Retained symbolic GP expression plus all 27 symbolic terminals at 21 and 63 bars: 55 choices |
| Feature polarity | Positive or negative |
| Timeframe weights | 15m, 1h, 1d, 1w, 1mo; independent [0,1] draws normalized to sum one |
| Maximum MTF effect | 0 to 0.25 score points |
| Exit mode | No exits, stop only, profit only, both |
| Stop fraction | 0.5% to 20% |
| Profit/stop ratio | 0.5 to 8; profit trigger is their product |
| Re-entry embargo | Rounded 1 to 42 daily decisions |
| Risk scalar | 0.25 to 1 |
| Trend deadband | 0.05 to 0.5 |
| Trend clipping | 0.5 to 3 |
| Trend horizons | [5,21,63], [21,63,126], [21,63,252], plus the exact baseline [63,126,252] |
| Fast volatility half-life | 5, 10, 20, 40 sessions |
| Slow volatility half-life | 45 or 60 sessions |
| Slow volatility weight | 0.25 to 1 |
| Minimum rebalance weight change | 0.005 to 0.05 |
| Annualized volatility target | 0.08 to 0.25 |

The retained spec is authoritative. Costs are fixed at 2 bps one way during
selection, rather than optimized as though favorable fill assumptions were a
strategy parameter. Post-selection stress evaluates higher costs separately.
Universe, accounting, source records and corporate actions are not search knobs.

The broad search samples uniformly from declared numeric/categorical domains.
Local refinement chooses among training elites, perturbs numeric values with a
shrinking Gaussian scale, and mutates categorical choices with 25% probability.
It is a derivative-free local search, not mathematical gradient descent. No finite
random sample exhausts this continuous, combinatorial space or proves a global optimum.

The fixed objective is mean four-fold Sharpe minus half its fold standard deviation
minus twice absolute maximum drawdown. Configurations with fewer than 20 fills,
incomplete statistics or runner warnings are ineligible. All folds are chronological
training slices; this objective does not claim statistical significance.

## Selection, checkpoints and Monte Carlo

1. Register source/dependency hashes, snapshot manifest and partitions, native
   receipt, dates, domains, objective assumptions and seed.
2. Build causally computed feature caches with source-bound metadata and frozen
   training eligibility. Full future arrays are never used to fit eligibility.
3. Evaluate every proposal on training only. Local refinement reads training
   scores, never validation or test scores.
4. Freeze training top 12 plus the baseline before opening validation.
5. Select one by validation score. Evaluate that selection plus baseline on the
   final chronological test period for reporting only.
6. After selection, report MTF/exit and individual-timeframe removal controls, preserving remaining contribution coefficients, and a cost ladder. Resample paired
   realized daily return blocks at lengths 5, 20 and 60, 5,000 paths each.

The final step is a **conditional return bootstrap**, not a fresh strategy replay
on synthetic market prices and not a selection-adjusted confidence interval.
A joint price-path Monte Carlo would need to resample consistent intraday/daily
OHLCV and corporate-action histories, then recompute all features and decisions.
Reusing fixed historical intraday signals on scrambled daily prices would be invalid.

Every trial has an immutable checksummed receipt. The same command resumes the
same search and reuses completed evaluations. Changed source, dependency, data,
seed, dates or domains require a new output directory. Writer locks protect both
feature preparation and search. Atomic cache publication can recover an interrupted
metadata-sidecar write without replacing published arrays.

## Commands

From `amat`:

```sh
uv run --locked autoresearch patterns search-parameters \
  --artifact outputs/genetic-patterns/2026-09-29-p9/artifact.json \
  --snapshot data/snapshots/alpaca-sip-shadow-2026-09-29-reconciled \
  --native-data data/research/genetic-patterns-p10-2026-09-29/native-15m.json \
  --measured-start 2020-01-02 --train-end 2023-12-29 \
  --validation-end 2024-12-31 \
  --global-trials 2048 --local-trials 256 --workers 6 \
  --seed 20260929 --output outputs/my-parameter-study
```

The generic `run_parameter_search` accepts another finite-domain spec and evaluator,
so additional methods can share the same checkpoint and selection discipline.
`evaluate_recipe(..., cost=...)` supports post-selection cost stress after the study
initializes its context. The retained reproduction driver also writes ablations
and return-bootstrap diagnostics.

## Scope of “all implemented methods”

This pass covers all 27 registered symbolic feature kernels, their candle-derived
features, the retained GP expression, all five timeframes and the listed portfolio
and exit parameters. It evaluates the existing V13 portfolio through its real
backtester. It does not equate every model or execution mode with a tunable scalar.

Fresh candle/symbolic GP evolution, learned ridge/router/entry policies, learned
risk exits and alternative portfolio universes require their own nested training
and data contracts. Learned risk episodes must be fully matured before training
cutoffs. The separate minute engine, shorting, trailing stops and intrabar order
semantics must not be silently spliced into the daily runner. The study context
records these boundaries explicitly rather than claiming they were measured.

Earlier phases already accessed this history. Chronological separation prevents
new feedback across phases but does not make the final period historically untouched.
Results remain retrospective research, including when a configuration beats the
baseline. An ideal setting depends on the objective, costs, universe and future
regimes; the output is a measured candidate within a declared search, not approval.

The post-selection ablations and cost controls replay their recipes over the
full history, then measure the test slice. They can change inherited holdings,
entry bases, embargo state and capital at the test boundary. They are
full-history counterfactual comparisons, not interventions initialized from an
identical test-start portfolio.
