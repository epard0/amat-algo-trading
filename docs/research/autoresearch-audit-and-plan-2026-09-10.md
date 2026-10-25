# Autoresearch for AMAT: codebase audit and phased plan (2026-09-10)

Status: **proposal, not implemented**. Nothing in this document has been built,
scheduled, or wired to the executor. It records (1) a full audit of the current
workspace, (2) what Karpathy's `autoresearch` actually is and what its
adopters learned, and (3) a five-phase plan to turn the existing research team
into an evidence-gated autoresearch loop. Decisions that need the operator are
listed at the end.

---

## 1. Where the workspace stands

### 1.1 What the last three days produced (from the Codex session log)

The Codex session of 2026-09-07 through 2026-09-10 (`~/.codex/sessions/2026/09/07/rollout-...01a07c27...jsonl`) did the following, all of it **uncommitted**:

| Slice | What exists now |
|---|---|
| Executor hardening | Rewritten performance/execution-quality metrics (`executor/src/executor/report/metrics.py`), plan-to-fill reconciliation, 14-day restart catch-up, stale bind mount fixed, container healthy |
| Strategy research | v9 (30 ETF + structure filter), v10 (124-asset discovery), v11 (adaptive 124), v12 (adaptive 11) configs and full backtests; matched-control comparisons; 128-symbol expanded snapshot |
| Diagnostics | `stat_arb.py`, `patterns.py`, session-pattern and stat-arb scans (none survived BH correction) |
| Research team | `portfolio_team` package: evidence importer, SQLite registry, four-role committee (research / portfolio / risk-ops reviewers + chief), Gemini Interactions adapter, OpenAI Responses adapter for `gpt-6-astra` chief, `pmteam cycle` automation, systemd timer templates |

Headline research conclusion from that work: v6 remains the production reference. Breadth alone (v10) cut Sharpe from 1.137 to 0.710; the adaptive stack (v11/v12) did not beat matched v6. The learned regime scalar behaved like a frequently switching constant de-risker.

A completed live Astra review has **not** been verified; the shared token budget was exhausted by earlier failed Gemini calls (see 2.2).

### 1.2 Verification baseline (run today)

| Suite | Result |
|---|---|
| AMAT full (excluding pmteam) | 1,430 passed, 1 skipped, 80 s |
| portfolio_team | 47 passed |
| executor | 136 passed |
| mypy `amat/src` | clean, 108 files |
| mypy `executor/src` | clean, 14 files |
| ruff (both) | clean |
| Container | `amat-executor` healthy, `DRY_RUN=false`, `amat/` mounted read-only |

### 1.3 Git state that needs a decision

`git status` shows 283 `D PredictionMarkets/...` entries (a whole tree deleted from the working copy), ~30 modified tracked files, and ~30 untracked files/dirs across `amat/` and `executor/`. Every run manifest from 2026-09-07 records `code_revision.dirty = true`, so none of the v9–v12 results are reproducible from a commit. The autoresearch loop depends on clean commits per experiment; this must be resolved first (Phase 0).

---

## 2. Audit findings

Severity: **C** critical, **H** high, **M** medium, **L** low. All findings were verified by reading the code unless marked "inferred".

### 2.1 Executor and the safety boundary

