---
name: experiment-governance
description: Govern AMAT experiments with pre-registration, immutable configs/results, complete attempt history, holdout controls, and multiple-testing awareness.
user-invocable: false
---

Before execution, create an experiment card containing hypothesis, decision, parent, sample, holdout boundary, config/data/code hashes, costs, benchmarks, seeds, and pass/fail criteria.

After execution:

- persist success, failure, and exceptions under the same registry;
- never delete or overwrite an attempted configuration;
- distinguish canonical, diagnostic, exploratory, and robustness runs;
- prohibit result-aware metric/sample/benchmark changes;
- count the full search space and report multiple-testing risk;
- link reports to immutable artifacts and the exact experiment card;
- require an explicit new hypothesis before expanding parameter search.

Use `${CLAUDE_PROJECT_DIR}/.claude/templates/experiment-card.md`.
