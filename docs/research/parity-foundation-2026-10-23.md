# Execution-parity foundation review — October 23, 2026

This review establishes the locally tested native decision/accounting contract and repairs concrete simulation and staged-execution differences. It does **not** establish equality between historical opening-window VWAP fills and actual Alpaca paper fills. No remote source, account, executor setting or order was changed by this review. No strategy was promoted.

The exact baseline remains `configs/strategy/amat_group_selection_volatility.toml`, v14.3.0, SHA-256 `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8`. The baseline's historical return must be reproduced under the frozen source/data contract before comparison with any candidate. The related QQQ numerical benchmark is measured separately; this document makes no QQQ outperformance claim.

## Scope and capability findings

| Component | Verified behavior | Boundary or remaining limitation |
|---|---|---|
| Current default decision and ordinary native plan | Same instructions for the tested current-default configurations, two seeded panels, 2/6/10 bps, and empty or inherited fractional holdings | These are deterministic regression scenarios, not prospective performance evidence |
| Native historical execution | Next-session observed window VWAP, explicit 1% entry participation cap, whole-share buys/partial rebalances, fractional full exits, costs and missed-fill records | Native simulation is a fill model; it does not reproduce an exchange queue or market impact |
| Native accounting | Raw marks, explicit split/dividend actions, buys consume notional plus costs, sells release net proceeds, no negative cash, reconciled ledger | An accounting identity cannot discover an omitted vendor corporate action |
| Local simulated account | Replays the native account and journals observations | Previously omitted the participation cap; patched locally and tested |
| Current default simulation target display | Uses the same explicit exchange-session feature precomputation as ordinary planning | Previously failed for adaptive rotation because sessions were absent; patched and checked against an exported native plan |
| Split holdings display | Applies splits to opening holdings before the same session's fills | Previously reconstructed quantities from fills alone, despite correct native equity; patched and tested with a real simulated 1.5-for-1 split |
| Live appended simulation tail | Operational observation with `research_eligible=false` while actions are unverified | Does not fetch or invent missing actions; excluded from verified comparison/promotion evidence |
| Staged executor quantities | Whole purchases after initial budget and final fresh-cash check; full fractional exits retained | Partial trims previously remained fractional; local repair rounds them and preserves original authorization/residuals |
| Actual paper fills | Separate bounded limit-order contract with cash reserve and observed broker receipts | Actual historical-VWAP/1%-volume fill parity remains **unverified**; a hold/no-trade session cannot establish it |
| Direct shorts | Current default native account is long-or-cash; a sale reduces an existing long | Negative positions/margin/borrow are outside this native contract; separate research implementations do not establish executor support |
| Inverse ETFs | Potentially representable as long shares in a future validated universe | Still require actual instrument history, corporate actions, liquidity, policy/allowlist and separate economic validation |

## Repairs and meaningful verification

`src/amat/simulation/__init__.py` now passes `max_entry_window_participation=0.01` and `whole_share_buys=True` explicitly. A thin-opening-volume regression executes the real native runner and checks that buys are integral and no larger than five shares when 1% of observed volume is 5.5 shares. Missed participation remains recorded.

The simulated report's quantity reconstruction now applies each covered split before that session's fills. A split is not a trade; summing fills alone loses the entitlement. A synthetic covered-split replay checks that reported holding weights plus cash divided by equity equal one. A separate action-ordering test includes a forward split, reverse split, same-session fills and a cash dividend. A dividend changes cash, not share quantity.

Appended live sessions still lack verified corporate-action coverage. Such observations are now explicitly marked ineligible for research. The old caveat saying missing actions always understate return was incorrect: an omitted split can distort measured returns in either direction. The corrected caveat states that the effect is unknown. This is a restriction on eligible evidence, **not a claim that live-tail action coverage was completed**. Frozen verified snapshot runs remain the campaign measurement source.

Adaptive rotation requires exchange sessions to construct its causal features. The simulated account's terminal-target display now supplies them through `precompute_decision_inputs`, as the ordinary planner already did. The current default can complete the simulated account step, and its reported next-session targets equal the positive targets of the exported ordinary plan on the same panel.

`executor/src/executor/batch.py` now rounds a positive-target partial sell down to an integer in whole-share mode. A zero-target full exit still preserves its fractional entitlement. Tests check original authorized quantity, cumulative fills, remaining unsent intent, absence of top-up/resubmission, post-budget shrinking cash and pending cash commitments.

The changed sell semantics enter the policy payload explicitly:

```json
{
  "partial_sell_quantity_quantum": "1",
  "full_exit_quantity_policy": "preserve_fractional_entitlement"
}
```

