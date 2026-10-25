# Current strategy, weak months and QQQ — October 23, 2026 research review

This is a read-only review of saved, sealed simulations and the configured incumbent. No strategy experiment, fit, deployment or promotion was performed for this report. The operational default remains **v14.3.0, all groups + volatility-triggered cost-aware switching**. The latest study calls that incumbent `core_daily`; that label must not be confused with the older two-group daily-core strategy.

The present model has a materially lower historical drawdown than QQQ but has **not achieved QQQ-like absolute returns** in the snapshot reviewed. A fair, native, costed QQQ benchmark is still required. Broad protection or simply reducing trades has repeatedly lowered returns; the next experiments should isolate portfolio concentration, reaction to genuine adverse changes, and the cost of missing recoveries.

## Source and reproducibility contract

The main source is `amat/outputs/analysis/2026-10-20-cost-aware-annual-allocation/study/core_daily-{2,6,10}bps.json`, with the same directory's `protocol.json`. All four canonical SHA256 seals were verified against their sidecars. First/final equity reproduces each reported full-history return. This review writes extracted metrics, 15 worst months, asset price diagnostics, annual QQQ comparisons and source byte hashes to `amat/outputs/research-review-2026-10-23/month-diagnostics.json`.

These native results use flat $100,000, January 3, 2017–October 12, 2026, full 2016 signal warm-up, observed next-session 09:30–09:35 VWAP, dividends/splits, whole-share purchases, native 1% opening-volume entry cap, native exits, missed fills and zero cash yield. Corporate actions can create fractional entitlements on sale. Costs are fixed 2/6/10 bps per side, assumptions rather than reconstructed historical bid/ask spreads. History has already been researched. Chronological annual slices continue the same account; fixed-rule slices are not newly trained walk-forward folds.

The snapshot is `amat/data/snapshots/alpaca-sip-core-2026-10-13-verified/`. QQQ and cross-asset diagnostics use its `daily.parquet` adjusted closes; no external market narrative is needed for the measured price comparisons below.

## Current incumbent and QQQ objective

| Native incumbent, cost per side | Full net return | Calendar CAGR | 2020-onward net return | Max drawdown | Fees | Fills | Missed instructions |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2 bps | 296.39% | 15.14% | 211.89% | 12.59% | $8,962.67 | 1,961 | 2,692 |
| 6 bps | 259.80% | 14.00% | 192.72% | 12.84% | $24,604.44 | 1,929 | 2,868 |
| 10 bps | 221.88% | 12.71% | 177.60% | 12.98% | $37,259.04 | 1,907 | 3,010 |

At 6 bps average invested exposure was 86.26%, annualized volatility 12.30%, annualized traded-notional turnover 23.42 times equity and zero-risk-free Sharpe 1.13. The 74.51 percentage-point full-return difference between 2 and 10 bps is **not** just the difference in cumulative fee dollars divided by initial cash: changed fees alter cash, integer sizing and subsequent account paths. The assumed cost also changes fills/missed instructions. Missed counts are instruction events, not necessarily distinct desired trades, broker failures or evidence of wrong orders.

For the same December 30, 2016 prior-close to October 12, 2026 marks, **QQQ gross adjusted-close growth was 573.81%**, calendar CAGR 21.56%, daily-close max drawdown 35.00% and annualized volatility 22.74%. This is a useful objective gap, **not** a comparable native execution result: it omits first purchase at observed opening VWAP, whole-share cash residual, fees/caps/missed fills and explicit cash dividend versus reinvestment treatment. A claim that the incumbent matches QQQ is unsupported. Conversely, raw QQQ growth alone does not establish an equal-risk advantage; its realized volatility and drawdown were much larger.

The next workflow should publish both an unlevered investable QQQ benchmark and an optional separately labelled equal-risk diagnostic. An equal-risk comparison must not replace the user's absolute-return QQQ objective. Benchmark risk scaling, financing and cash yield must be preregistered; do not retrospectively lever AMAT to erase the gap.

