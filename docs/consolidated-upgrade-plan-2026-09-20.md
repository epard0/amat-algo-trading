# AMAT consolidated upgrade plan — September 20, 2026

Status: consolidated roadmap with confirmed priority of reliable paper trading and defensible research, and confirmed architecture of LangGraph + existing llmrouter. Budget, reporting, persistence and research-objective decisions are confirmed below. This document consolidates CHANGES.md, AMAT_DEEP_RESEARCH_IMPLEMENTATION_INSTRUCTIONS.md, and the user's pasted alpha-engine article. The completed Phase A audit is recorded separately in `amat/docs/deep-research-phase0-audit.md`; operational readiness is not established by the plan. No strategy, deployment, or provider configuration was changed for this review.

## Recommendation and repository evidence

Prioritize reliable paper execution, data integrity, defensible evaluation, and observable research before expanding strategy complexity or markets. Build the research upgrade incrementally around existing boundaries.

- The workspace separates AMAT research/planning from an Alpaca paper-only executor. Preserve this contract.
- v13 contains eleven ETFs: SPY, QQQ, IWM, EFA, EEM, IEF, GLD, SLV, DBC, USO, UUP. Its 63/126/252-session trend horizons already provide multiple daily lookbacks; that is distinct from combining intraday/daily/weekly bars.
- `amat/docs/return-target-review-2026-09-14.md` reports v13 CAGR of 13.34% versus v6 5.52%, with maximum drawdown 15.01% versus 7.94%, worst v13 daily loss 9.87%, and conventional Sharpe 1.004 versus 1.010. These are reported historical results, not independently rerun here. Higher risk explains an important part of the return improvement. Investigate the loss and risk-control semantics before treating configured loss thresholds as realized-loss guarantees.
- That review uses development through 2022 and later historical validation through September 11, 2026, explicitly acknowledging previously inspected history. Existing program defaults also contain older holdout boundaries. Audit actual access history across workflows; a new date split cannot restore untouched status.
- `llmrouter/chain.py` already includes Gemini, OpenAI, NVIDIA, and OpenRouter transports, provider health, and budget hooks. Groq is absent from the inspected default provider map. Add it through the same boundary.
- `autoresearch/ledger.py` already owns SQLite state, experiments, cache, calls, and events, with a single-writer lock. `memory.py` checks historical/configuration compatibility and evidence hashes. Extend these controls rather than creating an independent source of truth.
- `amat/validation/distributions.py` already contains Gaussian, Student-t, Laplace, skewed, mixture, and clustered innovations. Monte Carlo and research robustness modules already include joint block bootstrap scenarios. Audit coverage and reporting before adding generators.
- Executor code includes reconciliation and restart/retry handling. Source presence is not proof of deployed behavior. A runtime audit remains necessary.
- The checkout contains substantial modified and untracked work. Establish a reproducible baseline that includes intended local changes before implementation; do not reset it.

## Ranked backlog

Ranking is by importance to the current daily ETF paper-trading system, not implementation order alone. Small routing improvements can ship while larger data work progresses.

