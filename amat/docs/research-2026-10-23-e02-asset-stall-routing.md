# Adaptive research experiment 2: asset-specific stalled-trend routing

## Functionality and measurement status

The current verified native strategy remains **v14.3.0**, all groups with volatility-triggered cost-aware switching. Its profile SHA256 is `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8`. Native strategy/accounting/planning remain long-or-cash. A sell reduces a long; this experiment does not add direct short, inverse or intraday execution support. The default has no active explicit stop-loss/take-profit exit or recovery embargo. Daily signal eligibility already permits re-entry. Configured daily-loss/drawdown/Kelly fields do not become active loss caps on this path.

The QQQ benchmark, exact holdings attribution, native simulation volume-cap/split/whole-share sizing repairs and current-control reproduction are established in the foundation report. Actual staged broker limits and historical observed VWAP have different fill/cash/participation behavior; that execution-contract validation is incomplete. A winning score override would also require a versioned native profile and exported-plan parity. The typed numerical evaluator works on actual records, but its automatic promotion-consumer integration and prospective enablement remain incomplete. No missing gate was marked passed.

This run began at **2026-10-23T19:07:25Z**, a late delivery of the 18:40 opportunity. Its hard stop is 19:47:25Z. Nine measurements ran serially under one durable managed writer and completed at 2026-10-23T19:20:53.674360+00:00 in 117.42 seconds. The bounded process controller reports no cleanup survivors. This is one actual research tick, not a claimed 18:40 on-time run or an invented additional tick.

## Decision and recovery-proposal audit

**Reject both new candidate arms and retain v14.3.0.** Both candidates fail every required full-history and continuing-account 2020-forward return comparison at all three costs. Both lower drawdown and improve 2023, but those benefits do not offset lost growth. They were never added to active profiles. Immutable protocols, source and outcome evidence remain retained.

Priority 2, signal-based restoration after actual risk exits, was **deferred without consuming an experiment**. Independent source reviews found no active baseline risk-exit/cooldown state to restore. Earlier QQQ danger reductions and staged recovery are already implemented and researched. Rebranding those rules or introducing a stop while calling it only a restoration change would not test the stated hypothesis. A future restoration test needs an independently justified actual exit state and measured re-entry delay.

## Preregistered experiment and invariants

Priority 3 became the second measured experiment. The three fixed arms are the unchanged default, asset-specific score routing, and an allocation-only ablation. One gate is shared across all eleven core ETFs: the asset's completed 21-session return is negative while its completed 63-session return remains positive. During that state, score routing uses the smaller of its native 63/126/252 long score and the existing 21/63/126 score. It preserves native base warmup, horizon agreement and availability. This is score routing, not complete per-asset strategy-context switching. Lower scores can change rankings and redistribute capital into other assets; it is not a guarantee of lower total portfolio risk.

The allocation ablation retains the original signals/rankings and multiplies each native target by the same routed/base score ratio. A zero/nonfinite base score uses a neutral ratio of one and cannot create eligibility. No parameters, recipes or asset-specific thresholds were fitted; no training labels or learned folds are used. The fixed gate and recipe pair were reviewed before outcomes and were not retuned. A prospective admission supplement preserves the original source-approved review while binding the latest independent test-file hashes; original evidence was retained rather than overwritten.

Every native account uses full 2016 signal seeding, $100,000 flat capital from 2017-01-03 through 2026-10-12, `alpaca-sip-core-2026-10-13-verified`, observed next-session 09:30–09:35 raw VWAP, explicit corporate actions, whole-share buys/native exits, real 1% opening-volume entry participation, recorded missed fills and zero cash yield. Costs are 2/6/10 bps per side; cost assumptions also enter the incumbent switching logic. These are modeled costs, not measured paper-broker spreads. All three new controls reproduce the foundation's entire 2,457-session row hashes exactly.

## All-cost comparison

| Cost per side | Arm | Full net return | 2020 onward | Maximum drawdown | 2023 return | Modeled fees |
|---|---|---:|---:|---:|---:|---:|
| 2 bps | Current default v14.3.0 | 296.39% | 211.89% | 12.59% | 6.55% | $8,962.67 |
| 2 bps | Asset-specific score routing | 249.46% | 185.90% | 12.25% | 9.79% | $14,414.59 |
| 2 bps | Allocation-only ablation | 205.69% | 159.08% | 12.14% | 8.84% | $9,544.47 |
| 6 bps | Current default v14.3.0 | 259.80% | 192.72% | 12.84% | 5.74% | $24,604.44 |
| 6 bps | Asset-specific score routing | 185.67% | 149.88% | 12.29% | 9.15% | $39,130.86 |
| 6 bps | Allocation-only ablation | 171.15% | 136.03% | 12.21% | 7.75% | $27,099.06 |
| 10 bps | Current default v14.3.0 | 221.88% | 177.60% | 12.98% | 5.07% | $37,259.04 |
| 10 bps | Asset-specific score routing | 145.66% | 127.64% | 12.54% | 6.70% | $58,168.63 |
| 10 bps | Allocation-only ablation | 130.10% | 111.25% | 12.17% | 6.08% | $40,173.68 |

At 6 bps, matched native whole-share QQQ returns **555.08%** with **34.75%** drawdown; the idealized fractional total-return reference returns **569.18%** with **35.00%** drawdown. Native QQQ remains the declared risk comparator. The default is lower risk but has not achieved the QQQ return objective. These rejected candidates widen that return gap.

