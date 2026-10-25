# Final AMAT adaptive research campaign — October 23–24, 2026

**Decision: retain v14.3.0. Five preregistered strategy experiments measured ten candidate variants; all ten were rejected and no default was promoted.** The campaign improved measurement, research controls and an inert native action adapter. It did not establish a better trading strategy or achieve QQQ return parity. The final scheduled review entered October 24 at 03:45:40 UTC (October 23 at 11:45:40 p.m. Detroit), with a 04:25:40 UTC cycle stop and the original 04:40:10 UTC campaign deadline. No new alpha experiment is launched in this final review. Final closure time and automation disposition are recorded in the sealed completion receipt.

## Functionality, accounting and execution scope

The active equity strategy is a diversified, condition-dependent long-or-cash allocator. A sell reduces or closes a backed long position; that is not a validated direct equity short. Native historical research supports explicit dividends/splits, raw observed opening fills and marks, adjusted seeded signals, whole-share purchases, native exits, cash balances, fees, missed instructions and asset-level attribution. Perps has a separate signed paper account and its own fee/funding ledger; it is not evidence that equity borrow, margin, recall or short corporate-action accounting exists.

The initial repository audit and later capability reviews distinguish local research computation, local UI/API and the separate remote paper executors/collector. Direct equity shorts, broad inverse-ETF sleeves and intraday agent trading remain unvalidated: retained data lacked the necessary compatible instrument/action/opening-liquidity or borrow/accounting/planner support. No synthetic negative-QQQ series, fictional borrow rate or fabricated opening liquidity was admitted. The frozen snapshot has verified action coverage; appended live tails without that coverage are not eligible research evidence.

TradingView/Alpaca open-position unrealized profit is measured since entry. It differs from prior-close account equity change and the monitor's 09:29 baseline session return. The existing October 23 dashboard/report keeps these separate; this final research review makes no new broker observation. Actual AMAT execution costs remain unavailable where the collector cannot establish them, rather than being replaced with simulated 2/6/10 bps fees. Perps funding/native fees remain separate from equity observations.

Historical VWAP plus a 1% entry participation cap is not a claim that the staged-limit broker would fill identically. The campaign repaired/tested local simulation cap/split display/whole-share partial-sell/target-parity defects, retained source identities and fairly replayed controls. A no-order paper hold session cannot prove actual-fill equivalence. Research side effects did not change remote bot lifecycle, orders, configuration, account state or default.

## Frozen measurement and scientific contract

All native strategy comparisons use `alpaca-sip-core-2026-10-13-verified`, full 2016 signal history, flat $100,000 from January 3, 2017 through October 12, 2026, observed next-session 09:30–09:35 VWAP, explicit dividends/splits, whole-share purchases/native exits, real 1% opening-volume entry cap, actual missing-fill behavior, zero cash yield and 2/6/10 bps per side. Annual 2020–2026 results continue the same account; they do not reset capital. No terminal liquidation is invented. Costs are assumptions, not observed broker spread/slippage.

Each admitted study fixed one mechanism, control plus two variants, hashes, causal data cutoffs and acceptance criteria before candidate outcomes, with independent source/outcome review. E04 was a distinct human-directed recovery-forecast hypothesis informed by E03, not a hidden retuning of a completed variant. Rejected economic configurations are not recycled under new names. All historical samples were reused in earlier research; chronological forward evaluation is not an untouched holdout. The exit-label foundation exposed year-end labels that mature after the next year's first decision, so entry-year membership alone would leak. No such learner was fitted.

Incremental promotion was explicitly allowed even if an older rejected candidate led one return cell; the old all-frontier veto was not applied prospectively. Candidates still needed every full/2020-forward return floor at all costs, a strict gain, preserved 2023, original-anchor/latest-default drawdown bounds below matched QQQ, robustness, accounting, causality, independent review, native reproducibility/planning and execution compatibility. Numeric success cannot substitute for unknown evidence gates. Automatic promotion remains disabled; the typed read-only evaluator has no default-apply authority and the legacy boolean receipt transition is unconditionally rejected.

## Baseline, QQQ and the return/risk gap

