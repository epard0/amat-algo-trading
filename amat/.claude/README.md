# Claude Code Project Bundle for AMAT

This directory is a project-scoped Claude Code control plane for the AMAT backtesting implementation. It separates always-on rules, progressively loaded skills, specialized subagents, deterministic hooks, project references, and reproducible quality gates so the main context stays focused.

## What is included

- `CLAUDE.md`: concise always-loaded project contract
- `settings.json`: shared permissions, sandbox, hooks, worktree policy, credential scrubbing, and status line
- `settings.local.json.example`: safe per-machine overrides without credentials
- `rules/`: path-scoped engineering, data, quant, testing, security, and documentation rules
- `skills/`: manual SDLC workflows plus auto-discovered domain knowledge
- `agents/`: planners, implementers, reviewers, and specialized quant/data workers
- `hooks/`: deterministic guards against destructive commands, broker-order code, secret leakage, malformed config, and unverified completion
- `scripts/`: bundle validation, quality gates, repository reconnaissance, and status-line rendering
- `references/`: normative strategy, architecture, validation, threat, source, and external-skill policies
- `templates/`: experiment, implementation, data-quality, validation, and handoff records

## Install

Copy the `.claude` directory to the root of the AMAT git repository. Then run:

```bash
python3 .claude/scripts/validate_bundle.py
claude doctor
claude
```

Inside Claude Code, inspect:

```text
/status
/permissions
/hooks
/agents
/skills
/context
```

Claude Code may require a restart if `agents/` or `skills/` did not exist when the current session started.

Create personal settings without committing them:

```bash
cp .claude/settings.local.json.example .claude/settings.local.json
```

Do not put API keys in that file. The checked-in policy denies common broker, cloud, GitHub, and model-provider credentials to sandboxed subprocesses and enables global subprocess credential scrubbing. Authenticated market-data ingestion should be run deliberately from a trusted human-controlled process or through a separately reviewed local policy—not by weakening the shared repository guardrails.

## Claude Code version and platform

Minimum recommended version: **Claude Code 2.1.219**. The bundle uses `workflowSizeGuideline` and current sandbox/subagent behavior introduced by that release line. Use the current stable release when possible.

The shared policy sets `sandbox.failIfUnavailable: true`. Claude Code's built-in Bash sandbox supports macOS, Linux, and WSL2, not native Windows. Install the documented Linux/WSL2 sandbox prerequisites rather than silently running unsandboxed.

## Primary workflows

```text
/bootstrap-project [optional scope]
/plan-phase <phase or deliverable>
/implement-phase <approved plan or task>
/run-quality-gates [fast|full]
/audit-backtest [run id or scope]
/dependency-research <library or API question>
/external-skill-intake <repository, plugin, or skill name>
```

Domain skills such as `honest-backtesting`, `market-data-integrity`, and `anti-lookahead-audit` are hidden from the slash menu and loaded automatically by relevant agents.

## Agent usage

Ask the main thread to delegate bounded work explicitly:

```text
Use the repository-cartographer to map the current data and backtest paths.
Use the data-engineer to implement the normalized bar schema in an isolated worktree.
Use the adversarial-reviewer to challenge the completed backtest for leakage and accounting defects.
```

Implementation agents use temporary git worktrees. They do not commit, push, merge, or alter the main checkout automatically. Review each worktree diff and integrate deliberately.

Auto memory is disabled because Claude Code stores it per repository and shares it across worktrees. For this research project, durable facts belong in reviewed files, while holdout observations and exploratory outcomes must not leak into unrelated sessions through implicit memory.

## External skills

The bundle intentionally does not auto-install third-party plugins. Skills and plugins can add instructions, scripts, hooks, tools, and network behavior. Review `.claude/references/external-skills.md` and run `/external-skill-intake` before installation.

High-signal optional candidates include:

1. **Superpowers** for TDD, planning, systematic debugging, review, and verification workflows.
2. **marketcalls/vectorbt-backtesting-skills**, preferably a reviewed single skill such as `vectorbt-expert`, `backtest`, or `walk-forward` rather than the entire collection.
3. **K-Dense Scientific Agent Skills** for a reviewed Polars/data-science reference skill.
4. **Microsoft debugpy's project pytest skill** as a compact example of repository-scoped Python testing guidance.
5. **Trail of Bits Skills** or its curated marketplace for narrowly selected security-review workflows.

The project's strategy timing, costs, accounting, data immutability, experiment registry, and holdout protocol remain authoritative whenever an external component disagrees.

## Design choices

- No giant monolithic prompt: stable essentials remain in `CLAUDE.md`; scoped rules and skills load only when relevant.
- No `.claude/commands/`: modern Claude Code unifies reusable slash workflows under skills; legacy commands still work but add no value here.
- No committed third-party plugin: installation and trust remain an explicit user decision.
- No `.mcp.json`: MCP configuration is root-level, not inside `.claude`, and no broker/data credential should be embedded in this shared bundle.
- No live-trading agent: the repository is intentionally incapable of broker order submission during the backtesting phase.
- No saved `.claude/workflows/` initially: normal project work uses auditable manual skills and bounded subagents. Dynamic workflows remain available with a medium size guideline for genuinely broad, independent work.
- Inline `!` shell blocks in project/user/plugin skills are disabled; deterministic scripts run through explicit tools and permissions instead.

## Security model

The checked-in settings and hooks enforce the following baseline:

- permission bypass mode disabled;
- Bash sandbox required and unsandboxed escape disabled;
- common credential files and environment variables denied to subprocesses;
- direct `curl`/`wget`, destructive filesystem commands, force pushes, and privilege escalation blocked;
- raw datasets, validated outputs, local settings, secrets, and credentials protected from edits;
- live mode, Alpaca trading-client imports, remote order endpoints, and production order submission denied;
- order-like code allowed only after confirmation in explicitly inert test/docs or simulation/backtest paths;
- post-edit syntax/secret checks and stop-time bundle/diff checks;
- no auto memory and no inline skill shell execution.

Hooks are defense in depth, not a substitute for review. Run `/permissions` and `/hooks` after installation and review any user-level/plugin configuration that also loads into the session.

## Validation

Validate only the bundle:

```bash
python3 .claude/scripts/validate_bundle.py
```

Run the fastest available project checks:

```bash
bash .claude/scripts/quality_gate.sh fast
```

Run the full project gate before claiming a phase complete:

```bash
bash .claude/scripts/quality_gate.sh full
```

The quality script detects available tooling rather than fabricating commands. A skipped check is reported as skipped, not passed.

## Suggested first session

```text
/bootstrap-project

Then inspect the generated repository map and use:

/plan-phase Phase 1: immutable Alpaca/FRED ingestion, normalization, manifests, and data-quality gates
```

Do not start strategy optimization until data, timing, accounting, and anti-lookahead invariants are implemented and independently tested.
