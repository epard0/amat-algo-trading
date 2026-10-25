# Twelve-hour performance campaign — progress report

**Execution-contract correction — cycle 006:** Earlier campaign tables used a native engine that allowed fractional fills, despite being described as whole-share simulations. Those values remain preserved for audit, but are not whole-share promotion benchmarks. Cycle 006 reproduces the old default exactly, fixes share rounding, and reports corrected results. The default strategy itself is unchanged.


Campaign remains active through October 20, 2026 at 11:17:38 AM America/Detroit. This report records actual completed work, not scheduled opportunities.

## Cycle 001 — broader participation

**Rejected. Overall default remains all-groups + volatility-triggered cost-aware switching (v14.3.0).**

Hypothesis: the four-position limit may exclude positive opportunities from the third group. Raise only the global selected-position limit from four to six, allowing at most two per each existing group. Keep signal eligibility, group logic, native sizing, risk/position/group caps, cost-aware switching, rebalance bands and execution unchanged. One fixed, new configuration was registered before outcomes. The economic configuration digest does not match a prior audited study.

| Approach | Return 2 bps | Return 6 bps | Return 10 bps | Drawdown 6 bps | 2020 onward 6 bps | 2023 6 bps |
|---|---:|---:|---:|---:|---:|---:|
| Current best | 290.52% | 264.58% | 226.81% | 12.77% | 198.77% | 5.79% |
| Up to six positions | 261.17% | 241.84% | 210.63% | 14.59% | 181.68% | 2.56% |

At 6bps the candidate lost 22.74 percentage points of full-history return, worsened drawdown from 12.77% to 14.59%, and reduced 2023 from 5.79% to 2.56%. Forward-period return also fell. It fails independently of unresolved historical coverage. No promotion or new active strategy profile is justified.

More positions did not simply raise total fees: full-history assumed fees fell from $24,802 to $19,721 and annualized turnover fell from 23.62x to 20.85x. However, fill count rose from 1,943 to 2,216, missed instructions from 732 to 968, and average invested exposure from 86.53% to 87.40%. These measurements show that lower dollar turnover/fees alone did not rescue the broader selection. They are not a per-asset causal attribution.

## Stress comparison at 6 bps

| Approach | 2018 Q4 return | COVID window return | 2022 return |
|---|---:|---:|---:|
| Current best | -7.08% | -9.47% | 2.42% |
| Up to six positions | -7.29% | -11.32% | 2.58% |

The candidate slightly improved 2022 but worsened the 2018 and COVID windows. Native monthly, annual 2020–2026 and recovery evidence is sealed in the trial records. These are fixed-rule chronological forward slices on continuing accounts, not freshly trained annual models or untouched holdouts.

## Historical comparison audit

The initial read-only inventory covers 26 study protocols and 451 source-produced trial records, including rejected arms, plus 43 read-only legacy ledgers containing 354 experiment identities. Of the comparable records there are 182 distinct session histories, with duplicate identities preserved. All native comparators require matching dataset hashes, execution dates, cash, entry participation, known native-ledger schema, completed evidence and unchanged frozen source. Old ledger objectives are not copied into this return frontier without fair contract review/replay.

| Cost per side | Highest verified full-history return | Highest verified 2020-onward return |
|---|---:|---:|
| 2 bps | 293.05% | 229.85% |
| 6 bps | 264.58% | 206.08% |
| 10 bps | 239.11% | 187.01% |

These maxima can come from different candidates. For example, the 2bps full-history floor comes from the previously rejected 40% concentration-cap candidate, while the 10bps floor comes from the always-on switching hurdle. Earlier rejected strategies remain binding comparators; a new candidate must clear the full per-cost/period frontier.

Coverage is not complete. Fifteen study/record issues and 43 legacy ledger contracts require classification, independent verification or fair replay. Some have shorter history, a different mandate or a different execution model. No overall-best promotion is permitted until relevant coverage is resolved; neither high legacy headline objectives nor known-invalid attempts are silently treated as comparable data. The inventory and exact reasons are saved in `audit-001.json`.

## Campaign infrastructure delivered

- Single-writer file lock, durable run/process identity, deadline checks, bounded measurement subprocess and immutable source/config copies.
- New sealed proposal, audit, protocol and record gates. Exact incumbent session history reproduced at all costs.
- Historical frontier includes rejected arms and gates incomplete coverage. Economic names/versions cannot evade deduplication.
- A bounded, sealed latest-completed-cycle publication drives the dashboard, so future cycles can publish new labels/results without hardcoding another study page.
- Campaign visual audit shows incumbent/candidate returns, the partial historical frontier, costs, annual/stress comparisons and coverage limitations. Rejected strategies are blocked from active selection.

## Next cycle guidance

Unconditionally expanding to six positions was falsified. Preserve the current four-position control. Choose a distinct mechanism supported by diagnostics; do not retune failed volatility percentiles or simply rename the six-position configuration. Resolve historical contract/replay coverage incrementally, prioritizing meaningful competitors and the strongest forward-return records. An engine improvement should be opt-in and isolated, or use explicit frozen-engine controls; never alter baseline behavior or add a meaningless parameter just to evade deduplication. Useful research/engineering improvements can stand independently of strategy promotion, with appropriate tests and no claim of return outperformance.

## Measurement limits

2017-01-03–2026-10-12 from flat $100,000, all 2016 signal history, verified Alpaca SIP eleven-ETF core snapshot, dividends/splits, native next-session 09:30–09:35 VWAP, 1% opening-volume entry cap, whole shares, missed fills, native exit policy and zero cash yield. Costs 2/6/10bps per side are assumptions, not observed historical spreads. Source/record seals and actual data partition bytes were checked. All history has prior research exposure; no future profitability or global optimality is claimed. No hosted LLM proposal/review is claimed for this Codex-generated hypothesis.

Current default and disabled/dry-run execution flags remain unchanged. Cycle 001 evidence lives under `amat/outputs/research-campaigns/2026-10-19-12h-performance/cycles/001-six-positions/`.

## Verification completed for cycle 001

Seven targeted strategy/campaign tests and all 65 dashboard backend tests passed. Lint/type checks and the frontend build passed. Browser verification passed for the dynamic campaign publication, historical frontier table, rejected selection blocking, cost/stress views and mobile layout; the previous percentile study also passed its regression browser check. The writer lock is released, no measurement is active, the default/raw authorization hash is unchanged, and dry-run plus execution-disabled flags remain true. The finite twenty-minute heartbeat remains active.

## Cycle 002 — unanimous trend confirmation (rejected)

Preregistered one native change: require all three trailing trend horizons to agree, versus two in the default. No fitted thresholds or new execution assumptions. The current default reproduced exactly at all three costs.

| One-way cost | Default full return | Candidate full return | Default 2020-onward | Candidate 2020-onward |
|---|---:|---:|---:|---:|
| 2 bps | 290.52% | 238.34% | 213.94% | 167.98% |
| 6 bps | 264.58% | 210.07% | 198.77% | 149.89% |
| 10 bps | 226.81% | 179.16% | 178.16% | 135.01% |

At 6 bps, drawdown worsened from 12.77% to 13.32%; 2023 fell from +5.79% to −2.27%. Fees increased from $24,802 to $27,149 and annualized turnover from 23.62x to 27.23x, despite average exposure dropping from 86.53% to 79.85%. Stricter eligibility did not deliver the proposed reduction in churn. Candidate outperformed in 58 of 118 months, but its largest relative monthly losses included March 2023 (−4.55 percentage points), July 2026 (−3.70), January 2023 (−3.33) and November 2023 (−3.22). These are descriptive portfolio comparisons, not a per-asset causal attribution.

| Stress window, 6 bps | Default | Candidate |
|---|---:|---:|
| 2018 Q4 | -7.08% | -5.24% |
| COVID window | -9.47% | -7.53% |
| 2022 | 2.42% | 6.01% |

The paired monthly block-bootstrap annualized log-return difference interval is −3.70 to +0.29 percentage points (95%, unadjusted for repeated research). Stress-window improvements do not offset lower full/forward returns at every cost, worse overall drawdown and worse 2023. Rejected; no active profile or default change.

Validation: 99 targeted selection, accounting, campaign and default tests passed from the AMAT directory. An initial invocation from the workspace directory produced three relative-path fixture failures; rerunning with the required project working directory passed. Browser checks passed for latest publication, frontier, rejected selection, cost/stress views and mobile. Native measurement verified observed data hashes and reproduced incumbent session hashes at every cost.

Audit 002 includes 185 distinct comparable histories, with rejected candidates preserved. Sealed replay triage identifies 15 unresolved study contracts, including 40 native recipe identities; 43 legacy ledgers remain inventoried. Triage does not resolve or waive any benchmark. Promotion remains blocked until coverage is resolved. Reused history and continuing-account annual slices are not untouched holdouts. No hosted model proposal/review was claimed.

Next: prioritize comparable replay of older native recipes and investigate selective protection that preserves early recoveries; do not retest unanimous confirmation or six-position expansion under new names.

## Cycle 003 — minimum trade value and historical replays

One preregistered new strategy ablation raised the minimum trade notional from $25 to $250. It produced **exactly identical session, monthly, recovery and metric histories at 2/6/10 bps**. The current incumbent also reproduced exactly against its original sealed reference. No benefit; rejected, with no active profile or default change. The existing 2% weight-change threshold appears to dominate this small notional threshold in this sample; avoid another below-band notional tuning exercise. Paired monthly uncertainty interval is exactly zero because histories are identical.

At 6 bps the unchanged results are 264.58% full-history return, 198.77% 2020-onward return, 12.77% maximum drawdown, +5.79% in 2023, 1,943 fills and $24,802 in fees. Annual, stress, monthly and recovery views are unchanged.

A separately preregistered methodology replay measured five older daily-group-validation native configurations under the shared 2017–2026 contract with native 1% entry cap, 2016 warmup, observed VWAP, corporate actions and all three costs. The current best was replayed again as a control. Economic configurations and historical identities are preserved in sealed mapping receipts. These are historical comparators, not fresh improvements or active choices.

| Historical strategy (6 bps) | Full return | 2020 onward | Drawdown | 2023 |
|---|---:|---:|---:|---:|
| Current best · all groups + volatility switching | 264.58% | 198.77% | 12.77% | 5.79% |
| Historical replay · Daily selection | 216.18% | 152.35% | 13.49% | 3.91% |
| Historical replay · v13 reference | 233.56% | 146.80% | 15.02% | 0.44% |
| Historical replay · Daily groups | 220.32% | 173.13% | 16.57% | -6.77% |
| Historical replay · Groups every 5 days | 220.17% | 173.56% | 13.88% | -6.94% |
| Historical replay · Daily groups + trend quality | 197.65% | 154.03% | 16.41% | -6.72% |

The post-replay audit contains **200 distinct comparable histories**. None of these five historical replays raises the per-cost full/forward frontier. Full monthly, annual, stress, turnover, cost and recovery evidence is sealed in `cycles/003-small-trade-floor/benchmark-replay`. The dashboard displays all 18 control/comparator cost rows alongside the main campaign results.

Five native recipe identities now have explicit source-bound replay receipts. The original study also contains rules, recovery and ML overlays: those four arms were deliberately not misrepresented by replaying their bare configs. Fifteen historical study contracts and 43 legacy ledgers remain under review; coverage stays incomplete and promotion stays blocked. The receipt is partial progress, not a waiver.

Verification: 36 targeted campaign/accounting/default tests passed; production TypeScript/Vite build passed; browser checks passed for sealed latest publication, historical replay table, costs/stress, rejected-candidate exclusion and mobile layout. No engine/accounting behavior changed. Reused historical data and continuing-account annual forward slices remain exposed research data. No hosted proposal/review was claimed.

Next: use sealed replay receipts to build strict partial resolution tracking, replay further fixed native arms and review external-overlay/fitted contracts. Seek a mechanism affecting meaningful orders or preserving recovery participation; do not repeat the $250 floor, unanimous confirmation, six-position or rejected percentile/volatility variants.

## Cycle 004 — combined metals risk cap (rejected)

Preregistered one structural risk change: lower the combined GLD/SLV cluster cap from 45% to 30%, with no reallocation of clipped exposure and no changes to eligibility, switching, order authorization, native exits or execution costs. This transfers an older A1 risk-control idea to the incumbent rather than applying a new trend or volatility gate. The incumbent reproduced exactly at 2/6/10 bps.

| Cost per side | Default full return | Candidate full return | Default 2020 onward | Candidate 2020 onward |
|---|---:|---:|---:|---:|
| 2 bps | 290.52% | 262.48% | 213.94% | 191.54% |
| 6 bps | 264.58% | 237.06% | 198.77% | 177.48% |
| 10 bps | 226.81% | 198.02% | 178.16% | 157.90% |

At 6 bps, 2023 improves from +5.79% to +10.06%, maximum drawdown from 12.77% to 12.25%, fees from $24,802 to $23,797, fills from 1,943 to 1,864 and annualized turnover from 23.62x to 23.01x. Average exposure drops from 86.53% to 83.55%. Sharpe rises slightly from 1.145 to 1.173, but the campaign requires net-return dominance, not a substituted risk-adjusted objective. Full and forward net returns fail at every cost. No candidate profile retained; no default change.

Stress-window returns at 6 bps: 2018 Q4 −7.08% default versus −7.15% candidate; COVID −9.47% versus −9.13%; 2022 +2.42% versus +1.82%. Largest relative monthly shortfalls include July 2020 (−2.84 percentage points), January 2026 (−2.26) and December 2025 (−1.73); November 2023 improves by 2.12 points. These portfolio comparisons suggest a persistent cap gives up useful upside participation as well as limiting losses; they are not per-trade causal attribution. Paired monthly block-bootstrap annualized log-return difference 95% interval: −2.32 to +0.50 percentage points, unadjusted for repeated research.

Engineering improvement retained: the auditor now verifies partial historical replay coverage from sealed receipts. It checks original frozen source/protocol identity, original/replayed economic configuration identity, every 2/6/10-bps comparable record identity and supported native arm semantics. An invalid receipt cannot partially bless earlier entries. Five replayed identities verify; overlay arms cannot masquerade as bare native recipes. Receipt proof is informative and never clears unresolved contract coverage automatically. The cycle publisher carries forward the earlier replay table only while its receipt remains verified; the dashboard displays the verified replay count.

Post-cycle audit: 203 distinct comparable histories, five verified replayed historical identities, 15 study-contract issues and 43 legacy ledgers still unresolved. Promotion remains blocked. The current-best/candidate cost, monthly, annual and stress views and earlier replay table remain available in the dashboard.

Validation: 38 targeted campaign/replay/accounting/default tests passed, including forged-receipt tests for missing cost identity, changed economic configuration and unsupported rules overlays. Ruff and mypy passed for the changed auditor; the cycle wrapper passed Ruff; TypeScript/Vite production build passed. Browser cost/stress/rejected-selection/replay-table/mobile checks passed. No fitted model, hosted proposal/review, engine accounting change or executor activation occurred. History remains previously researched.

Next: use the favorable 2023 protection evidence to motivate a genuinely selective risk mechanism that preserves metals upside; preregister before measuring and do not repeatedly tune static caps. Continue fixed native historical recipe replays and stricter partial coverage tracking; inspect overlay/fitted strategies under their real semantics.

## Cycle 005 — faster trend horizons and eight historical replays

One preregistered native strategy transfer replaced 63/126/252-session horizons with the older C1 recipe, 21/63/126. All-groups selection, two-horizon agreement, four positions, volatility-triggered switching, portfolio caps, native authorizations/exits and realistic execution stayed fixed. The current incumbent reproduced exactly at all three costs.

| Cost per side | Default full return | Candidate full return | Default 2020 onward | Candidate 2020 onward |
|---|---:|---:|---:|---:|
| 2 bps | 290.52% | 166.60% | 213.94% | 140.23% |
| 6 bps | 264.58% | 133.97% | 198.77% | 119.48% |
| 10 bps | 226.81% | 100.95% | 178.16% | 97.69% |

