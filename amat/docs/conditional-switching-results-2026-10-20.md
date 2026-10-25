# Conditional cost-aware switching research

**Decision: retain three research candidates; exclude five underperformers. Daily core groups remains the executor default.**

This experiment tests whether the previously rejected always-on cost hurdle becomes more useful when activated only during conditions that justify trading caution. Fixed rules and thresholds were registered before any trial, with no tuning after results.

# Conditional cost-aware switching study

| Approach | Status | Full return (2bps) | Full return (6bps) | Full return (10bps) | Drawdown (6bps) | Fills (6bps) |
|---|---|---:|---:|---:|---:|---:|
| Daily core groups · reference | incumbent | 262.75% | 220.32% | 187.59% | 16.57% | 2024 |
| Cost hurdle during high volatility | retained | 266.90% | 234.41% | 201.08% | 16.50% | 1933 |
| Cost hurdle during thin liquidity | rejected | 258.94% | 221.62% | 190.12% | 16.05% | 1943 |
| Cost hurdle during rank churn | rejected | 261.96% | 227.75% | 190.90% | 16.57% | 1911 |
| Cost hurdle during choppy trends | rejected | 262.30% | 227.40% | 205.57% | 16.03% | 1837 |
| Volatility or thin liquidity | rejected | 261.29% | 228.61% | 198.85% | 16.02% | 1893 |
| Volatility and thin liquidity | rejected | 260.88% | 219.28% | 187.17% | 16.57% | 2015 |
| Volatility, liquidity or rank churn | retained | 265.77% | 235.09% | 204.31% | 16.02% | 1832 |
| Combined conditions · double hurdle | retained | 274.87% | 234.89% | 200.71% | 15.61% | 1811 |

## Fixed condition definitions

- **activation:** Hurdle active if either candidate or incumbent meets gate at decision close; otherwise native membership switching; required exits unchanged
- **churn:** At least three native preferred-membership changes in trailing ten sessions
- **combinations:** Volatility OR liquidity; volatility AND liquidity; volatility OR liquidity OR churn; combined with twice round-trip hurdle
- **inefficient:** 20-day absolute net log return / sum absolute daily log returns <0.25
- **liquidity:** Latest observed opening dollar volume <50% of prior 60-session median OR trailing 20-session median opening dollar volume <$5m; $25k capacity proxy on $100k starting account
- **scope:** Lagged observed liquidity is a proxy, not a spread estimate or next-session volume forecast; no tuning after results
- **volatility:** 20-day annualized vol >25% OR 20-day vol >1.5 times 60-day vol

The hurdle uses five days of projected trailing 20-session mean log-return advantage against round-trip cost, optionally doubled. This is a fixed opportunity proxy, not a calibrated alpha forecast. Each 2/6/10bps trial uses its own known cost assumption. Activation checks either side of an optional within-group membership replacement. Normal within-holding sizing and rebalancing remain native; eligibility loss and group exclusion still exit without waiting. Membership churn is measured from the unmodified native reference preferences, rather than future fills or candidate-dependent histories.

## Retention and interpretation

Match or exceed daily core net return in full history AND 2020-onward at each of 2/6/10bps; 6bps full-history max drawdown must be no worse. No threshold fitting or validation-driven switching.

The three retained candidates pass all six full-history/2020-onward return floors and the 6bps full-history drawdown guard. They are available as research comparisons. No live profile was promoted or executor configuration changed. Their paired three-month-block-bootstrap uncertainty intervals still span zero, and eight challengers were examined on previously used history. An independently observed forward period remains necessary before treating the improvement as established.

| Retained rule | Annualized log-return difference, 95% interval at 6bps |
|---|---:|
| Cost hurdle during high volatility | -0.017 to 0.994 percentage points |
| Volatility, liquidity or rank churn | -0.122 to 1.086 percentage points |
| Combined conditions · double hurdle | -0.201 to 1.161 percentage points |

## Year-by-year forward slices at 6bps

| Year | Daily core | Volatility only | Combined | Combined, double hurdle |
|---|---:|---:|---:|---:|
| 2020 | 20.44% | 21.02% | 20.27% | 20.40% |
| 2021 | 18.34% | 19.01% | 17.97% | 16.97% |
| 2022 | 8.86% | 8.86% | 8.86% | 9.09% |
| 2023 | -6.77% | -6.69% | -6.16% | -5.67% |
| 2024 | 13.93% | 14.07% | 13.62% | 13.31% |
| 2025 | 32.74% | 32.42% | 33.05% | 34.42% |
| 2026 (partial) | 24.86% | 26.92% | 28.13% | 27.78% |

The double-hurdle combined rule reduced the 2023 loss from 6.77% to 5.67%, but lagged core in 2020, 2021 and 2024 at 6bps. These rules do not improve every month or every market regime. At 6bps the named 2018 Q4 and Covid slices were essentially unchanged; the double-hurdle combined rule raised 2022 return from 8.86% to 9.09%. This is trading-efficiency research, not evidence of crash protection.

## Execution efficiency at 6bps

| Approach | Filled trades | Annual turnover / NAV | Total assumed costs |
|---|---:|---:|---:|
| Daily core groups · reference | 2,024 | 30.21x | $28,766.27 |
| Cost hurdle during high volatility | 1,933 | 28.49x | $27,573.80 |
| Volatility, liquidity or rank churn | 1,832 | 25.82x | $24,589.30 |
| Combined conditions · double hurdle | 1,811 | 25.49x | $24,042.59 |

## History, costs and limitations

2016 signal warmup; flat $100,000 account Jan 3, 2017 through Oct 12, 2026. Fixed rules, annual forward evaluation 2020-2026 on continuing accounts; no model fitting or yearly account resets. All dates have prior research exposure.

Observed 09:30-09:35 VWAP, next-session execution, dividends/splits, 1% opening-volume participation and recorded unfilled instructions. 2/6/10bps combined spread/commission/impact assumptions, not measured quotes. Zero cash yield.

The verified eleven-core-ETF snapshot is `alpaca-sip-core-2026-10-13-verified`, with 2016 signal warmup and full measurement from 3 January 2017 through 12 October 2026. Annual forward slices carry the same account forward; they are not newly untouched holdouts. Historical liquidity gates use observed opening-window dollars through the decision close, not future execution-window volume or measured bid/ask spreads. The absolute liquidity threshold is a fixed $100k-account capacity proxy and is not a dynamically sized order estimate. Costs are flat assumptions stressed at three levels, not a fitted regime-dependent spread model. Cash earns zero.

## Dashboard and reproducibility

Default research comparison: **Daily core groups · switching by market conditions**. It displays core plus the three retained candidates, interactive growth/drawdown and monthly views, 2020–2026 annual comparisons, stressed markets, costs and turnover. The rejected audit preserves all measurements and a condition-frequency chart. The active comparison API rejects requests to select rejected candidates.

Run a fresh frozen study with:

```sh
amat/.venv/bin/python amat/scripts/run_conditional_switching_study.py --output <new-directory>
```

Evidence lives in `outputs/analysis/2026-10-20-conditional-switching/study/`; the sibling `frozen/` directory preserves the exact source/configuration. Protocol, 27 trial records and decisions (29 JSON objects) have verified canonical SHA-256 sidecars. Frozen source identity and every snapshot partition were verified; baseline session records exactly match the prior frozen switching study at all three costs. Per-trial records include the effective cost-specific configuration digest and gate-array digest. Native tests prove all-false gates exactly reproduce core and all-true gates exactly reproduce always-on switching, reject invalid masks, and preserve prefix/future-mutation causality. No order was submitted.
