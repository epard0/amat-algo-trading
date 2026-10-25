# E05: completed-session gap reversal and optional-buy timing

**Both candidates are rejected. v14.3.0 remains the verified default.** The reversal rule loses all six full-history and forward return comparisons. The gap-only ablation has isolated gains, including the highest-cost full-history result, but fails the lower-cost full-history and all-cost 2023 safeguards. No profile, default, bot, order or remote configuration was changed.

The actual local cycle entered at 21:37:34 UTC (5:37:34 p.m. Detroit), covering the 21:40:10 opportunity. Its hard stop is 22:17:34 UTC. Nine native measurements finished at 21:50:26 UTC in 98.65 seconds, with no surviving managed workers. The earlier arrival is not counted as an additional tick. This is experiment 5 of at most 10: five completed experiments, ten rejected candidate arms and zero promotions.

## Mechanism and pre-outcome review

A completed session with a large positive opening gap followed by a down candle may indicate exhausted buying demand. The fixed conditional policy defers only that asset’s optional whole-share BUY at the completed close, leaving reductions, exits and unrelated purchases intact. The gap must strictly exceed the population standard deviation of 20 daily log close returns ending at the preceding close; the current close must be strictly below its open. The second fixed candidate removes only the reversal condition. No thresholds were selected or revised from candidate outcomes.

This is a stateless decision filter. Consecutive qualifying days can defer successive decisions. It neither restores an old authorization nor promises a fill or an automatic retry. It does not impose a stop or reduce an existing position merely because a gap reverses.

Independent source and economic reviews approved this narrow hypothesis for falsification. The audit covered 46 non-frozen historical analysis protocols and the existing conditional-switching, group-risk, residual-cash and generic entry-feature implementations. An opening-gap feature already exists in the research registry; that earlier feature search is acknowledged. The tested completed-open/down-candle, per-asset native BUY mask differs from the earlier joint BUY forecast gates and whole-position volatility controls. It is not a renamed E01–E04 configuration.

## Data timing, execution and attribution

The frozen SIP panel contains actual positive finite adjusted opens for every one of its 29,799 cells: 2,709 sessions and 11 instruments. The loader rescales raw opens by the same row’s adjusted-close/raw-close factor. Opens and closes therefore use a common split/dividend-adjusted feature basis, while raw valuation, observed execution VWAP and explicit corporate-action ledger entries remain separate. Tests cover mechanical split/dividend changes and common feature-scale invariance.

At decision close t, the rule can observe open[t], close[t], close[t−1] and the prior 20 completed daily returns. It never reads t+1 prices, opening-window volume or future closes. The data’s full 09:30–09:35 VWAP/volume cannot establish order arrival, limit touches, queues or adverse selection. Actual opening-limit execution remains a separate capability prerequisite; this study must not be described as a verified limit-order strategy.

The archived baseline quantity-increase diagnostic at 6 bps contains 1,001 events outside corporate-action dates: 539 higher and 462 lower opening-window prices than the prior close. The signed notional difference is +$4,057.53, versus $127,345.02 of absolute movement; same-session mark gains total +$5,261.31. These inferred end-of-day quantity changes are not exact fill attribution, broker slippage, avoidable losses or proof of predictable gap alpha. No feature threshold was chosen from them.

Exact current-default monthly asset accounting instead shows losses that an entry-only filter cannot generally repair. September 2020 losses are led by SLV (−$6,949), QQQ (−$2,253) and GLD (−$1,156). February 2023 losses include GLD (−$3,751), SLV (−$2,880) and EFA (−$2,261). May 2019 has no filled purchases. The research therefore tests a limited entry-timing mechanism, not a comprehensive explanation for bad months.

## Shared native comparison

All arms start flat with $100,000 on January 3, 2017 and end October 12, 2026, preserving full 2016 signal history on `alpaca-sip-core-2026-10-13-verified`. Execution uses next-session observed 09:30–09:35 raw VWAP, actual 1% opening-volume entry capacity, whole buys, native exits/missed fills, dividends/splits, zero cash yield, and 2/6/10 bps per side. All three fresh controls reproduce the foundation’s entire 2,457 session-row hashes exactly, including account, attribution and execution counts.

Forward returns are continuing-account 2020-onward slices, with chronological 2020–2026 annual evaluations; they are neither annual resets nor untouched holdouts. The policy has no fitted labels or learned thresholds, so training-fold purge/embargo is not applicable. Prefix and future-input mutation checks remain mandatory and passed for both candidates.

Drawdown below is a positive loss magnitude.

