---
name: plan-phase
description: Produce an implementation-ready plan for a named AMAT phase, feature, defect, or research-to-code handoff.
argument-hint: "<phase, feature, or defect>"
disable-model-invocation: true
context: fork
agent: architecture-planner
background: false
effort: high
---

Create an implementation plan for: `$ARGUMENTS`

Read the current repository and normative `.claude/references/` files. Verify current external APIs from primary sources when the plan depends on them. Resolve ambiguity from repository evidence where possible; otherwise state a concrete assumption and its impact.

The plan must use `.claude/templates/implementation-plan.md` and include:

- current-state map and reusable abstractions;
- exact behavior, non-goals, and invariants;
- dependency-ordered steps with files, interfaces, tests, and acceptance criteria;
- data/schema/config migration and rollback;
- leakage, accounting, reproducibility, performance, and security checks;
- primary-source evidence for unstable libraries/APIs;
- explicit “done” criteria and a final adversarial-review step.

Do not edit the repository or output generic phases without file-level actions.