Rejected: at 6 bps maximum drawdown rises from 12.77% to 16.07%, 2023 falls from +5.79% to +4.42%, fees rise from $24,802 to $33,781 and annualized turnover from 23.62x to 40.98x. Fills increase from 1,943 to 3,121, missed fills from 732 to 916, while average exposure falls from 86.53% to 82.84%. Full and forward net returns fail at every cost. No active profile or default change.

At 6 bps, 2018 Q4 improves from −7.08% to −2.03%, COVID from −9.47% to −9.24%, but 2022 drops from +2.42% to −0.66%. The largest monthly relative shortfalls include February 2018 (−7.28 percentage points), September 2025 (−4.53), January 2025 (−4.38) and October 2022 (−3.57). March 2026 improves by +7.38 points, showing isolated recovery gains did not overcome the larger churn/return penalty. These are descriptive portfolio differences, not causal per-asset explanations. Paired monthly block-bootstrap annualized log-return difference 95% interval: −7.90 to −1.09 percentage points, unadjusted for repeated research; no untouched-holdout claim.

A separate preregistered common-contract replay measured eight historical v13-native arms: baseline, lower metals cap A1, lower symbol cap A2, inverse-volatility A3, daily selection B1, narrower rebalancing B2 and shorter-horizon C1/C2. Every arm ran at 2/6/10 bps with full 2016 warmup, 2017 flat-account start, observed opening VWAP, corporate actions and the native 1% entry cap. The incumbent control reproduced exactly again. The D1 cash-policy overlay was explicitly excluded from bare-config replay and remains unresolved.

| Historical v13 arm (6 bps) | Full return | 2020 onward | Drawdown | 2023 |
|---|---:|---:|---:|---:|
| Historical replay · v13 lower metals cap | 197.50% | 122.06% | 12.65% | 2.75% |
| Historical replay · v13 lower single-asset cap | 228.66% | 143.98% | 15.19% | -0.27% |
| Historical replay · v13 inverse-volatility sizing | 193.17% | 120.00% | 13.86% | -0.74% |
| Historical replay · v13 daily selection | 216.18% | 152.35% | 13.49% | 3.91% |
| Historical replay · v13 tighter rebalancing band | 226.12% | 141.64% | 15.68% | -0.21% |
| Historical replay · v13 21/63/126-day trends | 113.33% | 81.73% | 16.94% | 3.21% |
| Historical replay · v13 10/21/63-day trends | 52.37% | 41.34% | 20.25% | 4.43% |
| Historical replay · v13 baseline | 233.56% | 146.80% | 15.02% | 0.44% |

Post-replay audit: **224 distinct comparable histories and 13 source/config/cost-verified historical identities**. Identical baseline and daily-selection histories from the two historical sources remain separate identified records with shared-history deduplication in the audit. None raises the existing per-cost full/forward frontier. The dashboard displays 42 control/historical cost rows, alongside the current candidate curves and monthly/annual/stress views.

Engineering retained: source-reviewed v13 native arms are now eligible for strict replay-receipt verification; D1 is forbidden from masquerading as a bare native arm. Tests cover valid eight-arm receipts and rejection of a re-signed D1 mapping. Older five-arm tests now isolate their original receipt, so subsequent campaign receipts cannot invalidate a fixed historical fixture. Publication carry-forward supports multiple receipts and requires every referenced receipt to remain verified. Coverage is still incomplete: 15 historical study-contract issues and 43 legacy ledgers remain, including external overlays/fitted strategies. No promotion waiver.

Validation: 39 targeted replay/campaign/accounting/default tests passed; Ruff and auditor mypy passed; browser publication/replay-table/cost/stress/rejected-selection/mobile checks passed. No frontend source change required this cycle. Incumbent file hash remains unchanged; no executor activation or trades. No hosted model proposal/review was claimed.

Next: prioritize a faithful D1 cash-policy replay to finish the first full historical native study contract, then other source-bound fixed recipes and overlay/fitted contracts. Do not continue faster-horizon tuning or recycle failed sizing/volatility experiments. A selective protection proposal needs a mechanism preserving useful trend persistence, with tests preregistered before measurement.


## Cycle 006 — execution-contract correction and cash-policy test

A source audit found the native backtest generated fractional orders/fills. This contradicted the campaign whole-share requirement. At 6 bps the old incumbent has 1,939 fractional fills, including 1,015 fractional purchases; its total return was 264.58%. Previous reports incorrectly described that measurement as whole-share. The exact old sessions were reproduced in a separately labeled fractional diagnostic at all three costs, preserving rather than overwriting the evidence.

The corrected mode rounds purchases and partial rebalances down after observed participation limits, records discarded quantity as a missed quantity_increment, and preserves native full exits. It can preserve corporate-action fractional entitlements without inventing cash-in-lieu; in the actual 33 corrected arm/cost histories here, **every fill is whole-share**. Fees use the actual filled quantity. Backtest defaults now use the corrected mode; explicit legacy mode remains available for reproduction. Frozen prior sources are untouched. Original intent/account/policy contracts and the live executor are unchanged.

| Cost per side | Corrected default full return | Corrected default 2020 onward | Cash-policy candidate full return | Cash-policy candidate 2020 onward |
|---|---:|---:|---:|---:|
| 2 bps | 296.39% | 211.89% | 292.91% | 211.45% |
| 6 bps | 259.80% | 192.72% | 252.44% | 193.43% |
| 10 bps | 221.88% | 177.60% | 222.07% | 177.21% |

At 6 bps the corrected default has 12.84% max drawdown, +5.74% in 2023, $24,604 fees, 1,929 fills, 23.42x annual turnover and 86.26% average exposure. Corrected stress returns: 2018 Q4 −7.07%, COVID −9.54%, 2022 +2.42%. These supersede earlier whole-share claims; differences from fractional results are engine effects, not a newly discovered strategy advantage.

The preregistered candidate applied the existing proportional buy-budget policy (1% cash reserve, 0.2% price buffer) to the incumbent under the same corrected execution. **Rejected**: lower full returns at 2 and 6 bps, lower forward returns at 2 and 10 bps. At 6 bps: return 252.44%, drawdown 12.86%, 2023 +8.38%, fees $26,111, 2,127 fills and 25.29x turnover. It improved 2023 but did not dominate the corrected incumbent. Its paired monthly block-bootstrap annualized log-return difference 95% interval is −1.29 to +0.87 percentage points, unadjusted for repeated research. Full annual, monthly, recovery and stress diagnostics are sealed; there is no fitted model or untouched-holdout claim.

The rejected cash-policy candidate still supplies the corrected 10-bps full-return frontier (222.07% versus default 221.88%) and 6-bps forward frontier (193.43% versus default 192.72%). Those stronger comparable results remain binding for future candidates despite rejection.

All nine historical v13 variants were replayed under the corrected common contract, including D1 with its explicit execution cash policy. Source/config/policy/cost record receipts verify the original identities. This resolves the first complete historical source-study contract. The five older daily-group replays and earlier candidate leaders still need whole-share remeasurement.

The new-contract audit has **33 distinct comparable histories, nine verified historical identities and one fully replayed source-study contract**. Its 34 pending contract/receipt issues and 43 legacy ledgers keep promotion blocked. The 224 prior fractional histories and their strongest returns remain intact in the earlier sealed audits; they are explicitly incompatible until replayed, not discarded for earlier rejection. Old receipts requiring whole-share replay are not treated as current comparable proof.

Dashboard: corrected default curves, candidate rejection, explicit legacy diagnostic, all costs/months/annual/stress views and 30 corrected historical/control cost rows are published. The fractional diagnostic and historical comparators are excluded from active selection. Older studies carry a quantity-contract limitation; candidate counts distinguish genuine candidates from diagnostics/replays. No active candidate profile or strategy-default promotion occurred.

Validation: 99 targeted native integration, execution-planning, accounting, research-control, replay and default tests; 66 backend tests; Ruff; mypy across six changed source modules; TypeScript/Vite production build; dashboard browser checks including costs, stress, rejected/diagnostic exclusion, replay table and mobile. Only the owned local dashboard API was restarted (PID 696097); no executor restart, activation or trades. Configured strategy profile hash remains unchanged. No hosted proposal/review was claimed.

Next priority: replay the former frontier identities (concentration_cap, faster4, all_always, wider_band) and other meaningful native candidates under corrected whole-share execution before proposing promotion. These are exact economic-config benchmark repairs, not recycled volatility ideas under new names. Also redo the five daily-group historical arms. Preserve all earlier evidence and keep the policy disabled for live execution.

## Cycle 007 — corrected frontier and daily-reference replays

This preregistered methodology cycle tested whether the former fractional frontier rankings survive the whole-share correction. It remeasured the exact former leaders, all five fixed volatility-study ablations and three original daily-group configurations, without tuning or claiming new trading candidates. Two identical whole-share v13 histories were reused for the original daily-validation baseline/B1 identities, preserving both source identities rather than repeating those measurements. The corrected incumbent reproduced exactly at every cost. All 33 measured arm/cost histories contain zero fractional fills.

| Corrected historical comparator, 6 bps | Full return | 2020 onward | Drawdown | 2023 |
|---|---:|---:|---:|---:|
| Historical replay · always-on cost switching | 254.42% | 195.87% | 12.89% | 6.33% |
| Historical replay · 40% single-asset cap | 274.83% | 206.19% | 12.59% | 6.44% |
| Historical replay · two-day rank confirmation | 233.07% | 175.80% | 14.24% | 5.04% |
| Current default · corrected whole-share execution | 259.80% | 192.72% | 12.84% | 5.74% |
| Historical replay · four-week group ranking | 222.69% | 199.57% | 13.24% | 7.08% |
| Historical replay · inverse-volatility sizing | 210.70% | 162.11% | 11.99% | 8.87% |
| Historical replay · larger switching hurdle | 248.77% | 187.32% | 12.84% | 6.12% |
| Historical replay · original daily groups | 213.76% | 167.20% | 16.33% | -6.50% |
| Historical replay · groups every five days | 238.12% | 183.93% | 12.83% | -5.57% |
| Historical replay · original trend quality | 200.70% | 159.06% | 16.49% | -6.82% |
| Historical replay · wider rebalance band | 249.33% | 191.96% | 12.84% | 4.90% |

The 40% single-asset cap has the strongest corrected full-history return at every cost: **308.83%, 274.83%, 235.86%** at 2/6/10 bps. It improves 6-bps drawdown to 12.59% versus default 12.84%, but is **not eligible for promotion**: its 2-bps forward return 223.88% trails four-week group ranking at 230.26%; its 10-bps forward return 183.81% trails the wider band at 187.75%. It also fails 2023 preservation at 2 bps (6.5175% versus 6.5464%) and 10 bps (4.1963% versus 5.0710%). No outcome-dependent tolerance was introduced. Historical coverage remains incomplete independently of these failures.

At 6 bps the cap reduces annual turnover to 22.58x, fees to $24,326 and average exposure to 85.28%. Stress returns are 2018 Q4 −6.76%, COVID −8.87% and 2022 +1.96%, versus the default −7.07%, −9.54% and +2.42%. Its largest relative monthly losses include April 2019 (−1.34 percentage points), November 2025 (−1.14) and August 2026 (−1.12); August 2024 improves by +2.08 points. These portfolio differences are descriptive, not per-asset causal attribution. Paired monthly annualized log-return difference 95% block-bootstrap interval: −0.30 to +1.15 percentage points, unadjusted for repeated research.

| Whole-share return frontier | Full history | 2020 onward |
|---|---:|---:|
| 2 bps | 308.83% | 230.26% |
| 6 bps | 274.83% | 206.19% |
| 10 bps | 235.86% | 187.75% |

The full-history frontier is the 40% asset cap at all three costs. The forward frontier is four-week ranking at 2 bps, the cap at 6 bps, and the wider rebalance band at 10 bps. Every point remains binding even when its source strategy fails another promotion guard. The source-bound assessment is sealed in `frontier-assessment.json`. No new profile or default change.

Audit: **62 distinct whole-share histories, 22 verified source strategy identities and two fully replayed original study contracts** (v13 improvement study and the five-ablation volatility study). Original daily-validation baseline/B1 reuse is explicitly sealed, with its five native arm identities recovered; four external-overlay arms remain unresolved. There are 33 outstanding contract/receipt issues and 43 legacy ledgers, so overall promotion coverage stays incomplete. All prior fractional evidence remains preserved.

A meaningful replay hazard was verified: original `all_always` and `all_combined` have identical native economic configurations, but different external condition masks. A re-signed receipt cannot relabel an always-on replay as the conditional strategy; a regression test demonstrates the case using the actual original configurations. New audit entries now expose condition-mask and cash-policy hashes as well as quantity-contract identity. External-mask strategies still require faithful mask reconstruction and cannot be cleared by config equality.

Dashboard: the primary section now explicitly asks how the historical strategies compare, rather than implying a newly proposed trading candidate. It shows the corrected comparison bars, monthly/annual/stress results and 60 historical/control cost rows. Historical comparators remain unavailable as active selections. Validation: 10 focused campaign/replay tests passed, including source-identity reuse and same-config/different-mask rejection; Ruff and mypy passed; TypeScript/Vite production build and browser publication/frontier/replay/cost/stress/mobile/active-selection checks passed. The last engine correction validation of 99 native and 66 backend tests remains unchanged; no engine behavior changed this cycle. No hosted review, executor action or trades.

Next: continue common-contract replay of meaningful source families, prioritizing all remaining native group-recovery arms and external-mask switching families. Preserve explicit mask identities; do not equate bare configurations. Classify legacy fractional receipts as superseded only with sealed replacement mappings, never delete them or waive missing evidence. No threshold tuning or strategy promotion until benchmark coverage is resolved.

## Cycle 008 — complete native group-recovery family

This preregistered benchmark-repair cycle measured five missing native group-recovery variants and the original always-on double-cost hurdle. It reused verified original two-group/four-week/always-on references through sealed configuration-equality and record bindings. The current corrected default reproduced exactly at 2/6/10 bps. All 21 measured arm/cost histories have zero fractional fills. No new trading candidate or parameter tuning was claimed.

| Corrected comparator, 6 bps | Full return | 2020 onward | Drawdown | 2023 | COVID window |
|---|---:|---:|---:|---:|---:|
| Historical replay · always-on double cost hurdle | 252.17% | 188.88% | 12.39% | 5.35% | -9.08% |
| Current default · corrected whole-share execution | 259.80% | 192.72% | 12.84% | 5.74% | -9.54% |
| Historical replay · Allow all three groups | 244.63% | 187.40% | 13.50% | 5.90% | -9.45% |
| Historical replay · All groups + SPY reference | 244.63% | 187.40% | 13.50% | 5.90% | -9.45% |
| Historical replay · Group ranking · SPY reference | 214.01% | 156.76% | 16.21% | -4.18% | -9.80% |
| Historical replay · Group ranking · six completed weeks | 190.26% | 179.75% | 13.19% | 0.21% | -1.96% |
| Historical replay · Six-week groups + SPY reference | 212.80% | 181.50% | 13.46% | 2.82% | -2.01% |

None raises the existing full-history or forward return frontier. Six-week ranking reduces the COVID-window loss from −9.54% to −1.96% but sacrifices full-history return (190.26% versus 259.80%) and leaves 2023 near flat (+0.21% versus +5.74%). The always-on double hurdle lowers drawdown to 12.39%, but its full return 252.17% and forward return 188.88% trail the default at 6 bps. The all-groups QQQ/SPY reference variants have identical complete session histories at every cost; both economic/source identities remain preserved, with duplicate histories indexed once. No profile or default change.

The audit now has **77 distinct whole-share histories, 31 verified original strategy identities and three fully replayed original study contracts**. The entire native group-recovery study is resolved. The all-groups switching family has its four static references recovered, while its four external-mask variants remain unresolved. 32 contract/receipt issues and 43 legacy ledgers still block overall promotion.

