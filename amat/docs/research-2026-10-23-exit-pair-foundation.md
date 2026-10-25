# Held-position exit research: functionality audit and native paired-account foundation

**v14.3.0 remains unchanged.** Priority 9 needs a genuinely different economic target and further model/planning infrastructure. This cycle completed a source-bound independent audit and a small native paired-account mechanics diagnostic. It fits no model, measures no new strategy and spends no alpha experiment. The campaign remains at five completed experiments, ten rejected candidate arms and zero promotions.

## Functionality and existing-model analysis

The native default is long-or-cash and its current profile hash remains `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8`. Native exits, explicit dividends/splits, settled cash, whole-share purchases, 1% observed opening-volume entry caps and missed fills remain active. The research target-reduction callback reads an actual completed-close ledger. It can lower a target, but it is not an exported native candidate profile or broker policy. The runner starts a flat account with complete signal history; this exercise reconstructs state through full prefix replay rather than pretending that cash/holdings alone are a restart checkpoint.

Independent reviewers checked actual model source and prior artifacts, including eleven old protocol/diagnostic/result seals. The existing models answer different questions:

| Existing functionality | Actual target and controls | Why it is not the proposed held-position utility model |
|---|---|---|
| Annual logistic adversity model | Probability that the ungated daily-group 2bps account loses at least 3% at a close in the next ten sessions; nine market/shadow-NAV features; annually fitted train-only scaler; mature-label purge | Predicts an adverse event rather than the net difference between executing an exit and continuing the actual strategy. All-cost policies reuse the 2bps target. It does not condition on the candidate account’s actual held asset, cash, re-entry or native order path |
| Boosted entry-quality model | Multiple return/adverse/deferral forecasts, maturity offsets, equal-date weighting, three inner validation folds, bounded entry deferral and opportunity penalties | Changes additions, not exits of held positions. Its generic candidate contract rejects the current cross-sectional allocator. A useful existing implementation cannot simply be renamed into a compatible exit model |
| Earlier per-asset stop/target bandit | Idealized daily-high/low threshold payoffs, stop-first convention on ambiguous bars, twice-one-way fee assumptions, train membership by entry date | Does not establish completed-outcome maturity at selection, native next-window fills, actual held-state/cash coupling or paired re-entry. Both retained v6 grids activated zero symbols |
| New paired foundation diagnostic | Continue actual baseline policy versus one frozen five-decision target-suppression intervention, native whole account and terminal valuation | Demonstrates reproducible mechanics for a possible target. Three dates are insufficient for fitting, calibration, nested validation or efficacy claims |

The retained old adversity study’s full-history net returns are below its own unmodified daily-group control at every cost:

| Cost per side | Old daily-group control | Old ML adversity | Old ML plus staged recovery |
|---|---:|---:|---:|
| 2bps | 262.75% | 218.96% | 206.49% |
| 6bps | 220.32% | 187.16% | 177.62% |
| 10bps | 187.59% | 150.37% | 146.59% |

These are preserved historical outcomes, **not new matched v14.3.0 replays**. Their profile/source/accounting identities remain separate. The old model’s Brier error is worse than its constant training-rate comparator in all seven annual folds; its 0.35 threshold produces no exits in 2022–2026. That evidence argues against reusing this target or adjusting its threshold after seeing losses. It does not prove every possible learned exit policy must fail.

## Frozen mechanics question and preregistration

The new diagnostic was independently reviewed before outcomes. The approved driver, all 313 native library files, actual profile, helper, tests, reviews and archived controls were pinned in a sealed 329-input manifest. The original campaign protocol, runtime library and default were not amended.

Fixed cutoffs are the last observed closes of 2019, 2022 and 2025. At each cutoff, select the lexicographically first actual positive held asset, without inspecting its later outcome. Replay the native account from the original flat January 3, 2017 start using the full 2016 signal history. Compare:

1. Continue the unchanged baseline policy, including any natural exits. This is not a literal buy-and-hold comparator.
2. Set only the selected asset’s target to zero at decision closes `t..t+4`, then restore the ordinary allocator at `t+5`. Mark the account at close `t+6`, after the first lag-one restoration opportunity.

Restoration is permission, not a promise to buy. A baseline zero target can remain zero; whole-share residuals, participation caps, cash rejections and genuine missing fills remain in the account. A target already zero can also be inert under ordinary native bands; the callback does not invent a guaranteed liquidation. No previous order authorization, pending intent or account identity is edited.

For each cost, all 2,457 archived control rows reproduce exactly. Each intervention’s explained account prefix through the cutoff reproduces its current control exactly. The callback’s actual cutoff cash, equity and held quantities match the independently reconstructed control. Full prefix replay preserves the construction of internal pending orders, selection state and exit bases; this is not a newly implemented serialized checkpoint/fork API or proof of live restart parity.