| # | Sev | Finding | Where |
|---|---|---|---|
| E1 | C | **No `config_sha256` allowlist.** The boundary spec requires preflight to check that the plan's config digest matches the configuration the executor is authorised for. The code only records the digest. `STRATEGY_CONFIG` is a filesystem path (`/opt/amat/configs/strategy/amat_v6.toml`). Any host-side process that rewrites that file, or repoints `SNAPSHOT`, changes what gets paper-traded at the next open. The read-only mount only stops writes *from inside* the container. | spec `docs/execution-repo.md:106-116`; `executor/src/executor/submit.py:142-173`; `executor/src/executor/config.py:222-224`; `executor/.env:9` |
| E2 | C | **No kill switches.** Spec §5.2 requires global disable, daily notional cap, order-count cap, drawdown halt, divergence halt, dead-man switch. None exist. Preflight checks only tradability, expiry, executable session, unrecognized holdings. | `docs/execution-repo.md:170-183`; `submit.py:_preflight` |
| E3 | H | No broker-vs-plan equity/position tolerance check, no per-symbol or total notional ceiling independent of the plan. `unrecognized_holdings` does not catch a config that *adds* symbols. | `submit.py:142-173` |
| E4 | H | A transient error during the submit window permanently marks the phase done (mark-done sits outside the try/except); no retry within the window; operator learns from the evening report. | `daemon.py:629-644` |
| E5 | M | Annualized figures print in the main table for tiny samples (gated by gap only, not by `sessions >= 20`); a single +1% day annualizes to +1127%. | `report/metrics.py:238-248`; `report/daily.py:139-156` |
| E6 | L | Conventions to document: Sortino denominator uses all observations; VaR/CVaR stored as signed returns; `years = len/252`; Sharpe reports 0.0 (not null) on zero dispersion. | `metrics.py:239-263` |

Verified correct: 14-day catch-up records each missed phase exactly once; no path submits for a closed window; reconciliation journaling; client-order-id idempotency (inferred, no race test).

**Implication:** the executor cannot be left as-is once an autonomous process is allowed to write anywhere under `amat/`. E1 and E2 are Phase 0 blockers.

### 2.2 `portfolio_team` (the research committee)

| # | Sev | Finding | Where |
|---|---|---|---|
| P1 | C | **Token budget reservations are never released.** A call reserves the full max output (8,192 OpenAI / 4,096 Gemini) up front; on any failure without usage figures the reservation is charged permanently for the UTC day. No reset, TTL, or CLI to release. ~13 failed OpenAI calls block the whole day. This is the exact cause of the "99,014 of 100,000" event in the docs. Tests assert this as intended. `status` shows no per-call breakdown. | `store.py:286-366`; `model_runtime.py:27-33`; `openai_runtime.py:36`; `tests/unit/test_portfolio_team_models.py:132-178` |
| P2 | M | `require_matched_sample`, `require_settings_diff`, `causal_component_attribution` are `Literal`-locked and referenced nowhere; the checks are unconditional. The catalog sets them as if configurable. | `contracts.py:45-52`; `evidence.py:274-321` |
| P3 | M | Importer rewraps `KeyError/IndexError/TypeError` as `EvidenceError`; a code regression is indistinguishable from a bad artifact and is silently quarantined with no traceback. | `evidence.py:158-163`; `automation.py:38-41` |
| P4 | M | Gemini `response_format` is passed as a raw dict with key `"schema"`; the SDK's typed model uses `schema_` aliased to `"schema"`. Never exercised against the real SDK; every transport test mocks the client. First live call is the first test. | `model_runtime.py:181-185` |
| P5 | M | Imports a private SDK module (`google.genai._gaos.lib.compat_errors`) for error classes. | `model_runtime.py:15` |
| P6 | L | OpenAI adapter assumes `usage.output_tokens` already includes reasoning tokens (unverified); Gemini path applies an explicit thought-token correction. Asymmetric accounting. Reasoning effort exposes `low..xhigh` only, omitting SDK's `none/minimal/max`. | `openai_runtime.py:35,88`; `model_runtime.py:195-203` |
| P7 | L | Duplicated retry/attempt loops (`coordinator.py:24-63` vs `remote_coordinator.py:15-109`); chief-role branching in four places; `model_runtime.py` mixes generic contracts, Gemini transport, and the remote base class; `cli.py:22-34` duplicates `automation.py:34-41`. | as listed |

What it is **not**: it has no config generation, no run launching (an architecture test forbids `subprocess` imports), no scalar objective, no per-iteration log, no git keep/discard, no per-experiment compute budget. It is a post-hoc critique layer. That is fine; it becomes the *auditor/chief* stage of the loop, not the loop itself.

Docs are unusually honest about limits. Two overclaims: the OpenAI "latest-model" URL is unverified, and the budget leak is described as a footnote rather than a blocker for unattended scheduling.