| Year | Incumbent at 6 bps | QQQ gross adjusted-close diagnostic |
|---|---:|---:|
| 2017 | 9.63% | 32.62% |
| 2018 | 3.11% | -0.12% |
| 2019 | 8.74% | 38.96% |
| 2020 | 16.98% | 48.62% |
| 2021 | 28.22% | 27.42% |
| 2022 | 2.42% | -32.46% |
| 2023 | 5.74% | 54.85% |
| 2024 | 11.98% | 25.58% |
| 2025 | 33.89% | 20.77% |
| 2026 through October 12 | 20.20% | 21.86% |

The model protects materially in 2022 yet captures much less of several large equity up-years. Protection and upside participation must be evaluated together. Maximizing the number of nonnegative months alone would reward excessive cash and could worsen this objective.

## Worst months, exposure and activity

At 6 bps there are **43 negative months among 118 observed months**; October 2026 is partial. At 2/10 bps the counts are 41/41. Whole-share and path-dependent fills mean higher costs need not monotonically increase the number of negative months.

| Month | Net at 2 bps | Net at 6 bps | Net at 10 bps | Mean invested exposure, 6 bps | Fills, 6 bps | Fees, 6 bps | QQQ gross diagnostic |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2020-09 | -7.88% | -8.23% | -8.50% | 90.59% | 55 | $598.08 | -5.64% |
| 2020-03 | -7.40% | -7.53% | -7.65% | 84.21% | 27 | $278.38 | -7.29% |
| 2018-10 | -7.10% | -7.33% | -7.33% | 79.93% | 39 | $406.86 | -8.60% |
| 2023-02 | -5.18% | -5.28% | -5.37% | 83.88% | 24 | $285.34 | -0.36% |
| 2026-06 | -3.83% | -3.98% | -4.02% | 94.93% | 19 | $243.79 | -0.15% |
| 2019-05 | -3.89% | -3.88% | -3.81% | 99.14% | 0 | $0.00 | -8.23% |
| 2023-10 | -3.16% | -3.56% | -3.61% | 91.20% | 38 | $458.98 | -2.06% |
| 2026-03 | -3.33% | -3.31% | -3.27% | 79.24% | 27 | $489.86 | -4.84% |
| 2023-09 | -3.07% | -3.10% | -3.08% | 92.07% | 24 | $195.37 | -5.08% |
| 2021-11 | -2.91% | -2.92% | -2.94% | 99.15% | 6 | $45.21 | +2.00% |

These data disprove a single explanation that bad months are merely excessive trading: May 2019 lost 3.88% with no fills or fees. They also show that a QQQ-only bad-market detector is insufficient as a universal protection mechanism: February 2023 and June 2026 lost substantially while QQQ was nearly flat.

## What is observed, and what is still a hypothesis

The saved incumbent session rows contain aggregate equity, exposure, fees, fill/missed counts and turnover. They do **not** contain enough holdings/fill detail to reconcile current-incumbent monthly loss to each asset. The asset returns below are observed co-moves, not proof that the model owned those assets during their falls. Do not present them as causal portfolio attribution.

- **September 2020:** QQQ -5.64%, SPY -4.05%, SLV -17.50%, GLD -4.17%, USO -7.40%; incumbent exposure remained 90.59%. This suggests testing response to a synchronized reversal and correlated commodity/metal risk, after exact holdings attribution. The largest loss month also has 55 fills, so both risk and whipsaw/cost attribution deserve review.
- **March 2020:** SPY -12.62%, SLV -15.97%, DBC -17.32%, USO -55.45%, while IEF +3.73%. Broad shock and rapidly changing relative risks are visible in the actual snapshot; a model-specific failure mechanism requires per-session inventory and entry/exit attribution.
- **February 2023:** SLV -11.96%, GLD -5.37%, IEF -3.27% versus QQQ -0.36%; incumbent -5.28%. A technology-index-only trigger could miss non-equity weakness.
- **June 2026:** SLV -21.75%, GLD -11.68%, DBC -9.57%, USO -17.55% versus QQQ -0.15%; incumbent -3.98%, average exposure 94.93%. The analogous non-equity co-move merits investigation across all historical occurrences, not a rule keyed to this calendar month.
- **November 2021:** QQQ +2.00% while SLV -4.66%, DBC -8.77%, USO -16.17%; incumbent -2.92%, 99.15% exposure. This highlights possible opportunity-selection/rotation loss even in an equity up-month.
- **November 2022:** incumbent -2.51% with only 20.71% average exposure while QQQ +5.54%. Remaining asset losses and delayed re-entry must be separated; low exposure alone does not prove the cause. Recoveries are a crucial counter-test for every defensive proposal.

