# QQQ benchmark, execution foundation and first adaptive experiment

This run began at **2026-10-23 17:25:28 UTC (1:25 p.m. Detroit)**. Its manual forty-minute limit is 18:05:28 UTC. The finite campaign still ends at 2026-10-24 04:40:10 UTC. The manual run subsumes the 17:40 scheduled opportunity; no extra tick or second overlapping experiment is claimed. The next scheduled new batch is 18:40 UTC.

**Decision: retain v14.3.0 as the current verified default. Experiment 1 is complete; both fixed candidates are rejected. No strategy, broker account, paper-bot lifecycle or remote deployment was changed.** The simulation and local executor implementation repairs described below are engineering changes, not alpha improvements. Rejected policies were never added to active profiles or normal strategy selection; immutable reproduction evidence remains available.

## Measurement contract and control reproduction

All native accounts use the verified `alpaca-sip-core-2026-10-13-verified` snapshot, full 2016 signal seeding, a flat $100,000 account from January 3, 2017 through October 12, 2026, next-session observed 09:30–09:35 raw VWAP, explicit dividends/splits, whole-share purchases and native exits, the real 1% opening-volume entry cap, recorded missed fills and zero cash yield. Each scenario pays 2, 6 or 10 bps per side. Fees are modeling assumptions, not observed broker spreads or implementation shortfall. Annual 2020–2026 evaluations carry the account forward; they are not independent yearly resets or untouched holdouts. Every historical date has prior research exposure.

All three new baseline accounts reproduce **all 2,457 execution-date equity observations exactly** against the archived October 20 incumbent. The old record has an additional flat December 30, 2016 initialization row; filtering to the declared account dates resolves the apparent length discrepancy. The first experiment's new controls reproduce the **entire enriched session-row hashes** of the new foundation controls at all costs. Daily accounting is reconstructed from actual native fills and corporate actions, with zero per-asset profit-and-loss residual. This is a fair current-source comparison rather than repaired-candidate accounting against a stale benchmark.

| Cost per side | Default full net return | 2020 onward | Full maximum drawdown | Modeled fees |
|---|---:|---:|---:|---:|
| 2 bps | 296.39% | 211.89% | 12.59% | $8,962.67 |
| 6 bps | 259.80% | 192.72% | 12.84% | $24,604.44 |
| 10 bps | 221.88% | 177.60% | 12.98% | $37,259.04 |

Evidence is sealed under `amat/outputs/research-campaigns/2026-10-23-adaptive-12h/studies/qqq-foundation-1732/`. The independent typed foundation review is in that campaign's `research-review/parity-foundation/typed-gates-foundation.json`.

## Two distinct QQQ questions

The matched native QQQ reference holds a constant 100% QQQ target, retaining native order construction, whole shares, native minimum trades/rebalance bands, actual opening-volume participation, cash rejection, explicit corporate actions and accounting. A full QQQ position is deliberately outside AMAT's diversification limits. Dividend cash is counted once and can be reinvested only when the native band permits. An otherwise identical uncapped investor reference has the same history because the cap does not bind this $100,000 reference account. A whole-share order based on a previous close can miss after an adverse opening gap plus fees; this native behavior is preserved rather than artificially funded.

A separate idealized fractional total-return reference addresses the user's QQQ total-return objective. It invests at the observed initial January 3 opening VWAP including the entry fee, marks initial units at the actual raw close, then follows provider-adjusted-close ratios. It approximates fractional dividend reinvestment, adds no second distribution credit, assumes no terminal sale, and has no AMAT entry cap, band or tactical exits. It is an objective reference, not a native strategy or an interchangeable promotion gate.

| Cost per side | Native whole-share QQQ full return | Native QQQ drawdown | Idealized total-return QQQ full return | Total-return QQQ drawdown |
|---|---:|---:|---:|---:|
| 2 bps | 555.07% | 34.75% | 569.45% | 35.00% |
| 6 bps | 555.08% | 34.75% | 569.18% | 35.00% |
| 10 bps | 555.04% | 34.75% | 568.91% | 35.00% |

At 6 bps, native QQQ's forward return is 262.47%; the idealized total-return reference is 266.06%. The current default provides substantially lower historical drawdown but has not reached QQQ returns. Tiny nonmonotonic native returns across costs arise from discrete whole-share/cash execution paths; the reference fees themselves are nonnegative and separately recorded. The adjusted-price approximation does not establish actual broker dividend payable dates or reinvestment prices. The promotion risk comparator remains the declared matched native QQQ account.