The frontier remains: 40% single-asset cap full-history returns 308.83% / 274.83% / 235.86%; 2020-onward returns 230.26% (four-week ranking at 2 bps), 206.19% (cap at 6 bps), 187.75% (wider band at 10 bps). These remain binding regardless of older retention decisions.

A source-bound external-mask replay design is saved in `external-mask-replay-design-008.json`. It records original gate-array digests at every cost, original two-/three-group parent configuration hashes, source record identities and matching recipe-module hashes. Mask features are deterministic and independent of fills, so a corrected replay can require exact equality to the original registered gate arrays before measuring outcomes. No masked outcomes or masked coverage were claimed this cycle.

Engineering retained: `verify_registered_switch_gates` now rejects missing/extra aliases, nonboolean gates and changed gate bytes before any arm is measured when hashes are preregistered. A real-condition-function regression demonstrates rejection when another parent-preference history is substituted. Receipt validation also forbids an external gate or cash-policy execution from masquerading as a bare native recipe. A new actual-data counterexample shows that the proportional-budget run shares the native reference config but cannot validate that reference. This closes the reverse direction of the identical-config overlay hazard.

Validation: 21 focused conditional-switching, interaction, campaign and replay tests passed, including original parent-prefix causality, false/true gate parity, exact source-identity reuse and preservation of the duplicate benchmark identity. Ruff and mypy passed for the auditor and study runner. Dashboard publication/frontier/replay-table/cost/stress/mobile/historical-selection exclusion checks passed. No frontend rebuild, engine execution change, hosted review or executor action was required this cycle. Original strategy profile hash is unchanged.

Next: execute the four original external-mask all-groups switching recipes using the saved design. Register the original gate hashes before measurement, reconstruct the original parent preferences explicitly, and abort on any mismatch. Only extend receipt coverage when the original mask hash and every replay record hash agree. Keep the corrected default as the reproduced control; never use economic config equality to substitute a different mask. Continue remaining fixed source families/legacy contract classification afterward; do not promote while coverage is unresolved.


## Cycle 009 — original condition-mask replay

Original volatility masks reproduce the native default exactly at 2/6/10 bps. Combined conditions reduce 6 bps fills from 1,929 to 1,862 and fees from $24,604 to $22,780, but full-history return falls from 259.80% to 257.56% and drawdown worsens from 12.84% to 12.90%. Forward return rises from 192.72% to 195.85% and 2023 rises from 5.74% to 6.11%; these gains fail the full-history/frontier gates. The double combined hurdle returns 251.13% with 12.66% drawdown and 5.47% in 2023. None improves the prior all-cost frontier. February 2018 is the largest combined-rule monthly loss versus default: -2.70% versus -0.54%.

All four gate arrays match the original source-produced hashes before metrics. The two-group conditional parent uses its original preferences; the three-group variants use their own original ungated parent. Fifteen native cost runs include an exact corrected-default replay; all purchases remain whole-share. The original all-groups switching study is fully covered: 35 verified original identities across four complete source studies, 86 distinct comparable corrected histories. There are still 31 unresolved replay/receipt issues and 43 legacy ledgers; no overall-best claim or promotion is allowed. Every historical comparator remains excluded from active selection; evidence is preserved. Costs are assumptions, history is reused, annual forward slices continue the account, and uncertainty intervals are not adjusted for repeated research. No hosted model proposal or review is claimed.

|Strategy|2 bps full / forward|6 bps full / forward|10 bps full / forward|6 bps max drawdown|
|---|---:|---:|---:|---:|
|Historical replay · All groups + combined-condition hurdle|288.22% / 205.46%|257.56% / 195.85%|219.38% / 175.46%|12.90%|
|Historical replay · All groups + double combined hurdle|286.46% / 210.03%|251.13% / 184.44%|229.69% / 178.19%|12.66%|
|Historical replay · All groups + volatility cost hurdle|296.39% / 211.89%|259.80% / 192.72%|221.88% / 177.60%|12.84%|
|Historical replay · Conditional switching · parent reference|256.21% / 190.16%|222.94% / 171.24%|202.44% / 166.66%|15.09%|
|Current default · corrected whole-share execution|296.39% / 211.89%|259.80% / 192.72%|221.88% / 177.60%|12.84%|

Native volatility/default parity holds for every compact session field at every cost. This verifies the fixed research mask and native volatility behavior under the corrected engine; it does not certify live broker behavior. Full annual/monthly/recovery/stress/turnover/missed-fill and paired uncertainty evidence is sealed in cycles/009-original-condition-masks/study. Next methodological test: recover the original conditional-switching family using registered parent-specific masks rather than retuning conditions from these exposed outcomes.

Validation: 24 focused replay/campaign/conditional-mask/all-groups tests passed; Ruff and mypy passed. Browser checks passed for 45 benchmark rows, cost and stress views, inactive historical comparators, and 1024/390px layouts. Next source-bound replay design and monthly diagnostics sealed. Default strategy file hash unchanged; no promotion or executor action.


## Cycle 010 — original two-group condition family

All seven original two-group gates match their source hashes. None improves the existing all-cost return frontier. At 6 bps the strongest newly measured variant, combined conditions, returns 236.17% full-history and 181.94% forward, versus default 259.80% and 192.72%. It improves its own ungated two-group parent (213.76% full-history), reducing fees from $28,621 to $25,007, but 2023 remains -6.01% versus default +5.74%. Its worst relative months are November 2023 (-2.06% versus +3.48%) and December 2023 (-1.42% versus +2.93%). Choppy-trend filtering reduces the COVID-window loss to 3.60%, but returns only 220.84% full-history with 16.24% drawdown. Different group counts also affect these comparisons; the differences versus default cannot be attributed solely to gates.

All seven recomputed gate arrays match their original source-produced hashes before metrics. Twenty-four native cost runs include an exact corrected-default replay. Original two-group core and combined-double references are reused only with sealed configuration, gate and per-cost record identity proof. All purchases are whole-share. The source study now has all nine original identities covered. Coverage totals 44 verified original identities across five complete source studies, 107 distinct comparable corrected histories. Thirty unresolved replay/receipt issues and 43 legacy ledgers still block promotion. Every historical comparator stays excluded from active selection; evidence is preserved. Costs are assumptions, history is reused, annual forward slices continue the account, and uncertainty intervals are not adjusted for repeated research. No hosted model proposal or review is claimed.

|Strategy|2 bps full / forward|6 bps full / forward|10 bps full / forward|6 bps max drawdown|
|---|---:|---:|---:|---:|
|Historical two-group replay · Cost hurdle during rank churn|249.91% / 192.56%|222.69% / 177.74%|182.30% / 150.38%|16.33%|
|Historical two-group replay · Volatility, liquidity or rank churn|259.20% / 190.41%|236.17% / 181.94%|187.95% / 155.00%|15.89%|
|Historical two-group replay · Cost hurdle during choppy trends|260.53% / 196.75%|220.84% / 171.27%|202.13% / 164.00%|16.24%|
|Historical two-group replay · Cost hurdle during thin liquidity|248.10% / 186.78%|221.00% / 170.69%|187.67% / 151.59%|16.13%|
|Historical two-group replay · Volatility and thin liquidity|262.49% / 189.57%|210.15% / 164.12%|187.49% / 154.50%|16.33%|
|Historical two-group replay · Volatility or thin liquidity|246.42% / 188.84%|220.96% / 170.41%|193.41% / 154.35%|16.06%|
|Historical two-group replay · Cost hurdle during high volatility|268.69% / 196.54%|230.88% / 174.71%|202.06% / 163.40%|16.75%|
|Current default · corrected whole-share execution|296.39% / 211.89%|259.80% / 192.72%|221.88% / 177.60%|12.84%|

The combined-rule paired monthly annualized log-return delta interval is [-4.09%, +2.54%] versus default; repeated-research descriptive uncertainty, not evidence of superiority. November/December 2023 losses remain a recovery weakness of the two-group family. Full annual/monthly/recovery/stress/turnover/missed-fill and paired uncertainty evidence is sealed in cycles/010-two-group-condition-replay/study. Next: source-reviewed native allocation-switching, trend-quality and static efficiency comparator coverage.

Validation: 26 focused replay/campaign/conditional-mask/all-groups tests passed after the sealed audit fixture completed; Ruff and mypy passed. Browser checks passed for 69 benchmark rows, cost/stress views, inactive historical comparators, and 1024/390px layouts. Default profile hash and all prior frontier values unchanged. Monthly diagnostic and next native replay design sealed. Next design covers 15 distinct native configurations across allocation-switching, trend-quality and static efficiency families; 14 have not yet been measured under the corrected contract. No batch launched for that design during cycle010.


## Cycle 011 — native switching, trend quality and drift comparators

Fourteen original native variants replayed at 2/6/10 bps, plus the exactly reproduced current default and three reused original control identities. No new result improves the full/forward return frontier. At 6 bps the strongest newly measured variant, the two-group double cost hurdle, returns 240.14% full-history and 185.88% forward versus default 259.80% and 192.72%; 2023 remains -5.54% versus +5.74%. It improves its own two-group parent (213.76% full-history), reducing fees from $28,621 to $22,170. The three-day confirmation plus double hurdle cuts fees to $19,821 but returns only 209.75%. Wider 4% drift returns 225.58%; shorter trends with full agreement repair 2023 to +5.46% but cut full-history return to 130.73%. Different group counts also affect comparisons versus default. Table shows this batch, the original two-group control and the strongest prior return-frontier references; all 149 distinct corrected histories remain audited.

Forty-five native cost runs include the exactly reproduced corrected default. Fourteen configurations are new measurements under the corrected contract; three source controls reuse the same verified corrected two-group history. All purchases are whole-share. Allocation-switching and trend-quality source studies are fully covered; efficiency coverage is limited to four native static arms. External group filters remain unresolved. Coverage totals 61 verified original identities across seven complete source studies, 149 distinct comparable corrected histories; 28 replay/receipt issues and 43 legacy ledgers still block promotion. Historical comparators remain excluded from active selection, with sealed evidence retained. Costs are assumptions, history is reused, annual forward slices continue the account, and uncertainty is unadjusted for repeated research. No hosted-model proposal/review is claimed.

|Strategy|2 bps full / forward|6 bps full / forward|10 bps full / forward|6 bps max drawdown|
|---|---:|---:|---:|---:|
|Current default · corrected whole-share execution|296.39% / 211.89%|259.80% / 192.72%|221.88% / 177.60%|12.84%|
|Historical replay · Trade beyond 3% drift|253.11% / 191.29%|215.87% / 169.55%|187.58% / 153.52%|16.25%|
|Historical replay · Trade beyond 4% drift|249.16% / 191.13%|225.58% / 179.23%|189.74% / 157.81%|15.73%|
|Historical replay · Trade beyond 5% drift|238.83% / 187.84%|204.13% / 168.73%|170.93% / 148.64%|16.91%|
|Historical replay · Require all three trend horizons|244.95% / 176.93%|221.71% / 162.23%|183.37% / 138.55%|18.89%|
|Historical replay · Shorter trends + full agreement|177.38% / 148.65%|130.73% / 118.81%|93.38% / 92.58%|13.74%|
|Historical replay · Trend horizons · 21/63/126 days|169.73% / 147.78%|125.57% / 119.58%|85.62% / 94.89%|17.03%|
|Historical replay · Confirm rank for two days|237.40% / 172.73%|199.88% / 152.16%|179.96% / 143.68%|16.97%|
|Historical replay · Two days + cost hurdle|221.36% / 172.84%|202.24% / 161.24%|183.21% / 150.73%|15.30%|
|Historical replay · Two days + double cost hurdle|225.53% / 173.15%|206.32% / 162.03%|185.03% / 153.36%|15.07%|
|Historical replay · Confirm rank for three days|240.35% / 180.35%|203.71% / 161.59%|179.04% / 148.14%|16.37%|
|Historical replay · Three days + cost hurdle|230.54% / 181.20%|212.08% / 168.59%|190.94% / 159.44%|14.88%|
|Historical replay · Three days + double cost hurdle|230.75% / 178.42%|209.75% / 168.70%|187.10% / 154.47%|15.04%|
|Historical replay · Switch only above round-trip cost|246.07% / 190.67%|228.49% / 175.61%|201.42% / 163.59%|16.42%|
|Historical replay · Switch above twice round-trip cost|258.29% / 195.10%|240.14% / 185.88%|207.26% / 164.09%|15.21%|

The double cost-hurdle paired monthly annualized log-return delta interval is [-4.02%, +2.73%] versus default; descriptive repeated-research uncertainty. Its worst relative months are November 2023 (-2.04% versus +3.48%) and December 2023 (-1.42% versus +2.93%). Full annual/monthly/recovery/stress/turnover/missed-fill and paired uncertainty evidence is sealed in cycles/011-native-switching-quality-replay/study. Next: prove original group-risk overlay execution semantics before common-contract replay. The source/current overlay files differ by typing/bool wrappers; reconstructed risk arrays are equal, not file hashes.

Validation: 41 focused campaign/replay/conditional/all-groups/native switching/efficiency tests passed; Ruff and mypy passed. The efficiency CLI test was stale: it expected daily core despite the user-selected volatility default. It now checks the actual overall default, preserves explicit daily-core access and checks signal warmup. No production default was changed. Browser checks passed for57 recent/frontier rows, costs/stress views, inactive historical selection and1024/390px layouts. Next overlay design initially refused a false whole-file equality assumption; inspection found typing/bool-wrapper differences. Independent original/current regeneration yields identical full-history risk arrays for all three group-filter rules. Source-derived overlay hashes are now preregistered for the five remaining original arms; no overlay measurements were performed this cycle. Default profile hash and historical return frontier remain unchanged.


## Cycle012 — group-risk overlays with source semantic controls

All fifteen original fractional semantic controls reproduce exact session histories. Five whole-share group-risk filters then measured against an exactly reproduced current default at 2/6/10 bps. None improves the prior full/forward frontier. At 6 bps the group-stress filter returns 204.17% full-history and 162.95% forward with 16.76% drawdown and -7.00% in 2023; default returns 259.80% and 192.72%, drawdown 12.84%, 2023+5.74%. The group-shock filter reduces the COVID-window loss to 1.46% versus 9.54%, but full-history return falls to 199.76% and drawdown worsens to 17.65%. Group-trend filtering returns 153.92%; adding 3%/4% drift bands returns 152.50%/152.51%. Compared with their own two-group parent, all five have lower 6 bps full-history returns. These two-group filters are not direct gate-only changes to the three-group default. Fractional semantic probes are excluded from dashboard comparisons and the return frontier; their sealed proof remains available.

Thirty-three native cost runs: corrected default, five original fractional semantic probes and five whole-share overlay comparators, each at2/6/10bps. Every original probe reproduced exactly before corrected overlay trials. Registered hashes bind both risk multipliers and allow-increase arrays before metrics. Original/current functions regenerate identical arrays despite typing/bool-wrapper source differences. No new thresholds or fitting. Source efficiency family now fully covered across all nine original identities. Coverage totals66 verified original identities across eight complete source studies,164 distinct comparable corrected histories. Twenty-seven replay/receipt issues and43 legacy ledgers still block promotion. The fifteen fractional diagnostic records are incompatible with the corrected frontier and hidden from dashboard comparison charts. Costs are assumptions; history is reused; forward slices continue the account; uncertainty is unadjusted for repeated research. No hosted proposal/review is claimed.

