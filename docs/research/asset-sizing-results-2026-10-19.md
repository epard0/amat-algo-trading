# Asset-specific inverse-volatility sizing — 2026-10-19

**Decision: reject all six candidates; keep all-groups + volatility-triggered cost-aware switching as the leading strategy and overall default.**

## Fixed design

Each asset uses its own completed adjusted closes. The three fixed conditions are: close below its own 200-session mean; trailing 20-session annualized log-return sample volatility above 25% OR 20-session volatility above 1.5 times its own trailing 60-session volatility; either condition. Only selected assets can be resized. No QQQ-wide or group-wide sizing trigger is used. The existing incumbent volatility-triggered switching rule remains intact.

Two sizing rules for each condition create six independent candidates. A triggered asset score is multiplied by 20% divided by its native annualized volatility estimate. The 20% reference is the existing portfolio volatility target, not a fitted parameter. Reduce-only variants cap the multiplier at 1. Relative variants can favor lower-volatility assets. This ratio is dimensionless; applying raw inverse-volatility units to only some assets would introduce an arbitrary jump in score scale.

Untriggered asset scores are unchanged. Subsequent portfolio covariance targeting and caps can still rescale all final weights. Eligibility, group membership, switching, rebalance bands and execution are unchanged. No additional leverage permission or cap increase. All conditions are measured at a completed close, with execution from the next session. All six hypotheses and acceptance rules were frozen before measurement; no post-result threshold fitting.

## Measured results

Full history January 3, 2017–October 12, 2026, following 2016 warmup. Returns cumulative after assumed one-way costs; drawdown positive magnitude.

| Approach | Return 2 bps | Return 6 bps | Return 10 bps | Drawdown 6 bps | 2020 onward 6 bps | 2023 6 bps | Fills 6 bps |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current best · all groups + volatility switching | 290.52% | 264.58% | 226.81% | 12.77% | 198.77% | 5.79% | 1943 |
| Asset weak trend or volatility stress · reduce only | 265.74% | 226.54% | 215.14% | 12.19% | 177.06% | 5.42% | 1971 |
| Asset weak trend or volatility stress · relative sizing | 254.62% | 226.55% | 213.24% | 12.24% | 177.40% | 5.91% | 1975 |
| Asset volatility stress · reduce only | 266.25% | 226.84% | 215.42% | 12.19% | 177.05% | 5.42% | 1973 |
| Asset volatility stress · relative sizing | 257.82% | 225.82% | 214.53% | 12.19% | 175.92% | 5.42% | 1979 |
| Asset weak trend · reduce only | 290.21% | 263.03% | 226.23% | 12.77% | 198.85% | 5.79% | 1940 |
| Asset weak trend · relative sizing | 289.43% | 262.04% | 225.51% | 12.77% | 198.44% | 5.65% | 1930 |

Weak-trend reduce-only came closest: 263.03% versus 264.58% at 6bps, a 1.55 percentage-point loss. Its 2020-onward return slightly improved (198.85% versus 198.77%) and drawdown was effectively unchanged, but full-history return lagged at every cost. Every candidate lagged full-history return at all three costs.

Volatility-triggered reduction reduced maximum drawdown to 12.19%, a roughly 0.58 percentage-point improvement, while reducing full-history return by roughly 37.74 percentage points at 6bps. Relative sizing did not repair the return deficit. All six improved or matched the drawdown guard, but only weak-reduce and either-relative preserved 2023 at every cost.

Acceptance, fixed before results: match/exceed current-best full-history AND 2020-onward net returns at every 2/6/10bps; full-history drawdown at 6bps no worse; preserve 2023 at every cost; at least one strict return gain. No candidate passed. No active profile is retained for a rejected candidate. Recipe code and frozen evidence remain for reproducibility.

## Stress returns at 6 bps

| Approach | 2018 Q4 | COVID window | 2022 |
|---|---:|---:|---:|
| Current best · all groups + volatility switching | -7.08% | -9.47% | 2.42% |
| Asset weak trend or volatility stress · reduce only | -7.33% | -9.01% | 0.67% |
| Asset weak trend or volatility stress · relative sizing | -7.29% | -9.30% | 0.67% |
| Asset volatility stress · reduce only | -7.33% | -9.01% | 0.67% |
| Asset volatility stress · relative sizing | -7.26% | -9.30% | 0.67% |
| Asset weak trend · reduce only | -7.26% | -9.47% | 2.46% |
| Asset weak trend · relative sizing | -7.22% | -9.47% | 2.46% |

## Asset activation share of decision days

These are price-condition frequencies across all measured decision days, whether an asset was held or selected. Only selected assets can actually have scores changed. Both sizing rules use identical condition histories.

| Asset | Weak trend | Volatility stress | Either |
|---|---:|---:|---:|
| DBC | 38.62% | 10.99% | 46.11% |
| EEM | 32.36% | 16.00% | 40.50% |
| EFA | 24.05% | 8.55% | 26.70% |
| GLD | 28.57% | 8.71% | 35.61% |
| IEF | 43.71% | 0.69% | 44.40% |
| IWM | 29.87% | 21.20% | 36.55% |
| QQQ | 16.48% | 24.54% | 27.11% |
| SLV | 42.74% | 44.57% | 74.36% |
| SPY | 16.44% | 11.23% | 19.01% |
| USO | 43.06% | 66.06% | 74.97% |
| UUP | 33.17% | 0.85% | 34.03% |

USO meets the volatility condition on 66.1% of measurement days and SLV on 44.6%. These fixed thresholds therefore do not identify only rare stress for those assets. This describes trigger frequency, not a causal P&L attribution or evidence that an asset was held on each triggered day.

## Dashboard and default

The dashboard opens on the asset-specific study. It shows all seven approaches in a visual audit; all three cost scenarios; annual and stressed-market comparisons; and an eleven-asset, three-condition activation heatmap. Rejected candidates are blocked from active strategy selection. Previous studies remain available.

The leading default remains `amat_group_selection_volatility.toml` (v14.3.0) across guided actions, historical CLI, autoresearch and executor selection. Its raw authorization identity is verified. Execution remains disabled and dry-run. A sealed default-selection receipt records no retained challenger.

## Execution and limitations

Verified Alpaca SIP core eleven ETFs, $100,000 initial cash January 3, 2017, full 2016 signal warmup, next-session observed 09:30–09:35 VWAP, dividends/splits, native 1% opening-volume entry cap, whole shares and recorded missed fills. Native exit policy unchanged. Zero cash yield. Costs of 2/6/10bps per side are combined spread/commission/impact assumptions, not measured historical quotes.

Annual 2020–2026 views are continuing-account forward slices, not yearly resets or fresh model fits. All history has prior research exposure. The 2023 guard is not an untouched holdout; bootstrap intervals are descriptive and unadjusted for accumulated research search. No future profitability or global optimality is claimed.

The baseline reproduces every frozen parent session at each cost. Actual data partitions and canonical seals are verified. Tests cover asset independence, unselected assets staying at zero, reduction-only bounds, future-price invariance, nominal-price scale invariance, configuration round trips and legacy behavior. The large activation supplement is encoded compactly within the dashboard read bound; the sealed canonical JSON value is unchanged.

## Reproduce

```sh
amat/.venv/bin/python amat/scripts/run_asset_sizing_study.py --output /new/immutable/output
```

Evidence: `amat/outputs/analysis/2026-10-19-asset-sizing/study/`. Protocol, 21 trial records, decisions, activation and default-selection receipt are sealed. Source/config copies and conditions were frozen before runs.
