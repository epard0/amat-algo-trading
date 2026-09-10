# Portfolio team: offline and model shadow reviews

Update: OpenAI Responses and an explicitly routed `gpt-6-astra` chief are now
supported, along with a bounded `pmteam cycle` command. See the
[cohesive research workflow and scheduling guide](research-team-workflow.md).
The scheduler templates are not enabled and no executor integration is deployed.

Implemented on 2026-09-10: artifact import, SQLite registry, deterministic evidence
checks, four-role coordinator, scripted fake provider, policy resolution, and
immutable JSON/Markdown exports. The initial catalog contains six historical
runs and four declared comparisons.

The [v1 design](portfolio-team-v1-implementation.md) now also has an explicit
Gemini adapter, bounded requests, durable usage reservations, and a single-reviewer
baseline. The recurring watcher, discovery workers, admission service, and executor
integration remain planned. Fake-provider reports remain labeled simulations;
Gemini reports are labeled model interpretation of historical evidence.

## Run the workflow

From the `amat` directory:

```bash
uv run --locked pmteam import-catalog configs/governance/initial-reviews.toml
uv run --locked pmteam review v11-versus-broad-control --provider fake
uv run --locked pmteam status
uv run --locked pmteam show REVIEW_ID
uv run --locked pmteam replay REVIEW_ID --offline
uv run --locked pmteam review v11-versus-broad-control --provider gemini --model gemini-3.6-flash
uv run --locked pmteam review v11-versus-broad-control --provider gemini --model gemini-3.6-flash --single-reviewer
uv run --locked pmteam evaluate configs/governance/review-evaluation.toml --provider fake
```

From the parent workspace, add `--project amat` to `uv run` and `--root amat`
after `pmteam`. `REVIEW_ID` is the content-derived ID printed in the report path
and listed by `status`; it is not a strategy version or comparison alias.

The other comparison aliases are `v9-versus-incumbent`,
`v10-versus-incumbent`, and `v12-versus-incumbent-needs-matched-run`.

Gemini requires `GEMINI_API_KEY` and an explicit model ID. Only an allowlisted
evidence view is sent: measured facts, selected attribution/annual summaries,
comparison metadata, provenance, checks, and earlier role assessments for the
chief. Raw source files and filesystem paths are not sent. No web/broker/shell
tools are available to the model. The provider does not silently switch models.
The three first-pass reviewers receive independent inputs, but dispatch is
serialized by default to fit constrained provider quotas. Set
`--concurrent-requests 2` or `3` only when the account supports it; the chief still
waits for every first-pass review. Concurrency is part of the recorded settings.

Imports continue through independent comparisons and return a nonzero exit code
if any are quarantined. A bad re-import quarantines that alias instead of silently
reviewing its older evidence. Previous bundles and decisions remain in history.
Re-importing repaired evidence restores the alias to a verified bundle. Identical
evidence, policy, provider and protocol produce the same review ID.

## What is persisted

`data/governance/pmteam/registry.sqlite3` stores exact source bytes, normalized
bundles, comparison bindings, role attempts, decisions, and timestamped events.
SQLite WAL is enabled. One process holds `writer.lock`; concurrent CLI access
fails clearly rather than running competing coordinators. This is single-host
operation, not a distributed lease system.

Each completed review exports to `outputs/governance/REVIEW_ID/`:

- `report.md`: verified comparison table, deterministic checks, reviewer
  assessments (four for a committee, one for the baseline), missing-evidence
  requests, and limitations.
- `evidence.json`: sample metadata, fact references, selected study payloads,
  manifests, and configuration differences.
- `decision.json`: proposed/effective disposition, every assessment, hashes, and
  the permanent `execution_authority=none` value.
- `policy.json` and `protocol.json`: the exact offline policy and role instructions.
- `artifacts/SHA256`: exact imported manifest, results, and available config bytes.
- Real-provider reviews also export `model_calls.json` and hashed request/response
  records, including reported usage, latency, and sanitized provider errors.

Exports publish atomically and do not overwrite differing existing files.
Offline replay re-exports a committed review without provider calls. Completed
role responses survive restart; an unfinished attempt consumes its attempt
number and can retry only within the two-attempt-per-role limit. Exhausted
reviews remain failed instead of synthesizing a decision from missing reviewers.