An older, separately reconciled 2023 attribution in `amat/docs/2023-loss-and-native-recovery-2026-10-19.md` identifies silver as a major detractor for the **previous daily-core and all-groups variants** and shows that admitting all groups raised QQQ/SPY participation. That result motivates attribution work but must not be relabelled as this current whole-share incumbent's contribution table. Its older fractional execution contract and strategy differ.

## Recovery and stress constraints

At 6 bps the worst current-default episodes are:

| Peak | Trough | Recovered | Peak-to-recovery sessions | Drawdown |
|---|---|---|---:|---:|
| 2020-02-19 | 2020-03-31 | 2020-07-22 | 106 | 12.84% |
| 2026-01-29 | 2026-02-05 | 2026-05-14 | 72 | 12.60% |
| 2022-06-08 | 2023-03-15 | 2024-03-01 | 433 | 12.38% |

The 2022–2024 recovery duration is a distinct weakness even though the calendar-year 2022 result is positive. Stress returns are -7.07% in 2018 Q4, -9.54% in the COVID window, and +2.42% in 2022. A defensive improvement should be tested on drawdown depth, time under water, early recovery capture and missed upside, rather than a calendar-year result alone. A recovery policy should cap risk and use observed return/eligibility evidence to restore exposure; a monetary recovery quota or loss-chasing increase in risk has no evidentiary basis here.

## Previously tested ideas that must not be renamed and repeated

Source: `amat/docs/performance-campaign-results-2026-10-19.md`, especially corrected whole-share cycles 013–015 and 021–025; `amat/docs/quality-annual-allocation-results-2026-10-20.md`; `amat/docs/cost-aware-annual-allocation-results-2026-10-20.md`.

- Own-asset 80th/90th/95th volatility-percentile sizing, fixed volatility/weak-trend sizing and group/QQQ variants already failed all-cost frontier tests. Do not sweep neighboring percentile thresholds under a different label.
- The persistent 30% combined-metals cap improved 2023 and drawdown but sacrificed full/forward return; it is not an untried solution to silver losses.
- Six positions, unanimity across trend horizons, shorter 21/63/126-day trends, a $250 trade minimum, market-wide QQQ protection and slower five-session selection were rejected. The $250 threshold produced identical histories. Fewer trades/fees did not automatically mean better outcomes.
- 40% maximum position size with quality improves the incumbent in several measures; combining it with other changes is not automatically additive. Existing 2%/3% quality-parent buffers and annual recipe transfers have already been measured.
- The one-year cost-aware annual screen rejects every saved update and duplicates its quality parent. The three-year screen worsens 2/6-bps returns and duplicates the parent at 10 bps. Repeating those trailing-window screens is not a novel research idea.

The later source audit is more current than the closed twelve-hour campaign report: source review of 44 registries, 354 daily records and 239 distinct decision inputs is complete, with 717 cost replays. **Shared-contract coverage remains incomplete** for missing assets in the 38-ETF study and signed/index-sleeve accounting. This distinction prevents stale claims that the entire source review is still pending, and also prevents unjustified global-best claims.

## Ranked next steps for the interactive research workflow

These are proposed mechanisms for independent relevance, duplicate and order review. None is preregistered or measured by this report. Exact economic hashes and source-registry search must run before admission; promising names alone do not establish novelty.