| Rank | Proposal | Decision and scope |
|---|---|---|
| 1 | Executor trade and portfolio monitoring | Implement/verify first: account reconciliation, order lifecycle, restart recovery, stale data/plan handling, alerts, and risk-control behavior. |
| 2 | Analytics, metrics, and signal-lifecycle monitoring | Extend existing telemetry; connect strategy intent, actual fills, portfolio outcomes, decay/regime/capacity flags, research quality, and cost with stable IDs. |
| 3 | Complete v13 data coverage, target 2012–2026 | Implement coverage audit and reproducible ingestion for the eleven ETFs, including execution-window data and corporate actions. Older daily prices alone cannot reproduce v13 execution. |
| 4 | Quarterly and cumulative backtests plus simulation | Implement reporting over continuous runs; audit existing synthetic/bootstrap engines and add matched random-signal controls. |
| 5 | Training cutoff through 2025 | Implement only in a new versioned research program with explicit historical-use labels and prospective evaluation. Never silently mutate an old experiment. |
| 6 | Reusable agents, tools, prompts, contracts | Implement a small shared interface over existing packages; version prompts and tool permissions. Avoid broad package restructuring. |
| 7 | Deep-research upgrade: internal retrieval, ledger, bounded graph, critic, compression | Implement the V1 subset below. Preserve deterministic experiment governance and execution authority of none. |
| 8 | Routing, fallbacks, Groq/OpenRouter, requested defaults | Extend llmrouter and capability policy; instrument from the first call. OpenAI reserved for difficult judgment/coding per user preference. |
| 9 | Asset Regime Model parameter loop | Implement a bounded registered search after evaluation controls. Prefer stable parameter regions over the highest historical score. |
| 10 | Volume and liquidity data/features | Implement common timestamped contracts and execution diagnostics first; test incremental predictive value separately. |
| 11 | Minimal GUI/Web UI | Build read-only monitoring first, then bounded research launch/cancel. Use existing reports and CLI services. |
| 12 | Signal processing | Research small causal filters, smoothing, volatility normalization, and hysteresis via isolated ablations. |
| 13 | Multi-timeframe analysis | Experiment after bar alignment is validated; distinguish existing daily horizons from new bar frequencies. |
| 14 | Control theory | Use limited risk/exposure feedback experiments; constrain response, turnover, delay sensitivity, and failure behavior. |
| 15 | News sentiment quality and adaptive source ranking | Evaluate timestamps, relevance, entity mapping, duplicates, calibration, abstention, and model drift before adding predictive weight. |
| 16 | Wider assets and market scanning | Start with a bounded eligible universe and frozen selection rule; measure incremental portfolio value and selection bias. |
| 17 | Crypto research arms | Start separate BTC/ETH spot research; XRP and other assets follow data and liquidity qualification. Separate calendar and execution assumptions. |
| 18 | Custom indicators | Only implement a falsifiable mechanism not already captured by existing signals; compare with simple baselines. |
| 19 | General machine-learning features | Defer until data/labels/evaluation support them; start with regularized simple models. Existing disappointing ML experiments must inform proposals. |
| 20 | Gamma/option-flow and social/X sources | Defer broad ingestion until historical coverage, availability timestamps, cost, and relevance are established. |
| 21 | Asset-specific deep reinforcement learning | Defer as a separate research project with a validated simulator and strong simple baselines; no consistent-gain promise. |

More frequent monitoring belongs at rank 1 for operational events. More frequent LLM research or strategy rebalancing does not inherit that priority.

## Architecture decisions and amendments to the supplied specification

1. Keep `autoresearch.loop` responsible for experiment registration, frozen candidates, isolated execution, hashes, results, and promotion controls. Place deep research before proposals and outcome feedback after evaluation.
2. Confirmed: LangGraph is the sole outer research orchestrator, bounded ReAct workers run inside it, selected LangChain interfaces are used where useful, and llmrouter remains the sole remote LLM boundary. Google ADK is excluded from this implementation.
3. Reusable model cards belong in a capability catalog: provider/model ID, context limits, structured-output/tool support, pricing source/time, timeout, health, and evaluated task suitability. SDK adoption alone does not ensure behavioral consistency.
4. Instrument local telemetry and optional LangSmith in the foundation phase, moving it earlier than the supplied Phase 6. Remote tracing outages should leave local telemetry operating. Export allowlisted fields; verify callbacks do not automatically capture prompt/tool bodies.
5. Confirmed resolution of the conflicting RAM-only introduction and disk source snapshots in section 24: raw web/source bodies remain in bounded RAM; validated claims, approved evidence excerpts, source metadata, hashes, and experiment links may persist. Derived text remains untrusted data. A hash without retained content cannot reproduce extraction after a source changes; record that limitation.
6. Audit ALL persistence paths for source leakage: existing model request/response ledger, caches, exception logs, LangGraph checkpoints, traces, and temporary files. An in-memory virtual filesystem does not itself stop prompt injection or guarantee no OS swap/core-dump persistence. Define the intended threat-model scope before implementation.
7. Retrieval access must distinguish proposal-visible development evidence from restricted validation/holdout outcomes. Do not leak restricted outcomes through workflow scores, summaries, duplicate checks, or memory. A hidden service may block a duplicate without exposing its restricted performance.
8. Correct worker stop semantics: source minimum or model confidence alone is not success. Success requires material deliverables supported, provenance present, and material contradictions resolved or explicitly reported. Budget/deadline/stall exits return partial or insufficient evidence.
9. Budget limits must apply across the entire run, including workers, retries, escalation, compaction, and search charges. Use atomic reservation and durable settlement; nested per-worker limits must not multiply the global cap. Unknown usage remains conservative rather than being recorded as zero.
10. Build a narrow V1 before all specialist classes. Internal retrieval + Tavily + verification + proposal adapter provides a testable end-to-end result. Academic and code specialists follow demonstrated retrieval gaps.
11. Keep semantic source claims distinct from AMAT experiment evidence. A failed local strategy does not falsify a paper's claim in every market. Preserve failed experiments and uncertainty.
12. Procedural improvement proposes versioned policy/prompt changes for offline evaluation; it does not rewrite production prompts, risk rules, or strategy configurations automatically.

