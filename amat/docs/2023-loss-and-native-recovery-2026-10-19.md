# Why 2023 lost money, and native-model recovery research — 19 October 2026

**Decision: retain one canonical native research variant, `amat_group_selection_all_groups.toml`. Reject the other group/trend changes and all opportunistic index sleeves. Core and executor defaults remain unchanged; no live promotion.**

## Diagnosis at 6bps per side

Native daily core began 2023 at $181,963.22 and finished at $169,643.88, a -6.77% loss. Per-asset P&L reconciles to the account every session, including raw-share corporate actions, dividend cash, intraday fill P&L and fees. Contributions use each account’s starting-year equity and already include fees. Do not add the fees again.

| Asset | Core contribution | All-groups contribution | Core average weight | All-groups average weight |
|---|---:|---:|---:|---:|
| SPY | +1.26 pp | +3.96 pp | 5.9% | 16.1% |
| QQQ | +1.58 pp | +6.74 pp | 8.0% | 24.0% |
| IWM | -0.34 pp | -0.34 pp | 0.2% | 0.2% |
| EFA | -0.37 pp | +0.76 pp | 9.6% | 13.8% |
| EEM | -0.20 pp | -0.20 pp | 1.3% | 1.3% |
| IEF | -1.11 pp | +0.08 pp | 4.0% | 0.6% |
| GLD | +1.96 pp | +0.20 pp | 20.6% | 17.7% |
| SLV | -5.97 pp | -4.61 pp | 10.0% | 7.5% |
| DBC | -1.04 pp | -0.90 pp | 1.7% | 1.0% |
| USO | -1.81 pp | +0.17 pp | 7.3% | 5.4% |
| UUP | -0.73 pp | -0.28 pp | 6.8% | 4.2% |

Silver was the largest source of losses: −5.97 percentage points, or $10,860.54. Oil, bonds, broad commodities and the dollar also detracted. Gold, QQQ and SPY were positive contributors. QQQ and SPY adjusted-price buy-and-hold returns in the snapshot were +54.85% and +26.18%, respectively; core averaged only 7.97% in QQQ and 5.93% in SPY. The result is consistent with allocation/timing failures, not a generally falling equity market.

Recorded trading costs were $3,804.57, equal to 2.09% of opening-year equity. These are recorded expenses along the observed path, not the return difference from a separate zero-fee simulation. Core still had a negative result before adding those expenses back to that path. Average unused cash was 24.58%.

## What improved within the existing model

Change only `adaptive_rotation.max_active_groups` from 2 to 3. All three groups can supply candidates, while the existing four-position limit, two-per-group cap, trend eligibility, volatility sizing, symbol/cluster caps and native execution remain unchanged. This removes a group-exclusion bottleneck before asset ranking. It does not blindly buy all assets or discard risk limits.

The 2023 return became +5.56% at 6bps (+6.51% at 2bps and +4.33% at 10bps). Average QQQ allocation rose to 23.95%, SPY to 16.11%, and unused cash fell to 8.11%. Silver still lost 4.61 percentage points; the fix improves overall allocation/timing rather than pretending silver losses disappear. Recorded 2023 costs fell from $3,804.57 to $2,461.53 and annual turnover from 36.17x to 22.40x NAV.

| Measure at 6bps | Daily core | All groups |
|---|---:|---:|
| Full-history net return | 220.32% | 243.91% |
| 2020-onward net return | 173.13% | 188.81% |
| Full-history max drawdown | 16.57% | 13.50% |
| 2023 net return | -6.77% | 5.56% |

## Month by month at 6bps

| Month | Daily core | All groups |
|---|---:|---:|
| 2023-01 | 3.43% | 3.43% |
| 2023-02 | -5.31% | -5.31% |
| 2023-03 | 0.90% | 1.83% |
| 2023-04 | 0.94% | 1.77% |
| 2023-05 | -3.54% | -0.29% |
| 2023-06 | 0.54% | 3.41% |
| 2023-07 | 2.74% | 2.27% |
| 2023-08 | -0.86% | -1.11% |
| 2023-09 | -0.33% | -3.03% |
| 2023-10 | -1.62% | -3.26% |
| 2023-11 | -2.11% | 3.42% |
| 2023-12 | -1.42% | 2.78% |

## Group-selection experiments


