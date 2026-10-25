# Asset-specific volatility percentiles — 2026-10-19

**Decision: reject all six candidates; keep all-groups + volatility-triggered cost-aware switching as overall default.**

## Preregistered design

Each asset’s current volatility is the sample standard deviation of its latest 20 completed daily log returns, annualized by sqrt(252). Compare it strictly against the linear quantile of that same asset’s previous 252 daily volatility observations, excluding the current observation. The fixed trigger percentiles are 80, 90 and 95. No activation until all 252 prior 20-return observations exist: first possible row index 272. All measurement accounts start on the same date; until sufficient history exists, candidate sizing stays at the incumbent rule.

Each percentile is tested with two unchanged sizing rules from the prior asset study. A triggered selected asset’s score is multiplied by 20% / its native annualized volatility. The 20% is the incumbent portfolio volatility target, not a fitted reference. Reduction-only caps the multiplier at 1; relative sizing allows increases for lower-volatility assets. Unselected assets remain unselected; other asset scores remain unchanged. Shared covariance risk targeting and caps can still change final weights throughout the portfolio.

No fixed 25% or 20/60 volatility-ratio threshold is used for the candidate sizing trigger. The incumbent volatility-triggered cost-aware switching rule remains unchanged; this study isolates sizing triggers. Eligibility, group admission, rebalance bands, execution limits and corporate-action handling also remain unchanged. Completed-close conditions trade from the following session. Six fixed hypotheses were frozen before results, without follow-up tuning.

## Full-history results

January 3, 2017–October 12, 2026, following 2016 warmup. Cumulative returns after assumed one-way costs; drawdown positive magnitude.

| Approach | Return 2 bps | Return 6 bps | Return 10 bps | Drawdown 6 bps | 2020 onward 6 bps | 2023 6 bps | Fills 6 bps |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current best · all groups + volatility switching | 290.52% | 264.58% | 226.81% | 12.77% | 198.77% | 5.79% | 1943 |
| Own 80th percentile · reduce only | 277.30% | 244.92% | 218.44% | 12.80% | 185.82% | 6.17% | 1988 |
| Own 80th percentile · relative sizing | 275.17% | 232.07% | 205.26% | 13.47% | 178.99% | 5.52% | 2077 |
| Own 90th percentile · reduce only | 274.80% | 251.43% | 221.11% | 12.57% | 190.44% | 6.11% | 1973 |
| Own 90th percentile · relative sizing | 266.83% | 242.52% | 219.59% | 12.54% | 185.68% | 5.29% | 2013 |
| Own 95th percentile · reduce only | 275.13% | 247.97% | 223.84% | 12.41% | 188.73% | 5.79% | 1976 |
| Own 95th percentile · relative sizing | 273.23% | 237.45% | 212.37% | 12.41% | 182.77% | 4.93% | 1991 |

The 90th-percentile reduction-only candidate had the highest return at the central 6bps assumption: 251.43% versus 264.58%, a 13.15 percentage-point shortfall. It improved full-history drawdown from 12.77% to 12.57% and 2023 from 5.79% to 6.11%, but lost 2020-onward return (190.44% versus 198.77%). The 80th percentile leads candidates at 2bps, while the 95th percentile leads at 10bps; no candidate dominates across assumptions. All six lose full-history returns at all three costs and all lose 2020-onward returns at all costs.

Acceptance was fixed before results: match/exceed the parent’s full-history AND 2020-onward net returns at every 2/6/10bps; no worse full-history drawdown at 6bps; preserve 2023 at every cost; at least one strict return gain. No candidate passes. No rejected active strategy profile was created or retained. Research recipes and frozen measurements remain for audit/reproduction.

## Stress returns at 6 bps

