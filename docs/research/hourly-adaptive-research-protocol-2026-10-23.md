# Hourly adaptive AMAT research — sealed campaign protocol

**Status: authorized finite campaign, with executable protocol/state sealed by the coordinator.** Start2026-10-23T16:40:10Z, harddeadline2026-10-24T04:40:10Z (October23 at12:40p.m. toOctober24 at12:40a.m. Detroit). First cycle performs foundation verification; next11opportunities are hourly. No new strategy measurement or promotion is implied by creating the schedule. The old October19–20campaign remains closed. See `outputs/research-campaigns/2026-10-23-adaptive-12h/protocol.json` for machine-readable limits and current state for actual completed work.

## User-selected objective and changed promotion policy

The objective is incremental improvement of the current strategy toward QQQ total-return parity or better, while limiting drawdown, improving difficult-market behavior and testing intelligent conditional portfolio diversification. Direct shorts and inverse ETFs may be researched separately. An OpenRouter free-model portfolio agent is a distinct research-only experiment.

The user has **prospectively superseded the former requirement to dominate every historical frontier cell**. A stronger return from an older rejected candidate in one cell no longer vetoes a new incremental promotion. Preserve and display those candidates and the old frontier for context; do not rewrite their earlier decisions. Record the old and new policy identifiers and the user’s authorization in the new protocol.

Each experiment uses the latest independently verified default as its reproduced control. A verified new best may become the next default after that experiment. If no candidate passes, keep the current default. Always retain the campaign’s original default and QQQ as stable comparison anchors. Historical comparisons use full continuous accounts from the same starting capital; never concatenate winning periods from successive candidate versions.

## Hard limits and hourly schedule

* Ten distinct preregistered experiments maximum, then stop and produce the complete review/report. Do not replenish the budget because an experiment loses. Distinguish proposed, started, measured, invalid, incomplete, rejected and promoted counts.
* At most three total arms per experiment: one reproduced incumbent control plus at most two fixed candidate variants. A candidate cannot be retuned after its outcome. Necessary ablations must fit within this limit or consume a separately registered experiment within the ten-experiment allowance.
* Schedule twelve opportunities: an initial tick at `start_utc`, then eleven hourly ticks at `start_utc + k hours`, for `k=1..11`. Hard deadline is `start_utc + 12 hours`. There is no thirteenth research tick at the deadline. The coordinator sets real UTC timestamps and also displays America/Detroit times.
* Aim for 20–40 minutes of active work per tick, with a hard **40-minute tick limit** or the remaining campaign duration, whichever is shorter. Forty minutes is a wall-clock work limit, not an inference-call budget. Never sleep for an hour or hold a twelve-hour shell loop.
* The final scheduled tick prioritizes completion, validation and publication. If fewer than twenty minutes remain, launch no new experiment; checkpoint safely, publish the final comparison and pause the saved automation. Stop early if ten experiments are completed, a budget is exhausted or the agreed stop criterion fires.
* Resume unfinished validation before proposing a new experiment. A blocked or long-running experiment consumes subsequent ticks as needed; do not overlap writers, skip a missing gate or manufacture a completed hourly run. At the tick limit, terminate or checkpoint the managed process safely and retain partial artifacts. Do not let an unmanaged measurement run past the hard limit.
* Use a filesystem writer lock, durable run/process identity including start identity, atomic state writes, append-only events, immutable result directories and bounded read-only inspection. Check UTC on entry, before experiment launch and before promotion. After the deadline there is no new experiment or promotion; measurement already in progress may only be safely checkpointed/closed.

Codex schedules and coordinates the work; Python performs deterministic native measurements. Verify the actual automation host and availability rather than promising that desktop scheduling runs while the local computer is asleep. Broker execution monitoring is a separate remote service. If a tick is missed, record the gap; it did not secretly perform research.

## Gate zero: capability, accounting and benchmark report

