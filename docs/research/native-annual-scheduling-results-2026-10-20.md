# Native annual scheduling: final comparison — October 20, 2026

Native annual strategy and exit scheduling is implemented. The current default remains **all groups + volatility-triggered cost-aware switching**. All 16 fixed annual recipe variants are rejected for promotion. The tested engineering capability is retained; no rejected recipe was added to active profiles.

This is a follow-up study after the finite twelve-hour campaign closed. Its sealed final publication, state and paused automation are preserved.

## Shared measurement contract

Flat $100,000; January 3, 2017–October 12, 2026; 2016 signal warm-up; next-session observed 09:30–09:35 VWAP; whole-share purchases; native 1% opening-volume entry cap; dividends/splits; missed fills; zero cash yield. Costs are charged on each side.

The eight original saved recipe families were replayed on their original starting strategy and as fixed transfers onto the current default. No feature masks, selected recipes, model parameters or selection dates were retuned after outcomes. Annual activations are in 2024, 2025 and 2026 after prior-year selection cutoffs. Earlier stress windows exercise the starting strategy, not an annual learned policy that had not yet activated. All history has previously been researched; none is an untouched holdout.

The 26 family/reference identities map to 18 distinct measured account histories across all three costs. Identical histories remain explicit aliases, with every original identity retained in `annual-history-aliases.json`; they are not additional independent evidence.

## Results at 6 bps per side

| Strategy | 2017–2026 net return | 2020 onward | Largest fall | 2023 | Trading costs |
|---|---:|---:|---:|---:|---:|
| core_daily | 259.80% | 192.72% | -12.84% | 5.74% | $24,604 |
| annual_portfolio | 270.99% | 201.82% | -12.84% | 5.74% | $20,815 |
| annual_joint | 262.05% | 194.55% | -12.84% | 5.74% | $18,196 |
| frontier_cap40 | 283.25% | 214.19% | -12.20% | 7.73% | $24,942 |
| frontier_band | 278.46% | 214.38% | -12.52% | 5.96% | $23,193 |

The allocation recipe beats the current default at all three cost assumptions, but it falls below the strongest valid prior full-history and forward returns. At 6 bps it improves 2024 from 11.98% to 13.61% and 2025 from 33.89% to 37.23%, then trails in 2026 to date: 19.18% versus 20.20%. It reduces fills from 1,929 to 1,842. These gains do not satisfy the promotion rule.

| Cost per side | Default full return | Allocation recipe full return | Binding full-history frontier | Binding 2020-onward frontier |
|---|---:|---:|---:|---:|
| 2 bps | 296.39% | 311.81% | 317.78% | 242.36% |
| 6 bps | 259.80% | 270.99% | 283.25% | 214.38% |
| 10 bps | 221.88% | 239.87% | 249.40% | 199.40% |

Each frontier cell can belong to a different historical strategy. Previously rejected candidates remain binding where their comparable returns are valid. A gain over the default alone cannot earn promotion.

## Validation and implementation

- 15 pre-change economic control histories match exactly at 2/6/10 bps.
- All 78 final-source histories reproduce their measured counterparts exactly. Whole-share purchase violations: zero.
- 144 real-history observed-prefix planner checks across four representative recipe variants and all three costs match native orders, missed fills and exit embargo state. The 12 planning-check replays also match the full native account histories.
- 74 annual-scheduling/nested/exit/invariant tests and 57 execution/accounting/plan tests passed. Source lint and type checks passed.
- Dashboard backend tests and browser tests cover sealed evidence, rejected candidate selection, costs, both starting strategies, growth/monthly/annual/stress/uncertainty views and responsive layout. Final test receipts are recorded separately.

`ScheduledDecisionContext` is a versioned research declaration. Configuration, universe, execution, cash/accounting invariants, selection cutoffs, activation order, input arrays and identity are validated. Pending orders settle before activation; account cash, holdings, adjusted entry bases and exit embargoes remain continuous. Unsupported fractional execution and incompatible cash policies fail closed.

`plan_scheduled_decision` uses the native instruction constructor with the actual settled ledger and an observed market prefix. It requires no fabricated future market bar. Returned plans have no execution authority. Original authorized quantity, cumulative fills, remaining intent, pending cash, expiration and account/policy identity in the executor contract were not modified. No live annual profile was installed.

## Completed benchmark source review

All 44 registries were reviewed: 354 daily experiment records, 257 daily candidate identities and 353 program/candidate pairs, including rejected cases. Frozen source traces deduplicate to 239 distinct decision inputs. All 706 fixed prefix checks passed. All 239 inputs were measured at each cost: **717 whole-share economic replays**, representing 221 distinct measured account-history groups after exact deduplication. Every identity remains linked to its histories.

The audit checked 2,286 supervised fit-boundary records, 108 inner-validation records and 11 rule-router records. Recorded training label exits precede prediction/validation. These boundary checks and two fixed prefix checks per source pair do not substitute for counterfactual tests at every learned inference cutoff.

The fitted daily-group remainder is resolved: original scaler, model coefficients, probabilities and quotas reproduce; seven 2020–2026 annual fold prefix checks and label-maturity checks pass; all 15 native gated/control cost replays are complete. The original frozen shadow NAV is a signal input, not a stale economic benchmark. Both controls and candidates are remeasured under whole-share execution.

Another 38 intraday experiments were discovered in report files outside the empty SQLite experiment tables. Their raw results and identities are retained separately because their execution clock differs. Schema parse failures in newer readers were resolved with original frozen source; they were not used to discard candidates.

## Remaining coverage limits and decision

The source review is complete, while common-contract coverage remains incomplete:

- The broader 38-ETF study requires 15 asset histories absent from the fixed shared snapshot. No assets were dropped and no liquidity was fabricated.
- Two signed/index-sleeve studies use different accounting/execution assumptions. Their original returns are preserved; no unverified conversion into the native daily account is asserted.

The native annual variants fail the known valid frontier independently of these remaining limits. No variant is promoted and no overall-best claim is made.

The paired moving-block uncertainty diagnostic uses 100,000 resamples at 5/20/60-session block lengths. Adjusted intervals allow for the full variant/cost/block comparison family. At 6 bps the allocation recipe adds roughly 0.31% in annualized relative growth versus the default, with the 20-session adjusted interval spanning approximately −1.39% to +1.77%. This post-hoc reused-history result is not proof of a durable advantage.

## Dashboard and evidence

Open Performance, then **Can yearly updates improve the current strategy?** The dashboard includes the three cost assumptions, original/current starting strategies, all variants, return bars, account growth, monthly heatmap, annual/stress results, uncertainty and the expanded benchmark review. Rejected variants are available for viewing only.

- `publication.json` and `validation-receipt.json`: sealed annual comparison.
- `final-decision.json`: rejection reasons checked against the expanded frontier.
- `planning-protocol.json`, `planning-receipt.json`: 144 observed-prefix planning checks.
- `benchmark-review-final.json`, `benchmark-summary.json`: complete source/replay review, aliases and frontier.
- `legacy-replay/`: original trace exports and 717 shared-account cost records.
- `fitted-benchmark-replay/`: original fitted-fold reproduction and 15 economic records.
- `remaining-benchmark-review.json`, `intraday-report-review.json`: explicit incompatibilities and original raw evidence.
- `environment.json`: dependency versions and lockfile hashes.
- `implementation/`: frozen study and orchestration drivers.