The original and final baseline are the same v14.3.0 profile, byte SHA-256 `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8`. The baseline chain has one version and zero transitions. Initial reproduction matched 2,457 account-date NAVs against the archive; experimental controls reproduced entire enriched session records. The final adapter integration additionally replayed all three current-default histories and matched every complete session row, digest and metric.

| Per-side cost | Default full | Default 2020 onward | Default max DD | Default modeled fees | Native QQQ full | Native QQQ max DD | Idealized QQQ total return |
|---|---|---|---|---|---|---|---|
| 2 bps | 296.39% | 211.89% | 12.59% | $8,962.67 | 555.07% | 34.75% | 569.45% |
| 6 bps | 259.80% | 192.72% | 12.84% | $24,604.44 | 555.08% | 34.75% | 569.18% |
| 10 bps | 221.88% | 177.60% | 12.98% | $37,259.04 | 555.04% | 34.75% | 568.91% |

| Per-side cost | Native QQQ 2020 onward | Native QQQ forward max DD | Idealized total-return QQQ 2020 onward | Idealized QQQ forward max DD |
|---|---|---|---|---|
| 2 bps | 262.50% | 34.75% | 266.06% | 35.00% |
| 6 bps | 262.47% | 34.75% | 266.06% | 35.00% |
| 10 bps | 262.53% | 34.75% | 266.06% | 35.00% |

The native QQQ reference uses a constant 100% target with native whole shares, cash rejection/bands, observed liquidity, explicit distributions/actions and native accounting. It deliberately exceeds AMAT diversification limits and is a benchmark, not an admissible diversified candidate. Its uncapped whole-share investor reference has the same history at this account size because the cap does not bind. Dividend cash reinvestment follows native order rules; tiny nonmonotonic returns across costs arise from discrete quantity/cash paths.

The idealized fractional QQQ total-return reference uses the observed initial opening VWAP including its entry cost and provider-adjusted-close ratios, approximating fractional reinvestment without double-crediting distributions or terminal sale. It is an objective reference, not a native executable candidate or interchangeable risk gate. At 6 bps, default full return is 259.80%, native QQQ 555.08% and idealized QQQ 569.18%: gaps of 295.29 and 309.38 percentage points respectively. Default drawdown is 12.84% versus native QQQ 34.75%. Forward returns are 192.72%, 262.47% and 266.06%. Lower historical drawdown did not deliver QQQ-level growth. The benchmark is not risk/exposure matched, and this trade-off must remain visible.

![Final 6 bps comparison: baseline, QQQ and all rejected variants](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/final/figures/final-comparison-6bps.png)

## Complete experiment comparison

All percentages below are continuous-account net returns. Each cell is **full history / 2020 onward**. Every candidate was rejected under the unchanged promotion gates; a better isolated cost cell or lower fees is insufficient.

| Fixed candidate | 2 bps full / forward | 6 bps full / forward | 10 bps full / forward | Reject reason |
|---|---|---|---|---|
| Conditional joint-loss risk reduction | 287.20% / 212.07% | 249.74% / 193.91% | 224.62% / 181.25% | Full-history returns fell at lower costs; the highest-cost case also worsened drawdown and 2023 performance. |
| Always 75% risk · control | 182.67% / 138.14% | 160.05% / 127.12% | 140.62% / 115.87% | Lower returns in every full-history and forward comparison, despite lower drawdown. |
| Asset-specific stalled-trend score routing | 249.46% / 185.90% | 185.67% / 149.88% | 145.66% / 127.64% | Lower returns in all six full-history and forward comparisons. Lower drawdown and a better 2023 did not offset the lost growth. |
| Stalled-trend position reduction · control | 205.69% / 159.08% | 171.15% / 136.03% | 130.10% / 111.25% | Lower returns in all six full-history and forward comparisons. Lower drawdown and a better 2023 did not offset the lost growth. |
| Portfolio benefit vs buy costs | 279.38% / 205.94% | 272.50% / 203.83% | 230.18% / 178.57% | Lower full-history or forward returns at 2 bps. Lower 2023 return at 6, 10 bps. Larger drawdown at 2 bps. |
| Return-only buy-cost control | 296.72% / 213.91% | 265.17% / 197.85% | 231.70% / 181.85% | Lower 2023 return at 2, 6, 10 bps. Larger drawdown at 2 bps. |
| Recovery-aware buy gate | 289.67% / 206.99% | 256.88% / 191.01% | 234.10% / 181.13% | Lower full-history or forward returns at 2, 6 bps. Lower 2023 return at 6, 10 bps. Larger drawdown at 2, 6, 10 bps. |
| Always-fast forecast comparison | 257.99% / 200.06% | 235.53% / 185.18% | 218.41% / 176.17% | Lower full-history or forward returns at 2, 6, 10 bps. Larger drawdown at 2, 6, 10 bps. |
| Completed gap reversal BUY delay | 275.80% / 200.98% | 249.07% / 185.27% | 210.95% / 170.56% | Lower full-history or forward returns at 2, 6, 10 bps. Larger drawdown at 2, 6 bps. |
| Completed positive-gap BUY delay | 295.78% / 217.29% | 254.06% / 198.05% | 234.69% / 190.37% | Lower full-history or forward returns at 2, 6 bps. Lower 2023 return at 2, 6, 10 bps. Larger drawdown at 2 bps. |

