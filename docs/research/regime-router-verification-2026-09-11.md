# Regime router v4 verification

## Implementation and checks

The [implementation guide](regime-router-implementation.md) describes the delivered
rule router, three reusable feature families, research grammar, controls, and
remaining planned work.

Validation completed against the final source:

- `uv run --locked pytest -q`: **1,679 passed, 1 skipped**, 135.31 seconds.
  The existing skip concerns an undeclared `__init__.py` dependency layer.
- `uv run --locked mypy src`: passed for 137 source files.
- `uv run --locked ruff check src tests`: passed.
- `uv run --locked ruff format --check src tests`: 190 files formatted.
- `git diff --check`: passed; new document links also checked.

Tests include independently calculated feature values, frozen-universe breadth
coverage, zero volatility, future-price perturbations, state smoothing/history,
per-asset static means, malformed contracts, bounded outputs, candidate validation,
search-stage progression, and a real frozen router cycle with cached resume.
The source was held fixed during integration tests and real experiments.

## Real research session

Workspace: `/home/supasuge/Documents/amat-research/v4-regime-router-20260911`

Program SHA-256:
`3e4e12853b6309d7e7a1297e20c82880a9cba1c7a21070a58555e961804fa19f`

Snapshot: `alpaca-sip-2026-08-11`, using the baseline v6 universe.
Measured development window: **2018-01-02 through 2022-12-30**.
Normal/stress assumptions: **2/6 bps one way**, next-session execution.
No model calls, broker calls, or validation checkpoint selection occurred.

| Candidate | Normal Sharpe | Stress Sharpe | Outcome |
|---|---:|---:|---|
| Baseline | 0.7655 | 0.6597 | Remained incumbent |
| Efficiency router | 0.0030 | -0.1402 | Discarded |
| Breadth router | 0.3303 | 0.2632 | Discarded |
| Equal sleeve blend control | 0.3659 | — | Explanatory control, not promoted |

Efficiency candidate:
`e53a547f7c6e8ce242059c710f2c10c50ce614e6c84026c7e4028f92ea5d5f21`

Breadth candidate:
`961cbc148c222e63c233ca787776b6804fb1b170594ebbea3728bf2cfbd62d5a`

Both router recipes failed exposure-matched incremental information and the
static-blend bootstrap gate. The efficiency recipe also failed positive-fold,
stress-performance, nearby-parameter, and execution-delay checks. These are
negative results for the supplied hypotheses, not an implementation failure.
Neither recipe was promoted; both remain inspectable exploratory candidates.

Each router produced 14 explanatory control runs, eight diagnostic feature/component
traces, fit metadata, and primary/stress accounting artifacts. Verified **112
frozen input/runtime hashes** and **151 distinct saved output artifact hashes**
after completion. The ledger contains one baseline and two discarded experiments,
with no crashes. The second experiment resumed the same frozen session.

## Interpretation and next research

This establishes a functioning experiment path and reproducible negative evidence.
It does not establish that breadth, volatility changes, or regime routing improve
returns. Their incremental usefulness can now be tested separately in ordinary
signals and patterns, without requiring the router.

Do not tune these two recipes until their historical charts look good. Use the
saved sleeve/static controls to formulate a distinct mechanism and a bounded next
experiment. Fitted HMM/BOCPD/GARCH models and the live plan adapter remain separate
stages; adding them is not justified by these two routing results alone.
