# Core project rules

- Treat `.claude/references/strategy-contract.md` as normative for v1 strategy behavior.
- Keep the repository backtest-only. Never create or invoke broker order-submission paths.
- Preserve causality: data used at time `t` must have been available at `t`.
- Preserve reproducibility: resolve configuration, data version, code revision, dependencies, and seeds into every run manifest.
- Preserve evidence: do not overwrite raw data snapshots or validated run outputs.
- Prefer a small deterministic core over framework-specific or model-driven behavior.
- Never optimize a result by silently changing the benchmark, cost model, sample, missing-data policy, or holdout.
- State uncertainty and limitations. Do not use words such as “proven,” “safe,” or “profitable” without narrowly defined evidence.
- Stop and surface ambiguity when timing, corporate-action treatment, or accounting cannot be reconciled.
