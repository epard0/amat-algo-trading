# Agent-directed pattern research: implementation contract

Agents can choose a verified exploratory parent, define a mechanism and a
falsification criterion, and compose causal temporal conditions and price-path
templates. Pattern definitions are executable recipe data. The fixed universe,
costs, accounting, train/checkpoint boundaries and promotion criteria remain the
experiment controls. Existing frozen workspaces retain their recorded runtime.

Pattern conditions reference transformed feature terms in their own units. They
can require above/below levels or changes over completed sessions, with explicit
lag, observation window and required fraction. Sequence conditions use strictly
ordered, non-overlapping windows. Missing observations yield unavailable patterns,
not false confirmations. A price-path template compares a normalized trailing
log-price path with an agent-authored, bounded sequence of control points.

Both weighted and fitted signals can use these nonlinear pattern columns.
Diagnostics record pattern prevalence and forward-return association; controls
remove each pattern and each contributing term. Parent selection affects the
experimental comparison, while retention always compares to the global incumbent.
Model context contains bounded training evidence and editable parent recipes.

Acceptance: hand-computed sequence and shape tests; future mutation/prefix tests;
reference remapping in ablations; parent/protected-config and model-boundary
tests; a real frozen pattern experiment; existing quality gates. Pattern discovery
is adaptive research and does not imply independent statistical significance.
