# Propose, backtest, compare

From the workspace root or `amat`:

```bash
./research 5
```

This creates a new frozen research workspace, measures the baseline, proposes and
backtests five distinct candidate strategies, and opens `report.html` in your
browser. Run `./research` without a number for the same five-strategy default.
No workspace paths, initialization command, or provider-chain arguments are needed.

For agent-controlled 1–60 minute strategies on actual one-minute bars, use
`./research 50 --intraday`. See [intraday controls, metrics, and execution assumptions](intraday-strategy-development.md).

```bash
./research view
```

Reopens the latest session's report. During a running session, the report updates
after each completed attempt; refresh the browser to see progress.

Use `./research status` for the current phase, completed count, and provider/token
usage without interrupting a run. Analysis tables are recorded automatically;
`./research analyze` can rebuild them for an older completed session. See
[metrics and data recording](research-metrics.md) for the exported fields and definitions.

## What five iterations means

- Five different executable strategy recipes with completed backtest measurements.
- The baseline is an additional comparison row and does not consume an iteration.
- Inner fits, controls, scenario reruns, failed attempts, and duplicate recipes do
  not count as additional strategies.
- Different recipes can be variants within the same strategy family. They are
  not claimed to be five independent sources of alpha.

The engine prefers the configured LLM proposal chain. If proposals are unavailable,
it announces built-in search fallback and records the proposal source for each
candidate. Reviews use the existing LLM chain and appear in the report; missing
reviews are explicitly marked, never replaced with invented model output.

New sessions use this order for both proposals and result summaries:

1. Gemini `gemini-3.8-flash`, Google's latest stable Flash model as of 2026-09-12.
2. OpenRouter `openrouter/free`, which selects an available free model supporting
   the requested capabilities, including structured output.
3. NVIDIA NIM `nvidia/nemotron-3-ultra-550b-a55b`, through its free hosted API endpoint.

Set `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, and `NVIDIA_API_KEY` in the launching
environment. Missing credentials, provider errors and rate limits trigger the
existing bounded fallback and cooldown handling. OpenAI is absent from this
default chain, even if `OPENAI_API_KEY` is set. These are application API calls;
this setting does not change the model or limits of your Codex/ChatGPT conversation.
Free endpoints still have provider quotas, and the research session's shared
token budget still applies. Gemini billing depends on your API account.

The default is in [the chain configuration](../../amat/configs/autoresearch/llm-chain.example.toml).
Use `./research 5 --chain /absolute/path/to/chain.toml` for an explicit override.
Model versions are pinned for reproducibility; the OpenRouter free router's
selected model can vary and is included in the stored raw provider response.
Existing sessions retain their frozen summary configuration and runtime.

Model references: [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash),
[OpenRouter free router](https://openrouter.ai/docs/cookbook/get-started/free-models-router-playground),
[NVIDIA Nemotron Ultra endpoint](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b).

The batch has at most three proposal/measurement attempts per requested strategy.
If data, compute limits, or search exhaustion prevent completion, the report
shows the actual count (for example, 3 of 5), the failed attempts, and the stopping
status. The command returns a nonzero exit code for a short batch. It never fills
the comparison with repeated strategies to claim completion.

The advanced `run-one` command still permits an explicit replay of a previously
measured recipe using its verified cached results. That manual operation is
separate from automatic batch proposal selection.

## Automatic escalation

Every candidate is compared with the **best retained strategy**, starting with
the baseline. Clearing the frozen research-score improvement margin, quality
checks, and uncertainty test automatically replaces that incumbent. The next
candidate then competes against the new incumbent. Every new proposal inherits
that retained best as its parent. It may replace the entire signal style or combine
ideas from rejected strategies while preserving best-parent ancestry.

New sessions use the exposure-matched incremental score:

`matched candidate Sharpe − matched original baseline Sharpe − 0.01 × added complexity units`

The baseline scores zero. Every research parent uses that fixed original reference.
The minimum improvement over the current incumbent remains 0.05 score units;
a candidate with two added units needs more than 0.07 matched Sharpe improvement
to clear the initial baseline margin. Matching scales the more exposed strategy
down at the normal 2 bps costs, subject to the frozen exposure tolerance. It is a
retrospective development comparison, not a tradeable benchmark. Unresolved or
invalid matches have no score and cannot be escalated. Yearly consistency,
drawdown, stress, stability and uncertainty checks still apply.

Reports and analysis exports include the matched gain, complexity deduction,
score version, reference, exposure error and prior absolute score for diagnosis.
The prior absolute formula does not influence new rankings. Existing sessions
keep their frozen rules; start a new session to use this objective.
See [scoring design and verification](exposure-matched-search-score.md).
A higher raw return alone does not override failed checks. Each report card
explains why a strategy was escalated or rejected. Escalation is advancement in
research; it does not submit trades or deploy to the executor.

## Reading the report

The comparison table shows annual return after normal costs, Sharpe, maximum
drawdown, Sharpe under 6 bps costs, research score, and escalation status. Click a
strategy name for its hypothesis and decision explanation. Expand its details
for the LLM assessment, next experiment, limitations, and links to raw metrics
and market-condition tests. The report is a standalone HTML file with no remote
assets; text and JSON versions remain available alongside it.

## Optional controls

```bash
./research 5 --no-open                # Terminal/server use; print the report path
./research 5 --built-in               # Built-in proposals, with LLM outcome reviews
./research 5 --robustness historical  # Skip the extra synthetic/scenario simulations
./research view /absolute/path/to/session
```

Extended robustness is the simple launcher's default: 11 paired scenario cases
per strategy, in addition to historical tests and existing controls. Larger batches
take longer. Workers have a 900-second timeout; the default model budget is
300,000 tokens per UTC day, shared by proposals and reviews. Override it with
`--max-model-tokens-per-day` if needed. Provider credentials continue to come from
the environment.

Sessions are stored under `../amat-research` beside the workspace checkout. The
latest pointer is `amat-research/latest-session.json`. An explicit `--workspace`
can select another new directory. Existing directories are never overwritten.
The currently configured snapshot and date defaults remain those of the advanced
CLI: development through 2022-12-30 and a separate validation boundary of 2026-08-10.
Older frozen sessions retain their original code.

See the [five-strategy verification report](simple-research-verification-2026-09-11.md)
for an executed batch and its measured results.

See [strategy flexibility and the failure library](strategy-flexibility-and-failure-library.md) for broader exploration,
research briefs, and analysis of every unsuccessful attempt.

## Operator-selected research strategy

An explicit promotion records a measured recipe as the starting baseline for
future `./research N` sessions. It preserves the complete feature, signal,
interaction and trading-policy definition. The selection and its source
experiment are frozen into each new session. The new baseline has score zero;
subsequent candidates are evaluated relative to that baseline.

```bash
./research active
./research promote SESSION --candidate FULL_CANDIDATE_DIGEST --label E73 --reason 'Operator selection'
```

Promotions are recorded under the external research directory's `promotions/`
folder, with `active-strategy.json` pointing to the selected record. Automatic
rejections, failed checks and measurements in completed sessions remain intact.
An operator-selected strategy is not represented as having passed those checks.
Existing sessions retain their original baselines. Promotion grants no executor
or live-trading authority.
