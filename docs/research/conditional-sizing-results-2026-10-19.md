# Conditional inverse-volatility sizing — 2026-10-19

**Decision: reject all three candidates; preserve the current best overall default.**

Compared native, portfolio-wide conditional inverse-volatility sizing against all-groups + volatility-triggered cost-aware switching. Each candidate changes allocation power from 0 to 1 only while its fixed QQQ condition is active. Eligibility, group selection, switching, volatility target, exposure limits, rebalance bands and native execution remain the same. No model is fitted and no thresholds are optimized.

## Fixed hypotheses

- Weak trend: completed QQQ close below its trailing 200-session simple mean, including that close.
- Volatility stress: sample standard deviation of trailing 20 daily log returns, annualized by sqrt(252), above 25%; OR 20-session standard deviation above 1.5 times the trailing 60-session standard deviation.
- Either condition: weak trend OR volatility stress.

All conditions use only prices through the decision close. Sizing can trade from the following session. At other times allocation power returns to the incumbent value 0. This adjusts composition before native volatility targeting and clipping; it does not raise gross or symbol caps.

## Results

Full history January 3, 2017–October 12, 2026, following 2016 warmup. Returns are cumulative after assumed costs; drawdowns shown as positive magnitudes.

| Approach | Return 2 bps | Return 6 bps | Return 10 bps | Drawdown 6 bps | 2020 onward 6 bps | 2023 6 bps | Fills 6 bps |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current best · all groups + volatility switching | 290.52% | 264.58% | 226.81% | 12.77% | 198.77% | 5.79% | 1943 |
| Inverse-volatility during weak trend or volatility stress | 277.69% | 246.70% | 221.62% | 12.63% | 188.66% | 6.68% | 2031 |
| Inverse-volatility only during QQQ volatility stress | 278.80% | 247.46% | 221.93% | 12.63% | 189.10% | 5.97% | 2025 |
| Inverse-volatility only below QQQ 200-day average | 284.41% | 257.70% | 220.03% | 12.63% | 193.89% | 6.64% | 2015 |

## Interpretation

All three lagged full-history returns at every tested cost assumption and lagged 2020-onward returns at every cost. All improved 6bps full-history drawdown slightly and preserved 2023 at every cost, but that did not compensate for the return loss. Switching allocation modes also added trades: 2,015–2,031 fills versus 1,943 for the parent at 6bps.

Weak trend alone came closest, losing 6.88 percentage points of cumulative return at 6bps. Both volatility-conditioned versions slightly improved the 2018 Q4 and COVID stress slices, but all candidates reduced the 2022 return. The dashboard displays the complete rejected audit, cost scenarios, activation shares, monthly returns for the current best and annual/stress results for all four measured approaches. Rejected candidates cannot be selected as active strategies.

| Condition | Active share of decision days, full measurement period |
|---|---:|
| Inverse-volatility during weak trend or volatility stress | 27.11% |
| Inverse-volatility only during QQQ volatility stress | 24.54% |
| Inverse-volatility only below QQQ 200-day average | 16.48% |

Acceptance was registered before outcomes: match/exceed parent full-history AND 2020-onward returns at each 2/6/10bps, no worse full-history drawdown at 6bps, preserve 2023 at every cost, and at least one strict return gain. No candidate passed. No new active strategy profile or default change was made. Research recipes and evidence are retained for reproducibility only.

## Execution and validation limits

Verified Alpaca SIP core snapshot, eleven ETFs, flat $100,000 on January 3, 2017, full 2016 signal warmup, next-session observed 09:30–09:35 VWAP, dividends/splits, native 1% opening-volume entry cap, recorded missed fills, whole shares and zero cash yield. Native exit policy remains unchanged. Costs are combined spread/commission/impact assumptions of 2/6/10 bps per side, not measured historical quotes.

The 2020–2026 annual views use continuing accounts, not fresh yearly resets. All history has prior research exposure; the 2023 check is a preservation guard, not an untouched holdout. Bootstrap intervals are descriptive and unadjusted for the accumulated search.

The parent reproduced every frozen session at all three costs. Configuration round-trip tests caught an initial serialization defect before acceptance: conditional fields were omitted for allocation power 1. That invalid trial set was discarded and all 12 measurements rerun after the fix. The v2 measured records and decisions are intact. A reporting-only import error occurred after measurement/decisions; the protocol-bound activation supplement was generated with the corrected helper without changing trial records. The maintained runner includes the reporting fix.

Evidence: `amat/outputs/analysis/2026-10-19-conditional-sizing-v2/study/`. Protocol, 12 trial records, decisions and activation supplement have verified canonical SHA-256 seals; actual data partition bytes were checked.

## Reproduce

```sh
amat/.venv/bin/python amat/scripts/run_conditional_sizing_study.py --output /new/immutable/output
```