## Actual all-cost paired examples

The bounded run completed twelve native replays: three full current-control reproductions plus nine complete-prefix intervention accounts. All three commands returned zero in **52.35 seconds** and no worker survived cleanup. Independent saved-record verification confirms every pair’s accounting, observed VWAP, whole-share buys, entry cap, fees, misses and terminal arithmetic.

The following values are **basis points of the shared cutoff account NAV**, intervention minus continue-baseline at the common terminal close. They are local account differences, not annual returns, isolated asset alpha, a continuous strategy curve or independent samples across costs.

| Decision cutoff | Deterministically selected asset | Terminal close | 2bps cost | 6bps cost | 10bps cost |
|---|---|---|---:|---:|---:|
| 2019-12-31 | DBC | 2020-01-09 | +15.51 | +15.48 | +14.25 |
| 2022-12-30 | EFA | 2023-01-10 | -67.74 | -69.09 | -71.06 |
| 2025-12-31 | EEM | 2026-01-09 | -41.70 | -43.54 | -45.37 |

![Native paired-account mechanics](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/figures/exit-pair-native-mechanics-v2.png)

At 6bps:

- **DBC, December 2019:** the intervention sold all 1,563 shares January 2; the baseline naturally exited January 9. The restored target was zero, so no re-entry was required or missed. The terminal account difference was **+$190.33**. More favorable early exit in one example is not a deployable signal.
- **EFA, December 2022:** the intervention sold 719 shares January 3 and bought 761 shares January 10, versus 811 baseline shares at the terminal. Its account finished **$1,304.49 lower**. Entire-account window fees rose from **$40.48 to $86.28**. Lost market participation and the changed portfolio path matter alongside explicit fees.
- **EEM, December 2025:** the intervention sold 1,206 shares January 2 and bought 1,233 shares January 9, versus 1,206 baseline shares. It still finished **$1,303.31 lower**, with **$82.51** in window fees versus zero baseline fees. More terminal shares do not establish a better account outcome.

Whole-share remainder misses are retained as `quantity_increment`, not relabeled as failed whole-sized orders. Other-asset positions and fills can change because the accounts have different cash and NAV after the intervention. The DBC control also contains cash/participation misses in some cost scenarios, illustrating why identical asset price ratios do not reproduce the native whole-account label.

No episode difference is added to another or stitched into a winning account. These three examples have no declared statistical power, confidence interval or predictive-selection claim. No model observed or acted on a future outcome; the post-event label nevertheless becomes available only after its terminal close. Historical markets have been researched before and are not an untouched holdout. This exercise verifies the observed cases, not every dividend/split/recall or execution stress in a general paired-label engine.

## A concrete fold-boundary leakage check

All three fixed year-end examples have outcomes that mature in the next calendar year:

| Feature/decision close | Next-year first prediction | Outcome available only at terminal close | Eligible for that first next-year fit? |
|---|---|---|---|
| 2019-12-31 | 2020-01-02 | 2020-01-09 | No |
| 2022-12-30 | 2023-01-03 | 2023-01-10 | No |
| 2025-12-31 | 2026-01-02 | 2026-01-09 | No |

Selecting training episodes by entry/feature year alone would leak their later outcomes into the next-year initial fit. All overlapping labels must be purged, and any declared embargo applied after actual maturity. No model was fitted here and no contaminated prediction was generated. This is a concrete reason to build maturity-bound episode records before reusing the earlier per-asset training logic.

## What is still required before an ML experiment

Priority 9 remains deferred as an alpha experiment. A future target should explicitly name the intervention and comparator—for example, a frozen exit/embargo/restoration policy versus continuing the baseline—and measure native terminal utility from the actual account state. It must preserve candidate-state support, cash and pending commitments, original authorization, distributions, costs, whole-share fills and native risk exits. A price-drop classification or theoretical common-exit price ratio is not a substitute.

Build a provenance-bound episode dataset with enough independent decision dates, not a large count of overlapping asset/date labels. Store decision features and account state separately from later outcomes; the first admissible fitting time is strictly after every included terminal outcome and declared embargo. Inner chronological training/selection must use past-only preprocessing and mature labels; purge overlapping label intervals and retain a chronological outer test excluded from fitting and selection, explicitly labeled reused history. Freeze the utility horizon, threshold/calibration and abstention before outer outcomes. Annual 2020–2026 test accounts must remain continuous and reproduce current-default controls at all three costs.

The measured examples show why the target needs re-entry and opportunity cost: an apparently cautious exit may save a local loss, miss a recovery, change portfolio allocations or incur extra fees. They do not identify a profitable conditional rule. Do not retune the old adversity threshold or E01–E05 settings using these cases.

