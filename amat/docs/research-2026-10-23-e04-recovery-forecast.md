# E04: recovery-aware buy-cost forecasting

This run investigates why E03's buy-cost gates damaged 2023 and tests one fixed, causal alternative against v14.3.0. The October 23 manual cycle began at 20:17:22 UTC and must finish or checkpoint by 20:57:22 UTC. It replaces the scheduled 20:40 opportunity; it is not an additional hourly tick.

## Functionality and measurement scope

The current default is native long-or-cash allocation across eleven ETFs. This experiment does not add direct shorts, inverse instruments, intraday orders, stop-losses or take-profits. It only permits or vetoes optional whole-share purchases already requested by the current allocator. Native eligibility, group selection, mandatory reductions and exits, portfolio limits and account ownership remain in force. No previous authorization or quantity is revived.

The fixed measurement uses the verified SIP snapshot, full 2016 indicator seeding, a flat $100,000 account from January 3, 2017 through October 12, 2026, observed next-session 09:30–09:35 VWAP, explicit corporate actions, whole-share buys, native exits, the observed 1% opening-volume entry cap, missed fills and zero cash yield. Each arm is measured at 2, 6 and 10 bps per side. Annual 2020–2026 slices are chronological evaluations of the continuous account, not newly trained folds or untouched holdouts. This hypothesis was informed by previously observed history.

The native simulator sells first and processes buys in its deterministic order. An unaffordable requested buy can be rejected rather than resized. Decision-close fee and opportunity estimates therefore cannot guarantee a next-session fill. The broker's staged-limit fill contract and a new policy's exported native session plans remain separate validation requirements. Research does not change the running paper bots.

## Why E03 hurt 2023

The independent reconstruction reproduced 162 logged decisions across both E03 variants and all costs during October 24–November 30. Planned whole-share quantities and permits matched; reconstructed fee, variance and benefit arithmetic agreed within 1e-12. Monthly asset P&L reconciled to equity changes.

At 6 bps, v14.3.0 returned 5.7403% in 2023; E03's return-and-covariance gate returned 3.3734%, and its return-only comparison returned 4.1659%. Their November shortfalls were 2.3193 and 1.6924 percentage points respectively. November explains most of the annual damage.

Four mechanisms contribute:

1. The trailing 63-session forecast retained earlier weakness after prices recovered. On November 3, QQQ and SPY had gained about 6.5% and 5.8% over five sessions, while their 63-session adjusted returns remained negative.
2. All-or-none batch admission coupled different instruments. On November 9, the covariance account's five-session expected NAV contributions were QQQ +3.733 bps, SPY −5.183 bps and GLD +0.420 bps. The joint gate rejected the batch.
3. The covariance penalty raised the hurdle further near a decision boundary. November 10's return-only account permitted a 4.088-bps estimated benefit against a 4.066-bps fee. The covariance account had a different ledger, a 4.072-bps return proxy, a 0.895-bps variance penalty and a 4.046-bps fee, and rejected. This verifies the arithmetic mechanism without attributing the entire strategy difference to a common-account covariance effect.
4. Account paths then changed execution feasibility. At 6 bps the baseline first held QQQ again November 6 and SPY November 9. The gated accounts held QQQ November 13/14 and SPY November 27. SPY permission had already returned November 14: later insufficient-cash rejections explain part of the remaining delay. Repeated blocked proposals are not a count of lost fills.

See the [complete all-cost forensic analysis](../outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/e03-2023-diagnostic-v2.md) and its [holdings chart](../outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/e03-november-2023-holdings.png). Monthly return differences do not sum to compounded annual differences, and differing account paths prevent isolated trade-level causal attribution.

## Preregistered experiment

The three arms are unchanged v14.3.0, a conditional recovery forecast, and an always-fast forecast comparison. The conditional rule changes an asset's trailing mean forecast from 63 sessions to 20 only when its completed close strictly exceeds the maximum of the previous 20 completed closes, excluding the current close, and its trailing 63-session mean log return is nonpositive. Every asset uses the same rule. The always-fast arm uses the 20-session forecast throughout.

Both candidates retain E03's five-session opportunity horizon, risk aversion of one, annual covariance, log-to-arithmetic conversion, actual whole-share requested buy set, immediate one-way cost estimate and joint admission decision. The candidate cannot increase a native requested target or veto a required reduction. This is recovery-aware forecast selection, not an assertion that a previous position or blocked authorization must be restored. False breakouts, noisy forecasts, cash constraints and batch vetoes remain possible failure modes.

Independent review freezes the exact implementation and economic settings before outcomes. No calendar, year or ticker exceptions, outcome-driven threshold grid or post-result variants are permitted. The earlier unconditional 63-session E03 results remain archived benchmarks rather than a fourth new arm.

All promotion gates remain unchanged: full-history and 2020-forward returns at every cost must match or exceed the current baseline with at least one strict gain; 2023 must be preserved at every cost; drawdowns must satisfy the current default, original anchor and matched QQQ limits. Independent uncertainty, causality, accounting, stress, recovery, reproduction, native planning and execution validation are required. A numerical pass alone cannot promote a research callback.

