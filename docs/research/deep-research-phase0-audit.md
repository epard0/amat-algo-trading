# Deep research Phase 0 / Phase A audit

Status: Phase A audit complete, with operational failures and limitations recorded below. Audit date: September 20, 2026, America/Detroit (some evidence timestamps cross into September 21 UTC). Scope: baseline capture, source audit, historical-use inventory, regression checks, and read-only paper-executor deployment review. This is not a strategy promotion or a completed operational repair.

Confirmed design: reliable paper trading and defensible research first; LangGraph for bounded research orchestration; existing `llmrouter` as the intended common remote-model boundary. Existing experiment governance remains authoritative.

## Outcome and next-step constraints

Phase A identifies concrete integration points and preserves a baseline of the dirty working tree. Operational readiness is **not established**: the running executor has a container DNS failure and an older installed package that lacks controls already present in the checkout. Do not infer deployment safety from the passing checkout tests.

Before an external research V1 becomes the default, address the separate committee model transports, body persistence, per-workspace rather than global budgets, per-attempt accounting, and research/evaluation visibility. The current implementation has valuable controls; several guarantees in the upgrade specification are desired future properties rather than existing global invariants.

No orders, broker mutations, research proposals, model calls, new holdout evaluations, deployment changes, or source-code fixes were performed by this audit. Tests used their existing fixtures and mocks. Snapshot hashing and historical metadata inspection were read-only. Existing stored validation metrics were not opened for strategy selection.

## 1. Reproducible starting point

- Git HEAD: `7fd145a411e8f8a925b28cd4343dbf4f9a98a950`, branch `main`.
- The checkout has numerous pre-existing tracked modifications and untracked packages, tests, scripts, and configurations. HEAD alone cannot reproduce the audited code.
- [Baseline manifest](../../amat/docs/phase0-evidence-2026-09-20/baseline-manifest.json) records SHA-256 and size for 415 source, configuration, test, documentation and lock files, including intended untracked research work and CSV fixtures.
- [Source archive](../../amat/docs/phase0-evidence-2026-09-20/baseline-source.tar.gz) preserves those exact files. Credentials, market-data bodies, account state and caches are excluded. The manifest defines the archive scope; this is not a whole-machine backup.
- [Starting Git status](../../amat/docs/phase0-evidence-2026-09-20/git-status-before.txt) distinguishes the pre-existing dirty state from audit artifacts.
- [Final baseline integrity check](../../amat/docs/phase0-evidence-2026-09-20/baseline-integrity-after.json) confirms no audited source/config/test/lockfile changed. Only the pre-existing consolidated plan changed to reflect confirmed decisions and Phase A status; the audit/evidence files are additions.
- [Runtime and fixture identities](../../amat/docs/phase0-evidence-2026-09-20/runtime-and-fixture-digests.json) records Python 3.12.12, dependency/tool versions, baseline candidate identity, and hand-built fixture hashes.
- [Snapshot inventory](../../amat/docs/phase0-evidence-2026-09-20/snapshot-inventory.json) records six market manifests and verifies every declared partition against its stored digest. All 18 declared partition checks matched. This proves file integrity against the manifests, not economic accuracy or complete coverage.

The repository currently ignores documentation paths, including this audit and the prior consolidated plan. They exist locally but will need deliberate inclusion in a future review/commit; no force-add or unrelated Git operation was performed here.

### Baseline quality gates

Commands run from each project directory: `uv run --locked ruff format --check .`, `uv run --locked ruff check .`, `uv run --locked mypy src`, and `uv run --locked pytest -q`. Existing default test configuration excludes live LLM tests. No paid-provider tests were enabled.

| Project | Format | Lint | Types | Tests |
|---|---|---|---|---|
| AMAT | Pre-existing failure: one file | Pass | Pass, 162 source files | 1,995 passed, 1 skipped; 422.99 seconds |
| Executor | Pass | Pass | Pass | 177 passed |

