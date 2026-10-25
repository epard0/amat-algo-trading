# Separate OpenRouter portfolio-agent research arm — October 23, 2026

## Implemented boundary

The user requested an independently evaluated free-model portfolio monitoring/action arm, with stops, take profits, resizing, volume-aware entries, unused-cash deployment, long/short choices, intraday actions and hedges. The minimum implemented component is an **inert structured action contract and deterministic admissibility validator**:

- [Contract and validator](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/improvements/agent_portfolio_arm.py:1).
- [Focused economic refusal tests](/home/supasuge/Documents/amat-workspace/amat/tests/unit/test_agent_portfolio_arm.py:1).

There are no hosted model calls, added dependencies, shell/code execution, broker adapters, active profile changes or executor changes. Passing validation returns `admissible_for_replay_only`, `research_only=true`, `execution_authority=none`. It does not mean a proposal filled, was profitable, is economically optimal, or may be submitted to either bot.

The module is deliberately separate from the native long-or-cash strategy, the historical signed-sleeve scenario ledger and the remote paper monitor. A model's suggestion cannot silently relax any existing authorization or convert monitoring into trading.

## Research action contract

`research-agent-portfolio-action/1` binds the exact decision timestamp, scheduler-approved execution timestamp and execution clock, expiry, account identity, policy identity, complete evidence/account/context digest, original authorized-intent digest and gross/net/symbol limits. The trusted context carries confirmed cash, restricted cash, signed actual positions, equity, point-in-time marks and capacities, and observed existing intent state: original quantity, cumulative fills, remaining intent, pending cash, expiry and account/policy identity. Those quantities must reconcile.

The proposal provider is explicitly OpenRouter and the model must name a `:free` model ID. This suffix check is **not** proof the service currently lists the model, honors zero price, or supports a schema; the future provider layer must verify the catalog and zero-cost policy before requests. No model is selected or called in this implementation.

Accepted vocabulary is `hold`, `stop_loss`, `take_profit`, `resize`, `open`, `close`, and `hedge`. A non-hold action specifies a known symbol, buy/sell side, exact decimal-string quantity, bounded limit price and evidence identity. Stops/TP also carry a trigger price. Unknown fields, executable-code fields, unknown actions, nonfinite numbers, ambiguous directions and mixed hold/order output are rejected. Rationale is bounded text and is never evaluated as code.

The validator enforces:

1. Timezone-aware clocks; decision strictly before execution, execution strictly before expiry; account/market observations at or before decision and within the declared freshness budget. A model cannot substitute a later fill window or a stale account.
2. Marked equity equals confirmed cash plus signed position value within one cent. Restricted cash cannot exceed confirmed cash and must at least cover existing short proceeds. This is a basic research identity, not a complete stock-loan margin engine.
3. One action per symbol, with no action on a symbol that already has remaining authorization. A proposal cannot cancel, replace, enlarge, duplicate, assume expiry cancellation, or spend existing pending commitments. Full exits must close actual holdings; reversals require a separate settled close and new context.
4. Protective action direction: sell protection for a long, buy-to-cover protection for a short; no over-close. A long stop lies below the observed mark and its TP above; the directions reverse for shorts. The limit must support the declared trigger. A stop is a **proposal for a later fill model**, not a guaranteed maximum loss.
5. Whole-share risk increases within a conservative past-observed capacity bound. Legacy fractional holdings can be reduced or closed. The future replay must additionally constrain actual fills by the observed execution-window liquidity; a known prior capacity is not a license to reveal future volume early.
6. New cash commitments include fixed one-way execution costs and reserve requirements, subtract restricted and already pending cash, and never count proposed or unsettled sales as available funding. The v1 policy is unlevered. This intentionally rejects some economically feasible simultaneous reallocations until sales have genuinely settled in a later context.
7. Gross exposure, absolute net exposure and individual-symbol limits for both standalone and cumulative proposed increases. Potential or contingent reductions grant no risk credit before fills. Mark/limit pricing is conservative within the declared context; later gaps still require replay stress.
8. Direct shorts, inverse ETFs and intraday clocks are **independently disabled by default**. A short increase also requires explicit borrow availability, a known annual borrow rate, a verified short-stress capability and at least full collateral reservation, with a borrow-cost buffer through proposal expiry. Unknown borrow fails closed. Shorting an inverse instrument is outside v1.

V1 deliberately has no OCO/bracket arbitration, multiple actions per instrument, pair reversal, dynamic live-order mutation, leverage, multi-session unattended authority or arbitrary user-supplied functions. `hedge` is an opening proposal vocabulary item, not proof of negative correlation, hedge effectiveness or matched risk; the research gate must measure those. Expired intents do not automatically free pending cash or grant replacement authority.

## How to run the separate scientific arm