The new whole-mode policy identity is `efe5fbb4301f5b4485e52924139784d0b990c27661326da139ad61ab15f879f2`. An old-policy sealed batch cannot resume silently under this identity; the native identity guard refuses it before another submission. The default fractional-mode identity is unchanged. There was no remote deployment or migration of an active authorization. Any later deployment must be separately released against current state with the intended new policy identity and no overlapping writer.

## Test evidence

The sealed evidence is in `outputs/research-review-2026-10-23/parity-foundation/parity-summary.json` with its sibling SHA-256 seal. That record includes exact reviewed source hashes and JUnit hashes.

| Gate | Actual result |
|---|---|
| Affected AMAT simulation/current-default planning/native execution-accounting/annual schedule tests | **76 passed**, zero failures, errors or skips |
| Full executor package regression suite at the tested source state | **394 passed**, zero failures, errors or skips |
| Independent joint-downside hypothesis causality/static-control tests | **7 passed**, zero failures, errors or skips |
| Ruff lint and formatting on changed simulation/executor source and new tests | Passed after correcting only new test lint findings |

The earlier 389-test executor run was followed by the final full suite after policy-identity hardening and concurrent test additions. An intermediate new regression expected the wrong error-message regex; the implementation already refused the mismatched policy, and the assertion was corrected. The final full suite above includes that regression.

## Independent pre-outcome review of the first experiment

The first proposed fixed experiment reduces positive targets by 25% only when at least three targets of at least 5% have weighted downside-return correlation of at least 0.60 over the last 60 completed sessions, alongside negative target-weighted return over 20 completed sessions. Its three total arms are unchanged baseline, conditional reduction and unconditional static 75% targets.

The independently sealed mechanism review is `outputs/research-review-2026-10-23/parity-foundation/joint-policy-preregistered-review.json`. It was written **without inspecting candidate outcomes** and pins driver SHA-256 `bf7be57c49ead1fca9738462ee79bb4ec26ed883a13013232242ed4f1bef4fb5`. Registration also requires actual baseline month attribution showing joint losses in at least three assets. Those losses motivate the experiment but do not prove correlation caused them.

The causal return slice ends at the completed decision close. Future-price mutation after that close leaves the decision unchanged; another test verifies the completed close is actually included. Further tests establish reduce-only bounds, minimum meaningful holdings, incomplete-lookback abstention, negative-but-uncorrelated non-triggering, and an unconditional static ablation that does not depend on a conditional trigger. The static control tests whether conditional timing adds value beyond simply taking less risk.

**Verdict: approved for measurement only.** The new decision-policy callback still requires equivalent native exported planning support and exact reproduction before a winning candidate could be promoted. Baseline planning parity does not automatically establish parity for a new hook. Full-history return, all three costs, forward/stress/recovery/uncertainty gates and independent outcome review remain separate requirements. No generic receipt boolean should substitute for these calculations.

## Meaning of a passed foundation

A foundation pass permits controlled native research against the same frozen historical contract. It does not authorize live trading, unsupported short positions, a remote rollout or a statement that the broker has reproduced the backtest fills. A research candidate that fails a numerical or implementation gate remains inactive, with its sealed evidence retained. Improvements to accounting or reporting are useful engineering changes and do not themselves earn a strategy promotion.

## Integration regression follow-up

The coordinating full-unit run identified three failures that were separately reproduced and traced to byte-identical archived pre-edit tests and production implementations. The initial failures remain evidence; that run must not be described as originally passing.

Two tests still expected the earlier daily-core default even though both the ordinary CLI and autoresearch CLI already used the user-selected volatility default. Only the stale test expectations were corrected; explicit daily-core historical comparison, daily-profile loading and rejected-catalog coverage remain intact. Both complete files then passed seven tests. See sealed `historical-default-test-drift.json` and its original/corrected JUnit files in the foundation evidence directory.

The third test assumed stressed total missed fills must equal or exceed the baseline path's misses plus injected drops. The actual original failure was `41 >= 38 + 4`. Withheld orders change subsequent holdings, cash and instructions, so those two counterfactual paths need not have additive missed counts. The engine and original test were unchanged from the archived baseline. The corrected test audits each actual dropped instruction by session, symbol, side and requested quantity and proves it did not fill. An additional fixed-cash, fixed-window twenty-order test proves that exactly the tenth and twentieth eligible orders are withheld: two misses and eighteen fills. No engine economics changed.

The complete stress file, native execution/accounting file and both corrected default-test files passed **51 tests** together; scoped lint and formatting passed. The original failing stress test was also rerun from its immutable backup and preserved as a one-test failure. See sealed `stress-counterfactual-test-review.json`, `stress-original-isolated.xml` and `stress-native-default-corrected.xml`.