E01's joint-downside trigger appeared on only 25 sessions, concentrated in early 2018, with no direct COVID/2022 trigger. Reduced exposure harmed recovery opportunity; always-75% risk lost growth in every return comparison. E02's asset-specific stalled-trend routing and allocation-only ablation lowered drawdown/improved 2023 but materially reduced full/forward growth and increased turnover. E03 optional-buy cost gates reduced fees; their delayed re-entry sacrificed 2023 recovery. The return-only ablation passed all six return floors but failed 2023 at every cost and the 2 bps drawdown bound.

E04's fixed own-price breakout/shorter forecast alternative admitted 20 previously blocked same-account batches at 6 bps with no reverse permissions, but permission did not guarantee funding or a fill. It improved some November timing versus E03 yet remained later than baseline, and worsened drawdown/6–10 bps 2023. The always-fast ablation also failed. E05's completed-gap rules used only already observed opening information; the gap-only variant had some forward/high-cost return gains but failed low-cost full returns, 2023 and risk limits. Both E05 accounts remained unrecovered from the January 29, 2026 high through October 12 (175 sessions), while default recovered May 14 (72 sessions). No threshold was adjusted after these outcomes.

Independent final recomputation reproduces all 150 numerical gate outcomes, including failures, and exact control histories for all 15 study controls plus three fresh controls. The 42 published rows represent 39 unique full session histories: native and uncapped QQQ are identical at each of three costs, with both benchmark identities preserved.

No candidate has a positive lower uncertainty bound demonstrating improvement: 22 cost/variant intervals span zero and eight are wholly negative. Several exposure/routing arms have wholly negative intervals: E01 static 75% and E02 allocation-only at all three costs, and E02 routing at 6/10 bps. E01 conditional and E03–E05 intervals span zero; E05's simultaneous one-/three-month-block intervals also span zero. These descriptive intervals do not repair repeated-history selection or establish future performance. The three-arm limit preserved informative fixed ablations but not every exposure/cost pathway is isolated. Different continuous accounts cannot identify a single blocked trade's standalone causal value.

## Annual, stress, monthly and recovery diagnostics

The 6 bps baseline's annual forward returns below carry holdings/cash forward. 2026 ends October 12. Matching native QQQ is included for context, with a different concentration/risk profile.

| Year | Default return | Default max DD | Native QQQ return | Default fills | Default modeled fees |
|---|---|---|---|---|---|
| 2020 | 16.98% | 12.84% | 48.27% | 228 | $1,872.79 |
| 2021 | 28.22% | 5.63% | 27.15% | 97 | $1,250.62 |
| 2022-01-01–2022-12-31 | 2.42% | 10.34% | -32.19% | 112 | $1,643.89 |
| 2023 | 5.74% | 8.41% | 54.58% | 254 | $2,391.35 |
| 2024 | 11.98% | 7.47% | 25.32% | 223 | $3,369.65 |
| 2025 | 33.89% | 6.18% | 20.46% | 206 | $4,082.60 |
| 2026 through Oct 12 | 20.20% | 12.60% | 21.50% | 203 | $4,633.55 |