Candidate native profile/exported-plan parity, compatible execution-contract validation and actual nonnumeric verifier/promotion integration remain unresolved. The tested numerical bridge stays read-only and all promotion authority remains disabled. No remote rollout follows research evidence.

## Tests, failures and retained implementation

The affected suite passed **58 tests in 5.89 seconds**, covering the new pairing helpers, native exit rules and annual scheduling. The new helper suite independently passed 21 tests. Scoped Ruff lint and formatting pass. Tests cover causal asset selection, positive holdings, exact phase boundaries, no replacement cutoff, missing data/targets, input and captured-state immutability, optional observed panel fields and exact Decimal serialization.

Initial test invocations used a nonexistent filename and then the workspace directory rather than the package directory; the latter produced three configuration-fixture errors alongside 55 passes. Repeating from `amat/` passed all 58 without changing production source. Initial registration used system Python without `polars`, so no protocol, manifest or strategy measurement was created; the controller refused the missing manifest. Registration then used the existing AMAT virtual environment. These invocation failures are recorded in the sealed premeasurement receipt. A reviewer initially asserted the wrong controller success label; the corrected independent audit retains that review correction. None of these failures is relabeled as a successful measurement.

Only the new research driver and its tests are retained as executable additions. No native library economics, existing controller, strategy configuration, frontend/API source, remote bot, account state or broker order was changed. The dirty source baseline, including untracked files, is archived under `source-snapshots/hourly-2340-exit-foundation/` before changes. Existing benchmark generations and old rejected candidates remain intact.

## Dashboard, campaign state and next work

The current scientific dashboard continues to show the existing all-cost 42-row default/QQQ/candidate comparison, monthly/annual/stress/recovery plots and keep/reject limitations. This mechanics diagnostic adds explanatory source-bound findings rather than an invented ML strategy return row. Five strategy experiments, ten rejected candidate arms and zero promotions remain unchanged. The original anchor and current default are both v14.3.0.

The next queue item is the separately gated free-model portfolio-agent arm. Before any dispatch, recheck the current catalog/account availability, pinned zero-priced identity, remaining shared quota, exact sanitized prompt/action contract and deterministic constraints. Keep the original maximum24campaign attempts, maximum2percycle, 60second timeout, no paid fallback and prospective shadow requirement. One earlier failed429attempt remains the only campaign inference attempt so far. Closed-market hours cannot establish trading performance. Do not force additional experiments or grant production action authority to fill the budget.

## Reproduction evidence

- [validations/exit-pair-2340/protocol.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/validations/exit-pair-2340/protocol.json)
- [validations/exit-pair-2340/summary.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/validations/exit-pair-2340/summary.json)
- [manifests/hourly-exit-pair-2340.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/manifests/hourly-exit-pair-2340.json)
- [cycles/hourly-exit-pair-2340/receipt.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/cycles/hourly-exit-pair-2340/receipt.json)
- [research-review/20261023-2340-independent/exit-target-audit.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2340-independent/exit-target-audit.json)
- [research-review/20261023-2340-independent/exit-method-review.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2340-independent/exit-method-review.json)
- [research-review/20261023-2340-independent/exit-pair-source-review.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2340-independent/exit-pair-source-review.json)
- [research-review/20261023-2340-independent/exit-pair-outcome-audit.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2340-independent/exit-pair-outcome-audit.json)
- [research-review/20261023-2340-independent/root-premeasurement-tests.json](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261023-2340-independent/root-premeasurement-tests.json)
- [Native pair results at 2bps](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/cycles/hourly-exit-pair-2340/pairs-2bps.json)
- [Native pair results at 6bps](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/cycles/hourly-exit-pair-2340/pairs-6bps.json)
- [Native pair results at 10bps](/home/supasuge/Documents/amat-workspace/amat/outputs/research-campaigns/2026-10-23-adaptive-12h/cycles/hourly-exit-pair-2340/pairs-10bps.json)

## Cycle checkpoint

Actual entry:2026-10-23T23:37:00Z. Actual scientific/publication checkpoint:2026-10-23T23:57:43.160043+00:00 (20.72 minutes), before the00:17:00UTC hard stop. Eight actual completed campaign cycles; this cycle spends zero alpha experiments, fits zero models and dispatches zero hosted requests. Next scheduled opportunity:2026-10-24T00:40:10Z (October23,8:40:10p.m.Detroit). Focus next on the separate free-model shadow arm, preserving the original deadline/final03:40wake and budgets.

The focused campaign API suite passed24tests in0.09seconds. No frontend/API source changed, so previous full UI/package receipts are not relabeled as new builds. At1280,768and390CSS-pixel widths the research document, panel and all three table wrappers remain contained. The browser verification batch initially timed out; it was repeated successfully and the original viewport restored. The report wording correction requested by independent review is retained beside its initial reviewed version.