|Strategy|2bps full / forward|6bps full / forward|10bps full / forward|6bps max drawdown|
|---|---:|---:|---:|---:|
|Current default · corrected whole-share execution|296.39% / 211.89%|259.80% / 192.72%|221.88% / 177.60%|12.84%|
|Historical group-risk replay · 3% drift + group weakness|186.68% / 144.71%|152.50% / 128.46%|127.77% / 114.69%|17.30%|
|Historical group-risk replay · 4% drift + group weakness|196.21% / 154.42%|152.51% / 129.09%|123.88% / 111.29%|16.77%|
|Historical group-risk replay · Group shock filter|235.91% / 171.59%|199.76% / 151.48%|164.00% / 133.35%|17.65%|
|Historical group-risk replay · Group volatility filter|242.15% / 182.46%|204.17% / 162.95%|178.50% / 149.85%|16.76%|
|Historical group-risk replay · Group weakness filter|188.32% / 146.39%|153.92% / 126.31%|123.20% / 109.13%|17.77%|

Full annual/monthly/recovery/stress/turnover/missed-fill and paired uncertainty evidence is sealed in cycles/012-group-risk-overlay-replay/study. Reproducing original fractional histories is a source-semantics check, not a comparable return floor. Tests and audit deny overlay/native substitution, altered risk/entry arrays and false re-signed legacy histories. Next: corrected native sizing-family benchmark coverage; no threshold search.

Validation:49 focused native tests and67 backend tests passed, plus Ruff/mypy and browser checks for30 whole-share/frontier table rows, costs/stress, mobile layouts and hidden fractional probes. Only the owned dashboard API was restarted to load the comparison safeguard; no executor action. Against its own two-group parent, the group-stress filter lost1.65 percentage points in August2024 (+0.70% parent versus -0.95% filtered),1.49pp in February2018 (-1.57% versus -3.06%) and1.46pp in April2025 (-0.97% versus -2.43%); it gained3.58pp in March2020 (-1.48% versus +2.11%). These descriptive monthly tradeoffs illustrate why stress protection failed the full-history test. Its paired annualized monthly log-return delta interval versus current default is[-5.29%,+1.78%], unadjusted repeated-research uncertainty. Source-bound monthly diagnostics and the next historical native sizing design are sealed. Default profile hash and prior return frontier unchanged. Next design has13 distinct configs,12 requiring corrected whole-share measurement; no sizing batch launched this cycle.


## Cycle013 — original native conditional sizing families

Twelve original QQQ/group/asset sizing variants replayed under whole-share execution at 2/6/10 bps with exact current-default reproduction. No candidate improves the all-cost historical return frontier. Asset weak-trend relative sizing improves the default at 6 bps: 263.98% full-history/194.71% forward versus 259.80%/192.72%, with 12.83% drawdown and 2023+5.79%. But at 2 bps it returns 286.13% versus 296.39%; at 10 bps 220.91% versus 221.88%, and its 2023 return falls to 4.81% versus 5.07%. Its 6 bps paired annualized monthly log-return difference interval includes zero [-0.19%,+0.46%], unadjusted for repeated research. QQQ volatility sizing also improves 6 bps slightly but fails cheaper/dearer-cost comparisons. Asset volatility reduce-only sizing improves 6 bps drawdown to 12.14% and 2023 to 6.59%, but lowers full-history return to 234.23%. These isolated partial gains do not justify promotion; all historical identities and stronger rejected benchmark returns remain audited.

Thirty-nine native cost runs:12 original fixed sizing configurations and the exactly reproduced corrected default, each at 2/6/10bps. Three original default identities reuse the same corrected native control with configuration/record proofs. All purchases are whole-share; no new thresholds, fitting or profiles. All three source sizing studies now fully covered. Coverage totals81 verified original identities across11 complete source studies,200 distinct comparable corrected histories. Twenty-four replay/receipt issues and43 legacy ledgers still block promotion. Costs are assumptions; history is reused; chronological annual forward slices continue the account; monthly uncertainty is unadjusted for repeated research. No hosted proposal/review is claimed.

|Strategy|2bps full / forward|6bps full / forward|10bps full / forward|6bps max drawdown|
|---|---:|---:|---:|---:|
|Historical replay · Asset weak trend or volatility stress · reduce only|278.00% / 194.08%|230.45% / 175.47%|218.83% / 174.61%|12.17%|
|Historical replay · Asset weak trend or volatility stress · relative sizing|258.18% / 197.73%|226.87% / 180.29%|218.79% / 176.57%|12.20%|
|Historical replay · Asset volatility stress · reduce only|281.86% / 205.41%|234.23% / 181.14%|209.15% / 171.33%|12.14%|
|Historical replay · Asset volatility stress · relative sizing|289.09% / 208.93%|226.96% / 179.68%|210.18% / 174.76%|12.15%|
|Historical replay · Asset weak trend · reduce only|296.38% / 211.29%|244.18% / 192.39%|224.65% / 178.38%|12.84%|
|Historical replay · Asset weak trend · relative sizing|286.13% / 209.63%|263.98% / 194.71%|220.91% / 180.74%|12.83%|
|Current default · corrected whole-share execution|296.39% / 211.89%|259.80% / 192.72%|221.88% / 177.60%|12.84%|
|Historical replay · Group sizing · own weak trend or volatility stress|276.51% / 203.98%|249.97% / 189.05%|212.05% / 172.76%|12.75%|
|Historical replay · Group sizing · own volatility stress|293.97% / 209.47%|250.02% / 194.25%|215.26% / 170.98%|12.84%|
|Historical replay · Group sizing · own weak trend|279.65% / 206.18%|244.19% / 184.52%|209.38% / 170.67%|12.75%|
|Historical replay · Inverse-volatility during weak trend or volatility stress|294.85% / 207.26%|249.17% / 185.99%|218.67% / 175.63%|12.59%|
|Historical replay · Inverse-volatility only during QQQ volatility stress|287.92% / 201.86%|260.68% / 194.48%|215.72% / 174.23%|12.59%|
|Historical replay · Inverse-volatility only below QQQ 200-day average|285.48% / 199.90%|255.56% / 190.04%|221.58% / 176.15%|12.63%|

The native weak-trend relative asset rule changes one sizing mechanism with the same three-group default control. Its largest6 bps monthly benefit is May2024 (+2.89% versus+1.97%) and largest relative loss is November2019 (-0.86% versus-0.37%). All-cost gates remain binding; the stronger historical cap and forward-ranking references cannot be discarded because of earlier risk rejection. Current default profile unchanged. Full annual/monthly/recovery/stress/fees/turnover/missed-fill and paired uncertainty evidence is sealed in cycles/013-native-sizing-replay/study. Next: original percentile/campaign fixed-config historical repairs, not new volatility tuning.

Validation:70 focused native replay/campaign/mask/overlay/switching/efficiency/conditional/group/asset sizing tests passed, plus Ruff/mypy and browser checks for48 current/frontier rows, cost/stress scenarios, inactive historical selection and mobile widths. All12 sizing variants fail the preserved historical full/forward return frontier; sealed gate-results-013.json contains per-cost numeric checks, drawdown and2023 guards. No strategy profile/default changes. Native asset weak-trend relative sizing is a partial6 bps gain with a zero-overlapping uncertainty interval and failures at2/10bps, not a robust all-cost improvement. Monthly/recovery diagnostics and next original percentile/campaign001-005 replay design sealed. Next design has12 distinct configs,11 still requiring whole-share measurements; no next batch launched this cycle.


## Cycle014 — original percentile and first campaign proposals

Eleven original percentile/campaign configurations remeasured at 2/6/10bps under corrected whole-share execution, plus exact default reproduction. No candidate improves the preserved all-cost full/forward frontier. At 6bps the strongest non-identical new variant, p80 reduce-only sizing, returns253.61% full-history/186.24% forward versus default 259.80%/192.72%. The 30% metals cap improves 2023 to 10.32% and drawdown to 12.24%, but lowers full-history return to 231.17%. Six positions returns 239.16% with 14.23% drawdown. Faster trends return 134.52%. The 250-dollar trade minimum produces exactly identical whole-share sessions to the default at all three costs; no strict gain. Every original identity and stronger rejected return benchmark remains audited. Follow-up fixed 40% concentration/sizing interaction design is registered but not measured; historical coverage still blocks promotion.

Thirty-six native cost runs:11 original configurations plus the exactly reproduced corrected default, each at 2/6/10bps. Six original control identities reuse verified corrected control records. Fixed original settings; no new percentile/volatility thresholds or active profiles. All six source studies now fully covered. Coverage totals98 verified original identities across17 complete source studies,230 distinct comparable corrected histories. Eighteen replay/receipt issues and43 legacy ledgers still block promotion. All stronger prior rejected return floors remain binding. Native source/module and record seals preserved. Costs are assumptions; history is reused; annual forward slices continue the account; uncertainty is unadjusted for repeated research. No hosted proposal/review is claimed.

|Strategy|2bps full / forward|6bps full / forward|10bps full / forward|6bps max drawdown|
|---|---:|---:|---:|---:|
|Historical replay · Broader participation · up to six positions|261.90% / 187.75%|239.16% / 179.37%|205.13% / 165.83%|14.23%|
|Historical replay · Stronger confirmation · all three trend horizons|215.76% / 159.56%|203.04% / 146.85%|172.44% / 134.95%|13.33%|
|Historical replay · Fewer small orders · $250 minimum|296.39% / 211.89%|259.80% / 192.72%|221.88% / 177.60%|12.84%|
|Historical replay · Metals risk limit · 30% combined cap|266.52% / 189.10%|231.17% / 173.00%|198.99% / 153.42%|12.24%|
|Historical replay · Earlier recovery signals · 21/63/126-day trends|169.99% / 145.08%|134.52% / 121.14%|96.69% / 93.98%|16.05%|
|Current default · corrected whole-share execution|296.39% / 211.89%|259.80% / 192.72%|221.88% / 177.60%|12.84%|
|Historical replay · Own 80th percentile · reduce only|272.80% / 197.73%|253.61% / 186.24%|210.39% / 168.52%|12.87%|
|Historical replay · Own 80th percentile · relative sizing|271.35% / 199.90%|222.15% / 169.52%|209.82% / 166.09%|13.42%|
|Historical replay · Own 90th percentile · reduce only|276.17% / 198.14%|247.45% / 183.85%|219.08% / 173.58%|12.56%|
|Historical replay · Own 90th percentile · relative sizing|275.16% / 197.84%|249.47% / 188.01%|215.23% / 171.62%|12.51%|
|Historical replay · Own 95th percentile · reduce only|269.50% / 194.94%|239.41% / 185.22%|217.35% / 175.11%|12.37%|
|Historical replay · Own 95th percentile · relative sizing|278.41% / 204.92%|240.51% / 187.49%|209.34% / 167.95%|12.39%|

The first preflight stopped before any proposal/backtest because the loader assumed optional recipe_modules existed. That failed attempt and original audit are sealed; the corrected loader verifies the complete original source manifest and uses a fresh prepared audit. No hypothesis/config changed after outcomes. The 250-dollar minimum duplicates every compact native session field at all costs; identities retained but no new return history counted. Full annual/monthly/recovery/stress/turnover/fees/missed-fill and paired uncertainty evidence is sealed in cycles/014-original-percentile-campaign-replay/study. Next interaction design is independent unmeasured fixed combinations, chosen using exposed failures, not an untouched holdout.

Validation:78 focused native tests passed, plus Ruff/mypy and browser checks for45 current/frontier rows, cost/stress scenarios, inactive historical selection and mobile widths. Generic campaign receipt acceptance is restricted to five exact reviewed original protocol hashes; a known arm/schema with changed source protocol is rejected. The preflight loader failure is preserved with its audit, manifest and log; no measurement preceded correction. Source model validation also checked the unmeasured follow-up design before sealing. Three new fixed40% concentration/sizing combinations are economically distinct from every prior audited config; no outcomes measured this cycle. Native engine parity across87 modules confirms component references share the current strategy/planning/execution/accounting engine. Recheck all identities before next trial. No default/profile/executor changes. Next interaction design and engine proof are sealed; incomplete remaining historical/legacy coverage continues to block any promotion.


## Cycle015 — concentration/sizing interactions

At6bps,40% cap plus weak-asset relative sizing returns258.32% full-history/194.22% forward, versus default259.80%/192.72%;2023 falls to4.28% versus5.74%. Cap plus asset-volatility reduce-only improves drawdown to12.12% and2023 to6.94%,but lowers full-history return to245.83%. Cap plus QQQ-volatility sizing returns255.57%,drawdown12.59%,2023+6.41%. All three lose to the cap-only component in6bps full-history return(274.83%) and fail the preserved all-cost full/forward frontier. Component gains are not additive. No promotion. Costs are assumptions; reused history is not an untouched holdout.

Three new economically distinct fixed combinations were preregistered in proposal-015.json after source/data/parent identities and87 native engine module hashes matched. Twelve native cost runs reproduced the current default exactly. All three candidates are rejected by native campaign retention; stronger previously rejected comparator floors remain binding. Complete component deltas and monthly/stress/annual/recovery/fees/turnover evidence are sealed; no thresholds or cash/volume/fee rules changed, no active profile created.

|Strategy|6bps full|6bps forward|Max drawdown|2023|
|---|---:|---:|---:|---:|
|40% cap + asset volatility reduce-only|245.83%|187.47%|12.12%|6.94%|
|40% cap + weak-asset relative sizing|258.32%|194.22%|12.59%|4.28%|
|40% cap + QQQ volatility sizing|255.57%|193.98%|12.59%|6.41%|
|Current best · all groups + volatility switching|259.80%|192.72%|12.84%|5.74%|

Coverage correction: earlier inventories covered43 session ledgers but omitted the root global strategy registry. The read-only audit now explicitly includes it, with349 strategy identities and1 return-reference identities. Raw metrics/payload hashes are preserved; no private payload text is published, no eligibility filtering, and no raw annual objective is treated as a comparable return floor. Total legacy registries44; coverage remains incomplete. This does not change the prior frontier or retrospectively claim full coverage.

Next fixed native selector hypothesis is residual ranking with all groups admitted, not a no-op group-refresh timing change. Current selector uses original absolute scores for allocation; the proposed flag changes eligible-asset ordering via completed-week group-relative returns. It is absent from audited protocol configs, but global-registry source review remains unresolved. Existing causal prefix/partial-week tests apply; no new measurement here. Source-bound old-receipt supersession design is also saved without changing coverage classification. All monthly uncertainty intervals remain descriptive and unadjusted for repeated research. No hosted proposal/review is claimed.

Validation:53 focused global-registry/replay/gate/allocation/sizing/causal tests passed, plus Ruff/mypy and browser cost/stress/mobile/rejected-selection checks. The global registry reader uses a read-only transaction, preserves all entries including ineligible ones, stores raw annual/score metrics as text and hashes payloads without exposing their contents. Unit tests verify no database mutation or accidental database creation and no private payload disclosure. All12 native records and exact current-default reproductions are sealed. The core profile hash is unchanged; no executor action or promotion. Source-bound supersession preparation covers all5 old receipt003 identities and all8 old receipt005 identities; classification remains unresolved until guarded replacement proofs are implemented. Next residual-ranking design is unmeasured and economically absent from audited native protocols; no global novelty/coverage claim while registry sources remain unclassified.


## Cycle016 — native group-relative ranking

At6bps, group-relative ranking returns167.73% full-history and123.00% from2020, versus default259.80% and192.72%. Maximum drawdown worsens to17.44% from12.84%;2023 falls to5.35% from5.74%. Fills rise to2284 from1929 and annualized turnover to25.19x from23.42x. COVID return worsens to-14.11% from-9.54%, despite2018Q4 and2022 gains. Rejected at all three cost scenarios. The descriptive95% interval for annualized paired log-return difference is[-5.44%,-0.75%], unadjusted for repeated research. Costs are assumptions; reused history is not an untouched holdout.

Only residual_ranking changed. Six native runs reproduced the incumbent exactly at2/6/10bps; all candidate buys satisfy whole-share accounting. Full annual2020–2026 continuing-account forward slices, monthly recovery/stress diagnostics and original source-bound evidence are retained. Rejected profiles remain inactive.