| Stress window | Default return | Default max DD | Native QQQ return |
|---|---|---|---|
| 2018-10-01–2018-12-31 | -7.07% | 9.05% | -16.53% |
| COVID 2020-02-19–2020-04-30 | -9.54% | 12.84% | -6.57% |
| 2022-01-01–2022-12-31 | 2.42% | 10.34% | -32.19% |

The worst eight baseline months at 6 bps were:

| Month | Net return | Modeled fees | Fills | Mean exposure |
|---|---|---|---|---|
| 2020-09 | -8.23% | $598.08 | 55 | 90.59% |
| 2020-03 | -7.53% | $278.38 | 27 | 84.21% |
| 2018-10 | -7.33% | $406.86 | 39 | 79.93% |
| 2023-02 | -5.28% | $285.34 | 24 | 83.88% |
| 2026-06 | -3.98% | $243.79 | 19 | 94.93% |
| 2019-05 | -3.88% | $0.00 | 0 | 99.14% |
| 2023-10 | -3.56% | $458.98 | 38 | 91.20% |
| 2026-03 | -3.31% | $489.86 | 27 | 79.24% |

Exact holding/action/fill attribution in the foundation shows September 2020 losses in SLV, QQQ, GLD and SPY; February 2023 losses in GLD, SLV, EFA, EEM and IWM; October 2018 combined equity/commodity losses; and June 2026 commodity/equity losses partly offset by IWM. May 2019 illustrates losses without fills, which a transaction-cost gate cannot fix. These contributions support a portfolio/recovery hypothesis, but a historical correlation label is not automatically a causal trade-time feature.

At 6 bps the baseline has 1,929 fills, 2,868 missed instructions and average exposure of 86.26%. A missed instruction is not necessarily a missed investor opportunity or a broker operational incident; preserved native cash/rounding/liquidity rules can cause it. Aggregate missed counts across changed counterfactual portfolios are not additive. Fees total $24,604.44. Full-account maximum drawdown recovers from the February 19, 2020 peak on July 22 (106 sessions); the January 29, 2026 episode recovers May 14 (72 sessions). Longest-duration and largest-loss episodes are distinct; all recovery records remain sealed with dates and unresolved states. Dashboard annual/stress/month/recovery views retain their source-backed detail for all costs.

## Queue disposition and useful engineering

1. Conditional joint-correlation risk: E01 measured/rejected, including fixed exposure ablation.
2. Risk-exit restoration: deferred without an alpha trial because the baseline has no active risk-exit/embargo state; existing recovery economics were deduplicated. The separate human-directed E04 forecast study is counted once.
3. Per-asset recipe/score routing: E02 measured/rejected.
4. Netted cash/QQQ opportunity: deferred without a new trial; prior residual-cash/netting studies overlap and native capability work is still needed. Earlier rejection is not falsely called a replay against today's baseline.
5. Portfolio marginal buy cost: E03 measured/rejected.
6. Causal gap delay: E05 measured/rejected; completed observed data supported the fixed feature, not pre-window knowledge of future VWAP/range.
7. Actual inverse ETFs: capability-deferred; queried compatible instruments/data/action/opening coverage unavailable.
8. Direct equity shorts: capability-deferred; actual borrow/signed-ledger/planner/executor evidence missing.
9. Strategy-conditioned ML exit: three fixed all-cost native paired-account examples validate mechanics, not a learned continuous policy. Year-end label maturity revealed leakage risk. More provenance-bound episodes, mature nested folds, purge/embargo and native export are needed; no model fitted.
10. Free-model portfolio agent: capability/prospective-deferred; failed provider probes and synthetic adapter mechanics do not count as a completed investment experiment.