The first report section must audit the repository as it exists at campaign start: strategy and default identities; current dirty work; long/short signal and ledger support; native historical execution; paper execution; corporate actions; external cash flows; order authorization and partial-fill reconciliation; annual contexts; model fitting/selection; data coverage and dashboards. Classify capabilities as implemented/tested, research-only, operational-only or absent. Read later annual-scheduling and benchmark reviews, not only the older campaign final report.

Reconcile TradingView/Alpaca unrealized open-position P&L separately from day P&L, account NAV change, strategy inception return and the monitor’s 09:29 baseline. Open-position gains of about $2,000 can coexist with a smaller one-session account change. Derive exact values from broker sources and positions; do not assume the discrepancy is an error or silently relabel either quantity.

Build two explicit QQQ references on common dates: an investor total-return buy-and-hold benchmark and a benchmark using the declared native executable entry policy. Declare dividend reinvestment, corporate actions, whole-share rounding, idle cash, fees, inception, final liquidation and opening-volume participation. A capped entry may delay full investment; show this clearly. Define the cost-adjusted QQQ series used by each promotion comparison before seeing candidates.

Initially retain the reproducible historical anchor: 2016 signal warm-up; flat $100,000 from January 3, 2017 through October 12, 2026; verified observed next-session 09:30–09:35 VWAP; dividends/splits; whole-share purchases; native 1% opening-volume entry cap; native exits and missed fills; zero cash yield; 2/6/10 bps per side. If adding later or earlier data, version the data contract and remeasure controls under it. Do not pretend incomplete opening liquidity supports a longer executable history.

## Prospective incremental promotion gates

The sealed protocol must express drawdown as a positive loss magnitude or a consistently signed negative return so that comparisons cannot invert accidentally. A candidate must:

1. Reproduce its incumbent control exactly at all three costs under the same source/data/accounting contract. Any accounting repair requires fair control replays.
2. Match or exceed the incumbent’s net return in **both full history and continuing-account 2020-onward evaluation at each of 2/6/10 bps**, with at least one strict gain. Preserve the original 2023 safeguard at every cost unless the user explicitly changes that separate guard; removing the old all-frontier veto does not by itself remove this safeguard.
3. Have full-history and 2020-onward drawdown magnitudes no greater than both the latest incumbent and the campaign’s original default, and lower than the corresponding declared QQQ benchmark at each cost. If source/benchmark coverage is unresolved, mark this gate unavailable rather than pass it. If the final executable protocol narrows a drawdown scope, document that prospectively; never choose the scope after outcomes.
4. Pass accounting, causal-prefix/future-input mutation, stress, forward, monthly/recovery and native planner/backtest parity validation. Costs and uncertainty must remain credible; unresolved material robustness, source or reproducibility questions block promotion. A numerical return improvement alone is insufficient.
5. Produce a frozen native profile that exactly reproduces the measured candidate. An independent reviewer must verify the final change, gates, evidence identities and default references. Update coherent research/dashboard/CLI defaults only after a sealed promotion receipt. A research default promotion is not automatic remote broker deployment.

Compare the candidate with QQQ total return on the dashboard, including its return gap and drawdown advantage. QQQ return parity is the campaign goal, **not a requirement to forbid smaller validated steps toward it**: a candidate may advance before reaching QQQ if it passes the incremental gates above. Do not call that candidate a QQQ-beating strategy until it actually is one under the declared comparison. Preserve valid stronger historical candidates as comparators even though they no longer impose a cell-by-cell promotion veto.

Retain useful engineering changes with meaningful tests or measured performance evidence independently of strategy promotion. Faster code, cleaner reporting or corrected accounting does not earn a strategy-return promotion by itself.

## Scientific method and independent roles

Codex owns the objective, protocol, budgets and serialized work. Use independent agents where useful for repository capability analysis, source/data validation, monthly attribution, hypothesis review and execution-risk review. Reviewers receive the same frozen evidence and produce first-pass opinions without seeing one another’s answers; the coordinator synthesizes objections and orders work by decision value. Model-written shell text is never a job interface, and model recommendations cannot rewrite constraints or broker settings.