|Strategy|6bps full|6bps forward|Drawdown|2023|Fills|
|---|---:|---:|---:|---:|---:|
|Current best · all groups + volatility switching|259.80%|192.72%|12.84%|5.74%|1929|
|Group-relative asset ranking|167.73%|123.00%|17.44%|5.35%|2284|

Receipt maintenance: all13 original source identities from fractional receipts003/005 are matched exactly to currently verified corrected replacements. Atomic proof rejects missing identities/costs, changed source/config, external semantics and changed seals. Original invalid reasons remain in superseded_receipts; original records are untouched. This removes two stale receipt issues, not the two old benchmark-study reviews or unresolved original strategy arms.242 comparable cost histories;98 source identities;16 pending issues and44 unclassified registries. No overall-best or complete-coverage claim.

Next saved hypothesis is the existing native incumbent rank bonus on absolute ordering, a fixed one-flag experiment. No new measurement of that hypothesis here. No hosted proposal/review or live execution.

Verification:42 focused native tests passed (including8 complete/mutated supersession cases), ruff and mypy passed. Browser validated33 comparison rows, actual default metrics,2/6/10bps,2023 and all stress/forward views, inactive rejected selection and mobile overflow. No promotion; awaiting next scheduled cycle.


## Cycle017 — fixed incumbent rank bonus

At6bps, incumbent rank bonus returns199.57% full-history/159.75% forward versus default259.80%/192.72%. Maximum drawdown worsens to16.13% from12.84%;2023 falls to3.52% from5.74%. Fills fall to1458 from1929 and annualized turnover to15.71x from23.42x, but the reduced trading sacrifices returns. COVID return worsens to-10.64% from-9.54%;2018Q4 worsens to-8.03% from-7.07%;2022 slightly improves to2.62% from2.42%. All-cost full/forward frontier checks fail. Descriptive95% annualized paired-log-return difference interval[-3.46%,-0.43%], unadjusted for repeated research. Costs are assumptions; reused history is not an untouched holdout.

Only adaptive_rotation.hysteresis changed; existing rank bonus2 stayed fixed. Six native runs reproduced the default exactly at2/6/10bps. All annual2020–2026 slices, monthly/recovery/stress diagnostics, fees and uncertainty retained. Rejected candidate remains inactive.

|Strategy|6bps full|6bps forward|Drawdown|2023|Fills|
|---|---:|---:|---:|---:|---:|
|Current best · all groups + volatility switching|259.80%|192.72%|12.84%|5.74%|1929|
|Incumbent rank bonus|199.57%|159.75%|16.13%|3.52%|1458|

Source review advanced for the original native adaptive10-arm family: sealed source bytes verify and recipe module matches current. Only native configurations are used; D1 cash exception is absent. Later adaptive engine additions are inactive under original configs, but complete original/current score/scalar parity remains a required proof, saved in native-adaptive-replay-design-017.json. This is not a completed replay or coverage waiver.245 comparable cost histories,98 verified original identities,16 pending items and44 legacy registries. No promotion. Next fixed strategy design combines measured40% cap with3% weight-rebalance band, an alternative to broad membership retention; no measurement or additive-benefit claim. No hosted model review or spend claimed.

Verification:42 focused native strategy/campaign/receipt tests passed. Browser validated36 comparison rows, default metrics,2/6/10bps, annual/stress/forward views, inactive rejected selection and responsive mobile layout. Native engine unchanged. Largest monthly opportunity loss is2026-03: candidate-8.44% versus default-3.31%; other large relative setbacks2023-11 and2019-12. This illustrates that a turnover reduction alone is insufficient. Awaiting next scheduled cycle; no live execution.


## Cycle018 — cap and weight-rebalance-band interaction

At6bps, cap40+band3 returns263.41% full-history/195.54% forward versus default259.80%/192.72%. Maximum drawdown improves to12.79% from12.84%;fills fall to1564 from1929 and fees to$22,440.95 from$24,604.44. But2023 falls to5.24% from5.74%, and full/forward returns miss the historical frontier at every cost. Cap-only6bps full274.83%/forward206.19% remains stronger; combination does not add component gains. Descriptive95% annualized paired-log-return difference interval[-0.65%,+0.78%] includes zero and is unadjusted for repeated research. Costs are assumptions; reused history is not an untouched holdout.

Two fixed measured component settings combined; no threshold search. Six native runs reproduce the default exactly at2/6/10bps, all buys whole shares. Full annual2020–2026 continuing-account slices,monthly,recovery,stress,turnover,fees and uncertainty retained. Rejected candidate stays inactive.

|Strategy|6bps full|6bps forward|Drawdown|2023|Fills|
|---|---:|---:|---:|---:|---:|
|Current best · all groups + volatility switching|259.80%|192.72%|12.84%|5.74%|1929|
|40% asset cap + 3% rebalance band|263.41%|195.54%|12.79%|5.24%|1564|

Native original adaptive family: separate source validation runs original and current source roots on identical full signal panels/configs.40 arrays across10 arms match exactly, no tolerance; immutable probe/protocol/results retained. This is signal parity only. Old fractional returns remain incompatible and require corrected replay; no source coverage waiver. Auditor now explicitly separates this no-return protocol from strategy studies and refuses diagnostics carrying configs or cost-return records. Initial audit-018-final.json conservatively listed this as an extra incompatible protocol; audit-018-verified.json retains it as a non-return diagnostic. Original pending16 issues and44 global/session ledgers remain.248 comparable histories;98 verified historical identities. No promotion. Next source replay design binds this proof; independent unmeasured strategy design enables only existing structure-quality flag.

Verification:46 focused native strategy/campaign/replay/signal-diagnostic tests passed; ruff/mypy passed. Four diagnostic audit cases reject disguised return/config evidence and mismatched protocol identity. Browser passed39 comparison rows, current-default metrics, all costs/stress/forward views, inactive rejected selection and mobile layout. Diagnostic tests also pass when launched from workspace root after fixing their fixture path. No promotion; await next scheduled cycle.


## Cycle019 — adaptive source replay and native structure quality

At6bps, structure quality returns252.78% full-history/194.96% forward versus default259.80%/192.72%. Maximum drawdown improves to12.05% from12.84%;2023 improves to7.52% from5.74% and is preserved at all costs. Fills increase to2204 from1929;fees$24,422.28 versus$24,604.44. COVID return improves to-8.81% from-9.54%,2022 to2.79% from2.42%,while2018Q4 slightly worsens to-7.20%. Full/forward frontier tests fail at2/6/10bps. Descriptive95% annualized paired-log-return difference interval[-1.10%,+0.74%] includes zero, unadjusted for repeated research. No promotion. Costs assumed; reused history is not an untouched holdout.

All10 original adaptive identities now have corrected native source-bound comparisons. Six new native historical variants and one new quality candidate were measured in the main frozen study. One identity reused matching current-engine evidence. Three initially reused006-engine identities failed the pinned native source proof, so they were rerun with the current frozen engine and exact control in receipt-repair/study. Their session histories match existing histories; identities are retained without double-counting. The incompatible receipt019-0 and reason remain sealed, explicitly superseded only by complete source/config/per-cost replacements. No weakening of semantic guard.

|Strategy|6bps full|6bps forward|Drawdown|2023|Fills|
|---|---:|---:|---:|---:|---:|
|Current best · all groups + volatility switching|259.80%|192.72%|12.84%|5.74%|1929|
|Native structure-quality adjustment|252.78%|194.96%|12.05%|7.52%|2204|

Original adaptive family resolved under corrected execution; all replays, including rejected variants, remain benchmark candidates.269 distinct comparable cost histories,108 original source identities,18 source studies,15 pending reviews and44 legacy registries. Frontier unchanged. Next source review covers12 original core/broader ETF variants. Shared runner currently assumes core panel for every arm; design explicitly requires real per-config panel/action support, calendar/window completeness, causality and exact control before broader comparisons. No broader results or signal parity claimed here. No hosted proposal/review or live execution.

Verification:58 focused native/replay/quality/diagnostic cases passed across the main run and final integration test. Exact original/current arrays remain40/40; corrected replay receipts verify every original adaptive identity at all three costs. Tests reject changed source/engine/data, resigned design, unknown arms and unrelated whole-share supersession. Ruff/mypy passed. Browser validated72 comparison rows, current-default metrics, all costs/stress/forward views, inactive rejected selection and mobile layout. Current control reproduced in main and repair studies. No promotion; await next scheduled cycle.


## Cycle020 — verified30-asset universe and multi-universe study loader

At6bps, verified30 returns74.41% full-history/82.85% forward versus default259.80%/192.72%. Maximum drawdown worsens to18.96% from12.84%;2023 falls to3.22% from5.74%. Annualized turnover rises to48.68x from23.42x;fills2977 versus1929;fees$35,070.51 versus$24,604.44 at similar86% average exposure. COVID improves to-7.52% from-9.54% and2022 to13.28% from2.42%, but2018Q4 worsens to-9.53% from-7.07%. All-cost return/frontier/DD/2023 gates fail. Descriptive95% annualized paired-log-return difference interval[-11.23%,-3.46%], unadjusted for repeated research. No promotion. Costs assumed; reused history is not an untouched holdout.

Only universe/group symbol composition changed, with original broader real-estate cap0.35; existing caps/risk/trading rules fixed.30 assets exactly equal sealed manifest membership, chosen before results. Six native cost runs reproduce default sessions exactly. All annual2020–2026 forward,monthly,recovery,stress,cost/turnover/fees evidence retained. Desired signal membership entrants1002 versus447; labels explicitly avoid executed-position or asset-PnL attribution. Rejected broad candidate remains inactive.

|Strategy|6bps full|6bps forward|Drawdown|2023|Fills|
|---|---:|---:|---:|---:|---:|
|Current best · all groups + volatility switching|259.80%|192.72%|12.84%|5.74%|1929|
|All 30 verified assets · daily group selection|74.41%|82.85%|18.96%|3.22%|2977|

Coverage finding: original group study uses38 ETFs, of which15 are absent from the campaign snapshot. universe-coverage-gap-020.json preserves both source/campaign partitions and exact missing symbols. Original wide variants remain unresolved; the new30 variant is distinct, not a claimed source replay. No missing data fabricated or symbols silently removed. Source-native core variants may still be reviewed independently.

Useful methodology change retained: switching_study now caches each exact ordered universe panel and its corporate actions, checks reference-calendar equality and actual positive finite execution VWAP/volumes, and refuses mixed-universe external arrays before any data/return measurement. Tests verify actual VTI observations against single-asset loads and full30/core11 dimensions; reject calendar/order/price/volume errors and mixed masks/overlays. No production execution/accounting change; native87 source hashes unchanged.272 corrected histories,108 source identities,18 source studies;15 pending reviews and44 ledgers unresolved. Next saved unmeasured design combines fixed40% cap and existing structure quality on core11; no additive-benefit claim or threshold tuning.

Verification:91 focused native/campaign/replay/quality/switch-gate/decision-overlay/universe tests passed. Ruff/mypy passed after distinguishing universe cache keys from existing string metric keys. Actual VTI data equality, full30/core11 panel dimensions, calendar/order/price/volume failure cases and external mask/overlay preflight refusal passed. Browser validated75 comparison rows, current-default metrics,2/6/10bps, annual/stress/forward views, inactive rejected selection and mobile layout. Exact default sessions at all costs under new research loader. No production/native execution engine changed; no promotion; await next scheduled cycle.


## Cycle021 — fixed cap and structure-quality interaction

At 6 bps per side, cap + quality returns 283.25% over the full history and 214.19% from 2020 onward, versus the default’s 259.80% and 192.72%. Maximum drawdown improves to 12.20% from 12.84%; 2023 improves to 7.73% from 5.74%. It passes five of six return-frontier checks plus the drawdown and all-cost 2023 guards. The remaining failure is 2 bps forward return: 225.99% versus the historical four-week group variant’s 230.26%. The stronger results remain binding benchmarks despite rejection. A descriptive 95% annualized paired-log-return difference interval of -0.41% to +1.75% includes zero and is unadjusted for repeated research. Trading costs are assumptions; reused history is not an untouched holdout.

|Assumed cost per side|Candidate full net return|Candidate forward net return|Previous forward floor|
|---|---:|---:|---:|
|2 bps|313.04%|225.99%|230.26%|
|6 bps|283.25%|214.19%|206.19%|
|10 bps|249.40%|195.32%|187.75%|

This is better than the default but remains rejected by the complete campaign gate; no active profile created. All new full-history floors and6/10bps forward floors enter the audit regardless of rejection. Default reproduced exactly at all three costs; component returns, monthly/recovery/stress/annual slices, turnover/fees and uncertainty retained. At6bps fills2111 versus1929,fees$24,942.12 versus$24,604.44;COVID improves to-8.16%,2018Q4 to-6.76%,2022+2.37% slightly below default+2.42%. No additive-gains assumption.

Legacy-source mapping:43 ledgers,354 experiment rows (including all rejected/queued),350 measured results,354 candidate files,43 raw program seals and5464 registered text/source hashes verified read-only. All349 global entries link to source experiment IDs. At least136 measured results have positive fit_count; entry-model fitting metadata is also retained.33 programs start measurement2018-01-02,3 at2016-12-30,7 have intraday schemas. Source config/evaluator/manifest/candidate identities are bound without provider/proposal text. This does not establish cost/history/whole-share equivalence or resolve coverage; learned pipelines need causal rolling replay and cannot be represented by their bare native config. No ledger modified.275 comparable histories,108 historical source identities,18 source studies,15 pending reviews,44 legacy ledgers remain. Next fixed design combines the measured four-week/two-group exclusion with cap+quality; no thresholds searched or fresh holdout claimed.

Verification:52 focused native/campaign/quality/source/replay tests passed, including read-only ledger-source tests preserving rejected/queued rows, raw program/source seals, missing-file failures and private-payload omission. Expanded inventory also retains entry_model_fit_count; final tests and mypy/ruff passed. Browser validated78 comparison rows, updated three-cost frontier, exact default metrics, all cost/stress/forward views, inactive rejected selection and mobile layout. Source ledgers unchanged before/after inventory. New candidate excluded from active profiles while its stronger returns are preserved in the frontier. No promotion; await next scheduled cycle.


## Cycle022 — four-week group exclusion with cap and quality

At 6 bps per side, four-week groups + cap/quality return 233.75% over the full history and 207.32% from 2020 onward, versus the default’s 259.80% and 192.72%, and the all-groups cap/quality parent’s 283.25% and 214.19%. Maximum drawdown is 12.33%; 2023 returns 7.94%. The 2 bps forward return rises to 242.36%, setting a new comparable benchmark, but the candidate fails the other five return comparisons. COVID return improves to -1.90%; higher turnover and weaker early-history returns hurt overall performance. The descriptive 95% annualized paired-log-return difference interval of -4.44% to +3.10% includes zero and is unadjusted for repeated research. Costs are assumptions; reused history is not an untouched holdout.

|Cost per side|Full net return|2020-onward net return|2023|
|---|---:|---:|---:|
|2 bps|287.45%|242.36%|9.51%|
|6 bps|233.75%|207.32%|7.94%|
|10 bps|187.78%|177.07%|6.22%|

Default sessions reproduced exactly at all costs; new low-cost forward frontier retained even though candidate is rejected and excluded from active profiles. At6bps turnover32.20x versus default23.42x,fills2284 versus1929,fees$30,894.17 versus$24,604.44,average exposure80.31% versus86.26%.2018Q4 improves to-4.05%,COVID-1.90%,2022+2.77%. At2bps2017–2019 compound return13.17% versus parent26.70% explains the full/forward tradeoff. Annual,monthly,recovery/stress,fees/turnover and descriptive uncertainty retained.

Static legacy contract review:36 native evaluator programs and7 intraday programs preserved. All36 native main calls omit explicit execution_start and whole_share_buys, and their frozen native runner signatures lack the whole-share flag.8 calls have conditional entry-cap expressions;28 omit explicit caps. This is syntax, not executed behavior or a fractional-fill count; warmup can affect actual first fill, evaluator routing and training causality still need review. No source code executed. Three older v6 programs use worker.py rather than experiments.py; both routing paths are recorded with verified frozen source hashes. No legacy metrics become return floors or coverage waivers.278 comparable histories,108 source identities,18 source studies;15 pending reviews and44 legacy ledgers remain.