The separate frozen reference lives in `studies/qqq-total-return-reference/`. Its registration and source/data/calendar seals are distinct from native records; the numerical native loader cannot accept it as a native candidate.

## Execution-parity repairs and remaining limits

The local audit and regression evidence are detailed in `amat/docs/parity-foundation-2026-10-23.md` and `amat/outputs/research-review-2026-10-23/parity-foundation/parity-summary.json`.

- Simulation now passes the native 1% entry cap and explicit whole-share setting.
- Displayed holdings reconstruction applies splits before same-session fills, resolving a mismatch between correct account equity and incorrect displayed quantities/weights.
- Explicit exchange-session precomputation lets the current adaptive strategy display targets matching its exported native plan.
- Appended live-data tails without verified corporate-action coverage now declare `research_eligible=false`. Their bias is unknown, rather than necessarily conservative.
- Local staged whole-share mode floors partial trims while preserving fractional full exits. The changed semantics enter policy identity; old active batches fail closed rather than silently changing authorized behavior. Original quantities, cumulative fills, remaining intent, pending cash and account/policy identity remain intact.

**Actual broker fill equivalence is still unverified.** Historical observed VWAP with a volume cap differs from the broker's staged bounded-limit execution. A no-order hold session cannot establish fill equivalence. The new research callback also requires an equivalent native exported-plan adapter before it could be promoted. Neither gap is hidden by successful baseline sizing/accounting tests. The remaining execution-contract work is matching participation/cash-budget assumptions and testing bounded-fill sensitivity; it does not require every eventual broker fill to equal a historical VWAP. These research changes were not deployed to the remote paper executor, whose existing policy and account continuity remain unchanged.

## Exact poor-month attribution

At 6 bps, September 2020 lost 8.23%. Additive dollar contributions included SLV −$6,948.83, QQQ −$2,253.27, GLD −$1,155.88 and SPY −$1,025.08. February 2023 lost 5.28%, including GLD −$3,751.30, SLV −$2,880.14, EFA −$2,261.23, EEM −$909.73 and IWM −$579.49. October 2018 combined losses in SPY, QQQ, USO and DBC. June 2026 combined commodity and equity losses, partly offset by IWM.

These are reconstructed holding/fill/action contributions, not asset co-movement guessed from the total curve. Joint losses support testing portfolio coordination, but do not prove correlation caused the loss. May 2019 still illustrates that trading-cost reduction alone cannot cure a month with losses and no fills.

## Experiment 1: conditional joint downside reduction

Before candidate outcomes, two independent reviewers examined the fixed mechanism, its causality and the three-arm limit. Synthetic future-mutation, cutoff/threshold and static-control checks were saved with the exact driver hash. The registered design is:

1. Unchanged v14.3.0 control.
2. Reduce all positive target weights to 75% only when at least three targets are at least 5%, their weighted downside-return correlations over 60 completed sessions reach 0.60, and the target-weighted trailing 20-session log return is negative.
3. Always multiply targets by 75% as a fixed-severity exposure ablation.

The trigger reads current target weights applied to past returns, not the actual portfolio's realized historical return. It reads no next-session VWAP/volume, never raises a target, and preserves native mandatory exits. The static ablation is not an exactly exposure-matched beta control. Thresholds were not selected or retuned after observing outcomes. The callback is research-only pending native planning implementation.

| Cost | Default full / forward | Conditional full / forward | Static 75% full / forward |
|---|---:|---:|---:|
| 2 bps | 296.39% / 211.89% | 287.20% / 212.07% | 182.67% / 138.14% |
| 6 bps | 259.80% / 192.72% | 249.74% / 193.91% | 160.05% / 127.12% |
| 10 bps | 221.88% / 177.60% | 224.62% / 181.25% | 140.62% / 115.87% |

The conditional rule fails full-history return at 2 and 6 bps. At 10 bps, full and forward drawdown worsen to 13.0053% from 12.9820%, and 2023 return falls by 0.2364 percentage points. Its 6 bps paired three-month block bootstrap interval for annualized log-return difference is **−0.8330% to +0.1837%**, spanning zero. Static 75% lowers 6 bps drawdown to 9.76% but fails all six required full/forward return cells.

