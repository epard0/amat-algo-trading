# AMAT v6 design and latest-backtest trade audit

Measured 2026-08-13 on immutable snapshot `alpaca-sip-2026-08-11`. This is
burned-history research, not out-of-sample evidence and not a promise of safe
or future profits.

## Evidence reviewed

The latest report by completion time is
`outputs/reports/amat_v1_study_15mticker.pdf`, backed by run
`outputs/runs/study-amat-etf-20260813T205833.153459Z/{manifest,results}.json`.
Its underlying immutable dataset is `data/snapshots/alpaca-sip-2026-08-11/`:

- `manifest.json` identifies the provider/feed, dates, coverage, and partition hashes;
- `daily.parquet` supplies adjusted feature prices and raw valuation marks;
- `window.parquet` supplies next-session 09:30–09:35 raw VWAP and volume;
- `actions.parquet` supplies dividends and splits;
- `data/interim/alpaca-sip-2026-08-11.windows.json` is the ingestion-side window record.

The report JSON retains aggregate asset statistics but not individual trades.
The v6 audit therefore replayed the exact snapshot and persisted every
reconstructed round trip plus its decision inputs in
`outputs/v6/v6-audit-20260813T221018Z.json` (2.2 MB, 860 v1 trades and 881 v6
trades).

## Latest v1 result

| Metric | v1 latest |
|---|---:|
| Total return | +85.24% |
| Annualized return | +6.65% |
| Annualized volatility | 6.59% |
| Sharpe | 1.01 |
| Max drawdown | −8.69% |
| Fills / missed | 3,643 / 195 |
| Costs | $4,711.58 |
| Round trips | 860 |
| Win rate | 32.7% |
| Profit factor | 1.81 |
| Average / median trade | +$78.81 / −$38.05 |

TLT and IEF are the only losing portfolio sleeves: TLT −$3,628, 38.7% wins,
profit factor 0.64; IEF −$1,970, 42.2% wins, profit factor 0.73. IEF also paid
$757 in costs against a −$1,213 gross P&L, so turnover materially deepened its
loss.

## How and why v1 made each trade

Every one of the 860 reconstructed round-trip entries began with a
`position_transition`; later partial changes were rebalances and are coalesced
into their originating trade.

On completed session *t*, for each asset, v1:

1. computes adjusted-close log returns over 21, 63, and 252 sessions;
2. divides each by causal EWMA volatility and the square-root-of-time scale;
3. clips each normalized return to ±2, averages them, and applies `tanh`;
4. sets scores inside ±0.20 to zero and clamps negative scores to cash;
5. sizes positive scores inverse to volatility, scales the portfolio toward an
   8% volatility target, then applies symbol, cluster, and gross caps;
6. emits an entry when target weight changes from zero to positive; and
7. fills no earlier than the next session's 09:30–09:35 raw VWAP, paying 2 bps
   one way.

This explains apparently contradictory entries such as DBC on 2025-03-25:
the combined score was still +0.242 because its 63- and 252-session returns
were +6.9% and +2.1%, even though its 21-session return had already turned
−2.0%. The trade reversed within eight sessions and lost $1,527 in v1. TLT on
2025-04-02 was similar: 21-session −0.8%, but 63/252-session +6.1%/+1.1%, score
+0.257; it lost $1,289 in five sessions.

## Evidence-backed hypotheses for poor trades

### 1. Residual whipsaw is the largest loss mechanism

506 of 860 v1 round trips lost money within 21 sessions, for −$69,484 in
aggregate. This is not equivalent to “the strategy is bad”: the median is
negative while the mean and profit factor are positive, the classic trend
payoff where a minority of long winners funds many small reversals. Optimizing
for win rate or truncating winners with tight take-profits is therefore a
dangerous objective.

### 2. Horizon disagreement often precedes the largest losses

Several worst trades entered with short or medium returns already negative
while the 252-session return kept the equal-weight composite barely above the
deadband. Entries with all three horizons positive had +0.19% average
round-trip return; mixed and short/medium-negative groups averaged −0.23% and
−0.27%. This is a plausible confirmation feature, but it is not promoted: the
groups were discovered on the same burned sample and the short/medium-negative
group still earned positive aggregate dollars through its right tail.

### 3. High-volatility ranges are hostile, but timing them did not add value

The v6 four-quadrant detector combines ATR percentile, Bollinger-width
percentile, ADX, EMA slope/price position, efficiency ratio, CUSUM change
points, and three-session label confirmation. Baseline entries labeled
`volatile_range` were the weakest slice: 24 trades, 16.7% wins, about −$2,568.
However, the full causal regime/liquidity overlay scored Sharpe 1.04 versus
1.14 for its constant-6%-risk control. The slice is a useful warning and
diagnostic, not evidence that the detector should time capital.

