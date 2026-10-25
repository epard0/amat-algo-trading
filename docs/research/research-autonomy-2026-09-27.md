# End-to-end research development

The objective is automatic strategy development from the selected v13 reference,
with reproducible evidence and separate deployment authority. It is not complete.
The ordinary interface remains `./research run N`, `status`, and `report`.

## Implemented in this increment

New daily sessions independently compare the snapshot session dates against the
version-pinned `exchange_calendars` XNYS schedule before initialization completes.
The date/open/close schedule and package version are frozen with the program.
This checks market-session coverage, not individual symbol coverage, price quality,
or when historical information became available. It is not exchange certification.
The selected v13 reference snapshot matches all 2,665 sessions from 2016-01-04
through 2026-08-10. Old frozen sessions are not rewritten.

A new session freezes `configs/autoresearch/engineering.json`. After a batch with
at least three attempted candidates and no retained improvement, the workflow may
request one engineering proposal. The default limit is one attempt per session.
GPT-5.6-luna receives the current parent recipe, compact development outcomes and
four allowlisted source modules. Provider requests use the existing budget/cost
ledger. Raw error text, later validation outcomes and credentials are not included.
Built-in-only runs and old sessions without this policy make no engineering calls.

The engineer may abstain or supply complete replacements for at most two permitted
modules. The application checks original hashes, allowed paths, uniqueness and
Python syntax, then saves replacement files, a diff, provenance and a manifest in
`SESSION/engineering/REQUEST/proposal/`. It never imports or runs generated code.
The checkout and frozen runtime remain unchanged. Claiming an attempt is durable;
provider failure or interruption cannot trigger an unbounded retry loop.

Status is `awaiting_isolated_validation`, not approved. AST parsing is not a sandbox.
There is currently no automatic application, execution, strategy backtest or
promotion of generated code. Test suggestions from the engineer are advisory.
The main report and ordinary status output expose the engineering result.

## Next implementation order

1. **Isolated evaluation worker.** Run generated code in a disposable, version-pinned
   environment with no broker/model credentials, no network, no Docker socket,
   read-only inputs, resource/time limits and no writable checkout mounts. Use a
   trusted harness and mandatory tests outside the editable tree. Record image,
   source, dependency, fixture and output hashes. Failure feeds a bounded repair
   request; success grants only permission to run a development experiment.
2. **Code-aware experiments.** Give each candidate an identity including parent,
   code patch, data, execution model and evaluation contract. Compare the original
   parent and patched candidate under identical periods/costs. Preserve compatible
   APIs, causal prefix invariance and missing-data behavior. Never mutate an active
   session's frozen controller. Material validator/API changes require a new
   versioned program, with both baseline and candidate evaluated again.
3. **Independent validation and lifecycle.** Separate discovery, development keep,
   locked validation, risk approval, sizing, paper observation and deployment.
   A model cannot supply a passing gate value. Persist exact evidence and reason
   codes for every transition. A development winner is not a statistical discovery.
   Keep finite study families and a global trial registry across restarts/programs;
   do not repeatedly reuse a final holdout until a target is reached.
4. **Close monitoring feedback.** Convert deduplicated operational/regime/cost flags
   into prioritized research requests. Monitoring may request investigation or a
   deterministic risk reduction, but it cannot invent a replacement strategy or
   grant approval. Finish account corporate-action coverage and collect the actual
   observation window; elapsed time cannot be simulated by implementation tests.
5. **Broaden research capabilities.** Connect admitted web evidence, point-in-time
   data adapters and controlled new features only after the above tests pass.
   News sentiment, market scanning and crypto execution keep their existing gates.

## Roles and delegated control

| Role | May decide | Cannot decide |
|---|---|---|
| Research coordinator | Which eligible hypothesis to test next, priority, resource allocation inside frozen limits | Rewrite objective, dates or approval rules |
| Evidence/data bot | Retrieve allowed sources, identify coverage gaps, propose adapters, retain admitted evidence | Treat current news as historical point-in-time evidence |
| Strategy designer | Mechanism, feature recipe, parameter changes and explicit falsification | Assert profitability or statistical approval |
| Research engineer | Isolated source edits and bounded repairs using Luna | Change evaluator, risk caps, costs, execution or credentials |
| Experiment worker | Execute trusted test/backtest protocol and produce measurements | Choose favorable periods or drop failed trials |
| Reviewer | Challenge assumptions, request ablations, reject unsupported conclusions | Turn model agreement into statistical independence |
| Risk/sizing service | Enforce portfolio exposure, liquidity, capacity and sizing rules | Bypass failed validation |
| Monitor | Detect persisted incidents and queue new research | Promote its own replacement strategy |

Routine evidence formatting uses Groq/OpenRouter free defaults. Coding and complex
review use Luna. Routing follows task difficulty and measured quality, not the
number of bots. One orchestrator owns the loop; bots return typed artifacts.
The LangGraph orchestrator/worker and evaluator/optimizer patterns fit this design:
https://docs.langchain.com/oss/python/langgraph/workflows-agents

Most valuable workflow improvements after isolation: mandatory hypothesis and
falsification before execution; automatic mechanism ablations; retrieval of failed
experiments as well as winners; separate return and eligible-score leaderboards;
finite search budgets and stopping rules; and quality evaluations for the agents
that measure causal mistakes, invalid proposals, duplicate work and reproducibility.
Twenty percent annualized returns remains a separate research objective, not a
termination condition that licenses unlimited testing on the same held-out data.
