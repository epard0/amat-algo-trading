# Research native plan and broker execution contract — October 24, 2026

The user requested the remaining native plan and broker execution-parity checks, preserving exact quantities, conditions, expiry and pending commitments while leaving paper execution unchanged. This is post-campaign local engineering validation. The finite campaign stays closed, and no new strategy experiment, model request, default promotion or remote rollout is authorized by this work.

The **research native plan round trip is verified on fixed synthetic cases**. The **existing broker execution path is incompatible with this policy and safely refuses it**. Completing the checks does not make that incompatibility a pass. No real broker fills, market costs or prospective performance were measured.

## Lossless research native plan

The new inert `autoresearch.improvements.agent_native_plan` module exports an `AdapterPlan` into a capability-bound envelope containing native `ExecutionPlan` / `TradeIntent` fields plus the complete original adapter plan, conditions and context. Intents are constructed directly from exact Decimal quantities, never recomputed through portfolio weights or a rebalance threshold. Descriptive native weights have no sizing authority. Decision marks are explicitly separate from executable limits and modeled fill prices.

The envelope preserves the original account and policy identity, evidence cutoff, execution time, expiry, action kind, trigger and limit, current quantities, confirmed/restricted cash and complete original authorized-intent records. Original intent quantity, cumulative fills, remaining intent, pending cash and expiry are retained rather than replaced by new adapter actions. New action fills and residual quantities remain separate. An expired intent does not release uncertain commitments.

Restoration requires the caller's trusted typed `ResearchContext`. The file cannot establish its own account authority. The complete envelope is regenerated and compared, including canonical JSON identity; altered quantities, triggers, limits, expiry, pending cash, account, capability sets, execution authority, unknown fields and boolean/integer substitutions are refused even if an attacker recomputes the outer digest. The source attestation guard remains unchanged.

Both the outer envelope and its native projection carry explicit required capabilities and `engine=research-policy/1`. The unchanged legacy executor parser refuses each. This prevents accidental extraction of plain native quantities from silently dropping their conditions. This is a research native plan format, not an activated legacy strategy profile or registered daemon capability. Deliberately editing away safety fields cannot grant authority.

## End-to-end native validation

The frozen validation manifest pins all 316 native Python sources, executor runtime sources, helper, driver, fixture source and new tests. Before and after validation the existing attestor checks loaded research libraries against the pinned source root and exact bytes. The driver uses a writer lock, exclusive/fsynced per-case plan and attempt records, immutable receipts and a bounded 30-second executor witness subprocess with an allowlisted environment. A repeated output directory or prior receipt cannot silently rerun an attempt. These are local validation ownership controls, not a global production order journal.

Eight previously specified synthetic scenarios are replayed at 2, 6 and 10 bps: exact one-share resize, untriggered stop, triggered stop, gap beyond limit, native 1% opening-volume capped buy, hold with an original partially filled intent, expired execution window and take profit. Plan files are written, sealed, read back, restored against trusted state and executed through the native simulator/accountant. Expected fills, misses, ledger and attribution are independently constructed from fixed case eligibility and native instructions, rather than selected from the adapter's own eligibility result.

All **24 case/cost combinations pass** exact plan round trip, native fills, misses, ledger and accounting, original pending commitments, expiry, quantity conservation and prior-receipt refusal. The capped 20-share request fills five whole shares and retains fifteen; the prior 7-share intent retains three cumulative fills, four remaining and $400 pending cash. Missing/expired execution cannot fabricate a new valued ledger. Synthetic liquidity, quotes, ranges, fees and future fixture timestamps are not market observations or a prospective trading trial.

A separately frozen six-case quantity supplement extends this to **30 synthetic checks**. At each cost a 2.8-share partial trim produces the native two-share fill and retains 0.8, while a complete 10.25-share exit preserves the fractional full-close entitlement. In all six cases, an expired but unresolved original intent in another asset retains its original seven-share authorization, three cumulative fills, four remaining, $400 pending cash and unchanged original expiry. Its uncertain commitments are not released to finance a new purchase. The supplement saves actual independent native control fills, ledgers and accounting beside each restored plan/receipt. This supplements the earlier sealed results; it does not alter their preregistered cases or count as alpha research.

## Broker checks and actionable incompatibilities

The real executor parser is invoked in a separate local process. All **48 refusal checks** pass: 24 outer envelopes and 24 extracted native projections are refused by the existing capability guard.

The actual paper client's limit-order payload builder is also exercised against a replaced transport, blocked socket construction and blocked native read method, with synthetic credentials and acknowledgments only. All **21 non-hold wire witnesses** preserve numeric Decimal quantity and limit. This is serialization support, not evidence that `submit_plan` supports the research policy or that a broker accepted or filled any order. The helper is not imported or registered by the paper daemon.