## Source provenance

Preflight found one previously documented foundation-manifest difference: the separate numerical promotion evaluator had received a float-versus-integer configuration-digest correction. All other 312 recorded library files matched. The evaluator was not loaded by native measurement entrypoints; historical loaded-source attestations remain valid. Original evidence is preserved. E04 records both manifests, accepts only the exact independently reviewed evaluator change, pins all 313 current source hashes, checks them before and after each measurement, and reproduces the control. No economic engine code changed for this correction.

## Results

The completed outcome and independent decision are recorded below.

## Completed results and decision

All nine bounded native measurements completed successfully at 20:35:16 UTC (272.50 seconds). The unchanged control reproduces all 2,457 complete account-date records exactly at each cost against the foundation. All source and data checks passed. No background measurement processes survived cleanup.

**Both candidates are rejected. v14.3.0 remains the current default.** No native active profile, default configuration, execution authority or remote deployment changed.

| Cost per side | Arm | Full-history net return | 2020 onward | Maximum drawdown magnitude | 2023 net return |
|---|---|---:|---:|---:|---:|
| 2 bps | v14.3.0 control | 296.3919% | 211.8904% | 12.5905% | 6.5464% |
| 2 bps | Recovery-aware buy gate | 289.6660% | 206.9933% | 13.2060% | 6.6905% |
| 2 bps | Always-fast comparison | 257.9928% | 200.0622% | 12.7356% | 8.1143% |
| 6 bps | v14.3.0 control | 259.7973% | 192.7199% | 12.8368% | 5.7403% |
| 6 bps | Recovery-aware buy gate | 256.8773% | 191.0146% | 13.8182% | 4.7429% |
| 6 bps | Always-fast comparison | 235.5310% | 185.1792% | 13.0940% | 6.4248% |
| 10 bps | v14.3.0 control | 221.8801% | 177.6031% | 12.9820% | 5.0710% |
| 10 bps | Recovery-aware buy gate | 234.1046% | 181.1298% | 14.0977% | 4.1929% |
| 10 bps | Always-fast comparison | 218.4119% | 176.1717% | 13.2681% | 5.9176% |

The recovery-aware arm fails full-history and forward return floors at 2 and 6 bps, full/forward drawdown limits at all three costs, and 2023 preservation at 6 and 10 bps. Its gains at 10 bps cannot override those failures. The always-fast arm preserves 2023 at all costs but fails all six full/forward return floors and worsens full/forward drawdowns at every cost. The independent typed evaluator recomputed these comparisons from sealed account histories; this is not a self-asserted gate receipt.

At 6 bps, the conditional rule improves November from E03’s 1.1653% to 3.0524%, but remains below v14.3.0’s 3.4846%. Its 2023 return improves from E03’s 3.3734% to 4.7429%, still below the baseline’s 5.7403%. At 2 bps it modestly improves 2023; at 6/10 bps it does not preserve the baseline. Fixing the motivating month did not fix the complete strategy.

## Was the recovery mechanism active?

The independent effect audit proves the conditional rule never lowers the selected mean or benefit and never rejects a same-account buy batch that its legacy 63-session forecast would permit. Across 2023, at each cost it touches seven genuine whole-share buy decisions and changes six same-account rejections to permits. These are proposed decisions, not six extra fills or independent successful predictions.

At 6 bps, the recovery rule admits the QQQ/SPY batch on November 7: estimated benefit 8.060 bps of NAV versus a 3.557-bps fee; the old forecast on that same candidate account gives only 0.133 bps. QQQ is actually held November 8, while SPY is still cash-constrained. The strategy eventually holds SPY November 13. The old E03 covariance gate held them November 14/27; the default held them November 6/9. The alternative corrects part of the diagnosed lag while retaining cash and batch limitations.

Over the full 6-bps history, recovery conditions intersect 28 whole-buy asset-sessions on 27 decision closes. Twenty same-account batch rejections become permits; none become rejections. This is a local admission counterfactual on the candidate’s actual ledger, not a replay of the historical E03 account or revival of old intent.

## Costs, adverse periods and recovery

| 6-bps measure | v14.3.0 | Recovery-aware | Always-fast |
|---|---:|---:|---:|
| Modeled fees, dollars | 24,604.44 | 23,246.91 | 19,760.88 |
| Filled trades | 1929 | 1790 | 1587 |
| Missed-fill records | 2868 | 2613 | 2171 |
| Average exposure | 86.26% | 85.09% | 82.17% |
| 2018 Q4 net return | -7.07% | -5.50% | -5.54% |
| COVID-window net return | -9.54% | -7.78% | -6.85% |
| 2022 stress net return | 2.42% | 1.27% | -0.39% |

Fewer fills and lower modeled fees do not establish an economic improvement. At 6 bps annual turnover falls from approximately 23.42× to 22.16×/19.81× and exposure also falls. The conditional rule improves the 2018 Q4 and COVID slices but reduces 2022 stress performance. Longest underwater duration is 446 sessions for recovery and 437 for always-fast versus 433 for the baseline. Missed-fill counts include whole-share rounding residuals and repeated native constraints; they are not all completely missed orders.