For each experiment: diagnose a measured failure; state one falsifiable mechanism; search the economic registry for duplicates; preregister variants and predicted failure; implement the minimum; verify invariants and causality; measure with common controls; run fixed ablations/stresses; inspect uncertainty and opportunity cost; independently accept/reject/pending; reproduce a proposed winner; publish; update the failure map and next hypothesis. Preserve invalid attempts and corrections without relabeling them as successful originals.

Regime labels used to explain historical losses are not automatically valid trade-time features. For fitted choices, require mature labels, past-only preprocessing, nested chronological train/selection/test folds, purge/embargo for overlapping labels and recorded search counts. Reused 2017–2026 history is not an untouched holdout. Prospective paper evidence begins only after a policy is frozen; twelve wall-clock hours cannot create a long out-of-sample trading history, and a closed market creates no new fills.

## Prioritized queue and conditional diversification

The queue contains ten experiments, subject to independent relevance and economic-deduplication review: (1) conditional joint-correlation risk budget; (2) signal-based recovery; (3) conditional per-asset strategy routing; (4) native-netted QQQ opportunity allocation; (5) portfolio marginal-cost switching; (6) causally timed gap risk-increase limit; (7) actual inverse-ETF sleeve; (8) separately validated direct-short sleeve; (9) strategy-conditioned exit-opportunity ML; (10) OpenRouter free-model portfolio agent.

Experiment 10 replaces the provisional factorial-combination experiment. A combination of successful components may be preregistered only by replacing an unstarted queue item within the ten-experiment budget after review, never as an uncounted eleventh experiment. If no component survives, do not force a combination.

The router in experiment 3 uses a tiny frozen library of existing verified recipes, one shared causal routing rule and matched signal/allocation or exposure ablations. Different assets/strategy names do not prove diversification. Measure covariance, common factor risk, synchronized stress losses and cash opportunity cost. Per-asset fitting and unrestricted recipe searches are outside this small experiment. Existing annual schedules, risk filters and rejected sleeves must be deduplicated by economics and source-produced decisions, not their names.

## Experiment 10: OpenRouter free-model portfolio agent

The user has authorized investigation of a broader model-driven action space **for research only**. The agent may propose instrument choices from a verified allowlist, target signed exposures only where the ledger supports them, existing recipe assignment, entry/exit/hold intentions and a reasoned time horizon. A deterministic validator enforces data cutoff, schema, gross/net exposure, symbol/cluster limits, cash/collateral, whole-share execution, volume, pending commitments and risk exits. It rejects unsupported actions; it never executes arbitrary code or broker tools.

Include the unchanged incumbent and an explicit idle-cash/abstention alternative in evaluation. A prose request to use capital does not require full investment: holding cash or retaining a position must remain legal whenever safety, missing information, transaction costs or signal uncertainty justify it. Never force a new position to make the agent appear active, and never increase risk to recover prior losses.

### Critical inference contamination risk

A current pretrained model may already know 2020 crashes, later market recoveries, company outcomes and modern asset survival. Supplying only a historical market prefix does **not** remove that information from its weights. Hiding dates/tickers, using no web tools and constraining the prompt can reduce obvious cues but cannot establish that it lacked future knowledge. Present-day LLM actions replayed on old markets are contaminated exploratory simulations, not proof of historically available decision skill or unseen forward performance.

Therefore separate two paths: a model-generated **deterministic native policy** can be frozen, audited and tested under the same reused-history limitations as other proposed rules; an agent that directly chooses historical actions remains exploratory until genuinely prospective frozen-policy evidence and end-to-end execution parity exist. Do not promote a direct historical LLM-action agent solely because its backtest is high. Its historical action path must never be admitted as if current model weights existed at the old date.

Avoid survivor-only asset selection, current index memberships, revised macro/news, undated summaries and present-day liquidity. Require point-in-time instrument availability and original observation times. Prompts must contain only source-backed market inputs, no broker credentials/account numbers or sensitive operational state. Responses are untrusted input.

### Free-model discovery, cost and availability