1. **Freeze the control first.** Reproduce the actual default and QQQ references under the common dates/actions/costs/volume/whole-share contract. Keep the default independent of this arm.
2. **Build a point-in-time evidence packet.** Include only data available at decision time, current realized/simulated account state under the chosen replay, active authorizations, costs, limits and explicit uncertainty. No future monthly outcome, explanatory future regime label or final study score may enter the packet. Hash it before inference.
3. **Preregister the agent policy.** Fix approved free-model catalog snapshot, prompt, response schema, model ID/version if available, context-window rules, fallback, number of calls, variant count, token/time budgets and no-spend enforcement. Do not allow router changes to select a model after seeing the held-out result. Provider outages, rate limits, unavailable free models and invalid responses become explicit hold/abstention records.
4. **Collect and seal the response.** Save request/response bytes, timestamps, catalog/pricing evidence, usage/billing outcome, provider/model metadata and deterministic validation receipt. Retry rules and call count are part of the protocol. No hosted call was made for this implementation.
5. **Validate, then replay.** Feed only admissible proposals into a separately tested event simulator. Reject unsupported short/inverse/intraday actions rather than falling back to optimistic fills. Revalidate account state, expiry and remaining authority at every replay mutation; a receipt must not be reused against a changed context.
6. **Compare three arms.** Native no-agent, agent monitoring/advisory only, and agent actions in the research simulator. Include simple deterministic action policies with the same information/cost/risk budget, so improved outcomes cannot be attributed to extra information or hidden leverage. Report abstention, action-type frequency, cost, exposure, calibration, tail losses and missed recovery as well as total return.
7. **Challenge independently.** Independent reviewers receive all results, including rejected proposals, provider failures and failed hypotheses. Promotion requires the agreed common-contract frontier, QQQ/drawdown objective, multiple-testing treatment, causality, accounting and native planning/execution parity. An agent's self-rating is not an independent review.
8. **Observe prospectively.** Keep paper execution separate from this research arm. A prospective monitoring-only/shadow phase with locked prompts/models is the strongest evidence of incremental decision value; activation requires a separate capability and deployment review.

## Historical LLM contamination is a material limitation

A modern model can know subsequent market returns, crisis dates and company outcomes from training or retrieval even if its input packet ends at a historical date. Removing future rows does **not** establish that its learned weights are point-in-time. A backtest of October 2020 decisions using a 2026 model is therefore a retrospective policy experiment, not a clean historical out-of-sample test of an uninformed decision maker.

Require no browsing/tools during decision inference, exclude retrospective narrative, log model availability/training-cutoff information when known, and add date/ticker blinding or label permutations as contamination diagnostics. Such tests cannot certify absence of memorized information. An actually frozen pre-period model, if independently obtainable and auditable, may strengthen historical claims; otherwise reserve the strongest claims for prospective data collected after the policy/model/prompt is frozen. Free-hosted model aliases can change implementation silently, so archive returned fingerprints and treat unannounced changes as a new research identity, not a seamless continuation.

Do not select the best model/prompt from the same historical test and label it independent. Model selection, prompt changes and retry variants count as trials. Explanations and confidence are not verified market forecasts.

## Intraday data and execution prerequisites

Daily closes and five-minute opening VWAP alone cannot identify intraday stop/TP ordering, hourly reallocation, spread, liquidity or a later quote's availability. A daily high/low bar that touches both stop and TP is ambiguous. Before permitting those actions in a measured arm, provide timestamped actual intraday trade/quote bars on an exchange calendar with DST/halts, raw/adjusted corporate-action consistency, price ticks/share increments, missing-data policy, latency, spread and slippage assumptions, participation/volume constraints and conservative ordering of ambiguous events. Short research additionally requires borrow inventory/rate/recall and margin constraints or explicit scenario limitations.

The evidence `available_at` is the time the whole observation became usable, **not** a bar's opening label. Next-bar or next-quote execution must occur strictly later, after declared processing/inference latency. Stop-limit prices can gap past and remain unfilled; stop-market alternatives need a separately modeled adverse fill. None may fill at a trigger solely because an OHLC extremum crossed it. Cash from proposed sells remains unavailable until actual simulated/broker confirmation. Portfolio and order state must persist across repeated decisions and restarts without reissuing already authorized quantities.

The current validator supports explicit intraday clocks only behind a trusted capability flag. It does not establish that the required dataset, simulator or operational capability exists. The flag must remain false until independent tests provide that evidence.

## Verification and remaining work

The focused suite currently has **38 passing cases**, covering identity tampering, malformed model output types, action-code smuggling, wrong-side stops/TP for longs and shorts, over-close/reversal, pending intent overlap, pending-cash exhaustion, no hypothetical sale funding, stale/future/missing data, NAV mismatch, exact/integer/cap validation, independent direct-short/inverse gates, intraday gate, risk limits, lack of risk credit from contingent stops, and restricted short proceeds/collateral. These are admissibility tests, not strategy backtests.

Independent review found a multi-action valuation defect in the initial prototype: when checking a later action, the earlier action's price reverted from its accepted limit to the lower observed mark. A two-leg $960 proposal could then appear below a $900 risk limit. The corrected validator retains a conservative price for every accepted symbol throughout subsequent risk checks. Both action orders, gross/net/symbol limit refusal cases and an admissible lower-size control now have regressions. The initial evidence remains historical evidence of the prototype; it is not silently relabeled as having used the repaired validator.

Still required: provider adapter/catalog/budget enforcement, bounded inference and sealed response persistence, point-in-time packet builder, actual event replay for these actions, OCO semantics if later desired, short-loan lifecycle, historical/prospective comparison, trial accounting, dashboard presentation and independent review. A model recommending a plausible stop or unused-cash trade is not an implementation or performance result.