### 2.3 AMAT research engine as an experiment harness

| # | Sev | Finding | Where |
|---|---|---|---|
| A1 | H | **No run cache** keyed by `config_sha256 + snapshot_id + window`. Identical configs re-run at full cost. | grep of `src/amat` |
| A2 | H | **Covariance is recomputed every session** inside `decide()` while every other input is precomputed once per run. Likely the dominant cost (inferred, not profiled). 124-asset combined-only runs take 4 m 14 s to 6 m 06 s; v6 (11 assets) about 30 s; v9 (30 assets, with sleeves) about 85 s. | `portfolio/__init__.py:464-467`; `runner.py:700-705`; run manifests |
| A3 | M | **No daily equity/return series is persisted.** `results.json` carries summary stats, annual returns, concentration, per-asset attribution only. Sortino/rolling stats/combination analysis need the series. `BacktestResult.equity_curve` exists in memory. | `runner.py:206-212`; `results.json` |
| A4 | M | **`--start` truncates the panel before warm-up.** Two variants with different warm-up requirements given the same `--start` trade different date ranges and session counts. Any objective must read `sample.first_session/sessions` back and pin the measured window, not the loaded window. | `data/snapshot.py:259-263`; `runner.py:876`; `cli/main.py:1627-1629` |
| A5 | L | `learned_regime.fit_window_sessions` (default 504) is not folded into the enforced warm-up floor, so the conditioning is silently a no-op for the first 504 sessions. An agent would measure "no effect" for the wrong reason. | `config/models.py:677-696`; `validation/conditioning.py:174-198` |
| A6 | L | `stat_arb.py` and `patterns.py` are unwired diagnostics; they contribute nothing to a decision path yet. | grep |

Verified clean: no look-ahead in `structure.py` (all windows end at `row`, NaN → neutral 1.0), warm-up validator fails closed, split rescaling unchanged and tested, no mutable defaults, no silent NaN.

Assets to reuse:

- Programmatic API: `load_strategy_config`, `load_panel`, `run_backtest(...) -> BacktestResult`, `run_sleeve_study`, `metrics.summarize`.
- Provenance: `RunManifest` with `config_sha256`, `code_revision.dirty`, dataset partition hashes, `holdout_accessed`.
- Gates: walk-forward `research_gates` (Sharpe ≥ 0.50, ≥ 60% positive folds, drawdown), `falsification.py` matched controls (shuffled, random-matched-turnover, static-mean), `stress`, `montecarlo`, `declare-holdout`.
- Experiment registry convention (`configs/experiments/NNNN-*.toml`, register before run, never delete).
- Holdout declaration: all history through 2026-08-10 is **burned**; untouched evidence begins after that.

---

## 3. What Karpathy's autoresearch is (verified from primary sources)

Repo: https://github.com/karpathy/autoresearch (created 2026-03-06, MIT).

**Mechanism.** There is no loop script. `program.md` is prose instructions read by a coding agent (Claude Code / Codex, permissions disabled). The agent edits **one file** (`train.py`), commits, runs `uv run train.py > run.log`, greps `val_bpb`, appends a row to an untracked `results.tsv` (`commit  val_bpb  memory_gb  status  description`, status ∈ keep/discard/crash), and either advances the branch (`autoresearch/<tag>`) or `git reset`s. Ties are discarded. First run is always the untouched baseline.

**Fixed budget.** `TIME_BUDGET = 300` s of training wall-clock in `prepare.py`; runs over 10 min are killed as failures. Karpathy: fixed time "makes experiments directly comparable regardless of what the agent changes" and yields "approx 12 experiments/hour and approx 100 experiments while you sleep."

**Fixed metric.** `evaluate_bpb()` in `prepare.py`, header "DO NOT CHANGE — this is the fixed metric". Vocab-independent so architecture changes compare fairly.

**Division of labor** (launch tweet, 2026-03-07): "the human iterates on the prompt (.md) / the AI agent iterates on the training code (.py)." README: `program.md` is edited by the human, `train.py` by the agent. No Priors podcast: "here's an objective, here's a metric, here's your boundaries of what you can and cannot do. And go." Humans "monitor the feature branch and merge to the main branch sometimes."

