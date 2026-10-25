# Research loop and observability proposal

Status: implemented workflow foundation; further improvements proposed below.
Implemented foundation: Space Bunny OpenRouter default, independent research coding
route, correlated routing events, metadata-only model diagnostics, evaluator stages,
per-experiment lessons, report panels, and live CLI status.

## What the current code supports

The daily loop already has durable experiment IDs, frozen configurations, budget
reservations, failure memory, local proposal validation, backtests, retained-best
comparisons, and model-generated summaries. The outer LangGraph wrapper has only
prepare/research/verify nodes; its research node contains the full inner loop.
Expanding that wrapper alone would not make individual agent decisions observable.
The right unit of observation is a hypothesis, experiment, tool action, or model
attempt tied to durable evidence.

## Proposed research cycle

1. Frame a hypothesis with an expected mechanism, evidence references, and explicit
   falsification criteria before implementation or backtesting.
2. Have an independent critic identify leakage, confounding, redundancy with prior
   experiments, and the cheapest discriminating test. Keep criticism advisory to
   deterministic validity checks; a model opinion cannot authorize promotion.
3. Build the candidate with a coding route and run bounded local checks. Record
   each patch/artifact digest and parent experiment.
4. Run the registered experiment with frozen data, costs, risk constraints, and
   validation criteria. Reserve protected evaluation data for the existing
   validation protocol instead of repeatedly feeding it back to the proposer.
5. Verify measurements and compare with the retained incumbent. Distinguish an
   invalid experiment from evidence against the hypothesis.
6. Save a structured lesson with evidence IDs, scope, uncertainty, and a next-test
   recommendation. Deduplicate by candidate/experiment identity, not prose.

Use explicit stopping rules for exhausted budget, repeated equivalent candidates,
and sustained absence of informative results. Begin with serial stages; agent
parallelism should follow evidence that independent branches are useful and can
respect the existing single-writer ledger.

## Model roles and evaluation

| Role | Initial routing proposal | Evaluation signal |
|---|---|---|
| Coding and repair | Luna, then Gemini, Groq, and Space Bunny | Tests passed, valid patches, repair attempts, elapsed time |
| Evidence extraction and summaries | Existing economical route | Supported statements, schema validity, missing references |
| Hypothesis generation | Configurable research route | Novel valid proposals, falsifiable predictions, informative experiments |
| Independent critic/judge | Different configured model from proposer | Defects detected, unsupported objections, agreement with measured outcomes |
| Final adjudication | Preserve existing policy until decision | Evidence fidelity and calibration against deterministic decisions |

Do not enable automatic model promotion based only on speed or successful JSON.
Collect task-specific evaluation results before changing routing weights. Account
for availability and unknown usage separately from quality. A stronger judge adds
cost and latency and can still share the proposer's biases. The requested evaluator is implemented as the research judge.

## Observability product

Build a local run view showing phase, current task, last activity, budget remaining,
and interrupted work. Add an expandable timeline joining hypotheses, model attempts,
validation failures, tool actions, backtests, critiques, and decisions through IDs.
Explain each fallback and display requested versus served models. Separate measured
tokens from reserved tokens and unknown dollar costs.

A model comparison view should show valid-result rate by task, latency distribution,
rate limits, schema repairs, and experiment usefulness. Add filters for experiment,
role, model, and error. Export metadata to an external tracing service only through
an explicit redaction boundary; raw prompts are not required to explain routing.

The current foundation exposes per-model outcomes and a correlated call timeline
through JSON status and analysis exports, and model outcome counts through human
status. The report now includes judge records and routing events; CLI status supports live
watching. Further work could add a streaming browser dashboard, individual tool spans,
and role routing learned from a fixed evaluation set.

## Resolved decisions

- Research quality and observability receive equal priority.
- Keep Luna first for coding; Gemini precedes Space Bunny in fallback routes.
- Preserve existing Luna use; “jev-like” means an evaluator/judge agent.

Validation covers evidence-reference rejection, judgment/decision disagreement,
crash verdicts, budget exhaustion, idempotent judging, fallback order, trace privacy,
and report escaping. Existing research tests cover duplicate candidates and resume.

Recommended next increments:

1. Build a fixed role-evaluation set before changing routing weights: measured coding
   success, valid hypotheses, useful critiques, evidence fidelity, and latency.
2. Add a streaming browser view with filters by experiment, phase, role, and model.
   The current report is a snapshot and live watching is provided through the CLI.
3. Compare judge findings with known-defect experiments to measure missed problems
   and false alarms. A second judge is useful only if it adds measurable value.
4. Add proposal revisions driven by critique as an explicit, budgeted stage. The
   current critic informs the record and subsequent learning; it does not rewrite
   or veto the current candidate.
5. Add experimental branch scheduling only after single-cycle quality improves;
   preserve the single-writer ledger and frozen evaluation protocol.

Keep strategy promotion governed by measured checks.


The [Controller agent](research-controller.md) now owns initiation, read-only evidence
inspection, brief generation, batch dispatch, and follow-up decisions for both daily
and intraday research. This implements supervisory planning around the measured loop.
