# AMAT Backtesting Project

## Mission

Build a reproducible, adversarially validated backtesting platform for the Adaptive Multi-Asset Trend (AMAT) strategy. The first production candidate is a daily, long-or-cash ETF strategy. This repository is a research and backtesting system, not a live-trading application.

## Non-negotiable guardrails

1. **Backtest only.** Do not place broker orders, call order-submission endpoints, or add live-execution entry points.
2. **No profitability claims.** Report measured results, uncertainty, failure modes, and data coverage. A positive backtest is not proof of future profit.
3. **No look-ahead.** A signal formed after session `t` closes may execute no earlier than the configured window in session `t+1`.
4. **Immutable evidence.** Never hand-edit `data/raw/` or validated output artifacts. Create new versioned snapshots and manifests.
5. **One strategy core.** Research and event-driven runners must call the same signal, allocation, and constraint functions.
6. **Accounting must reconcile.** Every position change needs a fill or corporate action. Cash plus marked holdings must equal equity within declared numerical tolerance.
7. **Costs are mandatory.** Zero-cost runs are diagnostic only and must never be presented as expected performance.
8. **Holdout discipline.** Do not inspect or optimize against the locked holdout before the canonical configuration and validation protocol are frozen.
9. **No silent fallbacks.** Missing execution bars, ambiguous corporate actions, invalid schemas, or partial API pagination must fail closed or be explicitly recorded.
10. **No embedded secrets.** Credentials belong in the user's shell or secret manager, never in source, settings, reports, notebooks, or fixtures.

## Frozen v1 strategy snapshot

- Universe: `SPY QQQ IWM EFA EEM TLT IEF GLD SLV DBC USO UUP`
- Mode: long or cash; no leverage; no shorting
- Signal horizons: 21, 63, and 252 completed sessions
- Volatility: 20- and 60-session exponentially weighted estimates with a floor
- Signal: volatility-normalized trend, clipped, averaged, transformed with `tanh`, then dead-banded
- Portfolio target: 8% annualized volatility, subject to symbol, cluster, and 100% gross-exposure caps
- Signal time: after a completed session close
- Execution: next session's configured opening window, never the signal bar
- Primary cash assumption: 0%; Treasury-yield cash is a sensitivity only

The normative formulas, caps, timing, and promotion criteria live in:

- `.claude/references/strategy-contract.md`
- `.claude/references/validation-protocol.md`
- `.claude/references/promotion-gates.md`

## Target architecture

```text
immutable data snapshots
  -> validation and normalization
  -> shared feature/signal core
  -> covariance, allocation, and constraints
  -> target positions
  -> research runner + event-driven runner
  -> accounting and reconciliation
  -> walk-forward, stress, and parity validation
  -> reproducible report and promotion decision
```

Use Polars/NumPy/scikit-learn for deterministic research components, VectorBT only as an acceleration/diagnostic layer, and NautilusTrader as the authoritative event-driven adapter when that phase begins. Do not bury strategy mathematics inside framework callbacks.

## Working method

1. Read the relevant source, test, configuration, and reference files before editing.
2. State the invariant or acceptance criterion being changed.
3. Prefer existing project abstractions and maintained libraries over custom infrastructure.
4. For unstable APIs or library behavior, verify against current primary documentation before implementation.
5. Write or update tests before claiming completion.
6. Run the narrowest relevant checks first, then the full gate when the change is ready.
7. Review the diff for look-ahead, accounting, reproducibility, security, and accidental scope expansion.
8. Report exactly what passed, failed, or was not run.

## Standard commands

```bash
python3 .claude/scripts/validate_bundle.py
bash .claude/scripts/quality_gate.sh fast
bash .claude/scripts/quality_gate.sh full
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Commands must adapt to the repository's actual `pyproject.toml`; do not invent unavailable tools.

## Delegation

Use specialized subagents for bounded work that would flood the main context: repository mapping, external documentation, data audits, adversarial review, performance profiling, and independent validation. Use implementation agents in isolated worktrees. Do not delegate tiny edits or tightly coupled iterative debugging.

The implementation lead may coordinate specialists, but it remains responsible for integration, invariant preservation, and final verification. Subagent output is evidence to review, not authority to trust blindly.

## Completion standard

A task is complete only when:

- requested behavior is implemented;
- relevant tests exist and pass;
- lint/type checks applicable to the changed scope pass;
- generated or immutable artifacts were not hand-edited;
- data timing and accounting invariants remain true;
- the final response names changed files, checks run, results, and remaining risks.