Recommended path:

```text
Question → contract/budget → allowed prior knowledge → deterministic route
→ bounded research → normalized evidence → critic/limited follow-up
→ synthesis/admission → validated hypothesis → existing proposal/experiment loop
→ permitted outcome feedback
```

## Delivery phases and acceptance gates

### Phase A — Baseline and Phase 0 audit

Produce the requested `amat/docs/deep-research-phase0-audit.md` before research implementation. Trace model call sites, budget settlement, ledger tables, memory compatibility, proposal inspirations, tool boundaries, frozen mutation controls, historical access, report consumers, and migrations. Record code/config/data digests and a complete baseline of intended local work. Run both modules' required baseline checks; distinguish pre-existing failures.

Add a read-only deployment review covering configured strategy/snapshot, daemon health, journal, orders, positions, fills, and current reports. Use the authoritative container state when deployed in Docker. Do not submit an order to prove the audit.

Gate: concrete integration map, baseline evidence, historical-use register, executor discrepancy list, and unresolved architectural decisions documented. This planning review does not satisfy that gate.

### Phase B — Operational reliability and analytics

Trace each plan to client order ID, broker status, fills, and resulting position. Check duplicate submission after restart, ambiguous POST timeouts, partial fills, rejected orders, unknown holdings, missed schedules, stale snapshots, and reconciliation drift. Examine whether daily-loss/drawdown controls affect subsequent decisions versus intraday behavior; realized gaps can exceed configured limits.

Monitor broker order events where available, use bounded polling/reconciliation as fallback, and make cadence configurable. Maintain daily strategy semantics unless a separately tested strategy changes them. Emit actionable freshness, discrepancy, failure, and risk alerts.

Add versioned lifecycle records: candidate → validated → paper-approved → active → watch/restricted → retired, with explicit reasons, actor permissions, artifact references, and a separately governed revalidation path. This is a logical extension of existing registries and queues, not a requirement for five new services. Separate strategy approval status from daily order state.

Monitor three dimensions from the pasted article: signal performance relative to a suitable frozen validation/paper baseline; feature/regime drift; and execution capacity/cost drift. Track uncertainty and minimum effective sample sizes. Use actual execution-window participation, realized slippage, spreads and available liquidity in addition to ADV. Factor correlations are noisy crowding proxies, not direct crowding measurements.

Route diagnostic flags to `portfolio_team.research_queue` through a narrow event adapter after its interface audit. Deduplicate flags, impose cooldowns and queue budgets, and distinguish a data/execution incident from a strategy hypothesis. Performance concerns request review; deterministic hard-risk breaches use pre-authorized containment rules. Any changed allocation must pass risk → sizing → a fresh approved plan → executor. The monitoring or research agent never directly changes broker positions. Initially run statistical flags in shadow mode and review false positives before enabling any automated restriction policy.

Gate: targeted fault-injection and integration tests pass; unresolved orders cannot be treated as fills; restart does not duplicate intent; actual account reports reconcile within declared tolerances. Establish a scheduled paper observation window and operational sign-off criteria before rollout.

### Phase C — Data and evaluation foundation

Build a symbol × field × frequency × date × provider coverage matrix. Target 2012 through the latest completed available session in 2026 for v13, with explicit partial-year labels. Include warmup; a 2012 raw start does not imply a measured January 2012 start with a 252-session warmup.

