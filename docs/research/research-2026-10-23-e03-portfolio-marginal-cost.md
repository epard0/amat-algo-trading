# Adaptive research experiment 3: portfolio-level optional-buy cost gates

## Functionality and measurement status

The current verified default remains **v14.3.0**, all groups with volatility-triggered cost-aware switching, profile SHA256 `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8`. The native strategy, accounting and exported planner remain long-or-cash. A sell reduces a long; no direct-short, inverse or intraday capability was added. Native whole-share execution, corporate-action accounting, actual opening liquidity and exact default control reproduction remain the shared measurement foundation.

This new policy is a research-only native callback. It can reduce optional BUY targets, preserves every native sell/reduction target, and explicitly supports only the ordinary one-session lag where earlier instructions have settled before the next decision. It does not claim support for unresolved pending orders at longer lags. A frozen native profile and equivalent exported session-plan implementation would be required before promotion. The typed numerical evaluator is tested on actual records, but its automatic promotion-consumer integration and prospective enablement remain incomplete. Neither research callback was deployed or given order authority.

Native historical VWAP and the staged broker limit policy are different execution contracts. A meaningful parity gate requires matched decisions, account/policy identities, original authorized quantity, cumulative fills, remaining intent, pending cash, expiry and whole-share/order-recovery semantics under a declared compatible execution policy. It cannot promise that every real broker fill equals a historical VWAP. The current contract/price/cap/cash mismatch remains a real validation task; this report does not waive it or mark it passed.

The actual local cycle entered **2026-10-23T19:44:21Z**, for the 19:40 scheduled opportunity, and has a hard stop of **20:24:21Z**. Nine managed native measurements completed at 2026-10-23T19:56:29.056859+00:00 in 122.22 seconds, with zero cleanup survivors. No missed hourly work or extra tick is invented.

## Decision

**Reject both variants and retain the current default.** The return-only candidate improves all six full-history/2020-forward return comparisons, but fails 2023 preservation at every cost and worsens 2 bps drawdown. The portfolio-risk candidate improves 6/10 bps returns, but loses full/forward returns at 2 bps, worsens 2 bps drawdown and lowers 2023 at 6/10 bps. The acceptance policy was not changed after observing these gains.

At 6 bps the portfolio-risk candidate returns **272.50%**, versus **259.80%** for the default. This is a useful measured result but not a verified overall improvement. It pays about $1,084 less in modeled fees, makes 142 fewer fills and has slightly lower drawdown, while reducing 2023 return from **5.74% to 3.37%** and lengthening the longest recovery episode. Both paired uncertainty intervals span zero.

## Prior cash-opportunity audit and economic deduplication

Queue priority 4 was deferred without consuming an experiment. The existing opportunistic sleeve study already reserved core target commitments first, used only residual cash and netted shared instruments before fees/liquidity. It tested QQQ long-only and tailored directional alternatives. Moving those same economics into the native ledger is capability/accounting work requiring fair controls, not a newly distinct alpha mechanism. Its older rejected results do not establish failure against today's different v14.3 native whole-share contract.

The independent audit verified 145 saved protocol/result seals and byte hashes spanning fixed and unused-cash sleeves, switching/rank stability, conditional/all-groups switching, static trade bands and annual allocation cost screens. Priority 5 is distinct from the current member-replacement hurdle: it uses actual decision-close holdings and native emitted optional-buy quantities, prices their immediate marginal cost, and includes a joint portfolio covariance comparison. It affects new entries and within-holding buys as well as member changes; all sells remain untouched.

## Preregistered mechanism and fixed variants

The three fixed arms are the unchanged default; a portfolio-risk-aware optional-buy gate; and a return-only ablation. Each derives the BUY set from the native instruction builder, retaining its trade bands and minimum notional. Requested shares are floored to whole units at the known decision close. The same planned whole-share change determines both the benefit estimate and one-way fee estimate. Zero-whole requests produce no synthetic cost gate. Actual later fills retain native cash rejection, the observed opening-volume cap, missed fills, whole shares and unknown next-session prices.

The forecast is deliberately small and fixed: 63 completed daily log returns, a five-session horizon, and the existing past-only annualized covariance. Annual arithmetic drift is approximated as `252 × trailing_mean_log_return + 0.5 × covariance_diagonal`. The portfolio-risk arm compares the change in `w·mu − 0.5wΣw` between waiting and making the planned whole-share purchases. The return-only arm uses the same mean and removes only the quadratic covariance penalty. Expected benefit and estimated fee are both fractions of decision NAV. The rule admits the entire optional-buy batch only if its estimated benefit exceeds its estimated immediate BUY fee.

Waiting holds those optional increases at current exposure while keeping every native reduction target. Outward rounding includes a Decimal share-count proof so a blocked increase cannot become a microscopic forced sell. Flat holdings remain exactly zero. Fixed mandatory sell fees occur in both counterfactuals and are not charged again as incremental buy costs. Actual native returns still pay costs on both buy and sell fills.