The formatting failure is `amat/scripts/analyze_session_patterns.py:69`: Ruff would join two adjacent warning-string literals. It was recorded without changing the source during the baseline audit. AMAT's one skip is the existing undeclared-layer `__init__.py` case in `test_dependency_direction.py:117`; it is not a provider test. Combined: 2,172 passed, one skipped. Logs and exit codes are stored in [AMAT checks](../../amat/docs/phase0-evidence-2026-09-20/amat-checks.json) and [executor checks](../../amat/docs/phase0-evidence-2026-09-20/executor-checks.json), with adjacent per-check logs.

## 2. Model-call inventory and actual transport ownership

The relevant source call sites are:

| Caller | Purpose | Current route |
|---|---|---|
| `autoresearch/proposer.py:model_proposal` | Daily strategy proposals | `Router.complete` → `llmrouter` transport |
| `autoresearch/summaries.py:generate_summary` | Structured experiment commentary | Same router/ledger, optionally a separate chain |
| `autoresearch/intraday/research.py:propose` | Intraday recipe proposals | Same router/ledger |
| `autoresearch/intraday/research.py:review` | Intraday experiment commentary | Same router/ledger |
| `portfolio_team/remote_coordinator.py:remote_phase` | Specialist and chief assessments | `RemoteProvider.invoke` → committee-owned transport |
| `portfolio_team/model_runtime.py:GeminiTransport.generate` | Committee Gemini inference | Direct Google SDK `interactions.create` |
| `portfolio_team/openai_runtime.py:OpenAITransport.generate` | Committee OpenAI inference | Direct OpenAI SDK `responses.create` |

`llmrouter/transports.py` contains the shared OpenAI Responses, OpenAI-compatible Chat Completions, and Gemini Interactions implementations. SDK automatic retries are disabled. `Router.complete` provides credential checks, provider cooldowns, timeouts, failover, local validation and at most one explicitly requested repair across the chain.

**Finding R1:** `llmrouter` is not yet the sole remote-model boundary across the repository. The committee uses a distinct transport, request type, protocol hash, per-review budget and response validator. Preserve those semantics through an adapter; do not simply replace its calls and discard durable accounting.

The inspected `newsdesk` clients consume vendor news/sentiment APIs; they do not currently expose a general LLM sentiment worker. OpenRouter sentiment integration therefore needs a new evaluated adapter/workflow, not just another vendor URL.

The default autoresearch chain already selects `gemini-3.8-flash`, then `openrouter/free`, then NVIDIA. Groq is absent from `ProviderName`, credential mapping and fixed base-URL mapping. Domain calls still set effort/token/timeout values directly. Capability policy/catalog/pricing layers remain new work.

**Finding R2:** OpenAI-compatible structured-output fallback can make two HTTP requests inside one `Transport.generate`: strict schema first, then JSON-object mode after a supported schema-rejection error. The router records one reservation/call. This is bounded and marked `schema_downgraded`, but it is not one auditable reservation per wire attempt. Track each attempt and any unknown usage before claiming full per-action accounting.

**Finding R3:** `_finalize_chat` sets `ChatResponse.model` to the requested model string. The raw provider response may carry the actual routed model, but the normalized model field for `openrouter/free` can remain the alias. Add requested-model and served-model identities explicitly for sentiment comparability and pricing.

## 3. Token reservation, settlement and budget boundaries

`autoresearch/ledger.py:reserve` computes:

```text
reservation = byte_length(request.canonical()) + max_output_tokens + 2048
```

It sums `calls.charged` for the current UTC day in this workspace's SQLite database, refuses a request beyond `Program.max_model_tokens_per_day`, persists the full request and charge, then records a start event. This is a conservative byte-based heuristic, not an exact token count.

`settle` stores the full response. If both actual input and output usage are known, charge becomes their sum; otherwise the reservation remains. An interrupted call therefore retains its reserved charge. Every router failover/explicit repair enters this accounting. A failed credential/health preflight creates no reservation. Elapsed timing uses an in-memory monotonic start, so a restarted process cannot reconstruct that timer.

