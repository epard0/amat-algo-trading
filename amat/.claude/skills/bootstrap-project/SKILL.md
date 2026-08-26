---
name: bootstrap-project
description: Scaffold or normalize the AMAT Python backtesting repository from the approved architecture while preserving any existing implementation.
argument-hint: "[optional scope or constraints]"
disable-model-invocation: true
context: fork
agent: implementation-lead
background: false
effort: high
---

Bootstrap the AMAT repository for: `$ARGUMENTS`

1. Read `.claude/CLAUDE.md` and all files in `.claude/references/` that define architecture, strategy, validation, and promotion.
2. Map the existing repository. Preserve sound code and do not overwrite user work.
3. Create only the missing foundation needed for the first ETF backtesting milestone:
   - typed `src/amat/` package boundaries from the architecture reference;
   - `pyproject.toml` and locked dependency workflow using the project's existing package manager, or `uv` when none exists;
   - canonical configuration directories;
   - deterministic test skeletons and hand-built fixtures;
   - docs for strategy, data dictionary, execution model, and validation protocol;
   - ignored `data/` and `outputs/` layout without downloading market data;
   - CLI entry points that cannot submit broker orders.
4. Keep framework adapters optional until the pure domain core exists. Do not add live trading, credentials, or speculative ML components.
5. Validate imports, tests, formatting/lint/type checks that are actually configured, and the Claude bundle.
6. Return `.claude/templates/agent-handoff.md` content with the worktree/branch, files, commands, results, and unresolved decisions.

Do not commit, push, merge, or install an unreviewed third-party skill/plugin.
