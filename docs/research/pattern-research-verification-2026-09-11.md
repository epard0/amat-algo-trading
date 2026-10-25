# Agent-directed pattern research verification — 2026-09-11

This change expands agent research choices: verified experimental parents,
mechanism/falsification notes, lagged features, temporal event composition and
custom price-path templates. The [pattern research guide](agent-pattern-research.md)
describes the controls and examples.

## Reproducible program

Final-source workspace:
`/home/supasuge/Documents/amat-research/v3-pattern-research-final-20260911`

Program SHA-256:
`eafc759c0d74296570abfd85f00b95f5e3ea04a4a4398028d88cc250fc551686`

This freezes `strategy-development/3`, `causal-features/3` and the research-signals/3
adapter. It uses the existing Alpaca SIP snapshot, fixed v6 baseline, 2 bps normal
and 6 bps stress costs, and the common 2018–2022 development return window.
Limits are eight trials, six consecutive non-improvements, 360 seconds per worker
and 100,000 model tokens per UTC day. No model API calls were needed for these
explicit recipe tests.

The program first tests compression→breakout, then explicitly selects that
exploratory candidate as the parent of a combined compression/breakout plus
path-recovery/participation experiment. The combination has four feature terms
and two patterns. An entry threshold of 0.5 permits either pattern to activate
the equal-weight combination.

The program's ledger records both experimental-parent and global-incumbent
comparisons. Low exposure can leave a rare pattern eligible for the exploratory
pool while still blocking promotion. The combined candidate's parent is recorded
in `combined-parent-proposal.json`; neither hypothesis prose nor a stage label
changes the promotion criteria.

Both experiments completed with zero worker crashes and were discarded. The
compression/breakout objective was **0.15647**; the combined objective was
**0.19242**, versus **0.80609** for the incumbent. The combined recipe failed low
exposure, incremental exposure-matched information and nearby-parameter stability
checks. Improving on a weaker parent did not replace the global incumbent.

The first candidate saved eleven controls, and the combined candidate saved
fourteen, including each pattern, each feature, and the sequence timing control.
Both recorded ten chronological fits. Verification checked **139 artifact hashes**
across the baseline and two candidates, binary pattern-array dimensions/values,
fit cutoffs, explicit parent identity and global-incumbent comparison. Results are
saved in `pattern-verification.json`. The candidate digests are:

- Compression/breakout: `934bfbd4ee848882016700c944e63fd3b9977c1ef1a96c6e93035f560b6ad154`.
- Combined patterns: `66d860fbdfc00f13f89a4aaed5189aa6bb51e1b691a6e192b5dd0f050a0af807`.

Both exported comparisons completed scripted portfolio-team reviews. These
reviews verify integration and evidence handling, not independent strategy approval.
Replaying the completed catalog dispatched zero new reviews and reused both results.

An initial smoke workspace, `v3-pattern-research-20260911`, used program
`279653075fa6dbf80bab9ce75e319fcb0fff62fc4b9af96ccb090ee042bcfd1c`.
It completed the compression/breakout experiment with eleven saved controls and
three feature/pattern diagnostic sets. The candidate scored 0.15647 versus the
baseline's 0.80609 and was discarded for stress Sharpe, low exposure and lack of
incremental exposure-matched information. That smoke build predates preserving
both parents' patterns during combination and admitting low-exposure exploratory
candidates. The final-source program contains both refinements.

## Automated evidence

The focused signal-development suite passed 59 tests. New coverage includes:

- Hand-computed ordered conditions and threshold/change predicates.
- Unavailable input propagation, invalid references, overlapping sequence windows
  and warm-up limits.
- Price-path shape discrimination, normalization invariance and flat/missing paths.
- Future-data mutation and prefix invariance of weighted and ridge pattern
  forecasts, recorded fit states and pattern arrays.
- Feature lag applied after causal normalization.
- Ablation reference remapping, single-condition reduction and timing controls.
- Combining both parents' patterns and retaining their input mappings.
- Model validation against the selected exploratory parent.
- Rejection of unavailable parents and protection of the global incumbent when
  a candidate only improves on a weaker experimental parent.
- Rare-pattern frontier inclusion without waiving promotion or no-fill gates.

The final full AMAT suite passed **1,661 tests with one existing skip** in 209.72
seconds. The skip is the existing dependency-layer exclusion for `__init__.py`.
Type checking passed for 136 source files. Lint, formatting and diff whitespace
checks passed. The expanded model interface was exercised with a scripted
provider; this does not establish availability of the external provider chain.

## Interpretation

These are engine and research-process checks. Sequence recognition uses declared
windows, not an unconstrained search across all possible historical alignments.
Path similarity is a descriptive feature, not evidence that a visual shape is
predictive. The pattern examples are research hypotheses. The existing adaptive
search, retrospective exposure matching and bootstrap limitations continue to
apply. No executor or broker configuration was changed.
