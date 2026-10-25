# Free-model shadow arm: action-gate verification and prospective evidence

Run opened October 23, 2026 at 8:16:36 p.m. America/Detroit (October 24 at 00:16:36 UTC). This local manual cycle subsumes the campaign's 00:40 UTC opportunity. It is a research capability and safety study, with no new strategy-return experiment or deployment. The campaign default remains v14.3.0; five alpha experiments have completed, ten candidate arms were rejected, and zero strategies were promoted.

## Functionality, boundaries and a verified repair

The existing `agent_portfolio_arm.py` accepts untrusted proposals only through a deterministic research contract. It binds the account, policy, market evidence, original authorized intents and decision/execution/expiry timestamps. Tests cover schema, exact decimal/whole-share quantities, available evidence and freshness, confirmed and restricted cash, pending cash, cumulative action risk, gross/net/symbol limits, instrument and execution-clock capability flags. A successful validation grants replay-only admissibility, never order authority. Broad action names do not establish a native fill implementation. Actual cluster/product exposure and execution constraints still require the native portfolio/planning layer before any broader trial.

Independent review found a real cross-symbol pending-risk omission. With $1,000 cash, a 60% gross limit and an existing four-share QQQ buy commitment at $100, a proposed four-share SPY buy could pass: pending cash was reserved, but the remaining QQQ quantity was absent from the proposed risk book. Combined exposure could reach $800, above the $600 ceiling.

The retained conservative repair blocks new risk while any original intent has unresolved remaining quantity. It does not infer an executable price/risk envelope that the intent schema lacks. Holds and eligible different-symbol reductions remain available; original authorization, cumulative fills, remaining quantities, reserved cash, expiry and account/policy identities are preserved. An expired intent does not release uncertain commitments. Even a fully filled intent does not silently release still-reserved cash. A fresh settled context can admit the same proposed purchase.

All 50 action-gate regressions passed, including 12 new pending-risk cases; an independent reviewer reproduced all 50. Only this inert module changed among the prior 313 native Python source files; the other 312 match the earlier source manifest. This is an engineering correction with regression evidence. It is not a strategy or return improvement. No paper executor, perps account, remote configuration, order adapter or default was changed.

## One preregistered hosted capability request

A separate independent source review approved exactly one fixed synthetic hold-only request. The source and tests were frozen before outcomes. The driver uses the existing request/response contracts with a scoped OpenRouter SDK adapter: no generation redirects, no SDK retry, no provider fallback, no model tools, no paid fallback, 45-second request timeout and a 60-second outer timeout. It checks the actual UTC deadline before dispatch and again after the fsynced budget reservation. The bounded argv controller supplies a durable run identity and singleton writer lock. The exact sanitized prompt/schema/response, cutoff, model request, timestamps, catalog row, allowed account-limit fields and failure metadata are retained. Original failed/intermediate review and redaction tests are preserved separately.

The single inference request ran at 00:29:38.676701–00:29:39.139817 UTC. The pinned catalog entry `nvidia/nemotron-3-super-120b-a12b:free` advertised structured outputs and zero prompt/completion prices. The authenticated account reported 9 of 50 free requests used, 41 remaining, before dispatch. This account-wide quota is separate from the campaign ledger.

The actual result was **HTTP 404, `provider_http_404`**. The downstream Nvidia provider reported that its function endpoint was not found. This was not quota exhaustion, a generated refusal or a successful inference. Opaque provider account/user identifiers stay in restricted source evidence and are omitted from dashboard/report findings.

| Observation | Verified result |
|---|---|
| Fixed request attempts this cycle | 1; no retry or substitute |
| Campaign request attempts | 2 total, including the earlier HTTP 429 failure |
| Catalog price | Prompt and completion advertised zero |
| Actual served model | Unavailable |
| Model response/action contract | Unavailable; not validated |
| Actual reported inference charge | Unavailable; not relabeled zero |
| Trading/performance evidence | None |
| Paper execution or promotion authority | None |

A zero-priced catalog is not proof of actual billing. The new adapter independently checks finite reported `usage.cost`: zero can verify a zero charge, nonzero or invalid billing rejects, and absent billing remains unavailable. The exact pinned served identity must also be reported and match before a capability pass. The controller completed its evidence collection with no surviving child processes; that process success is separate from the failed provider outcome.