The ledger uses a nonblocking OS writer lock and a normal SQLite connection. `ProviderHealth` explicitly is not thread-safe. The daily loop is sequential; these components cannot be shared across arbitrary worker threads without an owner/serialization design.

The portfolio committee instead reserves input and output separately through `Store.begin_model_attempt`, checks per-review and per-day limits, and records the exact request as a hashed artifact. `finish_model_call` stores the response artifact and replaces each known usage dimension; missing dimensions keep their reservations. `remote_phase` bounds concurrency, attempts and deadline, awaits all in-flight results, and validates assessments after response capture.

**Finding R4:** Neither subsystem supplies account-wide model/search cost accounting. Separate workspaces/registries can each spend their own daily allowance. There is no price catalog, optional cross-run USD cap, Tavily charge accounting, or shared worker/run reservation hierarchy. Add accounting and optional limit support without weakening conservative unknown-usage charging. The user selected no monetary ceiling for now, so missing hard dollar enforcement is not itself a violation of the chosen policy.

## 4. Ledger schemas, keys and migration ownership

The daily/intraday `research.sqlite3` ledger has five tables:

| Table | Keys and columns |
|---|---|
| `state` | `key` primary key, `value` JSON |
| `experiments` | integer `id` primary key; `candidate`, `parent`, `proposal`, `status`, nullable `result`, `error` |
| `cache` | `candidate` primary key; `result` JSON |
| `calls` | integer `id` primary key; UTC `day`, `provider`, `model`, exact `request`, nullable `response`, integer `charged` |
| `events` | integer `id` primary key; `recorded_at`, `kind`, JSON `payload` |

State keys include `incumbent`, `status`, `stall`, `batch`, `progress`, `provider_cooldowns:<chain_digest>`, `summary:<experiment_id>`, `intraday_cooldowns`, and matched-follow-up keys built by `followup.py`. Candidate digests key training cache entries; `validation:<candidate_digest>` keys validation cache entries. Note the distinction: the current checkpoint path stores validation in **cache**, while memory also checks for legacy validation keys in **state** and, crucially, checkpoint files/directories.

Opening `Ledger` is not read-only: it creates tables and marks interrupted experiments as crashed, repairs retained incumbent metadata, and finishes interrupted progress. The audit used direct `sqlite3` connections with `mode=ro`, not `Ledger`, to inspect existing sessions.

The committee's `registry.sqlite3` separately contains `artifacts`, `bundles`, `bindings`, `reviews`, `attempts`, `events`, and `model_calls`. Artifacts and bundles use content hashes; attempts/model calls use `(review_id, role, attempt)`; reviews retain their own identity and status. Keep this history compatible rather than merging stores by renaming tables.

Daily experiment history primarily lives in the ledger. Intraday also stores its candidate/result state in `report.json` plus its digest and proposal/run files; the shared ledger chiefly supplies model-budget/events support there. Internal research must ingest both formats explicitly.

## 5. Existing experiment lifecycle and proposal integration

`workspace.initialize` verifies snapshot partitions, loads the v6 baseline, verifies available train/validation endpoints, copies source/data/lockfile outside the executor-mounted checkout, freezes compatible prior memory, and writes the program and its file digest. `load_program` checks the program file, every frozen input, the source/data file set, installed controller files and dependency versions.

`loop.run_loop` then:

1. Acquires the ledger writer lock, loads/creates the baseline and resumes the incumbent.
2. Generates bounded built-in or model proposals; respects trial, stall, replacement and batch limits.
3. Registers a proposal **before** applying it or running a backtest.
4. Applies allowed changes and resolves the selected parent; rejects automatic duplicate recipes.
5. Measures in a subprocess after rechecking frozen inputs and candidate limits; caches only hashed artifacts.
6. Compares with both experiment parent and retained incumbent; applies eligibility, score margin and uncertainty checks.
7. Records keep/discard/crash, updates the incumbent/stall state, generates commentary and exports reports.