Next unmeasured design tests native lagged signal-payoff risk budgeting on cycle021 all-groups cap/quality. It preserves inherited information-ratio/window/bounds and raises warmup to the native-required378 sessions. A feature-off matched-warmup diagnostic is mandatory, so early idle cash is not misattributed to quality. Full2017 start/cash reporting stays fixed; no earlier data fabricated. Proxy is signal-score times return, not actual holdings/net PnL. Exact current default and causality/bounds tests remain required. No formula search, learned model, hosted review or fresh holdout claimed.

Verification:54 focused native/campaign/quality/replay/source tests passed. Static-source tests prove legacy source is parsed without execution, preserve explicit versus missing quantity/start/defaults and keyword-unpack uncertainty, and omit code literal strings. Ruff/mypy passed. Browser validated81 comparison rows, updated low-cost forward frontier, exact default metrics, all costs/stress/forward views, inactive rejected selection and mobile layout. No promotion; await next scheduled cycle.


## Cycle023 — market protection on cap and quality

At6bps per side, cap and quality plus QQQ market protection returns148.80% full history and123.22% from2020, versus default259.80% and192.72%. Drawdown improves to11.77%, but2023 falls to4.01% versus5.74%. Average exposure65.17% versus86.26%; costs$18,599 versus$24,604. All six historical frontier comparisons fail. Q4 2018 -3.11%,COVID -3.73%,2022 +1.04%. Descriptive annualized paired-log-return95% interval -7.11% to -0.45%, unadjusted for repeated research. Fixed-rule annual continuing-account slices are not fitted rolling validation or untouched holdouts. Costs are assumptions. Saved378-session budget design also failed preflight: native January2017 start guard rejects insufficient warmup; no budget returns measured.

|Cost per side|Full net return|2020-onward|2023|
|---|---:|---:|---:|
|2bps|175.74%|134.55%|4.90%|
|6bps|148.80%|123.22%|4.01%|
|10bps|119.86%|102.64%|2.85%|

The exact incumbent reproduced at all costs. Candidate excluded from active profiles, sealed monthly/annual/recovery/stress/cost/fill evidence retained. The broad QQQ overlay can suppress non-equity allocations too; measured2022 exposure15.55% illustrates lost participation, not a causal attribution of all loss. No overlay threshold sweep. All prior rejected frontier leaders remain binding.281 histories,108 source identities,18 source studies,15 pending reviews,44 legacy registries. No promotion.

Correction to cycle022 next-design assumption: the native execution_start guard rejects378-session warmup at fixed2017 start; neither budget nor matched warmup diagnostic was measured. Three tests verify lagged information-ratio prefix/future invariance, matched config and actual native start guard. No earlier data, date shift or warmup bypass. Next unmeasured proposal tests one fixed five-session selection cadence with daily native risk/exits on cycle021 parent; first inspect timing/causality and dedup.

Verification:57 distinct native tests passed (23+37 with3 overlap); new budget tests rerun after annotations. Ruff and project-root mypy passed. Browser validated84 comparison rows, costs, stress/forward views, rejected selection guard and mobile widths without errors. Default remains unchanged.


## Cycle024 — five-session selection with cap and quality

Five-session cap-and-quality selection:6bps full return 256.23%,2020-onward 195.19%,drawdown 11.51%,2023 1.21%. Current default remains259.80%,192.72%,12.84% drawdown and5.74%2023. Candidate rejected; no default change. Costs $14,014.47,fills 1512,annualized turnover 13.57x,average exposure 84.28%. Return frontier guards passed 0/6;drawdown guard True;2023 guard False. Descriptive95% annualized paired-log-return interval [-0.014253946855528282, 0.012806219629189198];unadjusted for repeated research. Reused history and fixed annual continuing-account slices are not an untouched holdout or fitted rolling model validation; costs are assumptions.

|Cost per side|Full net return|2020-onward|2023|
|---|---:|---:|---:|
|2bps|274.51%|199.96%|2.19%|
|6bps|256.23%|195.19%|1.21%|
|10bps|241.62%|188.52%|1.65%|

Native member refresh anchored at warmup-1 with modulo5 cadence; current eligibility still checked daily. Actual full/prefix/future-poison score and allocator decisions agree at refresh and intervening sessions. Whole-share/native-exit accounting and observed-window limits unchanged; exact incumbent reproduced all costs. Monthly/recovery,annual2020–2026,2018Q4/COVID/2022,turnover/fees and missed fills retained; rejected profile excluded from active selection. No hosted proposal/review or model spending claimed. Next saved unmeasured interaction preserves daily selection and adds existing3% sizing band to cap+quality; no band search. Source/legacy coverage still independently blocks promotion.

Verification:67 native tests passed, including actual-snapshot five-session prefix/future-mutation decisions and existing daily-exit/schedule tests. Ruff/mypy passed; browser passed87 comparison rows,costs,stress/forward,mobile and rejected-selection guards. At6bps2023 the largest relative monthly losses against default were November-1.68pp,August-1.05pp,March-0.92pp. Slower selection reduced turnover but lost timely participation; monthly differences are descriptive, not isolated causal attribution. All prior frontier cells unchanged.284 comparable histories;108 original source identities,18 source studies,15 pending reviews,44 legacy registries. Default unchanged.


## Cycle025 — daily selection with cap, quality and sizing band

Daily cap-and-quality with3% sizing band:6bps full return 278.46%,2020-onward 214.38%,drawdown 12.52%,2023 5.96%. Current default remains259.80%,192.72%,12.84% drawdown and5.74%2023. Candidate rejected; no default change. Costs $23,192.87,fills 1714,annualized turnover 21.78x,average exposure 84.39%. Return frontier guards passed 3/6;drawdown guard True;2023 guard True. Descriptive95% annualized paired-log-return interval [-0.0037654654448561037, 0.014626772112140477];unadjusted for repeated research. Reused history and fixed annual continuing-account slices are not an untouched holdout or fitted rolling model validation; costs are assumptions.

|Cost per side|Full net return|2020-onward|2023|
|---|---:|---:|---:|
|2bps|317.78%|232.83%|6.86%|
|6bps|278.46%|214.38%|5.96%|
|10bps|247.35%|199.40%|5.53%|

Native daily member selection preserved;3% band applies only to existing-position sizing. Entries/full exits keep transition exception.181 native execution/accounting/planning/quality tests passed; exact incumbent all3costs. Whole-share/native-exit accounting and observed-window limits unchanged. Monthly/recovery,annual2020–2026,2018Q4/COVID/2022,turnover/fees and missed fills retained; rejected profile excluded from active selection. No hosted proposal/review or model spending claimed. Next prioritize source-bound core benchmark coverage from original group study; unavailable wider assets cannot be dropped and claimed equivalent. No threshold sweep.


Cycle025 raises three binding frontier cells despite rejection:2bps full317.778297041%,6bps2020-onward214.377835241%,10bps2020-onward199.397411651%. Other floors remain cycle021 full6/10 and cycle022 forward2. Drawdown/current2023allcostpass, but other3returncellsfail. Preserve strongest rejected comparisons.181 native tests and browser90rows/costs/stress/forward/mobile/selection checks passed; default exactly reproduced2/6/10bps.287 comparable histories,108 source identities,18 source studies,15 pending reviews,44 legacy registries; coverage remains unresolved.2023 relative todefault at6bps improvedNovember+0.399pp/February+0.331pp,while June-0.245pp/April-0.199pp; descriptive differences do not establish causal attribution. No active profile or native engine changes.


## Cycle026 — source-bound core benchmark coverage

Original core group family:48 original/current feature/session arrays matched exactly. Five core economic configurations reused current-engine common-contract results; one missing original groups+quality configuration replayed at2/6/10bps, plus exact default control. At6bps missing recipe returns238.88% full history,180.84% from2020,drawdown13.42%,2023-6.24%. Six core source identities now verified; six wider identities remain unresolved because15 required assets are absent. This partial proof cannot resolve the whole source study or legacy/fitted coverage. No strategy search, provider spend, hosted review, fresh holdout or default promotion claimed.

|Original source identity|Cost per side|Full return|2020-onward|2023|
|---|---:|---:|---:|---:|
|core_quality|2bps|268.77%|199.81%|-5.36%|
|core_quality|6bps|238.88%|180.84%|-6.24%|
|core_quality|10bps|210.14%|167.86%|-7.27%|
|baseline|2bps|263.29%|165.60%|1.32%|
|baseline|6bps|246.40%|158.57%|0.46%|
|baseline|10bps|215.92%|140.12%|-2.01%|
|B1|2bps|267.22%|182.75%|5.55%|
|B1|6bps|223.39%|155.69%|4.12%|
|B1|10bps|167.60%|124.73%|2.43%|
|groups|2bps|263.03%|191.46%|-6.54%|
|groups|6bps|238.12%|183.93%|-5.57%|
|groups|10bps|217.30%|172.28%|-6.62%|
|core_daily|2bps|266.42%|192.71%|-5.18%|
|core_daily|6bps|213.76%|167.20%|-6.50%|
|core_daily|10bps|182.42%|150.01%|-7.94%|
|core_both|2bps|239.57%|181.13%|-5.54%|
|core_both|6bps|200.70%|159.06%|-6.82%|
|core_both|10bps|176.65%|142.53%|-8.06%|

Exact source protocol/core configs/original records verified. Common panel uses all2016 history; repaired2017 flat account and native whole-share/cost/liquidity contract replaces original2024 short execution comparison. Forty-eight feature families/session arrays compared bit-for-bit including explicit empty optional arrays. New proof guard rejects source/data/engine mutations, re-signed altered proof, changed array bytes, unknown/wide identities. Current benchmark control exact all3costs. Monthly/annual/stress/recovery/fill/cost diagnostics retained. All original identities displayed, even where sessions deduplicate; five reused economic histories were not rerun or counted as new strategies. Current default stays unchanged; partial core receipts grant no wider/full-study or learned-model coverage.


Verification026:46 native proof/audit/receipt/cycle tests passed,ruff/mypy passed,browser108rows/frontier/costs/stress/forward/mobile/rejected selection passed. Zero invalid receipts.290 comparable histories,114 verified original source identities,18 fully covered source studies,15 pending reviews,44 legacy registries. Six core identities resolved without waiving remaining six wide identities; full group source still incomplete. All previous frontier cells unchanged; no promotion. New missing historical recipe6bps COVID- and yearly/monthly diagnostics are in its sealed native record; no synthetic execution or trained holdout claims. Next wake must preregister a new mechanism or methodology after reviewing evidence, with fixed20min cadence and15:17:38UTC hard stop.


## Cycle027 — one preferred asset per group

One preferred asset per group on cap40+quality+3%band:6bps full return 164.06%,2020-onward 149.84%,drawdown 15.39%,2023 7.43%. Default remains259.80%,192.72%,12.84% drawdown and5.74%2023. Rejected; no default change. Costs $19,853.54,fills 1098,annualized turnover 23.95x,average exposure 79.07%. Return frontier checks 0/6;drawdown guard False;2023 guard True. Descriptive95% annualized paired-log-return interval [-0.06490712558289458, 0.0013472927639312973],unadjusted for repeated research. Exposed history, fixed-rule annual continuing-account slices, assumed execution costs; no fitted model or untouched holdout.

|Cost per side|Full net return|2020-onward|2023|
|---|---:|---:|---:|
|2bps|180.96%|162.19%|8.66%|
|6bps|164.06%|149.84%|7.43%|
|10bps|141.17%|140.13%|6.73%|

Only native max_per_group2→1 changed from cycle025parent; exact native engine, core11/all3groups,dailyselection,cap40,quality,3%band,volcostgate preserved. Signal-level one-member rule checked across700observed sessions; actual held positions may transiently differ due to bands/partial fills, no hard holdings guarantee. Full/prefix/future-poison cache and allocator decisions identical. Native whole shares/openingcap/exits/cash/fees unchanged; default all3costs reproduced. All cost/annual/monthly/recovery/stress/fees/turnover evidence retained; candidate excluded from active selection. No provider/hosted review claim. Next mechanism must avoid member-count or volatility/band/cadence sweeps and build on specific failure evidence; historical source coverage unresolved.


Verification027:63 distinct native tests passed (21+43 with one overlap),ruff/mypy passed,browser111rows/frontier/costs/stress/forward/mobile/rejected selection passed. Initial broader test invocation from workspace root failed relative fixture paths; project-root rerun passed, no production change. Default exact allcosts.293 comparable histories,114 source identities,18 full source studies,15 pending reviews,44 legacy registries,zero invalid receipts. No new frontier cells or promotion.2023 improves7.43% but2022 drops to-0.82%,COVID-6.76%,2018Q4-7.00%; total full-history drawdown15.39% fails safeguard. Fewer preferred members lower exposure79.07% and fills1098,but annualized turnover23.95x exceeds default23.42x; fewer trades alone do not imply less notional turnover or better returns. Methodology remains exposed-history fixed rules, no trained holdout or hosted proposal/review claim.


## Cycle028 — legacy nested evidence integrity

Legacy nested evidence inventory verified1257 embedded-checksum receipts and archived equity paths with exact original evaluation-date/anchor reconstruction:1234 main-search runs and23 signal-contract preflight runs,zero path/return discrepancies. Main191 frozen runtime files and driver match registered hashes; two preflight folders lack191runtime files each, recorded explicitly. Preserve93main and3preflight world/fold/phase/cost/date scopes,including stronger raw histories, without comparing them to the campaign contract. Training/selection/test and synthetic worlds remain separate. No post-hoc stitching of fold winners; no model-causality,whole-share2017 contract or source-coverage waiver. First reader attempt failed closed on JSON whitespace convention; corrected compact writer checksum before outcomes,revision sealed. Current default exactly reproduced2/6/10bps; strategy and all historical leaders unchanged. No hosted proposal/review/provider spend or untouched holdout claim.

|Source directory|Verified receipt/path pairs|Separate scopes|Distinct recipes|Runtime files verified|Runtime files missing|
|---|---:|---:|---:|---:|---:|
|2026-10-08-nested-strategy-search|1234|93|623|191|0|
|preflight-reporting-failure|0|0|0|0|191|
|preflight-signal-contract-failure|23|3|17|0|191|

This expands source inventory, not comparable return floors or source coverage. Embedded SHA envelopes differ from standard protocol.sha256 files; originals remain untouched. No old Python code imported/executed. Reader tests reject envelope/path/return tampering and symlink escape; unchanged-file test passes. Evidence uses lower-exclusive/upper-inclusive evaluation slice and prior observed equity anchor, matching original writer. All1257 runs preserved, including train743/selection198/test293 main and train17/selection6 preflight. Main source has623 distinct recipe hashes, signal preflight17; no claim they are all distinct economic fitted strategies.

Raw historical test-scope maxima below are deliberately INCOMPARABLE diagnostic values, not claimed out-of-sample portfolio performance: multiple candidates evaluated per fold, different dates/contract, prior sample exposure. Do not stitch maxima into a return series.

|Fold|Cost|Test-scope runs|Strongest raw slice return|
|---|---:|---:|---:|
|outer-2025|2bps|88|40.03%|
|outer-2024|2bps|15|11.29%|
|outer-2024|10bps|2|9.12%|
|outer-2025|10bps|8|32.46%|
|outer-2026|2bps|5|15.76%|
|outer-2026|10bps|1|9.98%|

Next work must bind fit receipts,feature/label data/training cutoff and purge/embargo, then replay meaningful historical competitors under shared current contract. Nominal test labels/checksum integrity do not prove causal fitting or untouched holdouts. Default native control reproduced all3costs; no strategy change or promotion.