There were 25 conditional trigger sessions: nine in February 2018, eight in March 2018, seven in April 2018, and one in February 2023. No direct triggers occurred during the COVID or 2022 stress windows. February 2018 return worsened from −0.54% to −1.56%, and April 2018 from +0.53% to −0.02%. The rule reacted narrowly, reduced recovery opportunity, and did not provide broad protection in the target stresses. Small later differences can persist from altered shares/cash even without a contemporaneous trigger. This version does not justify another threshold search under a different name.

The sealed `studies/e01-joint-downside/typed-evaluation.json` independently recomputes required full/forward/2023 metrics and annual/stress/monthly results, checks exact control reproduction, and records uncertainty/recovery comparisons. Both candidates are rejected. No unsupported nonnumerical gate is marked passed and no activation authority is granted.

## Numerical promotion controls and next step

The new typed evaluator verifies finalized sealed records against expected profile/config/source/data/run/study/calendar identities, normalizes typed cost fields, derives metrics from equity histories and tests all-cost returns, drawdowns, original-anchor limits, QQQ and 2023. It separately requires source-bound independent validation artifacts. Testing actual records exposed and repaired an integer-versus-float canonical configuration hash issue in this new evaluator; native economics did not change, and both evaluator source versions are archived.

The old receipt consumer remains disabled. A tested numerical module does not, by itself, authorize automatic default updates; reviewed integration and a prospective policy amendment are still required. All previous valid and rejected candidates remain historical benchmarks. The user's incremental policy permits a verified improvement over the current baseline without an old-frontier dominance veto; no overall historical-best claim is made here.

The next scheduled cycle should audit actual risk-exit/re-entry delays and prior recovery studies before preregistering priority 2. If no distinct restoration mechanism exists in this already-daily strategy, document the duplication and advance to per-asset recipe routing rather than manufacture a new variant. Keep v14.3.0 as each next control until a verified winner passes every gate. The OpenRouter free-model arm remains a separate research-only experiment with no new successful inference or portfolio performance claimed in this run.

## Dashboard and verification

The local Performance dashboard publishes sealed measured results, a single 2/6/10 bps selector, monthly-observation growth/drawdown plots, annual/stress comparisons, all monthly returns and weak-month asset contributions. Rejected rows are comparison evidence, not active selectable trading profiles. The source publication retains immutable archived versions and references the independent reviews. Backend failures hide unverified publication data.

Completed targeted evidence at this checkpoint: 76 AMAT affected parity/accounting/planning tests, 394 full executor tests, 33 numerical-gate tests, 7 independent policy causality tests, 12 foundation/planner tests, and 7 idealized total-return tests. Frontend production build passed. API/launcher regression suite passed 147 tests at the integrated source state, including six focused campaign publication tests. The complete AMAT unit sweep finished with **3 failed, 3,502 passed and 2 skipped in 1,172.22 seconds**. Two failures were pre-existing expectations for the superseded daily-core default; the two corrected files passed all seven tests. The third is a cross-counterfactual stress-test assertion: a missed fill changes later holdings and other missed instructions, so aggregate misses need not exceed baseline misses plus injected drops. Its corrected regression now matches every injected miss to its original instruction and verifies it did not fill. A separate fixed twenty-order fixture proves exactly the tenth and twentieth instructions are withheld: two recorded misses and eighteen fills. The combined stress, native execution/accounting, default-selection and retirement rerun passed **51 tests**, with no economic source change. Both original failure evidence and corrected JUnit results are sealed in `stress-counterfactual-test-review.json`. The entire long suite was not rerun after these test-only repairs; its original result remains accurately recorded. The original full-run failures are retained rather than relabeled as an initially clean run.


The integrated browser view was checked at 1280, 768 and 390 pixel widths: the campaign and each table wrapper stayed within their container. All three cost selections produced the measured values, and keyboard inspection changed the selected monthly observation. The viewport was restored. Saved browser proof and source/artifact hashes are in the campaign `dashboard-verification.json`; a standalone 6 bps comparison is available in `figures/foundation-e01-comparison-6bps.png`.

Manual run integration completed at **2026-10-23T17:56:17.258354+00:00**, 30.82 minutes after entry, within the forty-minute ceiling. Twenty-one measurement outputs completed across three serialized controller receipts (nine native foundation, nine first-experiment, three idealized QQQ references). One actual experiment, two rejected candidate arms, zero promotions. No measurement process remains active. The hourly campaign remains active locally, with the next new opportunity at 18:40:10 UTC.