The new research-to-proposal adapter belongs immediately before the existing model proposal construction/validation, with a separate explicit investigate command for standalone research. Preserve `register`, `apply_proposal`, `validate_candidate`, `measure`, `execute`, selection and checkpoint ownership. Outcome feedback belongs after durable `finish`, with idempotency based on program/experiment/candidate identity.

`research.research_parents` currently offers **only the retained incumbent**, not an arbitrary multi-parent search graph. `inspirations.py` provides built-in regime-router and pattern hypotheses; `search.py`, `exploration.py` and `proposer.search_proposal` supply bounded seeds/refinements/ablations. There is no general web/academic researcher today.

**Finding R5:** The v13 return-target TOML is a plain strategy configuration, not a `V6Config` accepted by the daily autoresearch baseline loader. A read-only load probe failed on missing `asset_regime`; the standard strategy loader succeeds. The v13 study uses its dedicated return-target script. Do not promise that deep research can directly mutate v13 through the current daily `Candidate` contract without a separately registered adapter/design.

## 6. Memory generation, consumption and allowed tools

`memory.freeze_memory` scans sibling workspaces at initialization and produces `runtime/research-memory.json` with schema `research-memory/1`, research/market cutoffs, eligible/truncated counts, excluded-session reasons, and at most 500 records. Compatibility includes evaluation parameters, baseline, dates, evaluator source hashes, dependency versions and snapshot hashes. Training ending after the target cutoff is ineligible.

It rejects checkpoint-exposed workspaces, future/unknown research timestamps, missing artifacts, hash mismatches, candidate mismatches, and disagreement between ledger and objective metrics. Records contain experiment identity, mechanism/falsification, recipe, status, development score/gates, cutoff and provenance. These are development observations, not a clean future test.

`related_experiments` verifies the frozen memory file and uses deterministic feature/word overlap, returning at most four matches. `known_candidates` supports exact duplicate prevention. `model_proposal` combines these with the current training-only history and failure library, validates the full recipe locally and permits one bounded repair. The current memory is neither an embedding database nor an adaptive semantic/procedural knowledge graph.

`tools.inspect_tool` exposes `related` and `charts` through the CLI with digest checks and shared locking. Chart generation writes verified derived artifacts; it is not a completely read-only tool. There is no LLM tool-calling/ReAct dispatcher or general shell tool. Committee research requests become declarative tasks in `portfolio_team.research_queue`; task approvals bind review/queue/task hashes, and `followup.py` executes only registered bounded operations. Reuse this boundary instead of interpreting model text as commands.

## 7. Candidate mutation, worker and execution boundaries

- `contracts.py` uses frozen extra-forbidding records and bounded proposals. Editable paths are trend deadband/clip/horizons, volatility half-lives/slow weight, and rebalance weight delta; recipe features/signals have their own history and compatibility limits.
- `validate_candidate` reconstructs permitted changes against the baseline and rejects other configuration differences. Costs, universe, execution timing, sizing caps and warmup are not ordinary proposal-editable fields.
- `loop.execute` and `worker.run` revalidate at the subprocess boundary. The worker receives a stripped environment with no host credentials and fixed backtest mode. Timeout and source/data digests are checked.
- `Program.execution_authority` is literally `none`.
- `promotion.py` is explicit operator selection of a **future research baseline**, including discarded measured recipes with the override disclosed. It does not authorize trading or rewrite the original experiment verdict.
- The executor consumes the versioned plan artifact via subprocess and has a fixed paper broker endpoint. Research has no order-submission tool.

Limits: the worker environment is not a filesystem/network sandbox. Frozen snapshot directories may contain later dates even when the evaluator loads only training dates. Hash gates and trusted code enforce the current protocol; they do not constitute an adversarially inaccessible lockbox. Future research agents must receive allowlisted views and no unrestricted workspace-reading capability.

## 8. Validation/holdout separation and historical-use register

