---
name: external-skill-intake
description: Security- and methodology-review a third-party Claude Code skill, plugin, marketplace, agent bundle, or MCP before project use.
argument-hint: "<repository, plugin, marketplace, or skill>"
disable-model-invocation: true
context: fork
agent: security-reviewer
background: false
effort: high
---

Review external component: `$ARGUMENTS`

Use `.claude/references/external-skills.md` and the threat model. Inspect the actual current source, not only its README.

Report:

- identity, maintainer, license, release/commit, and activity;
- skills/agents/hooks/MCPs/scripts installed and their scopes;
- `allowed-tools`, shell preprocessing, network access, credentials, and generated files;
- install/update behavior and supply-chain exposure;
- conflicts with AMAT timing, data, accounting, holdout, benchmark, and backtest-only rules;
- minimal safe installation or adaptation path;
- verdict: `ACCEPT`, `ADAPT`, `QUARANTINE`, or `REJECT`.

Do not install or execute the component during intake. Never approve a full collection when one reviewed skill would satisfy the need.
