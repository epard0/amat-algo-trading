# Research engine and supporting packages

The `amat/` project owns strategy computation and immutable research evidence. Install it with `uv sync --project amat --locked`. Its six Python packages ship together, while their responsibilities stay distinct.

| Package | Purpose | Main entry/interface |
| --- | --- | --- |
| `amat` | Snapshot/data handling, domain records, portfolio construction, historical fills/accounting, backtests, validation and inert plans | `amat`; `amat.boundaries`; `amat.execution.intent` |
| `autoresearch` | Bounded daily/intraday candidate campaigns, causal evaluation, judging, selection/promotion evidence, funded-futures and crypto studies | `autoresearch`; root `./research` wrapper |
| `llmrouter` | Typed model requests/responses, explicit provider chains, bounded failover and cost/health accounting | `llmrouter.contracts`, `chain`, `transports`, `policy` |
| `deskreview` | Reviews and reports derived from the actual research functions | `deskreview` |
| `newsdesk` | Isolated vendor news ingestion, stored discovery and candidate assets | `newsdesk` |
| `portfolio_team` | Coordinated portfolio research/review, model policies, evidence and queue workflows | `pmteam` |

`amat` must not acquire broker order authority. Supporting packages are not alternate strategy implementations. In particular, reviews should use the engine's actual functions, and news ingestion must preserve its isolation boundaries. Package tests encode these dependency rules.

## Ordinary research workflow

1. Validate an explicit strategy configuration and identify its raw-file hash.
2. Audit a snapshot's source, feed, timestamp/adjustment policy and coverage.
3. Freeze a hypothesis, candidate budget, costs, chronology, baseline and selection rule in a new workspace.
4. Evaluate development data causally; retain failures, duplicates and inactive proposals.
5. Save selection before validation/external evaluation. Record repeated inspection and contamination honestly.
6. Retain measured reports/manifests; promote only through the applicable gates. A model opinion alone does not promote a policy.

Use `./research run 5 --plan-only` to inspect the campaign, then review the native command help before a real run. `run 5` asks for candidate count, not a return percentage. `./research intraday` and funded-futures modules have separate contracts. Resume an existing session only with its saved program and evidence; do not change costs or data partitions mid-run.

## Strategy programs

The daily ETF path uses configured multi-asset universes and switching/allocation rules. The current configuration default is `configs/strategy/amat_group_selection_volatility.toml`. Crypto/perpetual studies live in `autoresearch` and are consumed by the source-pinned Kalshi adapter. MNQ funded-futures studies use dedicated modules for indicator policies, trailing-drawdown windows, costs and robustness checks. These programs have different instruments, fills, account assumptions and evidence gates; success in one does not qualify another.

Research data and output are excluded from Git. Historic guides referring to a dated snapshot/output directory assume that evidence exists locally. See [data and evidence](../data-and-evidence.md), [strategy API](../strategy-api.md), [native AMAT guide](../../amat/README.md), [controller](../../amat/docs/research-controller.md), [workflow](../../amat/docs/research-team-workflow.md), and [model operations](../../amat/docs/portfolio-team-operations.md).