`Program.ordered` requires ordered load/measured/train/validation windows and validation not beyond `holdout_after`. Daily workers load to `train_end`; checkpoint explicitly runs to `validation_end`, preserves the first validation return, and removes training activity from the evaluated slice. Proposer context excludes validation files/dates/outcomes. Cross-session memory rejects checkpoint exposure.

`amat.validation.walkforward` owns the separate locked-holdout declaration and explicit-access-reason check. `amat.provenance.RunManifest` records `holdout_accessed` and requires a reason when true. These are workflow-specific checks, not a single global registry proving that no other script ever inspected a date.

Observed metadata:

| Evidence family | Period / observed status |
|---|---|
| 31 daily frozen programs | Train end 2022-12-30; validation and nominal holdout boundary 2026-08-10 |
| Three daily checkpoint workspaces | `v2-final-verification-20260911`, `v2-model-verification-20260911`, `v6-development-final-20260911` |
| Five intraday programs | Train end 2024-12-31; validation end 2025-12-31; distinct intraday schema |
| Intraday completed validation | One `final-validation.json` artifact observed; metadata/hash recorded without extracting its scores |
| Existing holdout declaration | Declared 2026-08-13; boundary 2026-08-10; explicitly labels prior history burned |
| v13 return-target study | Development 2018–2022; later historical validation 2023–2026-09-11; script/report explicitly acknowledge previously used history |
| Market snapshot inventory | All six begin 2016-01-04; latest two end 2026-09-11 |

All 36 program-file digests matched their stored `program.sha256`. This check did not revalidate every frozen runtime in every old workspace. Absence of a checkpoint artifact is not proof of untouched history elsewhere.

Machine-readable evidence: [historical-use inventory](../../amat/docs/phase0-evidence-2026-09-20/historical-use-inventory.json) and [validation-access metadata](../../amat/docs/phase0-evidence-2026-09-20/validation-access-metadata.json). Do not mark 2026 untouched simply by advancing training to 2025. No holdout was opened to complete this audit.

## 9. Telemetry, report consumers and persistence hazards

`telemetry.read_telemetry` reads state/calls/events/experiments without opening a mutating `Ledger`. Monitoring exposes phase age, budget/usage, provider outcome and pending-call information. `export_analysis` verifies source artifacts and exports strategies, daily observations, fills, misses, folds, scenarios, providers and timings, plus downside/tail/turnover diagnostics, failures and trade-monitor artifacts.

Consumers include `report.json`, `report.md`, `report.html`, analysis exports, CLI status/view/analyze, summary rendering, charts, failure-library export, and the generated `catalog.toml` consumed by portfolio review. Preserve existing schemas/identities; add optional correlated research metadata without changing trading numbers.

**Finding R6:** Full model requests and responses are already durably stored in `research.sqlite3.calls`; committee request/response bodies are stored in `registry.sqlite3.artifacts`. Adding raw web content to those prompts would persist it even if retrieval itself used RAM. Separate metering/audit metadata from retained body policy before introducing Tavily. Extend the same restriction to graph checkpoints, errors, caches and LangSmith callbacks. Existing internal research history should not be destructively purged as part of a migration.

The user confirmed that validated claims and evidence excerpts may persist, while raw retrieved pages remain RAM-only. Apply that policy across application-controlled sinks. Hashes alone cannot reproduce changed external content. Structured claims remain untrusted data even after admission. OS swap/core dumps and provider retention need separate verification before claiming a stronger system-wide guarantee.

## 10. Reusable evidence and review contracts

Reuse lower-level `llmrouter.contracts.Record`, provider request/response normalization, health classification, canonical hashing, local schema validation, and bounded repair behavior. Extend these carefully where actual model identity and billing-attempt metadata are missing.

Reuse committee patterns from `Fact`, `Check`, `Finding`, `EvidenceRequest`, `Assessment`, `Bundle` and `Decision`: exact evidence identity, allowlisted references, role identity and preservation of objections. `assessment_validation.validate_assessment` requires known citations, unique role-prefixed finding IDs and chief accounting for every earlier finding. `evidence.py` provides duplicate-key/nonfinite JSON rejection, safe local paths and evidence hashing. `evaluation.py` provides a case-catalog/evaluation pattern.