Validate adjusted signal prices, raw execution prices, splits/dividends, intraday VWAP inputs and volume, calendars, gaps, duplicates, timezone conversion, and publication/availability timestamps. Preserve immutable manifests. Proxy prices or daily-only execution approximations form explicitly different studies.

Report calendar-quarter returns and four contiguous historical blocks if desired, with full-period compounded returns, drawdown, turnover, exposure, costs, sample sizes, and benchmark/exposure-matched comparisons. Calculate slices from one continuous run with positions, warmup, and five-session selection state preserved. Chain quarter returns multiplicatively. Partial 2026 quarters stay labeled partial.

Maintain separate panels for historical evaluation, joint bootstrap robustness, hypothetical parametric market shocks, and random-signal null controls. Fit any estimated scenario parameters using the permitted development data only. Match random controls on opportunities, exposure, turnover and constraints as closely as practical; record seeds and avoid selecting favorable draws. Synthetic paths do not count as independent future market evidence.

Create a new training-through-2025 research program if selected. Register chronological inner/outer evaluation and purge overlapping labels where needed; fit transforms and regime models within each training fold. Label inspected 2026 data accordingly. Freeze a future prospective protocol before its observations arrive. Registry tracking must include all parameter trials, not only retained winners.

Implement an independently tested Deflated Sharpe Ratio diagnostic with declared return frequency, observation count, skew/kurtosis conventions, trial universe, and sensitivity to correlated trials. Validate against published examples. Select one coherent multiple-testing protocol for the campaign: a fixed family with suitable valid p-values and Holm correction, or a formally valid online procedure with explicit assumptions. Do not bolt the article's threshold onto DSR and claim guaranteed error control. Statistical diagnostics supplement economic, cost, stability, and prospective gates.

Gate: complete supported-period manifests, reconciliation of quarter/full-period metrics, causal feature tests, preserved frozen baseline outputs, and no restricted-result access through selection or memory.

### Phase D — Cost-aware research V1

Implement strict research/evidence/budget contracts, versioned model policy, prompt/tool registry, local telemetry, and optional redacted LangSmith. Add Groq and evaluated free routes through llmrouter. Keep Gemini `gemini-3.8-flash` as the preferred Gemini route and OpenAI `gpt-5.6-sol` for difficult analysis/coding, conditional on account capability probes. Use utility/free models only where task-specific quality holds up.

Implement internal search and exact/near-duplicate detection first. Extend the existing SQLite ledger through versioned migrations and a single-writer mechanism; do not let parallel workers share the current connection unsafely. Retain legacy memory compatibility.

Then add Tavily search/extraction, typed evidence, bounded workers, deterministic graph, one follow-up round initially, compaction preserving evidence references, and a memory admission gate. Use a provisional small-worker configuration until RAM/latency measurements justify expansion. The user selected no spending ceiling for now, as needed: represent optional per-run/day/month monetary caps as unset, retain complete cost/usage accounting, and keep bounded calls, iterations, concurrency, context, and timeouts. Do not change old frozen programs' token limits silently.

Connect validated hypotheses to existing proposal validation only after standalone evals pass. Outcome feedback respects development/validation visibility.

Gate: fixture cases cover duplicate work, conflicting sources, false citations, prompt injection, malformed outputs, provider/search failures, budget exhaustion, crash recovery, and compaction. Hard invariants: zero research execution authority, no budget overspend from concurrent reservations, no raw bodies persisted under the selected policy, no holdout disclosure. Evaluate citation correctness, contradiction detection, useful hypotheses, cost, and latency against a fixed baseline before enabling the new default.

### Phase E — Regime and signal experiments

Register a small parameter search for regime count, fitting window, refit cadence, risk floor, and unknown-regime behavior. Compare no-regime, simple-rule, and current-model baselines. Evaluate nearby settings and worst-fold behavior using exposure/cost-matched comparisons. An optional Bayesian search comes after a bounded coarse/random design proves useful.

Experiment separately with volume/liquidity, causal signal processing, bar-frequency combinations, and bounded exposure feedback. Ban full-sample normalization and centered filters in causal backtests. Avoid introducing a new indicator, model, universe, and optimizer simultaneously.

