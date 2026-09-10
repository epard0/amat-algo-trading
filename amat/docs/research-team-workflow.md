# Cohesive research workflow with an OpenAI chief

## Implemented boundary

The head reviewer can now use **`gpt-6-astra` through the OpenAI Responses API**.
It synthesizes independent specialist reviews, preserves objections, and orders
the next evidence requests by decision value. It does not replace the Python
coordinator, run arbitrary tools, or control the broker.

The existing `OPENAI_API_KEY` is read from the process environment. No key is
copied into configuration, source, requests, or reports. The transport pins the
official API host, disables SDK retries, requests `store=False`, and uses strict
structured output followed by local evidence/reference validation. It records
usage and rejects refusals and incomplete responses without investment verdicts.

Official guidance: [GPT-6 Astra](https://developers.openai.com/api/docs/guides/latest-model#gpt-6-astra-whats-new)
and [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
Reasoning defaults to `high`; the CLI allows `low`, `medium`, `high`, or `xhigh`.
Reasoning tokens count within the output allowance; they are not counted twice.

## How the components work together

```text
Market snapshots + point-in-time news conditioning
                       |
             AMAT registered experiments
            (candidate + matched control)
                       |
          manifest.json + results.json + exact config
                       |
        Explicit comparison catalog, then pmteam cycle
                       |
     Research / portfolio / risk-operations reviewers
                       |
           GPT-6 Astra chief: synthesis and priorities
                       |
       Research disposition + proposed evidence backlog
                       |
          Operator-approved follow-up experiments
                       +------------------> new AMAT run artifacts

Separately: tested strategy release -> operator deployment -> executor
```

The first-pass reviewers receive the same frozen evidence but not one another's
answers. The chief receives all three responses and the original evidence. The
default dispatch concurrency remains one for quota safety; independence does not
require simultaneous API requests. Chief routing and reasoning settings are
part of the review identity and the persisted requests.

| Component | Responsibility | Current connection to the team |
|---|---|---|
| `newsdesk` | Timestamped news records and point-in-time conditioning | AMAT can consume `newsdesk export-scalars` through study `--scalars`; the chief does not automatically browse or ingest raw articles |
| `amat` | Strategy features, regime models, sizing, backtests, attribution and reports | Supplies immutable run artifacts and exact historical configuration |
| `deskreview` | Scheduled observation/description of existing strategy behavior | Complementary observer; not the portfolio-team scheduler or admission authority |
| `portfolio_team` | Evidence checks, specialist objections, chief synthesis and research backlog | Implemented CLI, registry, reports and bounded scheduled cycle |
| `executor` | Broker reconciliation, orders, positions and runtime risk enforcement | No new write interface from the team; dated accounting/capability evidence adapters remain future work |

The research team should review **strategy versions and experiments**, not approve
every intraday order. Running LLM deliberations on the order path would add
latency and provider availability risk to execution. Intraday/daily strategy
decisions remain deterministic outputs of the tested strategy and executor.

## Use it now

Run from the `amat` directory with the approved credentials already in the
environment. Resolve dependencies once with `uv sync --locked`.

Import the existing declared comparisons:

```bash
uv run --locked pmteam import-catalog configs/governance/initial-reviews.toml
```

Gemini specialists, Astra chief:

```bash
uv run --locked pmteam review v11-versus-broad-control \
  --provider gemini --model gemini-3.6-flash \
  --chief-model gpt-6-astra --chief-reasoning high
```

This requires both provider credentials and quota. The OpenAI chief cannot
bypass an unavailable specialist. There is no silent provider/model fallback.

For a single Astra analysis without Gemini calls:

```bash
uv run --locked pmteam review v11-versus-broad-control \
  --provider openai --model gpt-6-astra \
  --reasoning-effort high --single-reviewer
```

Omit `--single-reviewer` for an all-Astra committee. For explicit cost/role routing,
`--provider openai --model MODEL_ID --chief-model gpt-6-astra` uses your chosen
OpenAI model for specialists and Astra for the chief; verify the chosen model's
account access and capabilities first. No model is selected implicitly.

Use the existing `evaluate` command with the same provider/chief options to
compare a single reviewer with the team. Its current cases are development
examples, not proof of investment performance or superior reviewer quality.

## The automatic review cycle

```bash
uv run --locked pmteam cycle configs/governance/initial-reviews.toml \
  --provider gemini --model gemini-3.6-flash \
  --chief-model gpt-6-astra --chief-reasoning high --max-reviews 1
```

Each invocation:

1. Reads only the explicitly registered comparisons. It does not scan arbitrary
   files or guess which strategy is the incumbent.
2. Re-imports and verifies artifacts. Missing/partial/inconsistent evidence
   quarantines the alias; old evidence is not silently substituted.
3. Derives the review ID from the frozen evidence, policy and provider protocols.
   Completed reviews are reused without further model calls.
4. Processes at most `--max-reviews` new or interrupted reviews (default 1,
   maximum 20), within existing request/review/day limits.
5. Exports reports plus `research_backlog.json` containing the chief's requests
   in priority order, their acceptance criteria and evidence identity. The
   backlog is explicitly `proposed_not_authorized`; it is not an executable queue.
6. Records a `review_cycle` event and prints a machine-readable summary.

Failed reviews are marked `needs_operator_retry` on subsequent cycles and are
not repeatedly submitted every 15 minutes. An interrupted review may resume
within its durable two-attempt-per-role limit. The explicit `review` command
can retry only if attempts remain. Exhausted review identities are not reset by
the scheduler; preserve their evidence/history and resolve them operationally.
Changing evidence or model/protocol settings creates a different review identity,
not permission to evade daily budgets or rerun research until it looks favorable.

Deferred work, quarantines and failures make the cycle return nonzero while
preserving its completed work. `deferred_cycle_limit` can simply mean that the
next scheduled cycle should handle the remaining registered comparisons.

## Schedule it without coupling it to the executor

Templates are in `deploy/amat-research.service.example` and
`deploy/amat-research.timer.example`. They are **not installed or enabled**.
The service is a separate oneshot research process, not an executor container
extension. Its filesystem writes are limited to the governance registry/reports;
it is not given broker credentials. The supplied paths match this workspace.

After checking model quotas, the catalog and the local paths, an operator can:

```bash
uv sync --locked
uv run --locked pmteam import-catalog configs/governance/initial-reviews.toml
install -Dm644 deploy/amat-research.service.example "$HOME/.config/systemd/user/amat-research.service"
install -Dm644 deploy/amat-research.timer.example "$HOME/.config/systemd/user/amat-research.timer"
systemctl --user import-environment OPENAI_API_KEY GEMINI_API_KEY
systemctl --user daemon-reload
systemctl --user enable --now amat-research.timer
```

The environment import names variables without printing their values. It lasts
for the user-manager lifetime; credentials must be supplied again after a manager
restart, or supplied through an explicitly configured secret mechanism. Persistent
credential storage and user lingering are separate operator choices, not silently
enabled here. User-systemd sandbox support should be verified on the target host.

Inspect with `systemctl --user status amat-research.timer`,
`journalctl --user -u amat-research.service`, and `uv run --locked pmteam status`.
Stop scheduling with `systemctl --user disable --now amat-research.timer`.

For container deployment, use a separate research service with read-only strategy
code/run inputs, writable governance state and only model credentials. Do not
mount the Docker socket or broker credentials into it. No container was deployed
or executor configuration changed by this implementation.

## Register new research and complete the loop

Existing study commands persist `outputs/runs/RUN_ID/{manifest.json,results.json}`.
After both candidate and control finish, add their run IDs, exact config paths,
study indices and engine to a governance catalog using the existing example
schema. Publish the catalog only after successful artifact writes. The following
cycle will pick it up; unchanged evidence will not incur repeat model calls.

For full automatic research, the **next implementation** should add a typed,
operator-approved experiment queue. Approved jobs would invoke allowlisted AMAT
operations (matched controls, cost stress, ablations, walk-forward validation,
session-pattern/stat-arb studies) in a broker-free worker, within compute/data
budgets. The worker would publish new artifacts and declared comparisons back
to the catalog. Model-written shell commands must never become jobs directly.

News-event ideas require timestamped source evidence and a registered hypothesis,
not an LLM's unsupported confidence. Intraday/overnight commodity hypotheses also
require the correct venue session calendar, tradable instrument, realistic costs,
and untouched evaluation. A chief's request does not establish those facts.

Admission to forward shadow/paper trading is a later deterministic gate with
matched evidence, accounting parity, execution capability, risk limits and
operator authorization. Existing aggregate imports cannot pass that gate.

## Verification and current live limitation

The existing key returned HTTP 200 for the exact `gpt-6-astra` model lookup.
This confirms model access, not a completed Responses analysis. At implementation
time the shared registry had 99,014 output tokens charged/reserved against its
100,000 daily limit, mainly from earlier Gemini work. A full Astra request reserves
8,192 output tokens, so live generation was not attempted and the limit was not
raised. Missing-usage reservations are not treated as actual billed tokens.

Offline tests cover strict request shape, incomplete/refused output, key scrubbing,
usage accounting, mixed-provider role routing, chief token reservation, cache
identity, cycle idempotency, quarantine and failed-job suppression. A live chief
review should be completed when the budget permits, before enabling scheduling.

Final verification: **1,477 tests passed, one existing skip** in the full AMAT
suite; **47 portfolio-team tests passed**; strict typing passed for 108 source
files, with scoped lint/format checks clean. An actual scripted cycle reused all
four existing reviews without model calls, and a saved Gemini review replayed
unchanged. No model-generation call, scheduler activation, or broker operation
was performed during this integration.