Do not reuse `Bundle` wholesale as a web-evidence contract: it describes paired AMAT runs. Introduce source/claim/task contracts under deepresearch and keep a one-way dependency; avoid importing `portfolio_team` from `llmrouter`.

## 11. Concrete file plan

The implementation can use the following minimum module set; later specialist additions should not force relocation of existing files.

New production files:

```text
amat/src/llmrouter/policy.py
amat/src/llmrouter/catalog.py
amat/src/llmrouter/pricing.py
amat/src/autoresearch/deepresearch/__init__.py
amat/src/autoresearch/deepresearch/contracts.py
amat/src/autoresearch/deepresearch/state.py
amat/src/autoresearch/deepresearch/graph.py
amat/src/autoresearch/deepresearch/planner.py
amat/src/autoresearch/deepresearch/dispatch.py
amat/src/autoresearch/deepresearch/react.py
amat/src/autoresearch/deepresearch/budget.py
amat/src/autoresearch/deepresearch/context.py
amat/src/autoresearch/deepresearch/evidence.py
amat/src/autoresearch/deepresearch/verifier.py
amat/src/autoresearch/deepresearch/synthesis.py
amat/src/autoresearch/deepresearch/memory_gate.py
amat/src/autoresearch/deepresearch/store.py
amat/src/autoresearch/deepresearch/observability.py
amat/src/autoresearch/deepresearch/proposal_adapter.py
amat/src/autoresearch/deepresearch/sources/__init__.py
amat/src/autoresearch/deepresearch/sources/base.py
amat/src/autoresearch/deepresearch/sources/internal.py
amat/src/autoresearch/deepresearch/sources/tavily.py
```

For V1, typed contradiction checking can live in `verifier.py`, workflow counters in `store.py`; separate `contradictions.py`/`workflow_memory.py` only when justified. Academic/GitHub adapters and procedural optimization are later additions.

New configuration: `amat/configs/research/{deep-research,routing,sources,context,observability}.toml` and operation-tier chains under `models/`. New tests: `test_deepresearch_contracts.py`, `test_deepresearch_budget.py`, `test_deepresearch_sources.py`, `test_deepresearch_graph.py`, `test_deepresearch_memory.py`, `test_deepresearch_boundaries.py`, `test_deepresearch_migrations.py`; use synthetic source fixtures and fixed eval cases.

Existing modifications:

| Files | Required integration |
|---|---|
| `llmrouter/contracts.py`, `chain.py`, `transports.py`, `health.py` | Groq identity/base URL; served-model metadata; explicit attempt accounting; serialized concurrency behavior |
| `autoresearch/ledger.py` | Versioned metadata/body policy and shared budget interfaces; conservative settlement retained |
| `autoresearch/proposer.py`, `cli.py` | Typed hypothesis adapter and explicit bounded research commands |
| `autoresearch/loop.py` | Minimal pre-proposal and post-durable-outcome hooks; no lifecycle rewrite |
| `autoresearch/memory.py`, `workspace.py` | Restricted evidence views, new-version frozen policy files, backward-compatible legacy retrieval |
| `autoresearch/telemetry.py`, `summaries.py`, `presentation.py` | Correlated research IDs/quality/cost; existing output semantics retained |
| `portfolio_team/model_runtime.py`, `openai_runtime.py`, `remote_coordinator.py`, `store.py` | Shared transport adapter preserving committee protocol, budgets and validation |
| `portfolio_team/research_queue.py` | Later typed, deduplicated lifecycle-flag intake; current approval boundary retained |
| `amat/pyproject.toml`, `uv.lock` | Pin compatible framework/observability/search dependencies only at implementation |

No strategy engine or executor change is required to implement deep-research V1. Executor discrepancies belong to Phase B. v13 support requires its own explicit adapter/program scope.

## 12. Migrations and compatibility