Gate: replicated incremental value under registered cost/stability/risk criteria, with failed candidates retained. More trades, higher exposure, or a better in-sample score alone is insufficient.

### Phase F — Usability and news

Build a minimal UI over existing artifacts: executor freshness, positions, fills, discrepancies, equity/drawdown, run progress, evidence, costs, and logs. Add bounded research submission/cancellation through the existing service/CLI contract. Require authentication for network exposure and one authoritative job queue. Research controls do not grant broker authority.

For news, assess a fixed time-stamped evaluation set before adding sources. Track relevance, symbol mapping, duplication, sentiment calibration, abstention, publication-versus-arrival time, source independence, latency, and actual model identity. Route dynamic free-model output as a separately identified experimental feature. Do not reconstruct historical sentiment using modern knowledge and label it point-in-time evidence.

Adaptive source weighting/prompt changes go through offline comparisons and versioned promotion. Add X only if its unique incremental information justifies access/ingestion costs.

Gate: UI agrees with authoritative reports; news adds measured value beyond price-only baselines; outages lead to an explicit unavailable/abstain state.

### Phase G — Universe and crypto expansion

Start a bounded scanner over a declared liquid universe, with point-in-time eligibility and fully costed selection. Separate scanner evaluation from discretionary selection of historical winners. Give each strategy arm a portfolio allocation and shared cross-arm risk constraints.

Start BTC/ETH spot research with venue/pair-specific data from 2016 only where actually available. Verify earliest listing and coverage for each venue/pair; do not promise every Binance/Coinbase pair has ten years. Handle gaps, UTC day boundaries, 24/7 trading, fees, spread, minimum order size, precision, outages, and quote-currency differences. Do not carry the ETF 252-session calendar or opening VWAP model over unchanged. Add funding and liquidation rules only if derivatives are deliberately introduced later.

Use separate manifests, calendars and strategy configurations. Keep the existing executor paper-only; any future crypto execution adapter requires its own explicit scope and review.

Gate: clean supported coverage, comparable simple baselines, observed paper behavior in an appropriate simulator/paper environment, and incremental portfolio value after costs and correlated exposures. DRL remains a separate later experiment.

## Explicit exclusions from the initial implementation

- Wholesale rewrite of the experiment lifecycle or simultaneous ADK/LangGraph orchestration.
- Automatic live strategy/code/risk-policy mutation or trading authority for research agents.
- Calling inspected history untouched after changing the training date.
- Synthetic price history presented as actual exchange history or proof of profitability.
- Raw source logging hidden in traces/checkpoints despite a RAM-only policy.
- Free-model randomness silently changing production sentiment semantics.
- Unbounded full-market search, broad gamma-flow purchases, or per-coin DRL before simpler evidence justifies them.

## Assessment of the pasted alpha-engine article

Adopt its lifecycle ownership, explicit risk/sizing boundaries, trial accounting, and decay/regime/capacity monitoring. Adapt them to AMAT's current paper-only plan/executor architecture. The article is conceptual guidance; its code does not establish the claimed statistical or security guarantees.

- The decay function sums failing windows anywhere in the history. It does not test consecutive failures. Implement a consecutive streak with reset, minimum sample size, uncertainty and cooldown; overlapping windows are not independent confirmation. A comparison against inflated in-sample Sharpe is also a poor production baseline.
- `alpha / (trials + 1)` is not Holm's ordered step-down procedure. Across an unbounded sequence its allocations sum without bound, so it does not by itself supply the stated family-wise guarantee. Fixed-budget Bonferroni/valid Holm or a properly specified online error-spending design are alternatives, provided their p-values remain valid under the research process.
- Repeated adaptive reuse of the same data can invalidate naive p-values; adding a correction does not repair leakage. Maintain the sealed evaluator and proper chronological design.
- `self.oos` is directly accessible to the research object. Storing a split in a constructor is not an access-control boundary. Keep restricted data behind the evaluator/process boundary and test that agents cannot retrieve it.
- A dictionary of role permissions is not enforcement unless all reads and writes pass through a constrained interface. State transitions need actor checks and atomic validation; execution must reject missing/stale approvals.
- Three passing folds is a policy choice, not a universal confidence guarantee. Window lengths, trade counts, dependence and market coverage matter.
- Do not reject every correlated strategy mechanically. Assess incremental net portfolio utility under factor/exposure/concentration limits and estimation uncertainty. A fully orthogonal portfolio is not a necessary condition for useful diversification.
- The article both limits monitoring to research flags and later gives it an implicit path to allocation reduction. Resolve this explicitly: flags enter research and risk policy; risk/sizing author a new plan; the executor validates and acts on that plan.
- A two-state HMM is an optional model, not a requirement. Start with simple drift baselines and compare with the existing regime machinery. Do not automatically replace a strategy every time noisy recent returns deteriorate.

