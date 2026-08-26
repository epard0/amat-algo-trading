---
name: implement-phase
description: Implement a bounded, approved AMAT plan or task in an isolated worktree and return a verified handoff.
argument-hint: "<approved plan, phase, or bounded task>"
disable-model-invocation: true
context: fork
agent: implementation-lead
background: false
effort: high
---

Implement: `$ARGUMENTS`

Treat an existing approved plan as authoritative. If no plan artifact exists, first derive a bounded internal checklist from the task and normative references; do not redesign unrelated modules.

Requirements:

1. Map the affected code and tests.
2. State the timing, accounting, data, reproducibility, and security invariants affected.
3. Add a failing test or exact fixture before changing financial behavior.
4. Implement through shared domain functions; keep framework adapters thin.
5. Delegate only independent, clearly scoped subtasks.
6. Run narrow checks, then the full applicable quality gate.
7. Inspect the complete worktree diff and challenge it for leakage and hidden fallbacks.
8. Return an agent handoff with branch/worktree, changed files, exact commands/results, and remaining risks.

Do not commit, push, merge, mutate raw data, open the holdout without protocol, or introduce live-order functionality.