Verification028:47 tests,ruff,mypy passed; browser111rows/costs/frontier/stress/forward/mobile/selection checks passed. Native engine unchanged,current default exactall3costs. Integrity1257paths/0discrepancies,main191runtime files/driver verified. All293 comparable histories and114 verified source identities preserved;18fullsource studies,15pending reviews,44legacy registries,0invalid receipts. No comparable-frontier/source-coverage admission from legacy raw paths; no new strategy/promotion. Strong raw nested scope results are audit evidence only; do not stitch winners or mistake checksum validity for fitting causality. Next wake review source-bound fitted receipts and physical past-only training/purge/embargo before replaying meaningful learned competitors. Initial envelope hashing whitespace failure is preserved in events and sealed proposal revision; corrected original compact convention passed actual-data reconstruction.


## User-requested original-mask conditional replay refresh

Revalidated all original two-group and all-groups conditional-switching source identities at2/6/10bps, exact original masks and current native whole-share engine. All original-mask variants remain below at least one updated full-history/forward return requirement; default unchanged. At6bps combined original two-group conditions return236.17% full/181.94% from2020,2023-6.01%,versus current default259.80%/192.72%,2023+5.74%. Differences versus current default include group-count policy, not masks alone. Strongest historical full6bps remains cap+quality283.25%,forward6bps cap+quality+3%band214.38%. Existing replays were source-verified and reused, not rerun under new names. Costs are assumptions; full2016 signals/flat100k2017start/observed nextsession openingVWAP/1%entrycap/nativeexits/wholebuys/corporateactions/zero cash yield preserved. Annual2020-2026 continuing-account slices,monthly/recovery/stress,fees/turnover and descriptive uncertainty are retained; no untouched holdout or hosted review claim.

|Family|Approach|Cost|Full return|2020-onward|2023|Max drawdown|
|---|---|---:|---:|---:|---:|---:|
|Original two-group conditions|Cost hurdle during rank churn|2bps|249.91%|192.56%|-5.41%|15.09%|
|Original two-group conditions|Cost hurdle during rank churn|6bps|222.69%|177.74%|-6.53%|16.33%|
|Original two-group conditions|Cost hurdle during rank churn|10bps|182.30%|150.38%|-7.94%|17.84%|
|Original two-group conditions|Volatility, liquidity or rank churn|2bps|259.20%|190.41%|-4.24%|14.03%|
|Original two-group conditions|Volatility, liquidity or rank churn|6bps|236.17%|181.94%|-6.01%|15.89%|
|Original two-group conditions|Volatility, liquidity or rank churn|10bps|187.95%|155.00%|-7.26%|17.27%|
|Original two-group conditions|Combined conditions · double hurdle|2bps|256.21%|190.16%|-4.26%|14.05%|
|Original two-group conditions|Combined conditions · double hurdle|6bps|222.94%|171.24%|-5.40%|15.09%|
|Original two-group conditions|Combined conditions · double hurdle|10bps|202.44%|166.66%|-7.42%|17.15%|
|Original two-group conditions|Daily core groups · reference|2bps|266.42%|192.71%|-5.18%|14.85%|
|Original two-group conditions|Daily core groups · reference|6bps|213.76%|167.20%|-6.50%|16.33%|
|Original two-group conditions|Daily core groups · reference|10bps|182.42%|150.01%|-7.94%|17.85%|
|Original two-group conditions|Cost hurdle during choppy trends|2bps|260.53%|196.75%|-4.29%|14.07%|
|Original two-group conditions|Cost hurdle during choppy trends|6bps|220.84%|171.27%|-6.42%|16.24%|
|Original two-group conditions|Cost hurdle during choppy trends|10bps|202.13%|164.00%|-7.42%|17.15%|
|Original two-group conditions|Cost hurdle during thin liquidity|2bps|248.10%|186.78%|-4.31%|14.09%|
|Original two-group conditions|Cost hurdle during thin liquidity|6bps|221.00%|170.69%|-6.30%|16.13%|
|Original two-group conditions|Cost hurdle during thin liquidity|10bps|187.67%|151.59%|-7.62%|17.57%|
|Original two-group conditions|Volatility and thin liquidity|2bps|262.49%|189.57%|-5.18%|14.85%|
|Original two-group conditions|Volatility and thin liquidity|6bps|210.15%|164.12%|-6.50%|16.33%|
|Original two-group conditions|Volatility and thin liquidity|10bps|187.49%|154.50%|-7.94%|17.85%|
|Original two-group conditions|Volatility or thin liquidity|2bps|246.42%|188.84%|-4.11%|13.91%|
|Original two-group conditions|Volatility or thin liquidity|6bps|220.96%|170.41%|-6.22%|16.06%|
|Original two-group conditions|Volatility or thin liquidity|10bps|193.41%|154.35%|-7.11%|16.88%|
|Original two-group conditions|Cost hurdle during high volatility|2bps|268.69%|196.54%|-5.02%|14.71%|
|Original two-group conditions|Cost hurdle during high volatility|6bps|230.88%|174.71%|-6.99%|16.75%|
|Original two-group conditions|Cost hurdle during high volatility|10bps|202.06%|163.40%|-7.97%|17.91%|
|All-groups interaction|All groups + always-on cost hurdle|2bps|283.64%|213.52%|6.64%|12.56%|
|All-groups interaction|All groups + always-on cost hurdle|6bps|254.42%|195.87%|6.33%|12.89%|
|All-groups interaction|All groups + always-on cost hurdle|10bps|233.53%|178.64%|5.43%|12.81%|
|All-groups interaction|All groups + always-on double hurdle|2bps|280.57%|205.65%|6.83%|12.45%|
|All-groups interaction|All groups + always-on double hurdle|6bps|252.17%|188.88%|5.35%|12.39%|
|All-groups interaction|All groups + always-on double hurdle|10bps|229.38%|176.27%|3.98%|12.52%|
|All-groups interaction|All groups + combined-condition hurdle|2bps|288.22%|205.46%|6.68%|12.67%|
|All-groups interaction|All groups + combined-condition hurdle|6bps|257.56%|195.85%|6.11%|12.90%|
|All-groups interaction|All groups + combined-condition hurdle|10bps|219.38%|175.46%|5.34%|12.81%|
|All-groups interaction|All groups + double combined hurdle|2bps|286.46%|210.03%|6.79%|12.72%|
|All-groups interaction|All groups + double combined hurdle|6bps|251.13%|184.44%|5.47%|12.66%|
|All-groups interaction|All groups + double combined hurdle|10bps|229.69%|178.19%|4.70%|12.82%|
|All-groups interaction|All groups · parent reference|2bps|282.21%|207.97%|6.79%|13.47%|
|All-groups interaction|All groups · parent reference|6bps|244.63%|187.40%|5.90%|13.50%|
|All-groups interaction|All groups · parent reference|10bps|212.71%|170.85%|5.18%|13.53%|
|All-groups interaction|All groups + volatility cost hurdle|2bps|296.39%|211.89%|6.55%|12.59%|
|All-groups interaction|All groups + volatility cost hurdle|6bps|259.80%|192.72%|5.74%|12.84%|
|All-groups interaction|All groups + volatility cost hurdle|10bps|221.88%|177.60%|5.07%|12.98%|
|All-groups interaction|Conditional switching · parent reference|2bps|256.21%|190.16%|-4.26%|14.05%|
|All-groups interaction|Conditional switching · parent reference|6bps|222.94%|171.24%|-5.40%|15.09%|
|All-groups interaction|Conditional switching · parent reference|10bps|202.44%|166.66%|-7.42%|17.15%|
|All-groups interaction|Daily core groups · original reference|2bps|266.42%|192.71%|-5.18%|14.85%|
|All-groups interaction|Daily core groups · original reference|6bps|213.76%|167.20%|-6.50%|16.33%|
|All-groups interaction|Daily core groups · original reference|10bps|182.42%|150.01%|-7.94%|17.85%|


User-requested conditional replay refresh verified17 original identities,51cost rows,12non-null original masks plus1ungated binding. Native whole-share replays reused with exact current native-source proof and original config/mask/data/record bindings; current default exact all3costs. Thirty mask/parent/causality/receipt tests passed; browser111historicalrows/costs/stress/forward/mobile/selection guards passed. Existing current default equals original all-groups+volatility conditional gate (6bps259.80%full,192.72%forward,5.74%2023). Original two-group combined236.17%/181.94%/-6.01%;all-groups combined257.56%/195.85%/6.11%,DD12.90% vsdefault12.84%. None improves every updated cost/full/forward frontier and safeguards. No new strategy/profile,experiment or scheduledtick counted. Existing293 comparable histories/114source identities retained;15pending/44legacy coverage remains. Latest publication intentionally references immutable010comparison study to restore visual conditional series, while preserving latest full frontier and111historicalrows.


Conditional refresh frontend follow-up: initial browser check found390px horizontal overflow after showing8conditional comparison series. Analysis panel now clips horizontal chart paint while wide tables retain independent scrolling. Historical replay revalidation receives the historical-comparison heading. TypeScript/Vite rebuild passed, final campaign browser test passed111rows/frontier/costs/stress/forward/mobile/selection; explicit390px table test pagefits andscrollLeft100with350pxviewport/699pxcontent. Corrected verification supersedes the prematurely recorded earlier browser-pass claim; original failed evidence and correction preserved. No native engine/profile/executor change, no new experiment counted.


## Cycle029 — declared fit evidence and checkpoint context

Necessary declared-fit evidence verified for7archived fits,including both preflight-failure folders: 14genetic checkpoints/1344recorded search slots. Registered chronological folds,observed training/last-feature dates,two-session purge,feature-file hashes/dimensions,checkpoint hashes,protocol/fold/world/template/dataset context and selected-GP-ledger membership all match. Original snapshot partition bytes and source-template hash checked. No source code or fitting executed. Dataset/fit-panel hashes have not been reconstructed and dynamic refit/prefix mutation remains outstanding; metadata consistency is not proof of physical past-only fitting or campaign comparability. Full inference caches include later rows by design; their existence alone is not a leakage finding. Current incumbent reproduced exactly2/6/10bps; default and all comparable return leaders unchanged. Original-mask conditional comparison visuals remain displayed as requested. No learned candidate,held-out alpha,hosted review or promotion claimed.

|Source|World|Fold|Training end|Last eligible feature|Purged rows|GP slots|
|---|---|---|---|---|---:|---:|
|2026-10-08-nested-strategy-search|historical|outer-2024|2021-12-31|2021-12-29|2|192|
|2026-10-08-nested-strategy-search|historical|outer-2025|2022-12-30|2022-12-28|2|192|
|2026-10-08-nested-strategy-search|historical|outer-2026|2023-12-29|2023-12-27|2|192|
|2026-10-08-nested-strategy-search|refit-gaussian-20261008301|outer-2024|2021-12-31|2021-12-29|2|192|
|2026-10-08-nested-strategy-search|refit-range-20261008302|outer-2024|2021-12-31|2021-12-29|2|192|
|preflight-reporting-failure|historical|outer-2024|2021-12-31|2021-12-29|2|192|
|preflight-signal-contract-failure|historical|outer-2024|2021-12-31|2021-12-29|2|192|

Feature bank may be evaluated through later inference dates after choosing formulas on training data. Calendar dimensions/hash matching alone does not show those features were generated causally or that fitting actually consumed only the declared panel. All flags physical_training_reproduced/campaign_comparable remain false. Source template parsed as an AST literal from hash-bound191-file frozen runtime; no old Python imported/executed. GP context binds original protocol/world/fold/dataset and template, selected programs appear in recorded fitted ledgers. Original source snapshot partition hashes verified. The test detects forged cutoff/purge/overlap/context/features/selected program. Main5fits plus1reporting and1signal preflight all retained even under failed folder names. No invalid receipts removed and no source coverage flag waived. Native2017–2026control/cost/forward/stress evidence reproduced separately under campaign contract.

Next: reconstruct original training panel and CandleDataset identities from exact source snapshot and cutoffs,then controlled training-prefix/future mutation and selected-policy review before a meaningful learned-model replay. Latest dashboard retains conditional source comparison series requested by the human; this method cycle adds no strategy return curves.


Verification029:54 distinct tests passed (including7new temporal/checkpoint/context integrity cases),ruff/mypy passed. Browser111historicalrows plus original conditional series,updatedfrontier,cost/stress/forward/mobile/selection checks passed. Plain-language publication revision preserves immutable original evidence and references010visuals plus029native control measurement separately. Default reproduced exactlyall3costs. All7fit declarations/14GPcheckpoints/1344recordedslots verified,zero failures; no physical training reproduction or causal waiver.293comparable histories/114verified source identities/18fullycovered sources/15pendingreviews/44legacyregistries unchanged,zero invalid receipts. No strategy promotion or executor action. Next rebuild original training panel/dataset identities before controlled fitted causality and common-contract replay.


## Cycle030 — historical training input reconstruction

All five archived historical training-panel and dataset hashes reproduced exactly under both the original frozen data libraries and the current libraries. Changing future prices after each cutoff left both input identities unchanged; changing a training observation changed them. The two synthetic-world fits were excluded. Missing preflight runtime copies were resolved for this input test only by verifying that their complete registered runtime manifests and baseline hashes match the available main archive. No model was fitted or promoted, and no historical learned return was admitted to the comparable benchmarks. Reconstructing inputs does not establish that the original fitting process used them causally. The current strategy reproduced exactly at all three trading-cost assumptions, and the return leaders are unchanged. Your original-mask, whole-share conditional-switching comparison remains displayed.

|Archived fit|Cutoff|Observed training end|Training sessions|Panel hash|Dataset hash|Future invariant|Training sensitive|
|---|---|---|---:|---|---|---|---|
|fits/historical/outer-2024|2021-12-31|2021-12-31|1511|True|True|True|True|
|fits/historical/outer-2025|2022-12-31|2022-12-30|1762|True|True|True|True|
|fits/historical/outer-2026|2023-12-31|2023-12-29|2012|True|True|True|True|
|fits/historical/outer-2024|2021-12-31|2021-12-31|1511|True|True|True|True|
|fits/historical/outer-2024|2021-12-31|2021-12-31|1511|True|True|True|True|

Source baseline and complete191-module manifest equal across main and both preflights; available main frozen data libraries therefore supply hash-identical historical constructors. Actual original snapshot daily/window/actions partition bytes verified. Child original/current modules isolated by PYTHONPATH with durable parent/childPIDs,writerlock and120second timeout; no driver import,model fitting,syntheticworld or return replay in input probe. The cutoff helper uses all market arrays on an exclusive prefix,matching original CandleDataset OHLC/adjusted execution prices/manifest. All five results exactly equal across source versions and satisfy10expected receipt hashes,5future-invariance and5past-sensitivity checks. This is a reconstruction/mutation test of the input boundary,not reproduction of training search,labels,or final strategy execution. All coverage flags remain unresolved. Exact native incumbent separately replayed with campaign2017flat-account/cost/window/whole-share/exits/caps rules.

Next controlled refit should start with original historical outer2024 fixed GP search192slots and physically truncated data; validate exact selected programs/fitness/purged labels under source-bound execution and fixed seed budget before expanding. Preserve failed attempts and avoid selection on test returns.


Verification030:56tests passed,ruff/mypy passed; browser111historicalrows and userrequestedconditionalseries/frontier/cost/stress/forward/mobile/selection passed. All5historical inputs match10recorded panel/dataset hashes under original andcurrent libraries,5futuremutation checks invariant,5trainingmutation checks sensitive. Model fitting remains unperformed; no causal or comparability approval. Native default exact2/6/10bps and87native module bytes unchanged.293comparable histories/114sourceidentities/18coveredstudies/15pendingreviews/44legacyregistries/0invalidreceipts unchanged. Two syntheticfits excluded. Next bounded original historical outer2024 GP refit192slots with source-bound screen and physically purged training labels,followed by exactselectedprogram/fitness/checkpoint comparison; not permission for allfold or campaign promotion.


