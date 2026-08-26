# Authoritative source map

Last reviewed: 2026-07-28.

## Claude Code

Use current official documentation and the published settings schema before changing project configuration:

- `.claude` layout: `https://code.claude.com/docs/en/claude-directory`
- Project memory and path-scoped rules: `https://code.claude.com/docs/en/memory`
- Settings: `https://code.claude.com/docs/en/settings`
- Permissions: `https://code.claude.com/docs/en/permissions`
- Skills: `https://code.claude.com/docs/en/skills`
- Subagents: `https://code.claude.com/docs/en/sub-agents`
- Hooks: `https://code.claude.com/docs/en/hooks`
- Worktrees: `https://code.claude.com/docs/en/worktrees`
- Sandboxing and credentials: `https://code.claude.com/docs/en/sandboxing`
- Environment variables: `https://code.claude.com/docs/en/env-vars`
- Status line: `https://code.claude.com/docs/en/statusline`
- Changelog: `https://code.claude.com/docs/en/changelog`
- JSON schema: `https://json.schemastore.org/claude-code-settings.json`

Configuration principles applied here:

- keep always-loaded `CLAUDE.md` concise and place procedures in skills;
- use path-scoped rules for instructions that apply only to specific code/data areas;
- use skills as the unified reusable slash-workflow mechanism;
- use subagents for bounded specialized context and worktree isolation for parallel edits;
- use deterministic hooks and permissions for rules that must be enforced rather than merely remembered;
- fail closed when sandboxing is unavailable and disable the unsandboxed escape hatch;
- deny credential files/variables and scrub provider credentials from child processes;
- disable auto memory because it is shared across worktrees in one repository;
- disable inline skill shell execution and require explicit, reviewable tool calls;
- pin minimum Claude Code behavior to v2.1.219 while recommending the current stable release.

## Project libraries and data

Verify current behavior against primary sources before implementation:

- Polars: `https://docs.pola.rs/`
- NumPy: `https://numpy.org/doc/`
- scikit-learn Ledoit-Wolf: `https://scikit-learn.org/stable/modules/generated/sklearn.covariance.LedoitWolf.html`
- pytest: `https://docs.pytest.org/`
- Hypothesis: `https://hypothesis.readthedocs.io/`
- VectorBT: `https://vectorbt.dev/`
- NautilusTrader: `https://nautilustrader.io/docs/`
- Alpaca market data: `https://docs.alpaca.markets/`
- FRED: `https://fred.stlouisfed.org/`

## Research anchors

Use original papers or publisher copies, not social-media summaries, for time-series momentum, covariance shrinkage, backtest overfitting, deflated Sharpe, and bootstrap methodology. Record the exact citation, version, accessed date, and implementation decision it supports.

A source supports an implementation choice; it does not excuse independent tests, point-in-time data controls, or evidence that the chosen convention matches the project's declared strategy contract.
