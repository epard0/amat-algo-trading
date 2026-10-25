# Native planning compatibility for the free-model shadow arm

This local cycle entered October 24, 2026 at 01:36:09 UTC (October 23 at 9:36:09 p.m. Detroit), covering the 01:40 scheduled opportunity. Its hard stop is 02:16:09 UTC. It tests an execution-contract prerequisite with explicit synthetic inputs; it is not a strategy-return experiment, historical replay, model inference or paper-session observation.

**Result: naive conversion of a valid shadow action into native target weights is not a verified export path.** The source review predicted this result before the managed checks. All three fixed cases were evaluated at 2/6/10 bps without changing the native rebalance rules, costs, source or default. No strategy was promoted, no model was queried and no broker was contacted. v14.3.0 remains the current and original campaign baseline.

## Functionality and why the contracts differ

`amat.planning.build_plan` computes the strategy's own allocation from its market panel. It does not accept arbitrary agent targets. `build_policy_plan` similarly computes an allocation and applies its declared research risk policy; it is not a general agent-order exporter.

The lower-level `build_execution_plan` accepts post-constraint target weights. It converts weights to quantities at the decision mark and applies native rebalance/minimum-notional rules. It emits an inert intent artifact, not an accepted broker order. Confirmed/restricted/pending cash, account identity, original authorized intentions, policy identity, limit and trigger semantics are not arguments to that generic builder. Its expiry is a native plan backstop rather than the original shadow-contract expiry. Those boundaries are deliberate; missing agent semantics are not evidence that the ordinary native planner is defective.

The agent validator has a different role. It checks a proposal against its supplied context, account/policy/evidence bindings, whole-share/capacity/cash/risk constraints and original intentions. Acceptance grants replay-only admissibility, not a promise that the native strategy's rebalance rules will emit the action. It does not simulate fills. The previously verified repair still blocks new risk while original intent quantities remain unresolved.

A proposed bridge must preserve or explicitly reject all relevant action and account semantics. It cannot equate a numeric quantity match with safe native execution, treat a decision mark as the proposed limit, turn a conditional stop into a market exit, release pending cash, rewrite original authority, or pass a new policy under the existing default's name.

## Preregistered falsifiable contract test

The hypothesis tested was that delta-to-weight projection could represent a valid agent action without changing quantities or conditional, limit, expiry and account semantics. Independent source review predicted counterexamples and approved the exact driver/tests before official managed outcomes.

All three fixtures use a synthetic $10,000 account: 10 QQQ shares marked at $100, $9,000 confirmed cash, no pending intent, and explicit synthetic QQQ/SPY evidence. Decision time is October 23 at 20:00 UTC; the original requested execution is October 26 at 13:30 UTC and expiry at 13:35 UTC. These are fixed interface inputs. They are not observed Alpaca market data, opening liquidity or a scheduled future trading trial.

The original contract is validated first. The diagnostic then deliberately projects the entire holdings vector to float weights and calls the generic native plan builder using v14.3.0's unchanged profile parameters. It retains the complete original context/proposal and their hashes. **It does not run the current strategy decision engine or replay its historical account.** The resulting artifact is labelled `NAIVE` and is never exported to an execution service.

| Fixed case | Validated original action | Native projection at each cost | Finding |
|---|---|---|---|
| Hold | Retain 10 QQQ shares | No intent | Structural no-order agreement; no execution or export certification |
| Whole-share resize | Buy one QQQ share, limit $101 | No intent | Target moves from 10% to 11%; the unchanged 2% native rebalance band suppresses it |
| Conditional stop loss | Sell the 10 held QQQ shares only under the declared $90 trigger and $89 limit | Unconditional native sell of 10 shares at a $100 reference mark | Quantity agrees numerically, but conditional trigger and limit semantics are lost |

The source predictions held in all nine case/cost checks. Native decimal `10.0` and proposed `10` were compared numerically, not incorrectly treated as different quantities because of string formatting.