The maximum was ten experiments, not an obligation to invent ten feasible trials. Actual alpha work remains five experiments/ten rejected arms. Eleven cycles were completed before this final twelfth opportunity; closure is pending the final receipt. Foundation/capability/engineering work is separate from alpha studies. Delayed/manual opportunities were recorded honestly, and an early unchanged 03:10 wake added no tick.

Retained tested engineering includes simulation cap/split-display/partial-sell/target fixes; source-bound typed numerical review and removal of the unsafe boolean baseline transition; atomic immutable dashboard generations and fail-closed readers; held-position paired-account diagnostics; the conservative pending-intent cross-symbol risk guard; and the separate exact-quantity native action adapter. Source archives, source/test reviews and actual failed attempts remain distinct from repaired versions. None of these changes alone earns alpha promotion.

The new research adapter binds validated proposal/context to exact native simulated orders/accounting. It preserves original quantities, cumulative fills, remaining intent, account/policy, pending/restricted/reserve cash and expiry; projected sale proceeds cannot finance buys. A zero-cash backed reduction remains legal. Opening quotes alone trigger stops/targets; complete raw ranges conservatively gate limits. Twenty-four fixed synthetic case/cost checks passed exact native fills/misses/ledger/attribution. A supplied previous receipt refuses retry, but the pure adapter cannot prevent a caller from omitting/rolling back a receipt; global production journal ownership is still absent. All nonempty corporate-action sets are refused, as are unsupported short/inverse/intraday actions. Calendar/halts/source completeness and legacy broker/profile export remain unverified. This is mechanics evidence using synthetic liquidity, not an agent skill/performance result.

## Free-model availability and prospective requirements

Two actual hosted dispatches were recorded against a maximum of 24: an early rate-limit failure and one pinned `nvidia/nemotron-3-super-120b-a12b:free` synthetic HOLD probe that failed downstream HTTP404. The later catalog showed zero prompt/completion pricing and the account reported 41 free requests remaining before dispatch. That was not proof of inference availability. No successful action, actual served model identity, actual usage billing or prospective market return was obtained. No paid fallback, provider substitution or repeated query-until-success was used.

A future direct-action shadow study must freeze model/source/prompt/schema, future schedule/horizon/stopping rules, causal observation arrival and latency, and a continuous isolated account with matched default/deterministic same-information controls within the arm limit. Its complete opportunity denominator must include failures, abstentions, blocked actions, missing data and no-fills. It needs actual native capabilities, all-cost return/risk/uncertainty and reproducibility. Current LLM weights may know historical outcomes; historical action replay is contaminated exploratory evidence. Closed-market hours create no new equity trading performance. No prospective Monday trial or new remote campaign was started.

## Recorded workload and failed attempts

The sealed controller inventory contains 16 receipts: 15 completed and one failed attempt, totaling 74 completed commands and 1,045.32 seconds of summed controller wall time. Commands include tests, read-only reviews and failure capture; they are not 74 investment experiments. Native research includes 69 historical account/prefix replays (nine benchmark foundations, 45 E01–E05 arm/cost accounts, twelve exit-pair foundations including nine truncated branches, and three final incumbent controls), plus three separate idealized QQQ references. Nine naive-projection witnesses and 24 adapter case/cost checks use synthetic fixtures, separately from historical returns. Tests run additional isolated fixtures but do not consume or create alpha-trial identities. Total human/Codex labor, CPU time, peak RSS, token consumption, energy and actual hosted billing were not measured by these receipt sums; no such efficiency claim is made.

The sole failed managed receipt is `hourly-readonly-consumer-2240`: optional `/usr/bin/time` was missing before any child review command started; the preserved direct-Python retry completed. Prototype/source/test preflight defects and repaired versions, initial registration/manifest/seal errors, provider HTTP429/404, wrong-working-directory package failures and deliberate bounded regression interruptions remain separately dated evidence. A successful wrapper receipt for the failed HTTP404 probe means correct failure capture, not successful inference. No failed or incomplete attempt is relabeled a scientific winner.

## Validation, limitations and handoff