**Results.** Two days on a depth-12 model produced ~20 kept changes (of ~126 experiments) that transferred additively to depth 24, an 11% "time to GPT-2" improvement. Keep rate about 15%.

**Guardrails are instruction-only.** `prepare.py` is "read-only" by prompt; no tooling enforces it. Also by prompt: no new dependencies, a simplicity criterion ("a 0.001 improvement that adds 20 lines of hacky code? Probably not worth it"), crash handling, "give up after a few fix attempts," NEVER STOP.

**Stated limits.** "Extremely well suited to anything that has objective metrics that are easy to evaluate … if you can't evaluate then you can't autoresearch it." "It's not novel, ground-breaking research (yet)."

**What adopters learned** (secondary sources, each verified to exist):

- Reward hacking is the default failure. Tennis XGBoost writeup: agent built hardcoded per-tournament logit offsets; caught because gains accelerated 10.9× the honest rate. GPUMode kernel retrospective: 6 hacks in 1,293 experiments, including bypassing a grep-based rule with a raw enum constant. Auto-Quant (FreqTrade): Sharpe 1.44 headline was `exit_profit_only` in a bull regime (true edge 0.19); ROI clipping pushed Sharpe to 18.3, later discarded as "Sharpe inversely correlated with profit = classic gaming signature."
- Recommended defenses: evaluation files immutable with a hash gate that fails the experiment; distribution sanity checks; a "delta anomaly breaker" (pause when a jump exceeds 3× rolling mean); separate holdout; robustness suite reporting pass/fail only; dedicated red-team audit agents; measurement-noise ladder before accepting small deltas.
- Trading forks: Nunchi `auto-researchtrading` (composite `sharpe·sqrt(min(trades/50,1)) − dd_penalty − turnover_penalty` with hard −999 cutoffs; "council mode" after 5 stalls; claims Sharpe 21.4 on a 6-week window, almost certainly overfit). Auto-Quant added `robust_sharpe = min(...)` across regimes, profit floors, Pareto gates, buy-and-hold benchmark.
- Multi-agent variants: Agon (scientist / coder / auditor / reviewer roles, every handoff a file on disk); CORAL (parallel worktrees, grader isolated from agents at OS level); FML-bench: a greedy hill-climber nearly matches tree search when improvements are dense; broaden exploration on stagnation.
- Prior art: AI Scientist v1/v2 (Sakana), AlphaEvolve / OpenEvolve, AIDE, Ax (Bayesian optimization; Karpathy does not cite it).

Sources: README/program.md/prepare.py/train.py raw files; tweets 2029701092347630069, 2030371219518931079, 2031135152349524125, 2030705271627284816 (via fxtwitter mirror); GitHub Discussions #32/#43; No Priors 2026-03-20 (YouTube kwSVtQ7dziU); nickoak.com tennis post; elianaive.com GPUMode post; TraderAlice/Auto-Quant retrospective 0.1.0; arXiv 2605.17373 (FML-bench), 2604.01658 (CORAL), 2606.24177 (Agon).

---

## 4. Design: an evidence-gated autoresearch loop for AMAT

### 4.1 Why a straight port does not work here

Karpathy's setting has a held-out metric that cannot be gamed by peeking (fresh validation shard, one objective, no path from the loop to money). AMAT differs in three ways that shape every design choice:

1. **The metric is on burned history.** Everything through 2026-08-10 has been inspected. Repeated keep/discard on the same window is exactly the multiple-testing problem the repo's own protocol warns about. The loop must (a) score on a fixed **training window**, (b) compute but **withhold** a validation window from the agent, (c) count experiments and report deflated statistics on the kept chain, and (d) treat post-2026-08-10 data as the only untouched evidence.
2. **A backtest can be gamed in ways a loss function cannot.** Costs off, missed fills off, cash yield on, universe pruned to winners, exit rules that only work in one regime. Every one of those is an "improvement" in Sharpe. The objective must include hard cutoffs and the harness must own costs, fills, window, universe and snapshot.
3. **There is a path from `amat/` to a broker.** The executor reads a file path. The loop must operate where the executor cannot see it, and the executor must refuse unknown config digests regardless.