| Cost per side | Arm | Full-history net return | 2020 onward | Maximum drawdown | 2023 return |
|---:|---|---:|---:|---:|---:|
| 2 bps | v14.3.0 control | 296.3919% | 211.8904% | 12.5905% | 6.5464% |
| 2 bps | Gap + reversal delay | 275.8037% | 200.9830% | 12.5951% | 6.6797% |
| 2 bps | Gap-only delay | 295.7810% | 217.2861% | 12.7011% | 4.8957% |
| 6 bps | v14.3.0 control | 259.7973% | 192.7199% | 12.8368% | 5.7403% |
| 6 bps | Gap + reversal delay | 249.0707% | 185.2745% | 12.8556% | 5.9229% |
| 6 bps | Gap-only delay | 254.0578% | 198.0534% | 12.8197% | 5.2378% |
| 10 bps | v14.3.0 control | 221.8801% | 177.6031% | 12.9820% | 5.0710% |
| 10 bps | Gap + reversal delay | 210.9491% | 170.5629% | 12.9433% | 5.1258% |
| 10 bps | Gap-only delay | 234.6921% | 190.3730% | 12.9030% | 4.1026% |

The reversal candidate fails every full/forward return floor and full/forward drawdown guards at 2/6 bps, despite preserving 2023 at each cost. Gap-only fails full-history returns at 2/6 bps, drawdown at 2 bps, and 2023 at every cost. Its 10 bps gain cannot be used to select a favorable cost-specific rule after seeing outcomes.

At 6 bps, matched executable QQQ returns 555.08% over the full history and 262.47% from 2020 onward, with 34.75% maximum full-history drawdown. The default remains well behind QQQ’s cumulative return despite much smaller drawdown. QQQ’s passive investment mandate differs from AMAT’s diversified risk mandate; the cost-adjusted native reference remains visible, with the separate investor total-return reference retained. No QQQ-outperformance claim is made.

## Mechanism activity, costs and opportunity loss

The filter was active, rather than inert. Independent replay reproduces every predicate from actual completed inputs and the native whole-share BUY set. At 6 bps, the reversal policy defers 98 asset BUY decisions across 86 decision sessions; gap-only defers 234 across 208 sessions. These are decision-intent counts, not broker orders, failed fills or independent forecasts.

| 6 bps measure | Default | Gap + reversal | Gap-only |
|---|---:|---:|---:|
| Modeled execution costs | $24,604.44 | $23,981.53 | $23,009.07 |
| Fills | 1929 | 1894 | 1847 |
| Average exposure | 86.26% | 85.87% | 85.02% |
| Annualized turnover | 23.42× | 22.93× | 22.24× |

Saving fees and reducing exposure did not produce an admissible improvement. Gap-only improves the COVID stress return from −9.5397% to −7.1129% and 2022 from +2.4171% to +3.2848%, while worsening 2018 Q4 and 2023. The reversal arm has much smaller stress changes and broad growth losses.

## Months, recovery and uncertainty

At 6 bps, the baseline has 43 negative months, reversal 44 and gap-only 41. A lower count does not establish better compounded performance or acceptable recovery. Reversal’s largest relative monthly shortfall is March 2025: +1.8338% versus +2.7798%, a 0.9461-percentage-point opportunity loss in an otherwise positive month. Gap-only’s worst relative month is February 2018: −2.3669% versus −0.5405%, 1.8264 points worse; November 2025 also loses 1.4973 points relative to the baseline. March 2020 improves to −5.0831% for gap-only versus −7.5314% baseline. These effects include the entire changed holdings/cash path; they are not a causal single-trade attribution.

Both candidates’ deepest 6 bps drawdown occurs from January 29 to March 23, 2026 and remains unrecovered at the October 12 endpoint, after 175 underwater sessions. The default’s January 29 episode instead recovered May 14 after 72 sessions; its own largest full-history drawdown is the 2020 COVID episode. The same 2022–23 recovery episode lasts 433 sessions for the default and 427 for gap-only. Lower maximum drawdown in one candidate therefore does not imply uniformly faster recovery.

The predeclared diagnostic compares all six arm-by-cost contrasts jointly over 118 paired monthly log-return differences. It uses 5,000 shared circular-block bootstrap draws, seed 2026102305, and 1- and 3-month blocks. Every 95% simultaneous interval includes zero. At 6 bps under 3-month blocks, reversal’s annual log-growth difference is −0.3078 percentage points with interval [−1.0155, +0.3999]; gap-only is −0.1635 points with interval [−1.3099, +0.9829]. These are conditional descriptive intervals on reused measured histories; they do not correct the entire historical research search or establish an untouched holdout.