1. Introduce an explicit schema-version/migration record, transactional migrations and backups for new writable research ledgers. Add namespaced research run/task/source/evidence/claim/link/admission tables without repurposing existing experiment IDs.
2. Add request/response body-retention policy, requested/served model, parent call/attempt identity, currency/cost-estimate metadata and reservations with explicit unknown states. Preserve existing charges and legacy bodies; do not rewrite recorded history to manufacture compliance.
3. Keep single-writer ownership. Parallel workers return results/events to the coordinator or a deliberately serialized store API; no shared cross-thread connection by assumption.
4. Preserve `research-memory/1` as a frozen compatibility input. New semantic/procedural retrieval must carry evidence-use class, availability/cutoff metadata and source references. Restricted validation observations cannot become proposer-visible lessons.
5. Do not migrate frozen program directories in place. Their copied code and hashes are part of the experiment. New behavior belongs to a new program/version; old runs resume with their frozen runtime. Adapters can read old data without modifying it.
6. Retain the committee registry's independent review/attempt identities and protocol hashes. New adapters need a protocol-version change and compatibility tests, not silent reinterpretation.
7. Preserve candidate canonicalization, including omitted-null `entry_model`. Keep raw config hashes distinct from canonical candidate/program digests and volatile report/manifest hashes.

## 13. Regression evidence and required future checks

Captured from this baseline suite's real-loop fixture: candidate recipes, objective outputs, and primary/stress daily records in [regression fixture digests](../../amat/docs/phase0-evidence-2026-09-20/regression-fixture-digests.json). These are comparison evidence from the current implementation, not independently proved strategy truth. Full manifests contain volatile provenance; compare stable numerical/contract content and intentionally versioned fields appropriately.

Baseline v6 candidate digest:

`9163074da25c43079b43a911092e613e67dc8c55cc1bc1f922f8b478176223c1`

Baseline objective file SHA-256:

`4ff380b97a574b2135771b7b1c34430270dced461a0e19c2368df9ff5c03a44c`

The captured fixture also contains candidates `a7273f02c96bc7a08ae47ffd4f9c60f98b1abf26cafed960899648a3cf8d4332` and `21900d5e4510b86bd1a794977c6b83d44498e66d26b8d0c82653d16415afe01c`, with full file hashes in the evidence. Historical candidate file identities are separately inventoried without reading validation scores.

Existing regression anchors:

- `test_autoresearch.py`: forbidden mutation; worker-boundary revalidation; stripped credentials; frozen input tampering; real loop/resume; ledger crash/lock/budget; provider failover; first validation return; cache tampering; parent/incumbent comparison; bounded duplicates and partial batches.
- `test_research_workflow_tools.py`: charged repair; budget refusal; compatible frozen memory; checkpoint contamination exclusion; chart evidence; approved matched follow-ups.
- `test_research_promotion.py`: operator selection preserves measurements/verdicts; source tampering rejected; promoted provenance frozen.
- `test_research_telemetry.py`: numeric export reconciliation, source hashes, first-return/tail metrics, output escaping.
- `test_research_summaries.py`: commentary cannot change the recorded decision; unavailable model behavior and summary tampering.
- `test_llmrouter_transports.py`: provider schemas/errors/usage and bounded schema fallback.
- `test_portfolio_team_budget_resume.py`: durable partial progress, atomic budget deferral, per-review versus daily limits, real failures not hidden by budget pauses.
- Executor `test_submit.py`, `test_plan_source.py`, `test_daemon.py`: preflight, plan parsing, idempotency, scheduling/recovery and risk limits; these exercise checkout code.

Add future tests for actual served-model identity, separately budgeted wire fallback, aggregate concurrent dollar/search limits, RAM-only persistence sentinels across every sink, source poisoning and invalid citation rejection, compaction preserving contradictions/cutoffs, migration idempotence/crash recovery, and no transitive validation leakage through workflow statistics. Existing tests do not yet prove these proposed features.

## 14. Read-only executor deployment audit