The original expiry is October 26 at 13:35 UTC; the generic plan expiry is October 27 at 13:35 UTC. The native earliest-execution date is a calendar lower bound to be resolved by the executor. Its October 24 lower bound does **not** establish a wrong-session defect: Monday execution may satisfy it. The missing binding is the original action's exact execution/expiry contract, not an invented calendar failure.

The resize's hypothetical cash commitments were $101.0202, $101.0606 and $101.101 at 2/6/10 bps. These are validator reservations, not filled notional, realized fees or measured performance. No future opening-window price, volume, partial fill, missed fill, gap or corporate action was simulated. Whole input quantities in these examples do not establish full whole-share fill parity.

## What remains blocked

All diagnostic outputs explicitly keep compatible native export, execution authority, performance evidence and promotion authority false. Even the hold case cannot certify an adapter that does not exist.

A future separately reviewed bridge needs a typed, source-bound envelope carrying the original action/context/account/policy/intent/cash/expiry identities; exact quantity and limit semantics; explicit treatment of native band/minimum rules; and refusal or supported execution of conditional actions. It must preserve prior authorization and uncertain outcomes. Whole-share sizing, conservative cash reservations, observed opening participation and missed/partial fills require native end-to-end fixtures. A changed economic contract requires fair current-default and QQQ control replays before strategy claims.

Stops/targets, intraday execution, direct shorts and inverse instruments remain separately capability-gated. This work neither enables them nor changes their flags. Real broker staged fills and historical VWAP are not universally identical; compatibility requires an explicit execution policy and cost/cap/accounting evidence, not a false demand for identical realized prices.

The prospective free-agent requirements from the prior report still apply: frozen future policy/model/prompt/schedule/horizon/stopping rules; a complete decision denominator including failures/holds; causal arrival and inference times; continuous isolated accounts and matched default/deterministic controls; all-cost risk/return/uncertainty; and native planning/execution verification. No such market trial began here. The previous hosted model remains unavailable after its HTTP 404; actual served identity and billing remain unknown. The campaign ledger stays at two attempts, with zero new calls this cycle.

## Evidence and verification

The managed wrapper completed three diagnostic commands and nine fixed case/cost witnesses at 01:43:20 UTC in 7.99 seconds, with no surviving processes. This is collection completion, not a passed execution gate. No new alpha experiment or return measurement is counted.

- Root affected regressions: 94 passed in 2.72 seconds, comprising the new diagnostic, existing native execution-plan and action-gate tests.
- Independent new diagnostic regressions: 19 passed in 2.27 seconds. These are repeats of the same cases, not additional unique cases.
- API/launcher regressions: 165 passed in 42.46 seconds, with the existing Starlette/httpx deprecation warning.
- UI build: 1,592 modules, passed in 5.80 seconds. The dashboard adds a native-plan compatibility finding inside the separate shadow panel.
- All 313 native library source files and the v14.3.0 profile match the prior sealed free-shadow source set. Only a new diagnostic script/tests and the conditional dashboard row were added in this cycle; no runtime strategy/executor/perps source or default changed.
- Initial registration used the wrong JSON-canonicalization convention for the reviewer's seal and stopped before publishing a manifest. The wrapper then refused that missing manifest without launching a child. This preflight failure is preserved in events; the declared compact review seal was verified before the successful preregistration. No measurement record was rewritten or counted twice.

The frozen diagnostic protocol is `ee21882174dd9164d7cc421b236aa849fa3e5ea3f9cbb86a3e0cc27a557ee719`; its argv manifest is `1f030835e890dd8d54c8e2e5b3266c0bdd5fb4dd4849ff0c959d326cf54d4557`. Evidence lives under campaign `validations/shadow-native-0140/`, `cycles/hourly-shadow-native-0140/` and `research-review/20261024-0140-native-shadow/`.

The dashboard retains all 42 numerical comparison rows and earlier rejection decisions. No synthetic return row or new investment gain is added. Campaign alpha counts remain five completed experiments, ten rejected candidate arms and zero promotions. The final publication, independent outcome review, actual finish time and browser verification are bound by the cycle completion record and state/events.
