# Controller: research oversight

`./research run` and the numeric shortcuts now enter Controller before launching
research. Controller is one model-driven supervisor, using the existing router and
its own durable audit ledger. It owns the daily or intraday campaign until a limit,
stop decision, unavailable provider, or research failure ends the invocation.

## Usage

```sh
# Up to 5 strategies, automatically planned and launched.
./research run 5

# Guide the objective and allow up to 4 separately assessed batches.
./research run 8 --max-batches 4 \
  --brief "Investigate whether the retained signal survives a simpler causal ablation."

# Intraday uses the same Controller entry point.
./research intraday 6 --max-batches 3

# Inspect a plan without starting backtests.
./research run 5 --plan-only

# Read a specific previous session as initial evidence.
./research run 5 --ledger /absolute/path/to/previous-session

# Resume a Controller-managed daily session. Saved campaign limits/routes are restored.
./research run --resume --workspace /absolute/path/to/session

# Resume a Controller-managed intraday session.
./research intraday --resume --workspace /absolute/path/to/session

./research status
./research report
```

`--workspace` names the research session, outside the project checkout. Controller
stores its separate audit in the adjacent `<session-name>-controller` directory.
Legacy low-level commands remain available through `./research advanced start` and
`./research advanced loop`. Controller resume requires its saved audit; it does not
silently take over a historical session that was created without Controller.

## What Controller knows and does

The initial context contains the operator's objective, selected mode, baseline
configuration, applicable promotion, feature catalog or intraday recipe schema,
training/evaluation boundaries, model budgets, experiment allowance, and available
actions. If available, the latest research ledger supplies initial development
evidence; `--ledger` selects another source explicitly.

Controller can:

1. Inspect specific experiment IDs through a read-only evidence adapter.
2. Launch a batch with a concrete brief, batch size, rationale, cited evidence, and
   stopping condition.
3. Inspect measured outcomes, learn from the saved judgments, and launch a follow-up
   batch in the same session with a revised brief.
4. Stop and explain why, or report a blocked provider, budget, or research failure.

The Controller chooses **the research brief and batch size**. Operator-supplied data,
research program, robustness settings, budgets and evaluation contract remain fixed.
Those parameters are included in its context and recorded alongside each plan. It
cannot rewrite experimental evidence, alter costs or data partitions, submit broker
orders, or promote a strategy implicitly.

Daily follow-ups preserve the incumbent and frozen program. Updated guidance is
passed as data to the frozen subprocess; the research writer records that guidance
in its own ledger and includes it in the next proposal context. No shell command is
constructed from model output. Built-in daily search remains deterministic: prose
guidance applies to model proposals, not to the built-in candidate generator.

Intraday now accepts a research brief and bounded batches. It preserves the incumbent
between batches and defers 2025 validation until all configured development iterations
have finished. Controller only receives development measurements, even when the final
report contains validation fields. If Controller stops before the full intraday search
is finished, that partial search does not expose validation results.

## Limits and recovery

- The strategy count is a campaign allowance, not a new allowance per batch.
- Default maximum: 3 batches; configurable with `--max-batches` (1–20).
- Controller has a separate daily reservation budget of 100,000 model tokens,
  configurable with `--controller-model-tokens`.
- Research retains `--max-model-tokens-per-day`, default 300,000.
- Planning/inspection steps are also bounded: `3 * max_batches + 2` across resumes.
- These budgets use the existing conservative token reservation accounting; they
  are not estimates of dollar cost. No budget increases occur automatically.
- A missing Controller model does not bypass Controller and start research anyway.
- Each launch is claimed before execution. On explicit resume, an interrupted
  launch is reconciled against read-only evidence and consumes its previously
  reserved iteration allowance. It is not automatically replayed.
- A partially initialized session without a readable research ledger cannot be
  resumed automatically. The failure and pending launch remain in the Controller audit.

Controller uses the complex research route: Luna, Gemini, Groq, then Space Bunny.
Gemini also precedes Space Bunny in coding, general utility, and judge fallbacks.
Explicit route overrides and historical frozen configurations remain authoritative.

## Audit and observation

The Controller audit directory contains:

- `task.json`: configured context, operator objective, and model routing policy.
- `evidence-N.json`: the exact development evidence snapshot used for a decision.
- `decision-N.json`: brief, parameters, rationale, evidence hash, and model provenance.
- `controller.json`: current/final status, batches, model calls, and charged tokens.
- `research.sqlite3`: Controller's own model-call and event audit, separate from the
  experimental research ledger.

Daily `./research status` includes Controller status and audit location. Console output
reports planning and dispatch progress for both modes. Model calls use metadata-only
persistence; structured task, evidence, and decision artifacts are deliberately saved
for review. Research ledger access uses SQLite `mode=ro` and `query_only=ON`; it never
opens the research writer's recovery constructor.

## Strategy extension contract

New daily Controller/start sessions freeze `extensions_only=true` and the existing
`strategy-development/9` contract, which supports both candle and symbolic genetic
adapters. Candidates preserve the baseline signal, entry model, allocator, universe,
risk controls and evaluation assumptions. An experiment changes one component:
bounded parameter tuning, one registered attenuation feature, or one genetic modifier.
Unrelated retained overlays must be copied. Genetic modifiers retain the existing
adapter's maximum 0.25 effect and cannot turn a zero baseline score into a position.
The proposer and research loop validate the parent-relative edit; process launch and
the worker also check that the baseline signal has not been replaced.

Intraday extends its existing E73-inspired recipe. Original signal terms and
interactions remain intact across generations, as do the universe, direction,
combination rule, allocation and daily risk caps. An experiment may tune at most two
scalar parameters or add, tune or ablate one supporting term from the minute feature
registry. No more than two added terms are allowed, each with absolute weight at most
0.25. This mode does not execute the daily genetic adapters. Both model proposals and
execution are checked, and the built-in fallback now changes one parameter while
preserving all retained terms and interactions.

Every proposal should explain its parent-relative change, mechanism and falsification.
Ablations establish whether an added component earns its complexity. Full replacement
signals, standalone invented strategies, and a parent ID attached to a rewritten recipe
are outside this contract. These are new-session rules; historical frozen runtimes and
advanced legacy programs retain their original contracts. Start a new Controller session
to use the new policy rather than resuming an older frozen search.
