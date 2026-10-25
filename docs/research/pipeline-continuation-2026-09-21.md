# Implementation continuation — September 21, 2026

This batch advances Phase B and Phase D foundations. It does not complete B–G.
Previous evidence and frozen research programs remain preserved.

Delivered: automatic cash/share reconciliation from a fixed observed baseline;
paginated broker activity; explicit missing/unsupported evidence states; automatic
status freshness; shared Groq → OpenRouter free → Gemini utility defaults;
Luna-only coding/complex routing; committee adapter with separately reserved
fallbacks; actual model provenance; compact research run/status/report interface.
`AMAT_MODEL_CHAIN` overrides the shared policy for new work. Existing frozen
chains and explicit overrides remain authoritative.

The v13 container recorded its baseline automatically and the next cycle reconciled
cash/share changes. This does not certify pre-baseline history, strategy ownership,
market valuations, tax or settlement accounting. The SPY buy remains pending_cancel;
held sell orders are unchanged. No trade or additional cancellation was submitted.
Existing disabled/dry-run configuration remains unchanged.

Groq is configured first but the active research environment has no Groq key.
A synthetic OpenRouter-free probe reached a model but failed local validation;
a second JSON-object probe succeeded with liquid/lfm-2.5-2.6b:free. Luna returned
rate_limit. No trading data was sent. Availability is not quality certification.
Probe metadata: outputs/routing-probe-2026-09-21.

Next, in order:
1. Finish B: corporate-action share adapters, verified baseline compaction,
   plan ownership, lifecycle registry, shadow monitoring and observation window.
2. Finish C: causal/calendar validation, matched period comparisons, registered
   statistical protocol and separate training-through-2025 program.
3. Finish D: unified costs, evidence admission, RAM-only web paths and bounded
   LangGraph research with fixture evaluations.
4. E/F/G remain behind their evidence gates; no new signal, news LLM sentiment,
   market scanner or crypto execution was enabled by this change.

Cash/share tolerances are $0.01 and 0.000001 shares. Activity pagination is bounded
at 100 pages of 100 and fails explicitly at the bound. A discrepancy never resets
the baseline. Two reads reject visible races; eventual broker posting can still
cause temporary discrepancies. Stock corporate actions are explicitly unsupported
until their adapters are validated. Compaction must preserve verified accounting
before the fixed-baseline history reaches its pagination limit.

