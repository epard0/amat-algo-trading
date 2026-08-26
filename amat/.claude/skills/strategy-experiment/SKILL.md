---
name: strategy-experiment
description: The protocol for proposing, measuring, and judging a new AMAT strategy version - freezing rule, config identity, falsification controls, registry entry, and what a REJECTED verdict requires. Use before creating any v5+ or modifying any frozen parameter.
argument-hint: "[hypothesis one-liner]"
---

# Proposing a strategy version

Every strategy change is a new frozen version with its own config file, its
own digest, and its own registry entry — whichever way the measurement lands.
Editing values in place invalidates every prior result.

## The protocol, in order

1. **State the hypothesis before measuring.** One sentence: what behaviour
   changes and why it should pay. If the mechanism can be expressed as a
   precomputed per-session array, prefer that shape — arrays can be
   leak-tested and swapped for controls; in-loop mutable state cannot
   (the v2 rejection is the cautionary record).

2. **Write the config as a complete frozen definition.**
   `configs/strategy/amat_vN.toml`. Sub-stances or conditioning parameters go
   in the file (or in a self-describing data file whose parameters travel in
   its header). Unknown keys must fail the loader. Record the digest:
   `sha256sum configs/strategy/amat_vN.toml`.

3. **Prove the null case is exactly the baseline.** Before measuring anything,
   pin by test that the mechanism switched off reproduces v1 bit-for-bit
   (v3: all-ones scalars; v4: lambda = 1). If the null case drifts, the
   mechanism is a third strategy, not a variant.

4. **Causality tests are mandatory.** A future-mutation test (rows after the
   cut multiplied; outputs at or before the cut bit-identical) for every new
   decision input.

5. **Measure once, against the controls, on the full sample.** Use
   `falsify_conditioning` (scalar overlays), `falsify_blend` (stance
   blends), or `falsify_universe` (universe changes): identical data, costs,
   funding, and corporate actions for every row; only the candidate mechanism
   differs. The controls are not optional. For per-session series: shuffled,
   turnover-matched random, static mean — and for blends, each stance alone.
   For universe changes (no series, so no mean to hide behind): the baseline
   plus a leave-one-out variant per added symbol — a candidate that a smaller
   universe matches is rejected, and an improvement carried by one symbol is
   named as concentration, not breadth. A candidate that does not beat every
   control on Sharpe is REJECTED, and "beats the controls" still only means
   "not obviously a coincidence"; walk-forward on untouched data is a
   separate, later bar.

6. **Record the verdict in `docs/experiment-registry.md`** with the full
   control table, the digest, the snapshot id, and what survives (mechanism,
   tests) versus what does not (the candidate). Rejections are recorded with
   the same care as candidates — a report that omits failed experiments is
   itself a rejection condition. Never annotate the verdict into the config
   file: that changes the digest and orphans the cited result.

7. **No parameter search without registering every trial.** One declared
   parameterization, measured once, is an experiment. A sweep is N experiments
   and every one goes in the registry. The sanctioned sweep shape is
   `amat sweep-target-vol`: split fixed before any result, ranking on
   training folds only, validation reported ranking-blind, candidate count
   in the verdict (`src/amat/validation/sweep.py`; experiment 0001 is the
   worked example).

8. **Register machine-readably, run, persist.** Write the
   `configs/experiments/NNNN-slug.toml` entry (hypothesis + falsification
   condition) BEFORE the run; run through the CLI so a run record lands in
   `outputs/runs/` (`--no-run-record` results cannot be cited); populate the
   entry's `run_id`/`outcome` after. `amat regime-experiment` and
   `amat sweep-target-vol` are the templates.

9. **A surviving battery is still not a candidate.** Experiment 0002
   (vol-state conditioning) is the cautionary record: nominal SURVIVES with
   a 0.0012-Sharpe margin over its own static-mean control — i.e. the
   timing unproven, the mean doing the work again. Check the margin at full
   precision in the run record, never from the two-decimal table.

## Precedents to read first

- v2 managed overlay — REJECTED (in-loop state, concentration gate).
- v3 regime conditioning — REJECTED (static mean beat it; no config written).
- v4 stance switching — REJECTED (timing informative, mean did the work).
- v3 news conditioning — mechanism wired, structurally identical to v1 until
  forward news history accrues.
- v5 breadth expansion — REJECTED (0.88 vs 1.01; leave-one-out monotonic:
  every added symbol diluted the book even though each was profitable in
  isolation — "good asset" and "good addition to a vol-normalized
  allocator" are different claims, and the second is the one measured).
- experiment 0001 target-vol sweep — frozen 8% unchanged; 6% earned
  eligibility (smooth plateau, small margin); the durable finding is the
  return/drawdown frontier, not a winner.
- experiment 0002 vol-state conditioning — nominal SURVIVES, margin over
  static mean 0.0012 Sharpe; not pursued (the third regime mechanism whose
  mean did the work).

All in `docs/experiment-registry.md`. The repeated lesson: switching and
conditioning mechanisms keep rediscovering that their *mean* is doing the
work. Any new candidate should explain, in the hypothesis, why it will not.