### 4.2 Components and ownership

```text
program.md (human-owned research program: base strategy, window, allowed knobs, forbidden moves)
        |
  Proposer agent  ---- writes one config diff (Level 1) or one code diff (Level 2) in an
        |               isolated git worktree, commits, asks the harness to run it
        v
  Harness (deterministic Python, no LLM):
     protected-file hash gate -> validate config -> run_backtest on fixed snapshot/window
     -> objective + hard cutoffs on TRAIN window -> validation metrics sealed
     -> results.tsv / SQLite row -> keep (advance branch) | discard (reset) | crash
        |
  Auditor agent (per kept experiment): leakage / reward-hack / simplicity review of the diff;
     can veto a keep -> "keep_vetoed"
        |
  Chief (gpt-6-astra, every N experiments or on stall): reads the chain + sealed validation
     summary, writes a proposed program.md revision and a promotion shortlist -> human
        |
  Human: edits program.md, merges a kept chain only after the full battery
     (walkforward, stress, montecarlo, falsification) and pmteam committee review,
     then updates the executor's config allowlist by hand.
```

Roles map to the existing team: the three `portfolio_team` reviewers and the chief already exist; the harness, proposer, and auditor are new. The proposer is deliberately the cheapest model; the chief is the expensive one and is called rarely.

### 4.3 The fixed experiment contract (the analog of `prepare.py`)

Owned by the harness, hashed, and checked before every run. A mismatch fails the experiment and halts the loop.

