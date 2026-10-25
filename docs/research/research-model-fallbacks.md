# Research model fallback policy

New research sessions use `configs/autoresearch/llm-chain.example.toml` for both proposals
and reviews. Routine proposals and summaries use this order:

1. Gemini `gemini-3.8-flash`
2. Gemini `gemini-3.5-flash-lite`
3. Gemini `gemini-3.1-flash-lite`
4. OpenRouter `stealth/space-bunny-alpha` (free preview)
5. OpenRouter `qwen/qwen3.8-27b:free`
6. OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free`
7. Groq `openai/gpt-oss-120b`

Gemini accepts `GOOGLE_API_KEY` or `GEMINI_API_KEY`; Google takes precedence when
both are set, consistently with its SDK. The SDK may still print its informational
key-precedence warning. Gemini and Groq follow the account's billing and quota;
only the OpenRouter routes above explicitly require free models. OpenAI is used for coding first; routine research stays on the chain above. The separate portfolio committee policy is unchanged.
`AMAT_MODEL_CHAIN` and explicit CLI chain files override the default.

Model capacity errors, invalid outputs, and timeouts do not disable sibling models.
Authentication and explicit account-quota failures still stop that provider.
Model cooldowns persist across research resumes and respect Retry-After when supplied.
Calls do not sleep or repeatedly retry a rate-limited endpoint. OpenRouter supports
bounded format fallback: strict JSON schema, JSON object, then schema instructions
without a response-format parameter. Local schema and research evidence validation
still apply at every level. There are at most two format retries per target and one
existing proposal-repair attempt per chain invocation.

Every attempt is durably reserved. Explicit HTTP request rejection (400, 401, 403,
404, 422, 429) or unsupported schema releases the token reservation if no usage or
output was returned. Successful usage is charged normally; timeouts, server errors,
unknown transport failures, and interrupted calls retain conservative reservations.
The daily token limit still applies to free models. Existing historical charges are
not rewritten. Exhausted routes report provider/model/error codes in review artifacts
and CLI output without exposing keys or raw provider errors.

## Existing sessions

Runs freeze their configuration. After the current batch exits, apply the new chain
to subsequent built-in strategy search and reviews from the `amat` project directory:

```sh
./research advanced loop /absolute/path/to/session --iterations 100 \
  --summary-chain configs/autoresearch/llm-chain.example.toml
```

To use model proposals as well, add
`--chain configs/autoresearch/llm-chain.example.toml`. To retry a saved unavailable
review, use:

```sh
./research advanced summarize /absolute/path/to/session --experiment 1 \
  --summary-chain configs/autoresearch/llm-chain.example.toml
```

A running process must restart to load code changes. An already exhausted daily
ledger still waits for UTC rollover; changing chains does not bypass the budget.

Model IDs checked against the [Gemini catalog](https://ai.google.dev/gemini-api/docs/models)
and [OpenRouter free catalog](https://openrouter.ai/collections/free-models) on
2026-09-27. Availability and account access can change independently of this policy.

New daily sessions execute their coordinator from the frozen runtime immediately
after initialization, so later checkout edits cannot interrupt or alter the run.
Reviews allow 60 seconds per provider attempt. Their evidence includes bounded
control warning examples and fill counts; failed controls are treated as incomplete
evidence rather than as proof of economic timing sensitivity.

## Coding and observability update (September 30, 2026)

Research and shared-policy coding requests use `coding_targets`: GPT-5.6-luna,
Gemini Flash, Groq GPT-OSS, then Space Bunny. Routine research retains its existing ordering. Complex research and crash
diagnosis use Luna, Gemini, Groq, then Space Bunny as well. Shared advisory/committee OpenRouter calls also default to
Space Bunny; shared OpenAI complex routing remains unchanged. Empty or absent coding routes preserve legacy complex/target fallback
semantics and serialized chain digests. Frozen sessions retain their own policies.

`./research status` includes model outcome counts. `./research status --watch`
refreshes every two seconds until the writer exits (Ctrl-C stops watching). The advanced JSON status and
analysis monitoring export include `models` and `call_timeline`: requested/served
model, purpose, task class, experiment, phase, tokens, timing, and errors. Router
events correlate each logical request with a trace ID and record target selection,
credential/cooldown skips, compatibility retries, local repair, and exhaustion.
Diagnostics contain metadata only. An unsettled call with no active writer is
labeled interrupted; reserved budget remains charged. Missing historical metadata
remains null. Token reservations are not dollar-cost estimates.

The exact requested model ID is listed as free by
[OpenRouter](https://openrouter.ai/stealth/space-bunny-alpha). No live inference
quality or availability certification is implied by configuration or unit tests.


## Evaluator and learning cycle

New default research runs register each hypothesis, then execute proposal critique,
measurement, deterministic comparison, outcome judgment, and summary. A judge uses
`judge_targets` (Groq GPT-OSS, Gemini, then Space Bunny), excluding the exact proposer model
when known. Distinct model IDs do not guarantee independent model families.
Legacy chains with no judge targets record the evaluator as unavailable.

Critiques and outcome judgments are advisory. Schema and evidence-ID validation
reject invented references, pre-measurement support, and supported judgments for
non-retained experiments. Crashes require an inconclusive assessment. Judge calls
use the same daily budget and cooldown machinery. Missing models and exhausted
budgets are recorded without overturning measured decisions. Each stage is cached
by experiment ID; outcomes become bounded lessons for the next proposal.

The report includes expandable judgment records, a model table, correlated call
timeline, and routing events. It is a recorded snapshot; use status watch for live
reads. No external tracing service or raw-prompt export is enabled. Read-only
monitoring uses a consistent SQLite snapshot and never acquires the writer lock.


Controller uses the complex research route and now owns normal research initiation.
See [Controller](research-controller.md) for the planning/launch/inspection cycle.
