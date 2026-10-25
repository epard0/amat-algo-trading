# All-groups plus conditional cost-aware switching — 19 October 2026

**Decision: retain three research combinations, reject two doubled-hurdle alternatives under the preregistered 2023 preservation rule. No executor/default promotion.**

The experiment tests whether the retained all-groups admission rule benefits from the previous conditional switching policies. It reproduces three parents—native daily core, all-groups alone, and the two-group combined-double conditional strategy—before evaluating five fixed challengers. All nine parent cost-case histories exactly match their previously frozen session records.

# Conditional cost-aware switching study

| Approach | Status | Full return (2bps) | Full return (6bps) | Full return (10bps) | Drawdown (6bps) | Fills (6bps) |
|---|---|---:|---:|---:|---:|---:|
| Daily core groups · original reference | incumbent | 262.75% | 220.32% | 187.59% | 16.57% | 2024 |
| All groups · parent reference | reference | 281.13% | 243.91% | 215.36% | 13.50% | 1982 |
| Conditional switching · parent reference | reference | 274.87% | 234.89% | 200.71% | 15.61% | 1811 |
| All groups + volatility cost hurdle | retained | 290.52% | 264.58% | 226.81% | 12.77% | 1943 |
| All groups + combined-condition hurdle | retained | 282.48% | 261.19% | 228.17% | 12.83% | 1882 |
| All groups + double combined hurdle | rejected | 290.65% | 254.90% | 233.25% | 12.58% | 1862 |
| All groups + always-on cost hurdle | retained | 284.83% | 258.93% | 239.11% | 12.83% | 1802 |
| All groups + always-on double hurdle | rejected | 282.41% | 254.82% | 236.64% | 12.39% | 1769 |

## What the rules mean

All groups may compete for the existing four positions, with at most two members per group and all original eligibility, sizing and risk caps. Cost-aware switching only delays an optional within-group replacement when both the incumbent and challenger remain eligible. Required eligibility exits remain immediate.

The fixed opportunity proxy is five days of projected trailing 20-session mean log-return advantage. The hurdle is two one-way costs (a round trip), optionally doubled. Each trial explicitly binds the hurdle to its known execution-cost assumption. It is not a fitted alpha forecast.

The volatility trigger uses 20-day annualized volatility above 25%, or 20-day volatility above 1.5 times 60-day volatility. The combined trigger also includes thin observed opening-window dollar liquidity and frequent native membership changes. The original conditional parent uses core’s own unmodified preferences; combinations use all-groups’ own unmodified preferences. The two always-on controls test whether the conditions add value beyond a blanket hurdle. Only observations through decision close are used.

## Registered retention

Match or exceed BOTH all-groups and conditional-double parents plus original daily core net returns in full history AND 2020 onward at every 2/6/10bps; 6bps full-history drawdown no worse than every reference. At least one strict return gain over the all-groups parent. Also preserve or improve all-groups 2023 return at each cost, to avoid undoing the diagnosed recovery. Fixed inherited conditions; no threshold fitting.

All five challengers beat the reference return floors and 6bps full-history drawdown floors. The doubled combined rule and doubled always-on rule were excluded specifically because they gave back part of the already diagnosed 2023 improvement. They should not be described as worse on total return. This exposed-year guard was registered before running the trial set; it is not an independent holdout or a guarantee about future years.

## 2023 preservation at 6bps

| Approach | 2023 return | Decision |
|---|---:|---|
| All groups + always-on cost hurdle | 5.88% | retained |
| All groups + always-on double hurdle | 5.16% | rejected |
| All groups + combined-condition hurdle | 5.89% | retained |
| All groups + double combined hurdle | 5.14% | rejected |
| All groups · parent reference | 5.56% | reference |
| All groups + volatility cost hurdle | 5.79% | retained |
| Conditional switching · parent reference | -5.67% | reference |
| Daily core groups · original reference | -6.77% | incumbent |

## Forward slice and execution efficiency at 6bps