## Cycle031 — bounded original historical fit reproduction

The original 2024-fold genetic fit reproduced both archived checkpoints, complete search ledgers and selected programs exactly from a physically truncated training dataset. Current libraries selected the same programs with exactly the same fitness values, but their checkpoints differ in registry identifiers and the resulting checksums. The preregistered exact-checkpoint requirement therefore remains failed. A third case intended to change only future inputs used an unverified import path and is excluded from causality evidence. All 576 completed search slots count toward the fixed budget; no extra fit was run. A new source guard checks actual loaded library paths and file hashes before fitting; it rejects the wrong-root case. Only one historical fold was tested, with no test-period returns used for selection. No learned strategy, source-coverage approval or promotion resulted. The current strategy reproduced exactly at all three trading-cost assumptions, and your original-mask conditional-switching comparison remains displayed.

|Case|Completed slots|Result|
|---|---:|---|
|Original frozen libraries|192|Both archived checkpoints,fitness ledgers and selected programs exact|
|Current frozen libraries|192|Selected programs and every fitness value exact;strict checkpoint differs|
|Intended original future-input mutation|192|Invalid source-path setup;excluded from causality evidence|

Original training prefix1511sessions through2021-12-31,lasteligiblefeature2021-12-29,two physically purged terminal rows. Source snapshot/dataset/panel hashes and frozen191module manifest checked. Current seed871 differs at149registryidentifier fields+1checkpoint hash;seed872156registry fields+1checkpoint hash. No other field differences,including fitness or genome/search history; diagnosis is post-outcome and does not relax registered strict criteria. Only one historical fold,not all rolling-fold strategy validation or untouched holdout. No source return leader/coverage admission.

Initial current case exited before fitting because frozen source and output folders both used current;failure preserved. Resumed only remaining two192slot cases. Resume path for intended original_future used an extra parent directory,nonexistent import root;fallback source was not attested,so the case is invalid regardless of numeric similarity. Its192slots count against the576budget. No additional fit permitted this cycle. The new attestation helper verifies imported amat/autoresearch/llmrouter file paths are under registered source root and bytes match manifest; six guard tests and actual38module preflight reject the mistaken root before fitting. Next controlled original_future-only proposal must embed this guard and use correct source root,without rerunning completed original/current cases.


Verification031:31unit tests passed,including6source-root/hash guards;actual38loadedmodule preflight accepts correct root and rejects mistaken root,0searchslots. Ruff/mypy passed. Browser111historicalrows/conditionalseries/frontier/costs/stress/forward/mobile/selection passed. Original outer2024 frozenfit exact;current all192fitness values/selectedprograms exact,strict checkpoints differ only305registrymetadata positions+2derivedchecksums. Futurecase excluded dueunattested importroot;576totalcompletedslots,0slots initialoutputcollision,noadditionalfit. These are correctly recorded research failures/partial proof,not a numerical model drift claim or successful causality gate. Native incumbent exactall3costs.293histories/114sourceidentities/18coveredstudies/15pendingreviews/44legacyregistries unchanged,0invalidreceipts. No source/model coverage or promotion. Next corrected futureonly case must embed loaded-root+byte attestation before andafter fitting and preserve existingcompleted reference.


## Cycle032 — source-attested future-data fit

The corrected future-data test passed for the original 2024 historical fold: changing only post-training-cutoff closes reproduced both archived checkpoints, complete fitness ledgers, selected programs and training-data/label hashes exactly. Actual loaded library paths and bytes were attested before and after fitting. This cycle used exactly 192 search slots and reused the previously completed original reference. The invalid cycle031 future attempt remains excluded; its separate 192 slots remain recorded. Current-library exact checkpoint identity and the other rolling folds remain unresolved. This methodology test supplies no new strategy returns or promotion. The incumbent replay reproduced at 2/6/10bps under whole-share execution over the full campaign history; the original-mask conditional-switching charts remain displayed.

The original training prefix contains 1,511 sessions through December 31, 2021; the last eligible feature is December 29, with two terminal label rows purged. Seeds 871 and 872 each consumed the fixed 96 slots. No validation/test performance was used to choose programs. Both before/after import manifests are sealed with the result. Historical-source coverage remains incomplete (15 pending reviews and 44 legacy ledgers); the six-cell return frontier remains binding. No hosted model proposal or review was used.


## Remaining folds and final comparison checkpoint — 2026-10-20T14:22:16.614113+00:00

All seven archived genetic-search fits now reproduce original checkpoints, complete ledgers and selected programs; post-training-cutoff input mutation leaves each fit unchanged. This includes all three historical folds, two preflight contexts and two separately labelled simulated stress fits. Current-library checkpoints match the independently predicted source-registry-only transition in every fit. Cycles033/034 consumed exactly1152/2304 search slots under fixed preregistered budgets. No new portfolio-return candidate was generated or promoted. Current default reproduces at2/6/10bps on the whole-share observed-market contract. Historical strategy/source coverage and broader end-to-end learned pipeline validation remain incomplete; all prior rejected-candidate return-frontier cells remain binding.

The source registry includes hashes of its implementation files. Current source bytes therefore produce a different registry identifier and derived checkpoint checksum. Across all seven fits, changing only those specific diagnostic identifiers predicts every current checkpoint exactly; fitness values, genomes, search context and selected programs are unchanged. Raw hashes and the earlier strict-check failure remain preserved. This resolves the observed difference without declaring the raw checkpoints identical or approving a learned strategy.

|Strategy (6bps/side)|Full-history return|2020 onward|Maximum drawdown|2023|Decision|
|---|---:|---:|---:|---:|---|
|Current default · all groups + volatility switching|259.80%|192.72%|12.84%|5.74%|Keep current default|
|40% cap + trend quality|283.25%|214.19%|12.20%|7.73%|Not promoted: misses other return-frontier cells|
|40% cap + quality + 3% trade band|278.46%|214.38%|12.52%|5.96%|Not promoted: misses three return-frontier cells|
|Four-week ranking + cap + quality|233.75%|207.32%|12.33%|7.94%|Not promoted: misses five return-frontier cells|

All results use full2016signalhistory,flat$100000Jan3,2017–Oct12,2026,observednextsession09:30–09:35VWAP,corporateactions,wholepurchases,native1%openingvolumecap/nativeexits,missedfills,zerocashyield and2/6/10bps/side. Full per-cost frontier,111 historical replay rows and original conditional-mask visual comparisons remain displayed. Seven fit checks do not close15source reviews or44legacyledgers,do not validate all family selection/inference pipelines,and cannot justify default promotion. Candidate improvements in individual metrics remain sealed audit evidence,not active profiles. No hosted proposal/review spend or executor activity. Campaign checkpoint is published before deadline; scheduled finite campaign closure remains required.

Verification033–034:30unit tests passed,including6new checks that reject changedfitness/genomes/context,badchecksum or unexpectedregistry. Ruff/mypy and TypeScript/Vite build passed. Both campaignbrowser suites passed:7foldrows,4source-backedstrategy rows andreturnbars,111historicalreplays,2/6/10costcontrols,stress/forward/mobile and rejectedselectionblocks. Native incumbent reproducedexactall3costs inbothcycles. No executor activation,no strategy promotion.


## Cycle035 — feature-bank inference causality

All seven archived feature banks now reproduce under both original and current frozen libraries: 168 feature arrays match archived values exactly, including NaN placement and past-only eligible-asset masks. Training-prefix inference and mutation of adjusted closes after the training cutoff leave earlier feature outputs unchanged (fixed absolute tolerance1e-12,relative0). Loaded-source paths and hashes were checked before and after each of14cases; no GPsearch or fitting slots were used. This adds feature-inference evidence to the seven previously reproduced genetic fits. It does not validate family/recipe selection, every intermediate inference cutoff, full learned portfolio execution or historical-source coverage. The current default control again matches all2/6/10bps results; no strategy promotion.

All14cases also verify source snapshot partitions,original191modulemanifest,sourcecacheSHA,fit panel/datasetSHA and eligible masks. Simulatedcases are archival inference diagnostics only,never observedcampaignliquidity orreturnfrontier evidence. Sevenfold training table now distinguishes featurebank checks from broaderpipeline validation;034strategycomparisonandoriginalconditionalcharts preserved. Freshaudit still15pendingreviews/44legacyledgers,293comparablehistories. No hostedprovider spend or executor action.

Verification035:81symboliccausality/checkpoint/sourceguard tests passed. Both browser suites passed after035verifiedpublication:7fitrows+4strategyrows/returnbars,cost/stress/forward/mobile,rejectedselection restrictions.168/168archivedfeaturearrays exact,14source-attestedcases,0searchslots. Native incumbent exactall3costs. No promotion; pending sourcecoverage and end-to-end learnedpipeline limits remain.


## Cycles036–038 — inference boundaries,recipe selection and execution

Inference passed756feature/cutoff combinations,each with prefix and postcutoff-close-change checks,across observed2020–2026annual ends and selection/test boundaries. All48archived recipe choices reproduced from training and selection-only records; no test returns entered ranking. End-to-end archived execution reproduced54logical historical cases (20unique histories,duplicates retain identities) for baseline and all8family choices across2024–2026 at2/10bps. These executions use the original scheduled,fractional runtime and are explicitly excluded from the current whole-share return frontier. The current runner lacks the archived annual strategy/exit-context interface,so current execution parity FAILED/UNAVAILABLE. No approximation,engine patch,sourcecoverage waiver or promotion was made. Current incumbent again reproduces at2/6/10bps.

The archived strategy can reproduce its own trades, but it cannot yet be used as a verified replacement. The current engine does not support its annual changes to strategy settings and exit rules, and the old engine permits fractional purchases. Those results cannot be compared fairly with today’s whole-share strategy. A versioned native scheduling implementation, fair control replays, accounting and planning parity tests are still required. Other historical benchmark reviews also remain incomplete.

The756feature/cutoff combinations cover current frozen libraries for all7archivedfits,12features each,observed2020–2026annual ends and fold selectionend/firsttest/midtest/lasttest boundaries. Each combination has two causality checks at absolute1e-12,relative0,equalNaN. No new fitting/search slots. Source-root+byte and archived cache identities verified.

Selection48choices=40original familychoices+5correctedjoint+3retuned choices. All16recorded training slots perchoice,rankings,finalists,selected recipe,2bps primary and10bps stress gates reproduced;training/selection taskphase/date bounds exact. Earlier037reader wronglycoerced listpayloads to dict and failed before0choices;failure/proposal/source preserved. Revised reader verifies compact checksums for list/dict payloads with unchanged cases,gates,tolerances. This verifies mathematical reconstruction,not historical write timing or an untouched holdout.

Execution54logicalcases=baseline+8families x3historicalfolds x2/10bps;identicaltask histories deduplicated with identities kept. Correctedjoint is used for the joint arm. Fresh outputroot; original protocol/baseline copied,fitcache linked read-only by use;original191module/snapshotpartitions verified. Dates exact,equity absolute1e-6,exposure1e-12,metrics fixed preregistered tolerances,count/dictidentities exact. Source runtime uses fractional purchases and a different activation contract;none of these outcomes enter comparable returns. Current native signature has neither decision_schedule nor ScheduledDecisionContext. This is an explicit parity gate failure. No late native-engine edits,fee/cap relaxation or guessed equivalent.

Dashboard retains final4strategycomparison/returnbars,allcostfrontier,111historicalreplays,conditionalcharts,monthly/annual/stress/forwardviews,and now adds a6step pipeline gate table with the currentexecution blocker. Default unchanged.15pending source reviews/44legacyledgers still prevent overall-best claim. Next safe project:versioned annual mandate scheduling+native/liveplanning+accounting tests and fair baseline/frontier replays;outside this finite campaign unless fully verified. Final scheduled cycle still must seal final campaign report and pause heartbeat beforedeadline.

Verification036–038:1512inferencecausality checks/48selection choices/54logical20unique archivedexecution cases passed with source/data/cache guards. Native scheduled whole-share parity remains explicitly blocked. TypeScript/Vitebuild and all3browser suites passed:6pipelinegatetable,4strategyrows/charts,7fitrows,111historicalreplays,cost/stress/forward/mobile/rejectedselection controls. Current native engine unchanged and incumbent exactall3costs ineachcycle. No new fits,sourcecoverageapproval or promotion. Finalclosure/pause still required beforedeadline.


## Cycle039 — selection-adjusted uncertainty

A preregistered shared circular-block bootstrap across all293deduplicated comparable cost/sessionhistories found no displayed challenger with a positive95%simultaneous lower bound under either fixed1-month or3-month blocks. Allsourceidentities remain attached to duplicate histories.5000draws perblocklength,seed2026102039,maxabsolute centered standardized statistic;zero-variance columns explicitly marked. This is an added conditional uncertainty diagnostic,not a new promotion gate or a complete correction for all past research. No fitting,newstrategyreturns,promotion or nativeengine changes. Current default full-history control reproduced exactly at2/6/10bps;existing learned-pipeline/currentexecution blockers remain.

All three displayed challengers have adjusted intervals that include zero. Their apparent growth advantages are not established as reliable gains by this diagnostic. The calculation uses118paired historical months and jointly accounts for293currently comparable cost/history combinations,including rejected candidates. Both fixed block lengths are shown. These are annualized log-growth differences in percentage points,conditional on measured histories;older incompatible or unreviewed research is not covered,and reused data is not an untouched holdout. Partial October2026 is included equally for every history. Existing promotion rules are unchanged.

|Challenger at6bps|Annualized log-growth gap|95%simultaneous interval,1monthblocks|95%simultaneous interval,3monthblocks|
|---|---:|---:|---:|
|40% cap + trend quality|+0.64pp|-1.81to+3.09pp|-1.28to+2.57pp|
|40% cap + quality + 3% trade band|+0.51pp|-1.35to+2.38pp|-1.14to+2.17pp|
|Four-week ranking + cap + quality|-0.76pp|-7.11to+5.58pp|-7.40to+5.88pp|

Shared resampling preserves paired incumbent/candidate differences and cross-strategy/cost dependence. Nonstationarity,unmeasured/incompatible strategies,adaptive research and partialmonths limit inference;do not interpret intervals as guaranteed future returns or overall multiple-testing clearance. Selection_uncertainty helper kept with6meaningful tests:zero/null behavior,shared identical histories,repeatability,family widening and invalidinput refusal. Native code/default/executor unchanged. Final scheduled cycle must seal full campaign report and pause heartbeat before15:17:38UTC.

Verification039: six new uncertainty tests, Ruff, mypy and the TypeScript/Vite build passed. All four browser suites passed, including interval values/charts, cost/stress views, mobile layout, pipeline blockers and rejected-selection restrictions. The 87-module native engine manifest and default profile hash remain unchanged; the incumbent control reproduced at all three costs. A readable, content-hashed final report draft includes cost, annual, monthly, stress, frontier, pipeline and uncertainty tables. Final scheduled closure and heartbeat pause remain pending before the hard deadline.


## Final campaign closure

Closed October 20, 2026 at 11:07:00 AM EDT, before the hard deadline. The saved heartbeat is PAUSED and no measurement is running. No experiment or promotion was started in this final cycle. Final self-contained report: `/home/supasuge/Documents/amat-workspace/amat/docs/performance-campaign-final-2026-10-20.md`. Current default retained; 293 distinct comparable cost/session histories remain in the frontier audit, with historical source coverage and current learned scheduled whole-share parity unresolved. All prior failures, rejected candidates and incompatible protocols remain preserved. The final dashboard retains strategy/return, cost, monthly/annual/stress/recovery, fit, pipeline and uncertainty comparisons. Closure receipt and content hashes are sealed in the campaign folder.

Final publication verification passed before the deadline: all four dashboard browser suites passed against the final publication, the served API matches its seal, both report copies match their content hash, no measurement is active, and the saved automation status is PAUSED. The campaign is closed with unresolved historical-coverage and learned execution-parity gates; no promotion was made.
