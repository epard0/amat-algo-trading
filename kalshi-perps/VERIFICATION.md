# Implementation verification — October 19, 2026

Delivered entirely within the independent `kalshi-perps` package. No strategy or
operating mode in `amat`, `executor`, or `frontend` was changed by this work.

## Forward data and strategy checks

The former blocker was missing intervals in native Kalshi trade-candle history.
The default now uses one continuous Kraken perpetual signal series per asset,
with actual Kalshi books, marks and funding for paper execution. Binance's futures
REST API returned HTTP 451 from this environment; it was not bypassed.

Read-only public data checks and forward **paper** observations confirmed:

| Underlying | Kraken symbol | Completed hourly rows | Missing intervals |
|---|---|---:|---:|
| BTC | PF_XBTUSD | 2,160 | 0 |
| ETH | PF_ETHUSD | 2,160 | 0 |
| DOGE | PF_DOGEUSD | 2,160 | 0 |
| SOL | PF_SOLUSD | 2,160 | 0 |
| NEAR | PF_NEARUSD | 2,160 | 0 |
| UNI | PF_UNIUSD | 2,160 | 0 |

All nine configured profiles completed an observation with **state=running** and
**ready=true**: DOGE/SOL balanced, lower drawdown and maximum training P&L; DOGE,
SOL, NEAR, UNI and BTC single-asset selections; and the original BTC/ETH reference.
Flat signals remained flat. The reference generated a simulated ETH order against
an actual Kalshi book, and `stop --strategy btc-eth-reference --flatten` closed the
paper position to zero. No demo/live venue orders were sent. No continuous runner
was left active.

Coverage, normalized-data checksums, source symbols, readiness and paper order
counts are retained in [FORWARD_DATA_VERIFICATION.json](FORWARD_DATA_VERIFICATION.json).
Full original receipts and immutable observed candles are in the ignored package
history cache. Every profile pins actual research source hashes and its frozen
selection artifact where applicable. Partial/trailing-exit experiments that were
still in progress were not promoted.

## Automated verification

- Package suite: **52 tests passed**. Coverage includes real research-engine calls,
  reference/profile parity, single-asset and multi-asset profiles, completed-bar
  causality, missing history, source pins, immutable profiles, parameter rejection,
  provider pagination/open-candle filtering, coverage, incremental cache anchors,
  revised-history rejection, contract normalization, independent configurations,
  public API, and the existing accounting/venue failure-path tests.
- Ruff lint and formatting checks passed for source and tests.
- Source distribution and wheel build passed.
- CLI remains exactly **run, status, stop**; strategy selection is an option.
- Existing persistent replay, atomic failure rollback, liquidation deficits,
  funding deduplication/historical-position backfill, IOC/FOK/depth, stop/flatten,
  signing, pagination, environment isolation and unknown-ack tests remain passing.

## Limits of the evidence

Ninety days of observed bootstrap history establishes present readiness, not exact
full-history research state or a strategy edge. Kraken prices can generate different
signals from the prior Binance research proxies. The package uses allocation-weighted
targets in a shared collateral account rather than reproducing independently
compounding research sleeves. Paper margin/liquidation and REST polling retain
the documented approximations. Live qualification, actual demo reconciliation,
measured execution tolerances and explicit limited-live activation remain separate
gates. Templates keep venue execution disabled.

## Running paper campaign

A supervised nine-account forward comparison was started on October 19, 2026.
Each account has $10,000 initial virtual equity in fresh state, separate from prior
smoke tests. Maximum-leverage sizing uses published side/size curves and the
initial-margin multiplier, with a 2% execution reserve. All nine accounts reported
running across successive background cycles. Live execution is disabled in every
campaign configuration; the process has neither Kalshi live credential variable.
The user service is active with restart on failure. Details and current reports
are linked in PAPER_COMPARISON.md.

Expanded package suite: **57 tests passed**. Added checks cover dynamic leverage
above the former 2x policy, side/size and initial-margin constraints, same-day
resizing, net take-profit episode accounting including late funding, and fresh
isolated paper-only campaign configurations. Lint and formatting passed.