| Approach | 2020-onward return | Annual turnover / NAV | Filled trades | Total assumed costs | Missed fills |
|---|---:|---:|---:|---:|---:|
| All groups · parent reference | 188.81% | 24.64x | 1982 | $25,271.03 | 716 |
| Conditional switching · parent reference | 182.05% | 25.49x | 1811 | $24,042.59 | 582 |
| All groups + always-on cost hurdle | 197.35% | 19.70x | 1802 | $20,407.76 | 696 |
| All groups + combined-condition hurdle | 198.02% | 22.09x | 1882 | $22,918.12 | 721 |
| All groups + volatility cost hurdle | 198.77% | 23.62x | 1943 | $24,802.49 | 732 |

The volatility-triggered combination leads the retained challengers at 2bps and 6bps; the always-on single hurdle leads at 10bps. At 6bps volatility-only adds 20.68 cumulative percentage points over all-groups, with drawdown falling from 13.50% to 12.77%. It has 1,943 filled trades versus 1,982 and slightly more recorded missed fills (732 versus 716). Annualized volatility is 12.25% versus 12.20%, so lower historical drawdown is not a claim of uniformly lower risk.

## Descriptive uncertainty versus all-groups parent

| Retained recipe | 95% interval, annual log-return difference at 6bps |
|---|---:|
| All groups + always-on cost hurdle | -0.220 to +1.078 percentage points |
| All groups + combined-condition hurdle | -0.092 to +1.125 percentage points |
| All groups + volatility cost hurdle | +0.152 to +1.183 percentage points |

The paired bootstrap uses three-month circular blocks and 5,000 deterministic resamples. The volatility-triggered interval is positive in this study. The combined and always-on intervals include zero. None is adjusted for the many experiments already performed on this history, and none replaces genuinely new forward observation. Passing the registered historical guards is research retention, not live promotion.

## History and execution

2016 signal warmup; flat $100,000 account Jan 3, 2017 through Oct 12, 2026. Fixed rules, annual forward evaluation 2020-2026 on continuing accounts; no model fitting or yearly account resets. All dates have prior research exposure. Previously exposed 2023 is a preservation guard, not a holdout.

Observed 09:30-09:35 VWAP, next-session execution, dividends/splits, 1% opening-volume participation and recorded unfilled instructions. 2/6/10bps combined spread/commission/impact assumptions, not measured quotes. Zero cash yield.

Clarification: the native 1% opening-volume cap applies to entries; sales follow the native exit execution policy. Flat 2/6/10bps costs represent combined spread/commission/impact stress assumptions, not measured historical bid/ask quotes. Actual next-session 09:30–09:35 VWAP observations, corporate actions and recorded misses are used; cash yield is zero. The full measurement is Jan 3, 2017 through Oct 12, 2026, with 2016 warmup. Annual forward/stress/monthly comparisons use continuing accounts and previously researched dates.

## Kept artifacts and dashboard

The dashboard opens “All groups · conditional switching combinations”, with both parents and the original reference, three retained recipes, growth/drawdown/monthly charts, annual and stressed-period comparisons, cost sensitivity, activation frequencies, and a 2023 preservation audit. Rejected candidates are unavailable for active selection.

Resolved kept recipes are sealed in `keepers.json` and mirrored under `configs/experiments/all-groups-switching-keepers-2026-10-19.json`. The conditional recipes require the causal gate as well as the native configuration. No incomplete ungated trading profile was created, and executor defaults/authorizations remain unchanged.

```sh
amat/.venv/bin/python amat/scripts/run_all_groups_switching_study.py --output <new-directory>
```

Evidence: `outputs/analysis/2026-10-19-all-groups-switching/study/`, with exact source/configuration in sibling `frozen/`. Protocol, 24 trial records and decisions (26 JSON objects, plus the sealed keepers manifest) verified against canonical sidecars. Frozen source identity and every snapshot partition verified. Unit tests check factor isolation, parent-specific churn, future/prefix invariance and all return/drawdown/year guards.