The statistical points are supported by [the original DSR paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), [Holm adjustment documentation](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/p.adjust.html), and [research on online family-wise error control](https://arxiv.org/abs/1910.04900). Code-specific defects above follow directly from the user's pasted examples.

## Confirmed implementation decisions

Confirmed: reliable paper operations and defensible research first; LangGraph + llmrouter; omit the custom coding-agent proposal. The X article is now available as user-pasted text and has been incorporated above.

1. Spending: no ceiling for now; use models, search, and data as needed for authorized work. Preserve metering and optional cap support; bounded workflow execution remains required.
2. Period reporting: both calendar quarters and four contiguous historical blocks.
3. Persistence: validated claims and evidence excerpts may persist; raw retrieved pages remain RAM-only. Apply this to application-controlled logs, caches, checkpoints and trace exports. Do not claim stronger OS/provider retention guarantees without verifying those separately.
4. Research objective: exposure-matched risk-adjusted improvement is the default; maintain a separate explicit return-target program. Do not merge the two selection objectives.

## External references checked

- [LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents): predetermined workflows versus dynamic agent behavior; supports the proposed separation.
- [Deep research example](https://docs.langchain.com/oss/python/deepagents/deep-research) and [knowledge-base routing example](https://docs.langchain.com/oss/python/langchain/multi-agent/router-knowledge-base): useful patterns, not replacements for AMAT governance.
- [Google ADK model integration](https://google.github.io/adk-docs/agents/models/): alternative framework/model integration path.
- [Gemini model catalog](https://ai.google.dev/gemini-api/docs/models) and [GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol): requested IDs are documented; actual account availability still needs probing.
- [OpenRouter free router](https://openrouter.ai/docs/guides/routing/routers/free-router): randomly chooses among compatible available models and reports actual model identity; availability and latency vary.
- [Groq models](https://console.groq.com/docs/models) and [Tavily SDK](https://docs.tavily.com/sdk/python/reference): verify capabilities in adapters, not domain code.
- [LangSmith masking](https://docs.langchain.com/langsmith/mask-inputs-outputs): supports selective omission/masking of trace data.
- [Coinbase candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles): historical data can be incomplete, no ticks can mean no candle, and requests require pagination for large ranges.
- [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf): supports limiting and recording search and separating strategy selection from evaluation.


## Implementation decisions confirmed after Phase A

- The first data implementation uses 2016 onward. The 2012–2015 expansion is deferred.
- The paper executor is authorized to use `amat_v13_return_target.toml` with its exact configuration hash. This is separate from the default exposure-matched research objective.
- Paper execution notional and order-count caps are explicitly unlimited; strategy allocation constraints and operational refusal checks remain in force.
- The user authorized cancellation of the stale non-AMAT SPY buy order. A cancellation request is not a confirmed cancellation.
- See `amat/docs/implementation-progress-2026-09-20.md` for implementation evidence and remaining gates.

## Latest confirmed routing and interface decisions — September 21

Routine work prefers Groq then OpenRouter's free auto-router, with Gemini as a
fallback. GPT-5.6-luna replaces the earlier Sol preference for coding and complex
tasks. Missing/unavailable Luna does not silently downgrade to another model.
Existing frozen research policies remain intact; new work uses the shared policy.

Keep normal operation small: executor run/status; research run/status/report.
Planning and monitoring happen inside those workflows. Configuration carries
provider and execution parameters; advanced diagnostic commands remain compatible.
The short delivery/next-step record is
`amat/docs/pipeline-continuation-2026-09-21.md`.