### 4. The rates sleeve is structurally mismatched in this sample

TLT and IEF combined frequent entries, short average holds, low profit factors,
and negative net P&L. A slow multi-horizon price trend can re-enter during bond
countertrend rallies while the secular rate move resumes. Removing rates after
seeing this would be hindsight universe selection, so v6 retains the original
12 assets and reports the weakness rather than silently deleting it.

### 5. “High volume” is execution quality, not a proven alpha filter here

Top-volume-quartile entries had a 27.8% win rate versus 35.1% for the bottom
quartile. Both buckets earned positive aggregate P&L, and the top bucket earned
more dollars, but volume did not monotonically predict trade success. V6's
experimental overlay therefore uses dollar volume as a hard tradability check
and relative volume only as a soft 75–100% priority on increases. Because that
overlay lost to its static control, canonical v6 records these diagnostics but
does not let them control capital by default.

### 6. Costs worsen marginal signals

The latest v1 paid $4,712 across 3,643 fills. The 6% v6 core reduced fills to
3,488 and costs to $3,881 without adding an execution shortcut. This helps the
rates sleeves but does not make them profitable. Increasing the 2-percentage-
point rebalance band may reduce costs further, but it requires its own
registered sensitivity because it also allows larger target drift.

## v6 implementation and measured decision

Canonical v6 is a new config and loader; v1–v5 configs and artifacts are not
edited. It uses the original 12-asset universe and core with the 6% volatility
target that ranked first in registered experiment 0001 and held on that
experiment's later validation slice.

| Same sample, $100k, 2 bps | Total | Ann. | Vol | Sharpe | Max DD | Fills | Costs |
|---|---:|---:|---:|---:|---:|---:|---:|
| Latest v1, 8% target | +85.24% | +6.65% | 6.59% | 1.01 | −8.69% | 3,643 | $4,712 |
| **Canonical v6, static 6%** | **+77.97%** | **+6.21%** | **5.46%** | **1.14** | **−8.04%** | **3,488** | **$3,881** |
| v6 regime/liquidity overlay | +61.29% | +5.12% | 4.91% | 1.04 | −7.08% | 3,144 | $3,207 |
| first, over-restrictive overlay (rejected) | +20.08% | +1.93% | 2.69% | 0.72 | −5.56% | 1,214 | $1,079 |

The result is a trade-off, not dominance: v6 gives up 7.27 percentage points of
total return for lower realized volatility, 0.65 percentage points less maximum
drawdown, lower cost, and higher in-sample Sharpe. The Sharpe difference is
inside the sampling error already documented for this history. v6 is a research
candidate, not promoted.

The regime detector remains active as auditable decision context. Setting
`asset_regime.capital_control_enabled = true` explicitly reruns the rejected
overlay experiment; it is `false` in the canonical file.

## Per-asset reinforcement learning decision

V6 implements the defensible RL-sized problem: an offline episodic contextual
bandit, one per asset. Each base-strategy round trip is an episode; actions are
`no explicit exit` or stop losses {5%, 8%, 10%} crossed with reward/risk ratios
{1.5, 2.0, 3.0}. Intraday ambiguity is conservative (if a daily bar touches
stop and profit, the stop wins). Training rewards are shrunk toward no-exit.
Only the training winner is tested later, using paired episode improvements and
a one-sided 12-asset multiplicity-adjusted lower confidence bound.

**Result: zero of 12 per-asset rules activated.** Every lower confidence bound
was at or below zero. TLT and IEF in particular selected training candidates
but produced exactly no validation improvement before uncertainty was applied.
Canonical `risk_learning.enabled = false`; no learned stop or take-profit is
used. This is the intended fail-safe result, not a missing feature.

Deep RL is not justified by 20–60 independent training episodes per asset. It
would add policy capacity and thousands of comparisons without adding data.

## Required next evidence

1. Do not tune these thresholds again on data through 2026-08-10; it is burned.
2. Paper-run v6 on sessions strictly after the declared holdout boundary.
3. Re-evaluate the 6% target, regime overlay, and any per-asset exit only after
   enough genuinely new trades exist; keep no-exit and static-risk controls.
4. Run the full 2×/3×/5× cost ladder and Monte Carlo/parameter-neighborhood
   checks before any promotion decision.
5. Treat “safe profit” as an objective (smaller drawdown, positive expectancy,
   liquid execution), never as a guarantee.