This is an uncalibrated opportunity proxy, not an optimal forecast or a guarantee of future fills/profits. It does not estimate a future exit fee or promise the next five sessions' returns. There is no model fitting, training label, per-asset threshold search, learned fold or outcome retuning. Forecast cutoffs are completed decision closes. Preregistered failure modes include noisy means, delayed entries, missed rebounds and no added covariance value beyond the ablation. Independent source approval and explicit bounded economic nonduplication were sealed before outcomes.

## Shared native contract and all-cost outcomes

All accounts start flat with $100,000 on 2017-01-03, use full 2016 signal seeding, and end on 2026-10-12 on `alpaca-sip-core-2026-10-13-verified`. Execution is observed next-session 09:30–09:35 raw VWAP, explicit dividends/splits, native whole-share buys/exits, real 1% opening-volume entry participation, recorded missed instructions and zero cash yield. Costs are 2/6/10 bps per side and also enter the incumbent switching hurdle. These are modeled assumptions, not measured broker implementation shortfall.

All three controls reproduce the entire 2,457-row native foundation account histories exactly, including cash, fills, costs and holdings attribution. There is no repaired-candidate versus stale-control comparison.

| Cost per side | Arm | Full net return | 2020 onward | Full drawdown | 2023 | Modeled fees |
|---|---|---:|---:|---:|---:|---:|
| 2 bps | Default v14.3.0 | 296.39% | 211.89% | 12.59% | 6.55% | $8,962.67 |
| 2 bps | Portfolio-risk buy-cost gate | 279.38% | 205.94% | 12.67% | 6.56% | $8,217.83 |
| 2 bps | Return-only buy-cost control | 296.72% | 213.91% | 12.67% | 6.36% | $8,553.40 |
| 6 bps | Default v14.3.0 | 259.80% | 192.72% | 12.84% | 5.74% | $24,604.44 |
| 6 bps | Portfolio-risk buy-cost gate | 272.50% | 203.83% | 12.73% | 3.37% | $23,520.00 |
| 6 bps | Return-only buy-cost control | 265.17% | 197.85% | 12.73% | 4.17% | $23,662.44 |
| 10 bps | Default v14.3.0 | 221.88% | 177.60% | 12.98% | 5.07% | $37,259.04 |
| 10 bps | Portfolio-risk buy-cost gate | 230.18% | 178.57% | 12.84% | 2.63% | $36,054.94 |
| 10 bps | Return-only buy-cost control | 231.70% | 181.85% | 12.92% | 3.08% | $35,690.48 |

The 2 bps drawdown is approximately 12.67% for both candidates, above the default's 12.59%. The return-only full-history gain at that cost is only 0.33 percentage points; it does not justify relaxing risk or year-preservation gates. The log-utility 2 bps full-history shortfall is 17.01 percentage points. All-cost covariance benefit is inconsistent: it helps relative to return-only at 6 bps but worsens full and forward returns at 2 and 10 bps.

At 6 bps, native whole-share QQQ returns 555.08% with 34.75% drawdown, and the separate idealized total-return QQQ reference returns 569.18% with 35.00% drawdown. No candidate or incumbent reaches the QQQ return objective. Native QQQ remains the declared matched risk reference; the idealized reference uses a different fractional reinvestment convention.

## Costs, opportunity and poor-market diagnostics

| Metric at 6 bps | Default | Portfolio-risk gate | Return-only control |
|---|---:|---:|---:|
| Fills | 1,929 | 1,787 | 1,805 |
| Annualized turnover | 23.42× | 21.89× | 22.08× |
| Average exposure | 86.26% | 84.77% | 85.04% |
| Negative months | 43 | 42 | 42 |
| Longest underwater episode | 433 sessions | 446 sessions | 437 sessions |

At 6 bps the portfolio-risk gate evaluated 1,197 optional whole-share BUY batches: 1,001 permitted and 196 blocked. The return-only arm evaluated 1,176: 991 permitted and 185 blocked. These are repeated decision proposals, not fills, real broker missed orders or independent trials. Summing repeatedly estimated hypothetical fees would not measure realized fees avoided. Baseline gate counts are not applicable because its policy is inactive.

| 6 bps month | Default | Portfolio-risk gate | Return-only control |
|---|---:|---:|---:|
| 2018-10 | -7.33% | -5.81% | -5.81% |
| 2020-03 | -7.53% | -5.44% | -5.44% |
| 2020-04 | 3.23% | 3.24% | 3.24% |
| 2020-09 | -8.23% | -8.22% | -8.22% |
| 2023-02 | -5.28% | -5.35% | -5.35% |
| 2023-11 | 3.48% | 1.17% | 1.79% |
| 2026-06 | -3.98% | -3.64% | -3.76% |