| Approach | 2018 Q4 | COVID window | 2022 |
|---|---:|---:|---:|
| Current best · all groups + volatility switching | -7.08% | -9.47% | 2.42% |
| Own 80th percentile · reduce only | -7.30% | -9.01% | 1.90% |
| Own 80th percentile · relative sizing | -7.34% | -8.32% | 1.19% |
| Own 90th percentile · reduce only | -7.21% | -9.01% | 1.90% |
| Own 90th percentile · relative sizing | -7.27% | -9.02% | 1.87% |
| Own 95th percentile · reduce only | -7.26% | -9.02% | 2.19% |
| Own 95th percentile · relative sizing | -7.27% | -9.29% | 2.23% |

## Asset activation rates

Fractions of all measured completed decision days, whether the asset was held or selected. Only selected assets can actually have scores changed. The first insufficient-history days count as inactive; no shorter measurement period is selected. Both sizing variants use identical triggers. These rates need not equal exactly 20/10/5%, because each threshold rolls through changing volatility regimes.

| Asset | 80th percentile | 90th percentile | 95th percentile |
|---|---:|---:|---:|
| DBC | 20.84% | 12.70% | 6.51% |
| EEM | 22.83% | 12.78% | 6.51% |
| EFA | 22.14% | 10.87% | 5.41% |
| GLD | 21.82% | 13.59% | 8.47% |
| IEF | 21.90% | 12.17% | 7.41% |
| IWM | 19.37% | 10.91% | 6.84% |
| QQQ | 23.44% | 14.49% | 9.08% |
| SLV | 24.30% | 14.73% | 10.22% |
| SPY | 20.96% | 12.94% | 7.65% |
| USO | 17.99% | 11.03% | 8.75% |
| UUP | 19.09% | 12.45% | 8.47% |

## Context from the previous fixed-trigger study

The previously sealed fixed-volatility reduction-only arm returned 226.84% at 6bps. The new 90th-percentile reduction-only arm returned 251.43%, improving on that earlier failed idea but still below the current best. This is context from a separate frozen study, not an extra newly measured control or a retention benchmark. Both studies use the same verified data and incumbent parent. USO’s trigger frequency fell from roughly 66.1% under the prior fixed volatility rule to 11.0% at its own 90th percentile; SLV’s fell from 44.6% to 14.7%. This confirms more comparable asset-relative trigger frequency, not profitability or causal P&L attribution.

## Dashboard and default

The dashboard opens on the percentile study. It shows all seven approaches in the visual audit, all three costs, annual and stressed-market comparisons, and an eleven-asset by three-percentile heatmap. Failed candidates cannot be selected as active strategies. Historical studies remain available.

The leading strategy remains `amat_group_selection_volatility.toml` (v14.3.0), verified as overall default with unchanged raw authorization identity. Execution remains disabled and dry-run. A sealed default-selection receipt records no retained challenger.

## Execution and validation limits

Verified Alpaca SIP eleven-ETF core snapshot; flat $100,000 January 3, 2017; full 2016 signal warmup; next-session observed 09:30–09:35 VWAP; dividends/splits, whole shares, native 1% opening-volume entry cap and recorded missed fills. Native exit policy unchanged. Zero cash yield. The 2/6/10bps combined spread/commission/impact costs per side are assumptions, not measured historical quotes.

Annual 2020–2026 slices use continuing accounts rather than yearly resets or fresh model fits. All history has prior research exposure. Rolling quantiles use past observations only, but this is not untouched forward validation. The 2023 test is a preservation guard. Bootstrap intervals are descriptive and unadjusted for the accumulated research search. No global optimality or future profitability is established.

The native baseline reproduces every parent session at all costs. Canonical seals and actual data partition bytes are verified. Tests compare quantiles to an explicit prior-only reference, enforce startup timing and flat-price behavior, test future-price and asset isolation, and verify cached/full-history allocation equals recomputation from each visible prefix. Baseline inactive fields preserve historical serialization. The gate is computed once in the shared native feature cache and reused by decisions; ordinary planning and research use the same code.

## Reproduce

```sh
amat/.venv/bin/python amat/scripts/run_percentile_sizing_study.py --output /new/immutable/output
```

Evidence: `amat/outputs/analysis/2026-10-19-percentile-sizing/study/`. Protocol, 21 trials, decisions, activation and default-selection receipt are sealed. Source/config copies were frozen before measurement.