At actual start, query the public model catalog and, with the configured key without printing it, the account-limit endpoint. Choose an explicit currently free model ID whose required capabilities are supported; record catalog time/hash, model ID, response model/provider, prompt/schema hash, temperature/seed where supported, usage and response/cache identities. Zero prompt/completion pricing must be verified; do not silently fall back to a paid model or `openrouter/auto`.

OpenRouter documents random model selection for `openrouter/free`; that router is useful for availability discovery but is a changing policy. Use a pinned free ID for the experiment. A provider/model change creates a new policy identity and cannot be hidden as the same arm. Free models and endpoints can be unavailable, rate-limited or noncompliant with requested structure; stop/defer honestly rather than invent results or increase budgets.

Set an explicit request/token budget after inspecting shared account availability. The sealed campaign ceiling is24 **total dispatched free requests**, with atmost2percycle, including retries and malformed responses, further limited by actual account remaining quota and other users’ consumption; this is separate from the 40-minute tick limit and must be sealed before calls. Count failed attempts. Cache exact accepted responses and replay them instead of querying until attractive decisions emerge. The whole historical daily path will usually not fit a small free request budget; use a fixed bounded policy-generation design or prospective shadow decisions rather than silently sampling only favorable dates.

Codex and independent native reviewers remain responsible for experiment relevance, implementation and the verdict; no completed OpenRouter proposal/review is claimed before a real successful call. Review generated policies against fixed constraints rather than assuming a free model’s self-review is independent validation.

### Official OpenRouter references checked October 23, 2026

* [Model catalog](https://openrouter.ai/api/v1/models): discover actual current IDs, pricing and supported parameters. Catalog presence does not prove the authenticated endpoint is available.
* [Free router documentation](https://openrouter.ai/docs/guides/routing/routers/free-router): model selection is random among compatible available free models, and availability changes.
* [Rate and account limits](https://openrouter.ai/docs/api_reference/limits): authenticated `GET /api/v1/key` exposes account limits; rate/provider failures must be handled.
* [Pricing](https://openrouter.ai/pricing): published free-tier limits are not a guarantee of unused quota or service availability for this account. Recheck at dispatch instead of hardcoding advertised daily limits.

These references justify the dispatch design. The coordinator verified authenticated account access and catalog availability. The first synthetic structured-output probe returned rate_limit; no successful hosted proposal or trading-performance evidence is claimed. See saved provider-availability and free-model-probe receipts. Catalog and successful authentication do not imply inference capacity.

## Publication, stop and handoff

Every tick updates durable state and meaningful findings without emitting unchanged status chatter. Dashboard views show current verified default, campaign-original anchor, QQQ references, all three costs, monthly/annual/stress/recovery comparisons, attribution, uncertainty and keep/reject/pending reasons. Rejected variants remain audit evidence and leave active profile selection. Distinguish actual paper NAV/fills/costs from simulation assumptions and unrealized position P&L.

At ten experiments or the hard deadline, publish a self-contained final report with experiment counts, invalid/incomplete work, default transitions, incremental and QQQ comparisons, robustness limitations, engineering changes, unresolved capabilities, compute/model consumption and a reproduction/handoff manifest. Seal final state and pause the saved automation ID. Preserve existing dirty work, original source evidence and the separately running paper bots. This design neither disables the authorized paper executor nor enables live funds or unsupported short execution.

### Later source-bound shadow-arm checkpoint

The October23 evening manual shadow capability cycle is documented in [the free-model report](research-2026-10-23-free-model-shadow-arm.md). It changes neither this campaign's immutable original machine protocol nor promotion authorization. The inert action validator now conservatively refuses new risk while any original intent has unresolved remaining quantity; this closes an independently reproduced cross-symbol pending-risk omission. The sole fixed hosted hold probe returned downstream HTTP404, with no served contract or billing evidence. Treat hosted availability and prospective performance as unavailable. Catalog prices and successful authentication are insufficient. All later studies must pin the repaired source and preserve original evidence; the existing strategy default and paper execution remain separate.