November 2023 illustrates the recovery cost: the portfolio-risk gate returned +1.17% versus +3.48% for the default. Eight decision batches were blocked that month, involving QQQ on eight decisions, SPY on six and GLD on one; the return-only arm also blocked eight batches. This is an observed association in different account paths, not a causal attribution of the entire return gap to those blocked orders. Do not tune a calendar-specific exception to repair 2023 after the fact.

At 6 bps, both candidates improve the 2018 Q4 stress return to about −5.01% from −7.07%, COVID to −7.47% from −9.54%, and 2022 to about +2.77% from +2.42%. September 2020 remains almost unchanged near −8.22%. The portfolio-risk gate improves 20 and worsens 21 of the default's 43 losing months, with two unchanged. The return-only arm improves 19 and worsens 22, with two unchanged. Longer underwater periods and lower 2023 returns remain material despite fewer losing months and better selected stress slices.

## Independent validation and uncertainty

The typed evaluator independently recomputed actual all-cost full/forward/2023 returns and drawdowns against current and original anchors and matched QQQ. It separately retained missing nonnumeric checks as unknown. Annual 2020–2026, stress, monthly and recovery results were checked from continuous account histories. Fixed chronological slices are not trained walk-forward folds or untouched holdouts; the history has prior research exposure.

The 6 bps paired three-month block-bootstrap interval for annualized log-return difference is **−0.534% to +1.237%** for the portfolio-risk gate and **−0.634% to +0.988%** for return-only. Both include zero. These are descriptive intervals conditional on reused history, not independent significance, multiple-search correction or a promotion uncertainty pass.

Verification includes six policy tests and four additional independent native synthetic replays that mutate future adjusted/raw/window prices without altering earlier account, policy, instruction, fill or missed-fill records. The initial combined affected suite passed 45 tests; the final pointer-only suite passed seven tests after reader/writer hardening. Full API/launcher regressions passed 165 tests, and the frontend production build passed. The full long AMAT sweep was not repeated because native economic engine source did not change; earlier full-suite failure and separate repair evidence remain preserved.

## Retained engineering improvement: atomic dashboard generations

The dashboard now reads a single atomically replaced pointer to a verified immutable publication generation. The pointer has exactly three versioned fields: schema, campaign protocol hash and generation hash. The reader checks campaign binding, lowercase hash shape, generation content hash, size, result capacity and absence of symlinks. An invalid present pointer fails closed without falling back to an unrelated legacy file. When no pointer exists, the old JSON/sidecar reader remains compatible.

The publisher durably writes an immutable generation, preserves the old committed pointer before touching compatibility files, then commits the new pointer last. Fault injection using real copied E02 records proved that an interruption between legacy JSON and seal leaves the old 18-row comparison readable; resuming produces 24 rows once with no duplicates. Failure of final pointer replacement likewise leaves the old generation readable. Corrupt pointers/generations fail closed. This verifies the injected process-level failures, not every possible power-loss/filesystem fault.

Production E03 publication now contains 30 source-bound outcome rows, including every earlier numerical benchmark. Detailed plots/monthly tables focus on E03; older detailed generations and native records remain sealed. No active candidate profile or trading configuration was added. The local API alone was restarted for the reviewed display reader change; no paper bot, broker order, remote deployment or account state was changed.

## Next research and evidence

Next priority is a causally timed gap risk-increase limit. First audit real pre-execution timestamp/quote availability and existing gap, cash and bounded-limit behavior. Do not use a future full-window VWAP or daily high/low as a feature allegedly known before execution. If no distinct supported mechanism exists, defer it and advance to capability audits for actual inverse ETFs/direct shorts. Do not retune either rejected E03 rule around 2023. The typed promotion-consumer and a future winner's native profile/planning/execution validation remain prerequisites for promotion.

![Growth, drawdown and annual comparison at 6 bps](../../amat/outputs/research-campaigns/2026-10-23-adaptive-12h/figures/e03-portfolio-marginal-cost-6bps.png)

- Frozen study and all nine outputs: `outputs/research-campaigns/2026-10-23-adaptive-12h/studies/e03-portfolio-marginal-cost/`.
- Independent source approval and methodology: campaign `research-review/20261023-1944-independent/`.
- Independent diagnostics and reproduction source: campaign `research-review/20261023-1907-independent/e03-diagnostics.json` and `e03-diagnostics-source.py`.
- Cash/switching dedup audit: campaign `research-review/priority4-cash-and-priority5-cost-audit.json`.
- Atomic writer/reader test receipt: campaign `research-review/atomic-publisher-fixture-verification.json`.
- Reader verification: campaign `research-review/publication-pointer-reader/implementation-receipt.json`.
- Actual measurement receipt: campaign `cycles/hourly-e03-portfolio-marginal-cost/receipt.json`.
- Dirty/untracked source baseline: campaign `source-snapshots/hourly-1944/manifest.json`.