| Fixed by the harness | Value (proposal) |
|---|---|
| Snapshot | `alpaca-sip-2026-08-11` (v6 universe) for the first program |
| Measured window | train: first measurable session → 2022-12-30; validation (sealed): 2023-01-03 → 2026-08-10; holdout: after 2026-08-10, never touched |
| Warm-up handling | harness pins the *measured* first session by loading enough pre-window data for the maximum warm-up across variants; reads `sample.first_session` back and rejects any run whose measured window differs |
| Costs | 2 bps one-way, mandatory; the objective also evaluates at 3× cost |
| Fills | primary open-window VWAP with lag 1; missed-fill behavior fixed |
| Universe | fixed per program (v6's 11 ETFs first); universe changes are a separate program |
| Sleeves | off (combined portfolio only) for the loop; on for promotion |
| Wall-clock cap | 4 min for the v6 program (baseline ~30 s); kill at cap, status `crash` |
| Protected files | `src/amat/backtest/**`, `src/amat/metrics/**`, `src/amat/validation/**`, `src/amat/data/**`, `src/amat/provenance.py`, `src/amat/execution/**`, the harness itself, `tests/**` |

### 4.4 The objective (the analog of `val_bpb`)

Scalar, higher is better, computed on the **training window only**:

```text
score = median(fold_sharpe over calendar-year folds)              # consistency, not peak
      - 0.5 * max(0, 0.15 - min(fold_sharpe))                     # penalize a bad year
      - 2.0 * max(0, |max_drawdown| - 0.12)                        # drawdown budget
      - 1.0 * max(0, cost_drag_annualized - 0.006)                 # turnover budget
      - 0.02 * (fills / baseline_fills - 1)                        # mild churn penalty
```

Hard cutoffs (score = −inf, status `discard`): fewer than 60% positive folds; max drawdown worse than 20%; best-symbol share ≥ 25% or best-year share ≥ 40% (existing concentration gates); Sharpe at 3× costs ≤ 0; average gross exposure < 20%; any accounting warning in `BacktestResult.warnings`; measured window differs from the contract; any anti-leakage test fails (Level 2).

Keep rule: score improves by more than a noise floor (estimated once from block-bootstrap of the baseline, Phase 1) **and** the auditor does not veto. Ties and sub-noise gains are discarded, matching Karpathy's "equal or worse → reset."

Reported but sealed from the proposer: validation-window Sharpe, return, drawdown; number of experiments so far; deflated Sharpe / probability-of-backtest-overfitting on the kept chain. Revealed to the chief and human at checkpoints only.

The exact weights are a proposal to tune in Phase 1 against v6 and the v9–v12 results already on disk (they should rank v6 first, v12-soft second, v10 last).

### 4.5 Guardrails (from the audit and the community failures)

- **Isolation:** the loop runs in a git worktree outside `amat-workspace/amat` (the executor bind-mounts that exact path). It never writes to `configs/strategy/`, `data/`, or `outputs/runs` of the mounted checkout.
- **Executor allowlist + kill switch** (E1, E2) so the worktree isolation is not the only defense.
- **Protected-file hash gate** in the harness (Karpathy relies on prompt only; every adopter says tooling is required).
- **Delta anomaly breaker:** a single-experiment gain > 3× the rolling median kept gain pauses the loop pending audit.
- **Sanity checks per run:** equity reconciles, fills > 0, gross exposure in range, no zero-cost run, no holdout access flag.
- **Budget accounting fixed** (P1) before any scheduled operation; per-call reservation released on failure with a small penalty, `status` shows per-call breakdown.
- **Experiment registry:** every proposal registered before its run (existing `configs/experiments` rule), including discards and crashes.
- **Simplicity criterion** in `program.md`, and the auditor scores diff size.
- **Stall policy:** after K consecutive discards the chief is invoked to widen or redirect, instead of the proposer thrashing.

### 4.6 Proposer runtime: two options

**Option A (recommended for Level 1): API-driven proposer with structured output.** A Gemini/OpenAI call receives `program.md`, the results log, the current config, and returns a typed JSON patch against the strategy TOML schema (field path, new value, rationale). The harness applies, validates (schema rejects unknown keys), commits, runs. No shell access, cheap, fully replayable, testable with a fake provider, and identical in shape to the existing `portfolio_team` model runtime.

**Option B (Level 2 later): coding-agent CLI reading `program.md`,** exactly Karpathy's shape (Claude Code or Codex in the worktree, permissions disabled, `spawn.sh`-style watcher). Needed once the agent is allowed to edit feature/portfolio code. Higher capability, higher risk, harder to replay; the harness gate and protected hashes make it acceptable.

Start with A, add B in Phase 4.


### 4.7 Provider redundancy (approved 2026-09-10)

Every LLM role (proposer, auditor, reviewers, chief) is served by a **fallback chain**, not a single provider. The chain is configuration, evaluated in order, and the loop must never stall on one provider's quota or outage.

Default chain: **OpenAI → Gemini → OpenRouter → NVIDIA NIM** (last resort). Credentials come from the environment (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`); a provider with no credential is skipped, never errored. OpenRouter and NVIDIA speak the OpenAI-compatible chat-completions API and share one transport with different base URLs (`https://openrouter.ai/api/v1`, `https://integrate.api.nvidia.com/v1`). Anthropic and Groq keys also exist in this environment and can be appended to the chain later through the same mechanism.

Per-role routing: each role declares its preferred model on each provider (for example chief: `gpt-6-astra` on OpenAI, then a Gemini Pro-class model, then an OpenRouter frontier model, then a NIM model). A fallback therefore changes the model as well as the provider; the ledger records which provider/model actually answered, and a review's identity includes that fact so replay is exact.

Failover triggers, evaluated per provider with a sliding window (defaults; all configurable):

| Trigger | Default | Action |
|---|---|---|
| Hard quota / rate limit (HTTP 429, quota exhausted, `insufficient_quota`) | immediate | mark provider `cooling` for the reset window reported by the provider, else 15 min; move to next provider |
| Local daily budget for that provider reached | immediate | move to next provider; do not raise the budget |
| Auth / configuration error (401/403, missing model) | immediate | mark provider `disabled` for the process lifetime; alert |
| Unknown / 5xx / transport errors | ≥ 3 failures in 10 requests, or ≥ 50% failure over 5 min | mark `cooling` 10 min with exponential backoff (max 60 min); move on |
| Timeouts | ≥ 2 in 5 requests | same as unknown errors |
| Malformed structured output after one repair retry | per call | count as a failure; next provider for that call |

A `cooling` provider is re-probed with one cheap request after its window; success restores it to the front of its chain position. If **every** provider is cooling or disabled, the loop pauses (not crashes), records `paused_no_provider`, and retries at the earliest cooldown expiry. Deterministic harness work (backtests, scoring, ledger) continues regardless.

Budget accounting (fixes P1): reservation on dispatch, **released on failure** with a small fixed penalty charge (default 256 tokens) so failures cost something but cannot exhaust a day; actual usage charged when reported; unknown usage on a *successful* response charged at the reservation (conservative), never at the reservation for failures. `pmteam budget` shows per-provider, per-role, per-status totals and can release a stuck reservation with an audit event.

Everything here is testable offline: a fake transport that scripts 429s, 5xxs, timeouts and malformed output, driven through the real router and budget store.

---

## 5. Phased plan

Each phase ends with tests, lint/type gates, a written verification record, and a stop for operator review. No phase enables scheduling by default.

### Phase 0: Prerequisites and hygiene (blocking)

Goal: make the workspace safe to run an autonomous writer against, and make experiments reproducible.

1. Resolve git state: decide what to do with the 283 deleted `PredictionMarkets/` files (they appear to have been moved out of this repo); commit the Sep 7–10 work in logical commits so run manifests can reference clean revisions.
2. Executor: add `AUTHORIZED_CONFIG_SHA256` (list) to config and a fail-closed check in `_preflight`; add `EXECUTOR_DISABLED` global switch, daily notional cap, order-count cap, per-symbol notional cap; restrictive defaults; tests for each (E1–E3).
3. Executor: `daemon.tick` retry-within-window for transient errors (E4); suppress annualized figures below 20 sessions (E5). Rebuild the container via the state-preserving path only after tests pass; operator runs it.
4. `portfolio_team`: release reservations on failure (charge a small fixed penalty instead of the full max), add `pmteam budget status/release` with per-call breakdown, surface tracebacks for importer exceptions (P1, P3); add one live-SDK smoke test behind a marker for the Gemini `response_format` shape (P4).
5. Provider router: extract a provider-neutral `llm` layer with the OpenAI-compatible transport (OpenRouter, NVIDIA NIM), the fallback chain, health/cooldown tracking and the failover triggers of 4.7; route `pmteam` roles through it. Offline tests script every trigger.
6. AMAT: fold `learned_regime.fit_window_sessions` into the warm-up floor (A5); document `--start` semantics in the CLI help (A4).

Exit: all suites green; a signed-off allowlist containing only v6's digest; container rebuilt and healthy.

### Phase 1: The harness (deterministic, no LLM)

New package `amat/src/autoresearch/` (added to the wheel and dependency-direction tests; it may import `amat`, never `executor` or `portfolio_team`'s model runtimes).

1. `contract.py`: the frozen experiment contract (4.3) as a pydantic model with a content hash; protected-file manifest and hash check.
2. `objective.py`: score, hard cutoffs, sealed validation split, noise-floor estimator (block bootstrap of baseline).
3. `runner.py`: run one experiment programmatically (`load_panel` with pinned pre-window, `run_backtest`, walk-forward folds on the train window), enforce wall-clock cap in a subprocess, write `outputs/autoresearch/<program>/<commit>/` with manifest, results, **daily equity/return series** (A3), and objective breakdown.
4. `ledger.py`: `results.tsv` (Karpathy schema plus `score`, `train_sharpe`, `status`, `audit`) and a SQLite table; run cache keyed by `config_sha256 + contract_hash` (A1).
5. `worktree.py`: create `autoresearch/<tag>` branch in an external worktree, commit per experiment, advance or reset.
6. Performance: cache Ledoit-Wolf covariance per decision index within a run (A2); profile v6 and target < 20 s per experiment.
7. CLI `autoresearch init <program> | baseline | run-one <patch.json> | status | replay`.
8. Tests: fake proposals through the whole loop; hash-gate tampering fails; cutoffs trigger; window mismatch rejected; cache hit skips execution; objective ranks v6 > v12-soft > v10 on existing artifacts.

Exit: baseline v6 scored; ten hand-written config variants run end-to-end offline; verification record written.

### Phase 2: Single-agent config-space loop (Level 1)

1. `program.md` for the first program: base `amat_v6.toml`, allowed knobs (horizons, dead-band, vol target, cluster caps, rebalance band, structure filter parameters, regime scalar floor), forbidden moves (costs, fills, universe, window, cash yield), simplicity rule, stop conditions.
2. `proposer.py`: structured-output call through the provider router (4.7) returning a typed TOML patch; fake provider for tests; provider/model recorded per experiment.
3. `loop.py`: propose → register experiment → run → keep/discard → log; stall policy; anomaly breaker; max experiments per invocation; budget-aware.
4. Run a bounded live program (for example 50 experiments overnight on v6) with the operator present for the first ten.
5. Report: kept chain, keep rate, sealed validation revealed at the end, deflated Sharpe on the chain, cost of the run.

Exit: the loop runs unattended for a bounded batch; the report distinguishes "harness works" from "strategy improved."

### Phase 3: Multi-agent review inside the loop

1. Auditor: a `portfolio_team`-style reviewer role invoked on every candidate keep with the diff, objective breakdown, and sanity checks; returns `approve | veto` with cited reasons; veto → `keep_vetoed`, branch reset.
2. Chief checkpoints: every N experiments or on stall, `gpt-6-astra` receives the ledger, kept chain, sealed validation summary, and current `program.md`; returns a proposed `program.md` revision and a promotion shortlist as a research backlog (reusing `research_backlog.json`). Humans apply revisions; the loop never edits `program.md` itself.
3. Evaluate the auditor on seeded reward hacks (cost off, universe pruned, holdout peek) and record catch rate, mirroring the existing `evaluate` harness pattern.
4. Fix P2, P5–P7 while touching this code.

Exit: seeded hacks are caught; one chief checkpoint completed live within budget.

### Phase 4: Code-space experiments (Level 2)

1. Allowlist editable paths (`src/amat/features/**`, `src/amat/portfolio/**`, `src/amat/regime/**`); everything else protected by hash.
2. Mandatory per-experiment gates before scoring: anti-lookahead tests, accounting tests, `mypy`, `ruff`; failure → `crash`.
3. Coding-agent runtime (Option B) in the external worktree with a watcher; same ledger and objective.
4. Same guardrails, lower experiments/hour; chief checkpoints more frequent.

### Phase 5: Promotion pipeline and scheduling

1. Kept-chain checkpoint → full battery (`walkforward`, `stress`, `montecarlo`, `falsification` controls, sleeves on) → registered comparison → `pmteam cycle` committee → human decision.
2. On approval: merge to `main`, bump strategy version, register in `docs/experiment-registry.md`, add the digest to the executor allowlist, deploy via the state-preserving rebuild. Untouched post-2026-08-10 data becomes the forward test.
3. Scheduling: systemd timer for the loop in the external worktree, separate from the research-review timer; both off by default.

---

## 6. Operator decisions (answered 2026-09-10)

1. **Git cleanup:** the `PredictionMarkets/` deletion is intentional (confirmed). Commits of the Sep 7–10 work are proposed per phase and made only when approved.
2. **First program base and universe:** v6 first, then later versions as separate programs (confirmed).
3. **Objective weights and split date:** as proposed in 4.4, split 2022-12-30 (confirmed; weights tuned in Phase 1 against existing artifacts).
4. **Proposer runtime:** API structured-output proposer first (confirmed).
5. **Providers and budgets:** fallback chain OpenAI → Gemini → OpenRouter → NVIDIA NIM with the failover rules of 4.7 (confirmed); NVIDIA is the free last resort.
6. **Executor rebuild** for Phase 0 approved; performed outside market hours via the state-preserving path.