Back up the database with SQLite's backup interface, or stop all writers before
copying its complete database/WAL state. The exact source blobs are stored inside
the database; report exports are additional copies.

## How to interpret the results

The fake provider requests evidence for the checks marked missing or failed.
Its disposition is not evidence that a language model independently analyzed or
rejected the strategy. It exists to exercise independent first passes, chief
synthesis, citations, policy enforcement, idempotency, and recovery.

The offline policy deliberately cannot shortlist a candidate. The supported
study envelope contains aggregate results, not the complete admission evidence
required by the design. All reviews have `readiness_status=blocked` and no
execution authority. This is distinct from the running paper executor's status;
the review service does not query or change that executor.

The current configuration's bytes count as historical evidence only when their
hash matches the manifest. Matching TOML syntax/identity does not establish that
today's executable loader supports every legacy field. Source market-data
partitions, accounting/parity test runs, and capabilities are explicitly unverified.

The report distinguishes return-interval hit rate from completed-trade win rate.
It suppresses deltas for mismatched samples, retains raw configuration differences,
and makes no causal claim about individual features. The saved v12/v6 comparison
intentionally exercises the missing matched-control case.

## Implemented policy versus design examples

The executable research-only policy is `portfolio_team.contracts.Policy`, exported
verbatim with each review. The earlier `portfolio-team-v1-policy.example.toml`
describes the broader admission workflow and is **not loaded** by this CLI.
Watch scheduling and positive admission gates remain unimplemented.

Real-provider limits live in `ModelSettings` and are recorded with each request.
Defaults are 90 seconds per attempt, 480 seconds per invocation, two attempts per
role, 4,096 generated tokens per Gemini attempt (8,192 for OpenAI),
800,000 input/32,768 output tokens per
review, and 2,000,000 input/100,000 output tokens per UTC reservation day. Input
reservations use prompt/schema byte counts plus framing allowance; they are
conservative estimates, not exact tokenization or a dollar spending guarantee.
Reported usage replaces a reservation when available, including thinking tokens
in output usage. Missing usage, crashes and timeouts retain reservations. If a
provider reports more than reserved, that actual usage is charged before any
further dispatch. No provider price table is assumed, so no dollar cost is claimed.

`--request-timeout` and `--review-timeout` can reduce or adjust the deadlines
within the validated maxima. The SDK's hidden retries are disabled. Authentication
and invalid-request errors do not automatically retry; transient errors and one
schema-repair attempt share the same per-role attempt budget. A local timeout
stops waiting and cancels the client request; it cannot guarantee the provider
ceased computation or billing. No incomplete meeting produces an investment
verdict. Review failures and usage appear in `status` and the registry.
Rate-limit responses use a 15-second retry delay within the review deadline.
The coordinator does not claim to implement a distributed quota limiter.

The `evaluate` command uses frozen comparisons and keeps expected issue labels
outside inference requests. It measures reference coverage for deterministic
checks, a single reviewer, and the full committee. The supplied two cases are
development examples, **not** a hidden benchmark. Extra cited references are
unadjudicated, not automatically false positives. Human semantic review and a
larger held-out fault corpus are still required to claim improved review quality;
none of these measurements establishes improved trading returns.
An incomplete evaluation still writes its report, but the CLI returns a nonzero
status. Failed reviews have no coverage score; they do not silently count as zero.

Provider implementation references: [Google Gen AI SDK](https://googleapis.github.io/python-genai/)
and [structured outputs](https://ai.google.dev/gemini-api/docs/structured-output).
The adapter uses the Interactions API with `store=False`, minimal thinking, and
SDK retries disabled. The legacy generation endpoint rejected the submitted
schema; the selected model completed a real smoke review after switching to
Interactions. The transport uses a reduced schema without `additionalProperties`
or collection/string length constraints; the local Pydantic validator still
enforces those requirements. Both schemas are recorded. Endpoint compatibility
and usage accounting also have mocked SDK-boundary regression tests.
Request-specific singleton enums constrain role and evidence identity; the local
validator independently checks both. The adapter handles the Interactions SDK's
separate HTTP-error hierarchy, including sanitized, retryable 429 responses.