New adapter evidence includes 169 affected regressions, 112 independent repeated checks, 165 API/launcher tests, a 1,592-module UI build, scoped type/lint/format checks and actual responsive browser proof. Counts from overlapping suites are not summed as unique tests. The earlier full AMAT unit sweep had 3 failures, 3,502 passes and 2 skips in 1,172.22 seconds; dated default-expectation and cross-counterfactual test corrections passed targeted reruns, while that old full run was never relabeled clean. A later wrong-working-directory full invocation was preserved, corrected and bounded after 389 passes; that was not a full-package pass. The final proper-directory package rerun completed: **3,776 passed, two skipped and one failed in 605.13 seconds**. The failure is `test_managed_source_and_config_bindings`: the full shared pytest process has loaded the legitimate `amat.risk` namespace, which has no `__file__`, so the attestor refuses it before checking its path. The same test plus existing source-attestation regressions pass in isolation (seven passed in 1.70 seconds). This is an open fail-closed namespace/process compatibility gap, not proof of a source escape or economic result defect; any future worker importing that namespace can also be refused. No historical-preexistence claim is made. The guard was not weakened and no source/test was changed to hide the failure. The complete run is **not a full-package pass**; its failed result and isolated check are sealed in `final/full-package-validation.json`.

No candidate passes all gates. Fileless namespace handling under source attestation, native broker fill-policy compatibility, candidate native profile/export/planning, full typed verified apply integration/prospective amendment, supported new-instrument accounting, global adapter journal ownership, corporate-action support and prospective agent performance remain open. Unknown gates stay unknown. Promotion remains disabled and no paper/remote action is authorized by these research artifacts.

The immediate next work, under a new explicit finite research authorization, is to resolve native executable-policy/data/journal compatibility and package validation gaps, then preregister a genuinely distinct recovery/opportunity or held-position mechanism. Mature-label nested folds/purge/embargo are mandatory for learning. Preserve the v14.3.0 control, original anchor, all rejected candidates and older research identities; run matched controls after any accounting/data contract change. Do not retune E01–E05, force full investment, silently extend this campaign, enable direct shorts or deploy the agent.

The final dashboard preserves all 42 campaign result rows and cost/curve comparisons, with earlier historical research accessible in the existing archive selectors. The current detailed plots remain the latest measured E05 study; final notes cover all five experiments and the separate engineering/shadow work. No new curve or stitched winning history is invented. A versioned source/reproduction inventory, independent final numerical/capability reviews, baseline chain and final receipt are linked below. The finite heartbeat is stopped at closure; the independently running VPS paper monitor is separate and continues under its own existing lifecycle.

## Review-draft provenance limitation

Two intermediate final-review JSON versions were replaced before their exact bytes were archived. Their known hashes and tool history survive, and the final numerical supplement records this limitation; no reconstruction of missing drafts is claimed. The final numerical review and later exact-report supplements remain sealed, and all underlying strategy records, preregistrations and scientific protocols remain preserved. The earlier approved report version (`a7d9968a…`) is archived byte-for-byte before this provenance correction. This limits review-draft reconstruction; it is not grounds to invent source evidence or waive a promotion gate.

## Source and reproduction index

* Machine comparison and per-record source hashes: `outputs/research-campaigns/2026-10-23-adaptive-12h/final/comparison-complete.json` (42 rows, all costs, 2023, full/forward risk and fees).
* Original sealed campaign protocol and final state/events: the same campaign root. Original protocol is never amended retrospectively.
* Native control/QQQ records: `studies/qqq-foundation-1732/`; idealized QQQ: `studies/qqq-total-return-reference/`.
* Fixed studies and independent typed verdicts: `studies/e01-joint-downside/` through `studies/e05-completed-gap/`.
* Actual bounded argv receipts: `cycles/`; original/changed source and preregistration archives are retained in each study/validation.
* Adapter report: `docs/research/research-2026-10-24-native-agent-adapter.md`; source/test/outcome/publication reviews: `research-review/20261024-0240-native-adapter/`.
* Final independent reviews: `research-review/final-0340/`; final regression evidence: `research-review/final-0340-full-package.*` and `final/full-package-validation.json`.
* Final closure, baseline chain, publication/browser evidence and automation disposition: `final/completion.json` and `final/reproduction-manifest.json`.