| Approach | Status | Full return (2bps) | Full return (6bps) | Full return (10bps) | Drawdown (6bps) | Fills (6bps) |
|---|---|---:|---:|---:|---:|---:|
| Daily core groups · reference | incumbent | 262.75% | 220.32% | 187.59% | 16.57% | 2024 |
| Group ranking · six completed weeks | rejected | 226.45% | 188.15% | 154.51% | 13.20% | 2181 |
| Group ranking · four completed weeks | rejected | 272.86% | 226.75% | 187.00% | 13.23% | 2219 |
| Group ranking · SPY reference | rejected | 246.04% | 203.58% | 170.09% | 17.76% | 1939 |
| Allow all three groups | retained | 281.13% | 243.91% | 215.36% | 13.50% | 1982 |
| Six-week groups + SPY reference | rejected | 244.23% | 200.21% | 173.76% | 13.22% | 1980 |
| All groups + SPY reference | retained | 281.13% | 243.91% | 215.36% | 13.50% | 1982 |


The all-groups variant passed native-core and retained conditional-double full-history and 2020-onward return floors at every cost, with lower 6bps drawdown and a better 2023 outcome at all costs. The SPY-reference/all-groups arm is economically equivalent: with every group admitted and no residual/risk overlay, the ranking benchmark cannot change membership. All three session histories are exactly equal; immutable raw decisions show both passed, while sealed `selection.json` removes the duplicate from active selection. Only one strategy profile is retained.

Four-week ranking also repaired 2023 (+7.25% at 6bps), but did not meet the overall reference/cost floors. It was rejected rather than selecting the rule that made the exposed year look best.

## Trend-quality/lag experiments


| Approach | Status | Full return (2bps) | Full return (6bps) | Full return (10bps) | Drawdown (6bps) | Fills (6bps) |
|---|---|---:|---:|---:|---:|---:|
| Daily core groups · reference | incumbent | 262.75% | 220.32% | 187.59% | 16.57% | 2024 |
| Require all three trend horizons | rejected | 261.88% | 224.06% | 182.90% | 19.65% | 1776 |
| Trend horizons · 21/63/126 days | rejected | 156.26% | 122.70% | 84.42% | 18.09% | 2928 |
| Shorter trends + full agreement | rejected | 193.34% | 137.01% | 99.63% | 13.30% | 2756 |


All three quality/lag alternatives were rejected. The experiment changes only native horizon agreement and trend windows, not asset-specific exclusions. No silver ban, calendar-specific switching, loss-recovery quota or risk escalation was introduced.

## Limits and next-use status

2023 was already exposed and explicitly used to diagnose a weakness; it is not a holdout. Full history is January 2017 through October 12, 2026, with 2016 warmup, observed next-session opening VWAP, corporate actions, 1% opening participation and 2/6/10bps costs. Annual forward slices use continuing accounts and previously researched dates. The all-groups paired 3-month-block-bootstrap 95% interval versus core at 6bps spans approximately −2.57 to +4.15 percentage points of annual log-return difference. It passes the registered historical guards but is not proof of a future return advantage or a guarantee against losing years.

Full-period annualized volatility is 12.20% versus 12.01% for core; average exposure rises to 86.61% from 79.10%. Lower historical drawdown does not mean the variant is unconditionally safer. Recorded missed fills rise from 630 to 716 over the full history. All original risk and execution limits remain operative.

## Reproduction

```sh
amat/.venv/bin/python amat/scripts/run_core_recovery_study.py --output <new-directory>
amat/.venv/bin/python amat/scripts/run_core_quality_recovery_study.py --output <another-new-directory>
amat/.venv/bin/python amat/scripts/diagnose_2023.py --config amat/configs/strategy/amat_group_selection_daily.toml --reference-record amat/outputs/analysis/2026-10-19-native-group-recovery/study/core_daily-6bps.json --output <new-diagnostic-directory>
```

The two native experiments contain 33 native trials; their frozen sources/configurations, all result sidecars, source identities and source data partitions were verified. Attribution files are separately sealed and bound to the exact native session histories; the dashboard refuses mismatched diagnostic or duplicate evidence. The retained profile matches the canonical measured recipe except its identifying name/version. Default/executor authorizations remain unchanged.

The canonical profile is also registered for offline backtests as `amat backtest -v daily-all-groups`. A real January-2023 CLI smoke run produced a PDF and native run record while retaining the full signal history. The generic default remains `daily-core`.