## Trading, monthly failures and recovery

| 6 bps metric | Default | Score routing | Allocation only |
|---|---:|---:|---:|
| Fills | 1,929 | 3,147 | 2,822 |
| Annualized turnover | 23.42× | 43.11× | 31.00× |
| Average invested exposure | 86.26% | 84.18% | 79.60% |
| Negative months | 43 | 41 | 46 |
| Longest underwater episode | 433 sessions | 408 sessions | 397 sessions |

The score router raises modeled fees by approximately $14,526 and produces 1,218 more fills. The allocation ablation also trades more despite holding less capital. Fee differences do not explain the entire return difference: changed positions, reinvestment, missed fills and compounding are endogenous. Historical missed instructions are model outcomes, not newly observed missed or duplicate orders in the running paper bots.

| 6 bps month | Default | Score routing | Allocation only |
|---|---:|---:|---:|
| 2018-02 | -0.54% | -3.78% | -7.38% |
| 2020-03 | -7.53% | -5.56% | -6.78% |
| 2020-04 | 3.23% | 0.91% | 0.63% |
| 2020-09 | -8.23% | -8.32% | -8.32% |
| 2023-02 | -5.28% | -4.46% | -4.17% |
| 2025-01 | 3.55% | 0.16% | 0.63% |
| 2026-06 | -3.98% | -1.89% | -3.18% |

Both candidates improved 22 and worsened 21 of the default's 43 losing months. Score routing softened March 2020 but sacrificed the following rebound; its COVID stress-window return is **−10.89%**, versus **−9.54%** for the default. It improves 2018 Q4 to −6.44% from −7.07%, but 2022 stress return falls to +1.96% from +2.42%. September 2020 remains slightly worse for both candidates. A lower count of losing months or faster underwater recovery does not by itself establish a superior portfolio.

The raw input gate occurs on 4,400 asset-sessions, with 3,421 reduced-score asset-sessions across the precomputed panel. These are not executed trades or actual held-position triggers. Independent diagnostics separately intersect gates with actual decision-close holdings and retain that distinction.

## Independent validation and uncertainty

Annual 2020–2026 continuing-account slices, 2018 Q4/COVID/2022 stress windows, all monthly returns, attribution, recovery episodes, fees, turnover and exposure were independently recomputed. These fixed-rule chronological slices are not trained walk-forward folds or untouched holdouts. All history has prior research exposure.

The descriptive 6 bps paired three-month block-bootstrap interval for annualized log-return difference is **−4.31% to −0.39%** for routing and **−5.10% to −0.85%** for allocation only. Both are negative conditional summaries. They are not independent future evidence, a multiple-search adjustment or a promotion uncertainty pass. Nonnumeric gates that were not completed remain unknown even though numerical rejection is decisive.

Completed verification includes 36 affected routing/numerical-gate tests, 148 API/launcher regressions and the frontend production build. Independent native synthetic causality tests changed future adjusted prices, raw marks and execution-window prices while preserving earlier cash/equity/instructions/fills/misses. The actual all-cost controls reproduce the full foundation session hashes. No long full-AMAT sweep was repeated because the native economic engine was unchanged; the prior full-suite failures and their separate test-only repairs remain recorded in the foundation report.

An independent isolated publisher harness confirmed preservation of all 18 earlier outcome rows, six correct new rows, exact displayed monthly metrics and refusal of duplicates, resealed foreign-run records, wrong study/campaign and outside-campaign paths without changing publication bytes. The local dashboard now retains all prior numerical benchmarks and focuses detailed plots on the latest experiment. Capacity is prospectively bounded at 80 result rows, enough for the ten-experiment budget; the 1 MiB publication bound remains. Older detailed plots/months remain in sealed archived generations and raw studies. An interrupted JSON/sidecar two-write publication can still fail closed until restored from a verified archive; this availability limit is documented.

## Next research and evidence

Before another candidate, audit native-netted cash opportunity against the already rejected fixed and unused-cash index sleeves. Launch only a distinct mechanism. If duplicate or unsupported, advance to portfolio-level marginal-cost switching. The current verified default remains the control; neither rejected stall variant may be retuned or renamed into another candidate. Complete reviewed typed promotion-consumer integration and candidate-native planning/execution checks before promoting a future winner. The separate free-model arm retains its existing dispatch budget and has no new successful inference in this run.

No paper bot, order, remote deployment, execution mode or account state was changed. Only the local API was restarted for the bounded display-capacity update; the independent remote paper monitor retains its separate lifecycle. This historical study is not a final October 23 live-session review.

![Growth, drawdown and annual comparison at 6 bps](../outputs/research-campaigns/2026-10-23-adaptive-12h/figures/e02-asset-stall-routing-6bps.png)

- Sealed study: `outputs/research-campaigns/2026-10-23-adaptive-12h/studies/e02-asset-stall-routing/`.
- Independent typed evaluation: `typed-evaluation.json`; economic summary: `independent-economic-summary.json`.
- Admission supplement and original approvals are retained beside the frozen protocol.
- Independent review/causality/diagnostics: campaign `research-review/20261023-1907-independent/`.
- Publication regression receipt: campaign `research-review/publisher-fixture-verification.json`.
- Actual measurement receipt: campaign `cycles/hourly-e02-asset-stall-routing/receipt.json`.
- Dirty/untracked source baseline: campaign `source-snapshots/hourly-1907/manifest.json`.