![E05 all-cost, drawdown, monthly and uncertainty comparison](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/figures/e05-completed-gap.png)

## Validation, immutable evidence and limitations

The new driver passed 13 substantive tests independently through two reviewers. Root reran those plus seven publication-pointer tests (20 passed); numerical-gate tests passed 33/33 and campaign API tests 24/24. Scoped Ruff check and formatting checks pass for the final two source/test files. Tests cover prior-only indexing, strict boundaries, invalid/missing data, action/scale basis, whole-share BUY intersection, unaffected purchases, preserved reductions/full exits, legacy fractional holdings without microscopic sells, and both-arm native future-mutation equivalence for instructions, fills, misses, cash and equity. No economic engine source changed. Final browser verification found narrow-screen overflow from status and scientific text; a scoped research-panel CSS wrapping fix was retained. The UI build passes (1,592 modules), and the full API/launcher suite passes 165 tests in 42.70 seconds with one existing Starlette/httpx deprecation warning. Earlier full AMAT/executor receipts are not presented as newly rerun package-wide regressions.

Initial audit/verification failures remain recorded: a diagnostic-print variable shadow, rejected numpy scalar serialization, a combined cross-project pytest import attempt, and style checks that saw the earlier unformatted test. The final test file differs only in formatting: reviewers proved AST identity, preserved original bytes/results, and sealed the v2 test supplement. The frozen measurement manifest and source archive pinned final test bytes before launch; the supplement was sealed during controls before candidate results, while the economic driver and original preregistration remained unchanged. No candidate was retuned.

The predeclared outcome diagnostic reader initially expected a turnover field absent from the native result schema. Its immutable original/failure are retained; the separate reader repair derives turnover from unchanged session rows. Hypotheses, contrasts, bootstrap draws/blocks/seed and strategy results did not change. This reader repair is not an alpha experiment.

Automatic promotion remains disabled. The independently typed evaluator rejects these candidates numerically and also leaves unresolved native profile/planner/execution and reviewed promotion-consumer integration gates unavailable. A seal, passing test or historical return cell cannot waive them. Remote paper bots, account state, original authorizations, pending commitments and order lifecycle remain untouched.

Dashboard publication contains 42 source-bound result rows, retaining every previous numerical benchmark. Detailed growth/drawdown, annual/stress and monthly comparisons focus on E05, with older generations retained immutably. Cost selectors show actual 2/6/10 bps results and explicit rejection reasons.

## Evidence and next question

The next scheduled opportunity is 22:40:10 UTC (6:40:10 p.m. Detroit). Do not retune or rename the rejected gap rules. Queue priority 7 first requires an inception-correct inverse-ETF data/action/liquidity and existing signed-study audit; direct-short borrow/collateral/dividend/capability requirements remain separate. Before any cash/affordability-engine alternative, audit actual native order contracts and prior mechanisms, then preregister a distinct hypothesis with fair control and QQQ replays. No new experiment is authorized by this report alone.

Reproduction and review files:

- [protocol.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/studies/e05-completed-gap/protocol.json)
- [hourly-e05-completed-gap.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/manifests/hourly-e05-completed-gap.json)
- [receipt.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/cycles/hourly-e05-completed-gap/receipt.json)
- [typed-evaluation.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/studies/e05-completed-gap/typed-evaluation.json)
- [independent-economic-summary.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/studies/e05-completed-gap/independent-economic-summary.json)
- [independent-policy-effect-summary.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/studies/e05-completed-gap/independent-policy-effect-summary.json)
- [gap-economic-review.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2140-independent/gap-economic-review.json)
- [gap-capability-review.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2140-independent/gap-capability-review.json)
- [implementation-review.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2140-independent/implementation-review.json)
- [implementation-review-v2.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2140-independent/implementation-review-v2.json)
- [e05-diagnostics.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2140-independent/e05-diagnostics.json)
- [root-regression-verification.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2140-independent/root-regression-verification.json)

The browser verified all three cost selectors, keyboard month inspection (September30 to October12), current v14.3.0, both rejected E05 rows, and source-backed recovery/uncertainty notes. Requested viewport widths1280/768/390 produced observed CSS widths1164/698/355 because of browser scaling. At355CSSpx, initial document scroll width442 exceeded client341; after the scoped wrapping correction both are341, and panel/table wrappers are contained at all three observed widths. The original viewport was restored. [Final UI verification](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2140-independent/final-root-ui-verification.json).
