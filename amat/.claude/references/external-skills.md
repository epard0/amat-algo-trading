# External skill and plugin candidates

No third-party component is installed automatically. A skill is executable project configuration: it can inject instructions, include scripts, invoke tools, add hooks through a plugin, and influence scientific decisions. Pin a reviewed commit or plugin version, inspect every file, and test it in a credential-free disposable worktree before enabling it.

## Selection rule

Use an external component only when it closes a concrete capability gap more cheaply and safely than maintaining an equivalent project-local skill. Prefer one narrow skill over an entire marketplace or broad trading bundle.

The AMAT strategy contract, timing model, immutable-data requirements, accounting ledger, cost model, experiment registry, holdout protocol, and promotion gates always override external material.

## 1. Superpowers

Repository: `obra/superpowers`

Best fit: test-driven development, systematic debugging, implementation planning, code-review discipline, worktrees, and verification-before-completion.

Official Claude Code marketplace path:

```text
/plugin install superpowers@claude-plugins-official
```

Why optional rather than bundled: it provides a broad development methodology and SessionStart behavior that overlaps with this project's own workflows. Check the current release and open compatibility issues, then decide whether to use the plugin wholesale or adapt a small number of skills.

## 2. VectorBT Backtesting Skills

Repository: `marketcalls/vectorbt-backtesting-skills`

Best fit: VectorBT API patterns, fast diagnostic backtests, cost and robustness checks, walk-forward references, and tearsheet workflows.

List skills before installing:

```bash
npx skills add marketcalls/vectorbt-backtesting-skills -l
```

Review one skill at a time, for example:

```bash
npx skills add marketcalls/vectorbt-backtesting-skills -s vectorbt-expert
npx skills add marketcalls/vectorbt-backtesting-skills -s backtest
npx skills add marketcalls/vectorbt-backtesting-skills -s optimize
```

Cautions:

- optimization templates can create false-discovery pressure;
- market-specific fee defaults may not match AMAT;
- same-bar assumptions, indicator defaults, and tearsheet metrics require inspection;
- VectorBT is an acceleration/diagnostic layer, not the authoritative accounting engine;
- never install via `npx` until the package, install script, target files, and pinned source commit are reviewed.

## 3. K-Dense Scientific Agent Skills

Repository: `K-Dense-AI/scientific-agent-skills`

Best fit: selective Polars, numerical computing, scientific Python, statistical analysis, and reproducible research guidance.

Cautions: the repository is intentionally broad. Review and adapt only the relevant skill directory; do not copy unrelated scientific domains into project context. Confirm examples against current Polars/NumPy/scikit-learn documentation.

## 4. Microsoft debugpy pytest skill

Repository path: `microsoft/debugpy/.claude/skills/pytest/SKILL.md`

Best fit: a compact, production-repository example of path-local pytest guidance. It is more useful as a pattern to review than as a dependency to install wholesale.

Cautions: debugpy's test layout and commands are not AMAT's. Adapt conventions rather than copying repository-specific assumptions.

## 5. Trail of Bits Skills

Repositories:

- `trailofbits/skills`
- `trailofbits/skills-curated`
- `trailofbits/claude-code-config`

Best fit: narrowly selected security-review, static-analysis, variant-analysis, dependency, and fix-verification workflows; also useful as a reference for defensive Claude Code configuration.

Cautions: security tools may require additional binaries, network access, or large output. Select only the skill needed for a specific review and preserve this project's sandbox, permissions, credential, and no-order boundaries.

## 6. AGIPro Claude Trading Skills

Repository: `agiprolabs/claude-trading-skills`

Potentially useful: selected walk-forward validation, risk, statistical-method, or feature-engineering references.

Cautions: the collection is crypto/DeFi-first, very broad, and includes ML/RL/trading capabilities outside AMAT v1. Cherry-pick and audit. Do not install the full collection, broker-facing skills, strategy-generation loops, or anything that optimizes the locked holdout.

## 7. Other trading collections

Examples include `HyperFrequency/skills` and `tradermonty/claude-trading-skills`.

Treat these as research leads, not trusted dependencies. Many trading-skill repositories optimize for breadth, demos, discretionary analysis, or strategy generation rather than point-in-time data, ledger reconciliation, and false-discovery control. They should not replace project-local quant controls.

## Intake procedure

Run `/external-skill-intake <source>` and record:

1. Repository identity, owner, license, maintainer activity, release, and exact commit hash.
2. Requested install mechanism and every file it would create or modify.
3. All `SKILL.md` frontmatter: invocation, tools, agent, model, context, and background behavior.
4. All shell interpolation, scripts, hooks, MCP servers, package installers, and lifecycle behavior.
5. Network destinations, local bindings, environment variables, credential expectations, and telemetry.
6. Any broker client, order route, account mutation, live-data entitlement, or remote code-execution path.
7. Conflicts with AMAT timing, data, costs, accounting, holdout, experiment, or reporting contracts.
8. Dependency and transitive dependency review, including install scripts.
9. Credential-free isolated-worktree test and before/after diff.
10. Decision: accept unchanged, adapt locally, quarantine for further review, or reject.

## Pinning record template

```text
Component:
Repository:
Commit/release:
License:
Files accepted:
Files rejected:
Tools/scripts/hooks added:
Network/credential requirements:
AMAT conflicts and adaptations:
Reviewer:
Review date:
Decision:
```