References checked: [Alpaca activities](https://docs.alpaca.markets/us/docs/account-activities),
[pagination](https://docs.alpaca.markets/us/reference/getaccountactivities-2),
[Groq models](https://console.groq.com/docs/models),
[OpenRouter free](https://openrouter.ai/openrouter/free/apps),
[OpenAI Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

Validation: the full AMAT run recorded 2,008 passes, one existing skip and three
failures. One direct boundary regression (asyncio imported by report commentary)
was fixed by moving the synchronous wrapper into llmrouter. A long integration
also correctly rejected controller edits made during its run. Stable-source
reruns passed: 263 boundary/policy/advisory checks and both affected end-to-end
research integrations (163.73 seconds). Formatting, Ruff and mypy pass. All twelve
frozen candidate/objective/daily artifacts from the full-run fixture match their
original hashes. Final reruns then passed against stable source. Executor checks:
217 tests passed, plus formatting, Ruff and mypy.
Detailed results: outputs/routing-probe-2026-09-21/validation.json. Deployment and
baseline proof: phase-bc-evidence-2026-09-20/pipeline-*-2026-09-21.json.

## Automatic deployment observations and incidents

Implemented the next Phase B increment in executor.operations. Every daemon tick
now updates deployment-registry.sqlite3 and its rebuildable operations.json status
projection. The ordinary CLI remains executor / executor status. This imports only
the configured digest authorization; research validation is explicitly not_imported.
States are unapproved, paper_approved, active (a matched fill has been observed),
and watch (unresolved operational incidents). Submission mode is separate.

Plan payloads and exact intent identities persist beyond archive-file retention.
Changed plan evidence or conflicting identities raise flags. Symbol equality alone
cannot attribute a trade. Missing/stale observations do not resolve incidents.
Portfolio discrepancies require three distinct observations over at least eight
minutes; cached observations cannot advance the streak. Flags are observational,
have no execution authority, and are not yet consumed by an automated research loop.
The projection limits flags to 100 and reports the omitted count; SQLite retains all.
Statistical decay monitoring remains unavailable without a frozen performance baseline.

Validation: 235 executor tests passed; Ruff, formatting and mypy passed. The rebuilt
container is running healthy, with installed operations source matching the workspace.
Current authorized v13 digest is unchanged. All monitoring projections are fresh;
portfolio cash/share changes reconcile to the recorded baseline. Six open flags
cover three unmatched, aged outstanding SPY orders. The buy remains pending_cancel;
two sells remain held. These flags indicate account operational issues, not v13 decay.
No v13 fill was attributed. EXECUTOR_DISABLED=true and DRY_RUN=true are preserved.
Evidence: phase-bc-evidence-2026-09-20/operations-deployment-2026-09-21.json.

Remaining B work: validated share corporate-action adapters, verified baseline
compaction, full research candidate/risk/sizing lifecycle and an observation window.
Then C completes causal/calendar checks and the registered statistical protocol;
D connects evidence admission and bounded research orchestration. This increment
completes observed deployment ownership and shadow operational incidents, not all
of B or the full research-to-execution lifecycle.

## Verified checkpoints and configurable return targets

Phase B: automatic checkpoints now advance the cash/share baseline only after
an exact reconciliation, at 30 days with new activities or 1,000 new activities.
Content-hashed proofs retain the previous baseline, full activities, result and
next baseline. The complete ancestor chain is verified before subsequent reads.
A discrepancy, including a tolerated nonzero residual, cannot be checkpointed.
Proof persistence precedes baseline replacement. Original query coverage remains
unchanged so later corrections/deletions are detected. This reduces effect replay;
it does NOT remove the broker pagination bound or compact historical evidence.
Share corporate-action adapters still await verified payload semantics/fixtures.

Phase C: the separate fixed-recipe study supports one command:

    ./research returns run --target-cagr 0.20 --output /home/supasuge/Documents/amat-workspace/amat/outputs/return-target-20pct-01

The target is sealed in a new protocol before development screening and applied
to development/later-period CAGR and rolling-year metrics. At most one selected
candidate advances to later historical validation. No eligible candidate produces
an explicit decision without opening validation. This is a bounded fixed-recipe
study based on the existing v6 baseline and 2018–2026-09-11 data contract; it is
not an LLM loop until success, not pristine out-of-sample certification, and not
a change to the normal exposure-matched score. Existing frozen studies retain
their saved source; a new study requires a new output directory. No expensive
research study or strategy promotion was started by this implementation.

Validation: 247 executor tests; 37 focused research/routing/audit tests; formatting,
Ruff and executor/script type checks. The checkpoint code is deployed in the healthy
v13 container with source parity and fresh observations. No checkpoint was forced
on the young live baseline. Portfolio remains reconciled; six existing incidents
remain open and submission disabled. Proof: phase-bc-evidence-2026-09-20/checkpoint-target-deployment-2026-09-21.json.

Next unfinished work: safe partitioned activity retrieval before history reaches
its page bound; verified share corporate-action fixtures; complete research approval
lifecycle; causal/calendar and statistical protocol checks; evidence admission and
bounded LangGraph integration. Full B/C/D and later E/F/G remain unfinished.

## V13 research baseline and B/C/D implementation continuation

New normal daily sessions now start from configs/strategy/amat_v13_return_target.toml.
The legacy E73 promotion remains immutable and readable; only its known omitted
signal.selection=None field is normalized after verifying the original canonical
candidate digest. It cannot silently replace the user-selected v13 allocator.
V13 uses a separate validated core research envelope; the v6 loader is unchanged.
Parameter/attenuation proposals preserve v13's allocator and execution constraints.
The frozen baseline reference includes the source study's protocol, selected recipe,
development, later historical validation and decision. The original study's 13.40%
normal-cost later CAGR and 14.05% lag-two scenario are distinct measurements.

Daily completed candidates are additionally retained in best-strategies.sqlite3
outside the checkout, grouped by data/window/objective/reference contract. Raw
return leaders and eligible-score leaders are distinct. Prior better candidates
cannot be replaced by a worse result. The per-session ledger is authoritative;
secondary archive failures are recorded and retried on resume. Imported 207 prior
completed daily results without errors, plus the selected v13 return reference.
Reference archival does not promote a strategy or reinterpret a failed gate.

B: checkpoint baselines now compact their activity maps to counts and digests,
with full activity evidence retained in the verified checkpoint chain. Bounded
activity retrieval splits exhausted windows with overlap and conflict rejection;
late changes remain detectable because the original audit coverage is retained.
Documented stock-split legs and stock-dividend share effects have strict adapters.
These are the activity-stream schema, NOT undocumented legacy REST SSP quantities;
the paper REST connection does not supply the new stream. Legacy ambiguous share
events remain unsupported rather than guessed. Operational observation records
require distinct open-market observations with bounded gaps: 20 qualifying completed
sessions, at least 30 minutes observed per session, no operational incident. This
is operational reliability evidence, not a performance-validation track record.

C: each new daily workspace freezes its session calendar and finite statistical
protocol. A separate --program training-2025 selects December 31, 2025 as training
end without altering old programs. Matched exposure-control results now emit paired
calendar-quarter/four-block comparisons. Dates must match exactly; missing dates
cannot be removed by an inner join. The registered bootstrap diagnostic allocates
alpha across a finite trial family, includes unsuccessful trials in its count,
and reports resolution limitations. It is NOT an anytime-valid acceptance theorem
or an automatic deployment gate. Independent exchange-calendar certification and
final inferential/risk approval integration remain open.

D: normal daily run_loop now uses a bounded outer LangGraph prepare/research/verify
workflow over the existing trial/token/subprocess bounds and durable ledgers. Graph
state carries phase metadata only; implicit LangSmith tracing is disabled. Shared
metadata-only cost records cover routed research/utility/committee attempts and
provide search/market-data categories. Unknown prices and usage remain unknown.
A bounded HTTPS allowlist retriever keeps raw pages in memory and supports explicit
excerpt admission. This is a reusable primitive, not a completed autonomous web
research agent or semantic claim verifier; raw pages do not enter graph state.

Evidence gates remain closed: E/F/G have no new signal, news LLM sentiment, market
scanner or crypto execution enabled. Full B/C/D should NOT be marked complete:
remaining live stream coverage, observation time, independent calendar verification,
final statistical/risk lifecycle integration, and full web-research admission/tool
integration are tracked separately from the implemented foundations.

Final v13 baseline selection verification:
- Explicit active research pointer now selects growth_rotation4_20_weekly,
  candidate 8d09315e5f6dad10774014c96e7411f65ac450d90831d8ec94c869bac7c325cf.
- The original E73 promotion bytes remain unchanged. Reference selection verifies
  configuration and study artifact hashes before writing the pointer.
- Normal new daily sessions use the verified September 14 reference snapshot.
  Existing frozen sessions retain their original baseline and contract.
- Compatible fallback generation validated 100 distinct candidates while preserving
  the v13 allocator. Selection itself grants no execution authority.
- Final focused regression suite: 86 passed in 65.35 seconds; mypy passed 59 source
  files; Ruff checks and changed-file formatting passed. Earlier broad validation:
  2038 AMAT tests passed, one skipped, and 258 executor tests passed.
- Docker executor remains running and healthy, with trade submission disabled.

## September 27 continuation: engineering handoff and independent calendar

New daily sessions freeze an independent XNYS schedule audit, including UTC opens,
closes and package version. The v13 snapshot matched 2,665 market sessions through
2026-08-10. This completes an independent session-date comparison, not symbol-level
coverage, causal data-availability certification or final statistical approval.

An unsuccessful batch can automatically request a Luna engineering proposal with
a frozen per-session attempt limit. Replacements are confined to four feature and
routing modules, checked against their original hashes and Python syntax, and saved
outside the frozen runtime. Source, diff and provenance remain reviewable. Failed
and interrupted attempts stay consumed. No generated code executes or promotes.
New status/report output exposes the pending isolated-validation state.

Validation: 84 research/engineering tests and 278 boundary/CLI/routing tests passed;
mypy passed 52 source files; Ruff and formatting passed. A fresh v13 initialization
verified both frozen policies without model calls or trades. The Luna dispatch path
was fixture-tested; provider availability was not live-probed. See
research-autonomy-2026-09-27.md for the next five implementation steps and bot roles.
Full end-to-end code evaluation, adoption and deployment remain incomplete.
