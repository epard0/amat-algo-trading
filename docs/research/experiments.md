# Experiment registry

Every attempted configuration is registered **before** its result is inspected.
Failed experiments are never deleted. This directory is the audit trail that
makes multiple-testing risk measurable instead of invisible.

## Rules

1. Register first. Create the entry, commit the intent, then run.
2. Never delete or edit a past entry. Supersede it with a new one.
3. Record the outcome verbatim, including "worse than baseline" and "aborted".
4. The locked holdout may not be inspected until the canonical configuration
   and the validation protocol are frozen. Any holdout read is recorded in the
   run manifest with a reason (`holdout_accessed`, `holdout_access_reason`).
5. A configuration change motivated by a holdout result is an automatic
   rejection condition, not an iteration.

## Entry format

One TOML file per experiment, named `NNNN-short-slug.toml`:

```toml
id = "0001-baseline"
registered_at = 2026-07-30T00:00:00Z
hypothesis = "What is being tested and why, stated before the run."
base_config = "configs/strategy/amat_v1.toml"
changes = []              # explicit diff from base_config; empty for baseline
sample = "walk-forward, excludes locked holdout"
benchmarks = ["SPY", "60/40", "equal_weight", "cash", "signal_free"]
status = "registered"     # registered | running | complete | aborted
# Populated only after the run finishes:
# run_id = "..."
# outcome = "..."
# notes = "..."
```

This directory opened on 2026-08-13 with entries 0001 (target-volatility
sweep) and 0002 (volatility-state conditioning). Earlier experiments -- the
v2 managed overlay, v3 news/regime conditioning, v4 stance switching, and the
v5 universe expansion -- predate it and are recorded in
`docs/experiment-registry.md`, which remains the narrative registry; entries
here are the machine-readable registrations that precede each run.

See `.claude/references/validation-protocol.md` ("Experiment hygiene") and
`.claude/skills/experiment-governance`.