| Requirement | Verified result | Broker execution consequence |
|---|---|---|
| Exact quantities and original limits | Research plan round trip and offline Decimal wire serialization pass | Necessary, insufficient for full policy parity |
| Opening-only stop and take-profit conditions | Preserved in the research envelope and native simulated decision gates | Existing wire has no opening-only trigger contract; ordinary submit path would send unconditioned sells |
| Complete-window conservative limit eligibility | Preserved in research replay | A resting limit order may fill during only part of the range; full-window range/VWAP is known retrospectively |
| Original expiry and single opening attempt | Original instant is preserved; invalid windows are blocked | Existing wire uses DAY lifetime, without original cutoff; pre-submit expiry checks do not prove resting orders cease filling |
| Native whole-share 1% opening-volume cap | Five of twenty shares filled in the fixed 550-volume fixture | Real complete-window capacity is unavailable before the window ends; the wire has no equivalent volume rule |
| Existing pending commitments | Original intent/cumulative/remaining/cash identities exact | Legacy batch initialization does not consume this research context; its separate receipts cannot be replaced by this envelope |
| Broker order ownership and unknown outcomes | Existing executor regressions run separately | No global production journal integration or live/paper reconciliation is established for the research adapter |

These differences cannot be solved by renaming a reference mark as a fill, changing DAY orders to unconditional sells, assuming future opening liquidity, removing capabilities or releasing expired uncertain commitments. Such substitutions would weaken the user's contract. A future broker-capable implementation needs a separately reviewed causal order-lifecycle policy, actual session/halts/quote data, durable original-intent ownership and explicit treatment of limit queue, cancellation and late fills. It must be compared under a compatible measurement contract; universal equality between broker fills and historical opening VWAP is not asserted.

The existing staged executor already has important safeguards: its submission cutoff is the earlier of plan expiry and opening-window end, it requests cancellation after cutoff, keeps unconfirmed orders and buy commitments pending, and reconciles later fills. The successful 396-test executor suite includes expiry/cancel confirmation, late-sale-fill refusal, missing-order/no-retry, immutable quantity/account/policy identity and fractional full-exit regressions. Those safeguards remain intact. A cancellation request is not a guaranteed cessation of fills at the cutoff, and the legacy owner has no implemented handshake for the new research conditions/context. Its tested lifecycle is not relabeled as research-policy execution parity.

Corporate-action unit transformations, inverse instruments, direct shorts, intraday/OCO paths, production ownership and genuinely prospective agent performance remain unsupported. No successful hosted inference or profitable agent trial is inferred from these mechanical checks.

## Regression and source continuity

The 758-file baseline includes existing untracked work. All 315 pre-existing native library files, the executor runtime, frontend source, v14.3.0 profile and attestation guard are byte-identical. One research library module, one test module and two offline validation scripts are new. There is no production configuration, broker path or strategy-engine edit.

The full AMAT package regression completed with **3,822 passed, 2 skipped and zero failures/errors** in 1042.38 JUnit seconds. The final counts and exact log/JUnit hashes are recorded in the sealed completion receipt. The initial affected check passed 99 tests; the broader final affected suite passed 155 tests. The full executor suite passed 396 tests in 7.52 seconds, the API/launcher suite passed 165 in 42.40 seconds, and the UI build passed with 1,592 modules in 5.90 seconds. Scoped Ruff formatting/lint and mypy pass. Initial lint formatting and mypy invocation/monkeypatch typing failures were corrected before the frozen validation; no failed scientific outcome was relabeled as a pass.

## Dashboard and operational boundary

The dashboard distinguishes verified synthetic native plan parity from unsupported broker execution parity. All 42 scientific comparison rows and cost histories remain unchanged. The latest default remains v14.3.0, with five completed strategy experiments, ten rejected arms and zero promotions. The closed campaign is not resumed or rescheduled. Real paper performance and costs keep their separate observation source; unknown fill/cost evidence stays unavailable.

No SSH operation, remote deployment, executor restart/stop, order submission/cancellation/retry, paper configuration change, credential access or account-state replacement is part of this validation. Both paper bots and their independent monitor keep their existing lifecycle. Local regression success is not a new assertion about their current remote health.

Evidence is under `amat/outputs/repairs/2026-10-24-native-plan-broker-parity/`: pre-edit hashes/archive, frozen validation manifest, 24 exact sealed plans/attempts/receipts, independent native comparisons, broker wire/refusal witnesses, source continuity, regression outputs and final dashboard proof.