Evidence: [container inspection](../../amat/docs/phase0-evidence-2026-09-20/deployment-inspect.json), [state summary](../../amat/docs/phase0-evidence-2026-09-20/deployment-state.json), [DNS comparison](../../amat/docs/phase0-evidence-2026-09-20/dns-probe.json), and [installed-versus-checkout diff](../../amat/docs/phase0-evidence-2026-09-20/deployed-vs-checkout.diff). Summaries omit credentials and account identifiers.

| Observation | Consequence |
|---|---|
| `amat-executor`, image `sha256:70bf96b0cd21e9b9b2b8afef2976f53322eebd52ee4458f21f670f9e461cdb8b`, created September 7; running but unhealthy | Process existence is not service readiness |
| Initial health capture had 22 consecutive failures; recent probes fail DNS for account access; daemon clock access also fails | Current account/order/position state cannot be verified through the container |
| Host resolves `paper-api.alpaca.markets`; container probe returns temporary name-resolution failure | Container DNS/network path is the immediate investigation target; exact root cause not established |
| `DRY_RUN=false`, fixed paper adapter; v6 config, August 11 snapshot | Paper order submission is enabled when connectivity returns; v13 is not deployed |
| Installed `config.py`, `submit.py`, `daemon.py`, `cli.py`, `report/daily.py` differ; `report/trade_monitor.py` absent | Checkout test results do not describe all deployed behavior |
| Deployed `config.py`/`submit.py` lack checkout `RiskLimits` and associated enforcement | Config allowlist, kill switch, notional/order caps and equity-drift checks in checkout are not deployed |
| Last saved plan September 15; last report/equity observation September 17; September 18 report recorded missed | A fresh operational report is unavailable; do not interpret missing data as no trading risk |
| Latest report has six positions, zero orders for its session, no submission and a warning | Its `reconciliation_complete=true` only describes that report's local order matching, not proof of current or prior-session broker reconciliation |
| September 15 journal records six submissions; last execution-reconciled event September 17 | Journal submissions alone do not prove fill status; follow-up needs broker reads after connectivity restoration |
| Latest plan has undetected Git revision and 24 appended sessions outside the frozen snapshot digest | Operational provenance is incomplete, despite explicit plan caveats |
| Journal contains two malformed JSON lines, 41,475 `phase_missed` and 13,702 `report_written` records over its retained history | Investigate historic event duplication/corruption; these totals do not mean that many distinct missed sessions or reports |

The AMAT bind mount is read-only; the named `executor_executor-state` volume is authoritative, not checkout `executor/state`. Read-only mount permissions do not make the host checkout immutable: host edits remain visible to the planning process.

Priority Phase B work: diagnose container DNS; reconcile actual paper orders/positions and missed sessions; prepare a reviewed deployment of the existing safety controls with required configuration; establish reliable code/data provenance and report freshness; investigate malformed/duplicated journal history. Re-enabling connectivity may allow the existing daemon to resume paper actions, so couple repair with explicit current deployment/risk-state review rather than assuming a harmless dashboard repair.

## 15. Confirmed decisions and Phase A completion criteria

The user confirmed during Phase A:

- No spending ceiling for models/search/data for now; use as needed for authorized work. New optional monetary caps should be unset, with costs fully recorded. Keep bounded calls, iterations, concurrency and timeouts; do not silently change old frozen programs' token limits.
- Both calendar-quarter reporting and four contiguous historical blocks.
- Validated claims/evidence excerpts may persist; raw retrieved pages remain RAM-only across application-controlled sinks.
- Exposure-matched risk-adjusted improvement remains the default research objective, with a separate explicit return-target program.

These decisions supersede earlier pending questions in the planning discussion; no additional clarification is needed to complete Phase A.

Phase A completion means the integration map, baseline artifacts/tests, historical-use register and operational discrepancies are documented. It does **not** mean all readiness checks passed. Phase 1/Phase D implementation must preserve this baseline and resolve its applicable design gaps; Phase B should address the operational findings before expanding trading scope.
