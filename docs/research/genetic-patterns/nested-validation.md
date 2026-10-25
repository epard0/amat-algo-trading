# P4: nested symbolic search validation

Implemented 2026-09-28. `autoresearch patterns validate-search` repeats the P3
symbolic discovery and portfolio-selection pipeline inside historical outer
folds, then repeats that whole adaptive procedure under a registered feature-time
alignment null. Reports explicitly distinguish a completed experiment from a
method that passes its historical-support gates. Execution and promotion authority
remain `none`. This command currently supports the symbolic grammar; P1 candle
search remains available through its existing discovery/replay commands.

## Three isolated stages per fold

1. **Fit:** run fresh genetic and equal-budget random searches for every registered
   seed. The fit input physically ends at `fit_end`. Event labels enter at t+1
   and exit at t+2, so slicing before label construction purges all labels crossing
   the boundary. Rank distinct patterns across seeds by training-screen score and
   freeze the top K per method. Record every slot, failure and cache hit.
2. **Inner selection:** replay every shortlisted pattern on the separate interval
   after `fit_end` through `selection_end`. Authoritative AMAT accounting,
   exposure-matched incremental Sharpe, node complexity, existing quality gates,
   6-bps costs and a one-session delay determine eligibility. Select the best
   positive eligible score per method; otherwise retain the incumbent. Write
   `selection.json` before constructing the outer simulation.
3. **Outer measurement:** evaluate that frozen choice after `selection_end`.
   Check selection-period accounting prefix parity. Preserve the full market
   calendar and record paired candidate/incumbent daily returns, stress/delay
   results, warnings, fixed body/ATR and bullish-candle controls, remove-pattern
   parity, leaf removals and bounded strength perturbations. Diagnostics never
   reselect a pattern.

The existing inner quality gate requires at least two qualifying calendar-year
folds. The registered example therefore uses two-year inner windows. A shorter
window may mechanically reject every contender; the command does not relax that
gate after seeing results. The default effect cap is 0.25. A requested 1.2×
strength perturbation at that cap is recorded explicitly as unchanged rather
than being presented as evidence of robustness to a larger effect.

Every fold is an independent full-history replay from `load_start`. The selected
recipe inherits its own hypothetical pre-selection holdings into the outer
interval. This does **not** simulate the transition from the incumbent's actual
holdings when the selection changes. Pooled fold returns are conditional
historical diagnostics, not a continuous executable switching equity curve.

## Whole-search alignment null

The null moves complete normalized feature rows jointly across assets using
circular shifts. Fit, inner-selection and outer intervals receive separate
shifts; no donor crosses a split boundary. Every method and seed in a replicate
uses the same mapping. Feature-unavailable rows remain unavailable and in place.
Prices, OHLC geometry, execution volumes, corporate actions and accounting never
move. The parent freezes all mappings before any discovery outcome.

For every replicate, rerun all searches, shortlist selection, authoritative inner
selection, incumbent fallback and outer selected-policy measurement. Null feature
observations may come from later rows within their own split; this is an explicitly
noncausal offline counterfactual, not an executable strategy. The null preserves
joint feature rows and price paths, but destroys their time alignment. It does
not preserve feature/return volatility-regime relationships. Exchangeability and
stationarity remain assumptions; the resulting p-values are conditional alignment
diagnostics, not universal significance or promotion approval.

The predeclared statistic is `252 * mean(candidate daily return - incumbent daily
return)` pooled over nonoverlapping outer dates. This arithmetic annualized excess
is not CAGR. Each null replicate contributes the maximum selected-policy statistic
across the genetic and random methods. Both observed methods are compared with
that same maximum distribution to account for the declared two-method family.
Tail p-values use `(1 + exceedances including ties) / (B + 1)`. Reports include
resolution and a Wilson interval for the underlying Monte Carlo exceedance rate.
The interval is not a confidence interval for economic profitability.

Incumbent fallback is a valid selected policy with exactly zero excess. A crashed,
corrupted or nonfinite null run is **not** zero: it leaves the experiment incomplete
and prevents statistical publication. Failed genotypes and ineligible seeds are
retained in their ledgers and consume the registered search budget.

## Paired uncertainty and support gates

A paired circular moving-block bootstrap uses identical resampled dates for the
candidate and incumbent. It resamples each outer fold separately, preserving its
sample count and never crossing a fold boundary. It reports 95% intervals for
annualized mean excess and Sharpe difference. Missing/nonfinite returns are
rejected, not silently deleted; zero-variance Sharpe is explicitly unavailable.
This bootstrap is conditional on the selected patterns and complements rather
than replaces the complete-search null procedure.

Historical-support gates require all of:

- At least two outer folds and at least one selected incremental policy.
- The registered minimum number of outer dates with absolute paired return
  difference greater than 1e-12; the same configured floor also applies to fit
  screen effect support, with each role disclosed separately.
- Positive mean outer excess and a paired mean-excess interval wholly above zero.
- Family-adjusted alignment-null p-value at most 0.05.
- Positive mean outer-fold Sharpe improvement at both 6-bps cost and one-session
  delay, against the incumbent under the corresponding intervention.
- No outer accounting/runner warnings.

Passing these gates means conditional historical-development support only.
Broader researcher choices, held-out asset-group discovery, truly untouched final
history, actual switching costs and shadow operation remain unassessed.

## Freeze, failure and resume

The parent protocol binds the incumbent, snapshot/calendar, actual panel contents,
fit datasets, registry and formula source, runtime versions, action-exception
ledger, seeds, splits, shortlist size, support gates, null mappings and total slot
budget before results. Candidate slots are capped at two million, and materialized
null mappings at twenty million cells. Source and inputs are checked again before
publication.

Each search stores resumable population checkpoints. Each completed fold has a
checksum and a manifest of every child artifact. `--resume` requires identical
protocol and data; completed folds are verified and reused. An interrupted
portfolio fold must reproduce the exact saved inner selection before proceeding.
Changed source, mappings, action exceptions, market data or completed evidence
fail closed. Progress and failures are written to `progress.json`.

## Registered engineering run

Run from `amat/`:

```bash
uv run --locked autoresearch patterns validate-search \
  --protocol configs/genetic/p4-smoke.json \
  --incumbent configs/strategy/amat_v13_return_target.toml \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --output outputs/genetic-patterns/2026-09-28-p4
```

Repeat exactly with `--resume` after interruption or to verify cached completion.
Use a fresh output directory for a changed protocol.

The checked-in plan uses the incumbent's complete 11-ETF universe, start
2016-01-04, and the same four normalized terminals as P3. Each search has population
16, one generation after initialization, depth three edges and at most 15 nodes.
Both methods use seeds 11 and 29; each method shortlists two distinct patterns.

| Fold | Fit through | Inner selection returns | Outer returns |
|---|---|---|---|
| outer-2021 | 2018-12-31 | 2019–2020 | 2021 |
| outer-2022 | 2019-12-31 | 2020–2021 | 2022 |

Two methods × two seeds × 32 slots × two folds × (one observed + three null
pipelines) = **1,024 candidate slots**. Paired uncertainty uses 1,000 replicates
and 20-session blocks. Three null replicates imply minimum p=0.25: this is an
engineering verification, incapable of passing the 0.05 null gate. Increase the
budget only in a newly frozen experiment; never extend a run selectively until
its p-value becomes favorable. At least 19 null replicates are needed even to
attain 0.05, and that remains coarse Monte Carlo evidence.

The incumbent and historical periods were already used in earlier research.
None of these dates is relabeled a pristine holdout. See
[p4-verification.md](p4-verification.md) for actual results, tests and evidence.
