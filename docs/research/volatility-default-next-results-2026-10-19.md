# Current default and five next improvements — 2026-10-19

Overall default: `amat_group_selection_volatility.toml` (v14.3.0), explicitly selected by the user. Shared native strategy logic now implements the exact retained volatility rule; normal backtest cost stress binds the hurdle to the known cost assumption. Guided dashboard actions, historical CLI, autoresearch entry points and executor fallback/config identity select this profile. Execution remains disabled and dry-run.

The native baseline reproduced every frozen all_volatility session at 2, 6 and 10 bps. The five independent hypotheses were frozen before measurement. No thresholds were selected after outcomes. No additional candidate passed; no rejected strategy profile is offered for active selection. Failed recipes and sealed evidence remain solely for reproducibility.

## Measured results

Full history: January 3, 2017–October 12, 2026, with 2016 warmup. Returns below are cumulative, after assumed costs. Drawdowns are positive magnitudes. The 2020 onward column is a continuing-account forward slice, not a fresh untouched holdout.

| Change | Return 2 bps | Return 6 bps | Return 10 bps | Drawdown 6 bps | 2020 onward 6 bps | 2023 6 bps | Fills 6 bps | Decision |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Lower single-asset cap · 40% | 293.05% | 256.25% | 231.80% | 12.63% | 194.36% | 4.50% | 1873 | rejected |
| Two-day replacement confirmation | 271.03% | 247.60% | 211.76% | 14.26% | 181.78% | 4.99% | 1816 | rejected |
| Current best · all groups + volatility switching | 290.52% | 264.58% | 226.81% | 12.77% | 198.77% | 5.79% | 1943 | incumbent |
| Inverse-volatility allocation | 246.45% | 221.31% | 194.10% | 12.02% | 168.41% | 8.63% | 1975 | rejected |
| Cost hurdle · 1.5 times round-trip cost | 290.52% | 263.32% | 224.82% | 12.77% | 199.04% | 5.79% | 1935 | rejected |
| Wider rebalance band · 3% | 281.48% | 254.84% | 232.50% | 12.84% | 197.40% | 5.20% | 1624 | rejected |

## What each test addressed

1. **3% rebalance band:** reduce small resizing trades; reduced fills substantially but lost central-cost returns and worsened drawdown.
2. **Two-day replacement confirmation:** avoid transient rank changes; delayed beneficial switches and failed return/drawdown guards.
3. **1.5× cost hurdle:** require a larger estimated benefit during volatile conditions; nearly matched the parent but failed full-history returns at 6 and 10 bps.
4. **40% single-asset cap:** reduce concentration; helped at 2 and 10 bps but lost at 6 bps and weakened 2023.
5. **Inverse-volatility sizing:** reduce exposure to volatile holdings; improved drawdown and 2023 but sacrificed too much full-history return.

Acceptance: match or exceed current-best full-history and 2020-onward returns at every 2/6/10 bps; no worse full-history drawdown at 6 bps; preserve 2023 at every cost; at least one strict return gain. All five failed. These outcomes do not establish global optimality.

## Execution and uncertainty

Eleven core ETFs on the verified Alpaca SIP snapshot, $100,000 initial cash, next-session observed opening VWAP, dividends/splits, native 1% opening-volume entry participation, recorded missed fills, whole shares, zero cash yield. Costs 2/6/10 bps per side are combined spread/commission/impact assumptions, not historical measured quotes. Native sell policy remains unchanged. Annual/stress slices and monthly performance are available in the dashboard, together with the rejected-experiment audit and all cost scenarios.

All dates have prior research exposure, including 2023. No untouched forward validation or future profitability is claimed. Paired three-month bootstrap intervals are descriptive and not adjusted for the accumulated research search.

## Reproduce

```sh
amat/.venv/bin/python amat/scripts/run_volatility_next_study.py --output /new/immutable/output
```

Evidence: `amat/outputs/analysis/2026-10-19-volatility-next/study/`. Protocol, 18 trial records and decisions have verified canonical SHA-256 sidecars. Parent session identity is checked at all costs. Source/config copies were frozen before any measurement. Later packaging, formatting and native stress-cost binding changes do not alter this immutable copy.