The descriptive paired three-month block-bootstrap 95% intervals for the annualized log-return difference at 6 bps are −0.94 to +0.77 percentage points for recovery and −1.94 to +0.49 for always-fast. Both include zero. These intervals are conditional on reused history, not independent prospective evidence or a multiple-search-adjusted significance pass.

## Every 2023 month at 6 bps

| Month | v14.3.0 | Recovery-aware | Always-fast |
|---|---:|---:|---:|
| 2023-01 | 3.4236% | 3.4249% | 3.4433% |
| 2023-02 | -5.2762% | -5.3467% | -4.7378% |
| 2023-03 | 1.8434% | 1.7193% | 2.2721% |
| 2023-04 | 1.7721% | 1.3519% | 1.7835% |
| 2023-05 | -0.3630% | -0.3341% | -0.3608% |
| 2023-06 | 3.3976% | 3.4695% | 3.2580% |
| 2023-07 | 2.6014% | 2.6634% | 2.5967% |
| 2023-08 | -1.0189% | -1.2228% | -1.3449% |
| 2023-09 | -3.1044% | -3.0578% | -3.0295% |
| 2023-10 | -3.5645% | -3.4009% | -3.3982% |
| 2023-11 | 3.4846% | 3.0524% | 3.3747% |
| 2023-12 | 2.9313% | 2.8378% | 2.8818% |

## QQQ context and remaining validation

The matched native QQQ reference at 6 bps returns approximately 555.08% over the full history and 262.47% from 2020 onward, with a 34.75% full-history drawdown. The current default and both candidates have substantially smaller drawdowns but still trail QQQ’s cumulative return. This study does not claim the broader QQQ objective has been achieved. The idealized dividend-reinvestment reference is displayed separately from executable native QQQ.

Both candidates already fail numerical acceptance, so there is no reason to create an active production profile or enable promotion. Native candidate session-plan/export parity, the broker fill contract and reviewed typed promotion-consumer integration remain unresolved requirements. Their status is not inherited from the baseline and is not marked passed by this research.

The next useful research direction is to audit the affordable, executable buy set and whether per-intent admission can avoid one weak forecast vetoing another instrument. Any change to cash allocation or execution accounting requires a new prospective contract and fair control/QQQ replays; comparing a repaired candidate to stale engine results would be invalid. Re-running these rejected forecast settings under new names is not a new hypothesis.

## Verification and saved evidence

- Eight new policy/provenance unit tests and independent native future-mutation prefix replays passed. The combined affected policy, numerical-evaluator and publication regression suite passed 54 tests in 14.62 seconds.
- The local API/launcher suite passed 165 tests in 84.70 seconds, with the existing Starlette/httpx test-client deprecation warning. The UI production build passed; lint and formatting checks passed. No production economic engine source changed.
- The study protocol, commands, data/source identity, exact-source independent review, all nine measurements, typed evaluation, diagnosis, uncertainty, monthly/stress/recovery detail and exact driver/helper/test/evaluator byte archives are preserved.
- The dashboard publication retains all previous numerical benchmarks and adds the six all-cost candidate rows, for 36 rows total. Detailed growth, drawdown, annual, stress and monthly views focus on this experiment.

Study: `outputs/research-campaigns/2026-10-23-adaptive-12h/studies/e04-recovery-forecast/`

Study protocol canonical SHA-256: `974c783a4962fa8b5d530835f88095f6b3dae17fb76f1b6b4c53a021a4650060`. Independent typed evaluation: `df61a83f33ee3789eef151e72fe3865ce0165aa9a6987472d0e6254da92ac999`.

![Six-basis-point comparison](../outputs/research-campaigns/2026-10-23-adaptive-12h/figures/e04-recovery-forecast-6bps.png)


## Independent dated drawdown addendum

The conditional arm’s largest adverse monthly difference at 6 bps is August 2022: −3.1160% versus the baseline’s −1.4647%, a 1.6513-percentage-point shortfall. Its exposure, fees and filled-trade count are all lower in that month. The aggregate evidence supports timing/composition/carry-in portfolio paths as hypotheses; it does not identify a particular asset cause or prove a false breakout.

At 6 bps, the default’s maximum full-history drawdown is the February 19–March 31, 2020 decline, recovered July 22. The conditional arm’s maximum instead occurs from June 8, 2022 to March 15, 2023, with recovery on March 20, 2024 after 446 sessions. In that same 2022–23 episode, the default loses 12.3817% and recovers March 1, 2024 after 433 sessions, versus the candidate’s 13.8182%. The always-fast comparison loses 13.0940% and recovers March 7, 2024 after 437 sessions. The new rule therefore increases the depth and duration of a different adverse regime even while improving the motivating November rebound.

These details are bound to the independent sealed `independent-causal-limits-addendum.json`. This final report version retains the earlier sealed report and receipt unchanged.