1. **Measurement foundation:** build a native, execution-matched QQQ buy-and-hold control and current-incumbent per-asset/per-fill attribution for all months. Reconcile cash, dividends, splits, actual missed fills, drawdown and fee effects to the sealed account to fixed tolerances. Add upside/downside capture and recovery diagnostics. This engineering step cannot earn strategy promotion on speed or reporting alone.
2. **Separate cash/participation from signal quality:** use attribution to identify whether a weak month comes from holdings that remain eligible, delayed entry after recovery, or capital blocked by failed fills. Choose the next experiment based on repeated mechanism counts across periods, not the most dramatic isolated month.
3. **Correlation-shock risk budgeting:** only if concentration evidence supports it, test one past-only downside co-movement detector that reduces the *joint* budget of currently held correlated groups when correlations rise abruptly. Keep original trend eligibility and exits; define an explicit, causal restoration rule. Compare with a matched static-risk ablation. This differs economically from fixed metals caps or independent volatility-percentile sizing, but needs full deduplication. Test missed upside in metals rallies and equity recovery windows.
4. **Separate de-risking from re-entry hysteresis:** if whipsaw attribution supports it, test a single fixed asymmetric restoration mechanism after an actual protective exit. Never recreate expired or already filled authorization. A parent feature-off control and a matched exposure ablation distinguish recovery participation from simply taking more risk. Include gap/reversal periods; do not optimize restoration against 2023 or June 2026.
5. **Opportunity-cost-aware allocation:** if repeated strong-equity underparticipation survives attribution, test one explicit and bounded change to the cash/eligible-equity competition instead of a fixed 20% QQQ sleeve. Portfolio-level netting, exposure, available cash and deterministic conflict resolution must be native and causally aligned. Compare with same-risk long-only controls. Existing rejected signed/opportunistic sleeves must first be reviewed so the new proposal is economically distinct.
6. **Learned exit-risk indicator only after simple controls:** predict a decision-relevant forward loss or adverse-selection event, conditional on current eligible holdings, using a small fixed past-only feature set. Require calibrated probabilities, nested rolling training/validation/test, matured labels, purge/embargo, locked thresholds, feature/cutoff mutation tests and a simple fixed-rule competitor. The decision threshold must include costs and missed recovery opportunity. Compare incremental value against volatility, trend and correlation features already in the engine. A high classification score alone is not a trading gain.
7. **Portfolio netting/execution efficiency:** only if ledger attribution finds meaningful avoidable transactions, reduce opposing or redundant target changes without altering original authorization. Fair engine-control replays are mandatory. Do not compare repaired accounting to stale results or imply paper fills match historical VWAP.

Each strategy experiment should lock one hypothesis and a small fixed variant set before outcome measurement, keep the unchanged incumbent and relevant parents, use all three costs, run chronological/stress/monthly/recovery analysis and native planning/accounting parity, then obtain independent review before interpreting promotion gates. Rejecting an active profile must preserve its strongest valid comparable historical return and source identity in the frontier. A comparison against QQQ should become a displayed target, with the user choosing whether it is an immediate hard promotion gate or a long-term objective alongside strict incremental gains.

The existing frontier requires full-history and 2020-onward dominance at all costs over every valid prior comparable candidate, not just the operational default. The latest 6-bps frontier already includes smaller-position annual quality at 287.81% full and 217.93% forward, while 2-bps and 10-bps cells can belong to other rejected arms. No new objective should be silently substituted for the user's prior acceptance policy. An interactive workflow should explicitly settle the practical tradeoff between absolute QQQ return, drawdown budget and statistically credible incremental progress before experiments start.

## Limits of this review

This report establishes descriptive weaknesses and measurement priorities. It does not prove a predictive adverse-regime classifier exists, promise positive performance in every month, verify short-account economics, supply new strategy return evidence, or authorize a new deployment. Current default behavior and the paper bots are unchanged. The full functionality/long-short audit and actual October 23 broker-session review are separate sections of the parent report for this run.