OpenRouter documents a changing random policy for its free router, which is why this study pins a named model; authenticated limits must be checked separately from catalog availability. See the [official free-router documentation](https://openrouter.ai/docs/guides/routing/routers/free-router) and [account-limit documentation](https://openrouter.ai/docs/api_reference/limits). No quota evasion or paid substitution was attempted.

## Prospective evidence required before a direct agent performance claim

No prospective investment trial was started by this synthetic check. Before one can start, freeze its complete policy identity, source/prompt/schema/model, future decision schedule, observation horizon, minimum opportunity coverage and stopping rules. The current campaign ends before the next equity session; a synthetic future execution timestamp does not create a Monday trial or authorize extending this campaign.

1. **Causal observations and latency.** Retain source timestamps and actual arrival times, decision cutoff, inference start/finish and a separately available execution event. Completed bars cannot appear before their close. Market/account packets must be fresh at the decision. Inference must finish before the modeled order event.
2. **Every scheduled opportunity.** Count missing/stale data, exhausted quota, 429s, timeouts, invalid schema, blocked actions, holds, partial/no fills and model/provider changes. Do not select only successful calls or attractive market dates. A changed served model is a new policy cohort.
3. **Three continuous economic arms at most.** Reproduce the latest native default, use one frozen deterministic same-information/abstention rule, and maintain the isolated agent research account. Preserve continuous capital and all cash, original intentions, fills, remaining commitments, fees, distributions, restarts and funding where relevant. Cash and hold are valid; there is no forced-investment rule.
4. **Matched evaluation.** Use actual later observations and conservative fill/cost assumptions, with 2/6/10 bps scenarios, exposure/turnover, monthly and stress losses, recovery and missed upside, paired uncertainty and full denominator disclosure. Do not stitch winning periods or shorten the evaluation after seeing results.
5. **Separate capability proofs.** Direct shorts need observed stock-loan availability/rates/recalls, collateral/margin and signed native ledgers/plans. Inverse ETFs need genuine product history, actions and opening liquidity. Intraday stops/targets need event-path data, gap handling, fill ordering and execution costs. Default short/inverse/intraday flags remain false; synthetic flag tests cannot substitute for those proofs.
6. **Promotion gates remain binding.** A performance candidate must pass current-baseline full/forward returns at all costs, preserve 2023, original-anchor and current-default risk limits, declared QQQ drawdown limits, robustness, accounting, causality, reproduction, independent review and native profile/planning/execution validation. The automatic promotion path remains disabled and has no verified apply contract.

Present-day pretrained models may know historical outcomes. Masking dates or supplying an old prefix does not erase training information. Direct historical model actions remain exploratory. A model-generated deterministic native rule can enter normal frozen-rule research, with reused-history limitations; direct discretionary-agent claims require genuinely prospective evidence. No market return was measured by this probe, and closed-market hours cannot establish adaptive trading skill.

## Validation, publication and retained limitations

- Root final action tests: 50 passed; independent action tests: 50 passed.
- Root final probe tests: 26 passed in 3.59 seconds; independent final probe tests: 26 passed in 4.90 seconds. Mocked tests include redirect refusal, exact identity, malformed output, quota/budget limits, durable reservation and deadline handling, billing classification, output binding, and nested authorization redaction. Mocked tests are not hosted inference evidence.
- UI build passed (1,592 modules); API/launcher regressions: 165 passed, with one existing Starlette/httpx deprecation warning. Scoped Ruff and formatting checks passed. Full AMAT package results from earlier runs remain separately dated evidence; they were not rerun or relabeled here.
- The dashboard adds an explicit shadow-arm section and preserves all 42 historical comparison rows, cost selectors, detailed curves and earlier rejections. No synthetic agent return row is invented. Unknown model identity, billing and prospective performance are visible.
- Evidence is under `outputs/research-campaigns/2026-10-23-adaptive-12h/research-review/20261024-0040-free-arm/`, `validations/free-shadow-0040/`, and `cycles/manual-free-shadow-0040/`. The sealed protocol is `acb7ee1af2f5771ea71cef6806a10699320a4378aa3f3dddb6a7edf76d9bc308`; measurement manifest is `e46141889da6b0b1dd99b99d23370e5b0ad2723f166ce81ba0fe164bd40edff9`.

The useful retained improvement is the pending-intent gate hardening and source-bound capability reporting. Hosted model availability, actual inference cost, direct-agent prospective performance and native trading integration remain unverified. v14.3.0 remains the verified default, and both paper bots continue under their existing independent authority.
