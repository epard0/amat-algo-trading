# Agent-directed pattern research

New workspaces use `strategy-development/3`. Agents can choose the experimental
parent, formulate a mechanism and falsification criterion, and author executable
temporal patterns or price-path templates. The fixed accounting, risk, costs,
sample boundaries and promotion gates remain the controls for the experiment.

The [implementation contract](pattern-research-design.md) records the scope.
The [verification report](pattern-research-verification-2026-09-11.md) records the
test coverage, frozen program and real pattern experiments.
The [engine guide](strategy-development-engine.md) covers initialization and the
underlying signal, fitting and evidence paths. Existing frozen v1/v2 programs
continue with their original code; create a new workspace to use these additions.

## Research choices

A proposal can now include:

- `parent_candidate`: an offered candidate digest, or `null` for the incumbent.
  The offered library contains the incumbent, baseline and verified exploratory
  frontier. A discarded recipe can be a useful experimental parent.
- `research.mechanism`: why the observations might predict an outcome.
- `research.falsification`: what result would undermine that explanation.
- `stage`: `pattern`, `refine`, `combine`, `ablate`, `policy` or `manual`.
- The full replacement `signal` and attenuation `features`, plus allowed config
  patches relative to the selected parent.

The model receives editable parent recipes and bounded training evidence,
including per-pattern matched/unmatched forward returns and control deltas.
Choosing an unavailable parent fails validation. The selected parent determines
the experimental comparison; a keep must still beat the global incumbent.
Mechanism and falsification prose are persisted research notes, not executable
code or assertions that a hypothesis has been established.

## Patterns as executable conditions

A signal may contain up to three named `patterns`, each with one to four
conditions referring to its feature-term indices. Conditions see transformed
feature values **before** each term's center/scale/tanh mapping.

| Control | Meaning |
|---|---|
| `predicate: above` / `below` | Feature value is at least / at most `threshold` |
| `predicate: rising` / `falling` | One-session feature difference is at least / at most `threshold` |
| `window` | Last 1–21 decision sessions in the condition's window |
| `lag` | Window ends 0–63 sessions before the current decision |
| `min_fraction` | Required matching fraction of observations, from 0.5 to 1 |
| `mode: all` / `any` | Conjunction / disjunction of window conditions |
| `mode: sequence` | Conjunction over oldest-first, non-overlapping windows |
| `weight` | Signed contribution from −2 to 2 in a weighted signal |

For a sequence, every earlier condition must satisfy
`earlier.lag >= later.lag + later.window`. Thus a compression window ending two
sessions ago can precede a two-session breakout window ending today. The windows
are explicit; this is not an unspecified search over arbitrary past occurrences.
Missing observations make a pattern unavailable, including `any` conditions.

Example pattern fragment, where term 0 is a trailing volatility percentile and
term 1 is breakout distance in volatility units:

```json
{
  "name": "compression_then_breakout",
  "mode": "sequence",
  "conditions": [
    {"term": 0, "predicate": "below", "threshold": 0.35,
     "lag": 2, "window": 5, "min_fraction": 0.6},
    {"term": 1, "predicate": "above", "threshold": 0.1,
     "lag": 0, "window": 2, "min_fraction": 0.5}
  ],
  "weight": 1.0
}
```

Matches become binary 0/1 columns, mapped to −1/+1 for signal combination.
Setting ordinary term weights to zero creates a pure pattern rule under the
weighted learner. Ridge instead learns coefficients for the ordinary features,
interactions and pattern columns. Signed weights have their existing weighted-only
meaning. Trading policy remains separate from pattern recognition.

Every feature also supports `lag_sessions`, applied after its causal rolling
transformation. The feature lag plus condition lag/window/change history must
fit within the fixed warm-up. Negative lags and arbitrary expressions are rejected.

## Agent-authored price-path shapes

The seventeenth feature family, `path_similarity`, compares the trailing log-price
path with a template of 3–12 control points bounded to −3..3. The template is
linearly interpolated over the feature lookback; both paths are centered and
normalized. The result is a correlation from −1 to 1, invariant to price level
and positive path scale.

For example, `[1, 0, -1, 0, 1]` describes a trough and recovery. Agents can author
asymmetric recoveries, plateaus, repeated troughs or other bounded shapes instead
of selecting a fixed named chart pattern. Similarity does not itself imply that
a shape predicts returns. Flat paths score zero; incomplete/nonpositive paths
are unavailable. Constant templates are rejected.

## Search, diagnostics and controls

The built-in search now includes a pattern stage after initial screening, even
when no candidate has been retained. It starts with compression→breakout and
recovery-with-participation hypotheses. These are examples of the grammar; model
proposals can use other shapes, term combinations, thresholds and event windows.
The deterministic proposer is still a bounded heuristic search, not an autonomous
code-writing or unrestricted data-mining agent.

Pattern definitions participate in frontier diversity along with feature families
and forecast correlation. Low exposure alone no longer excludes a rare pattern
from the exploratory pool; promotion still requires the frozen exposure gate.
Combination search preserves patterns from both parents and remaps their feature
references, rather than adding feature terms while losing the second pattern.
Full recipes, research intent and actual ancestry are
saved in the experiment ledger. Each pattern receives availability, persistence,
redundancy and forward-return diagnostics; matched/unmatched cohorts report counts
and mean net returns at 5/20/60 sessions.

Ablations remove each pattern and contributing feature. Removing a feature removes
its conditions and remaps remaining references; a one-condition sequence becomes
an `all` pattern. Removing the only remaining weighted signal restores the core
baseline signal as the control. Sequence recipes also get a timing control that
sets condition lags to zero and evaluates them contemporaneously. Existing delay,
exposure, nearby-window and block-shuffled controls still run.

`signals.npz` now contains `pattern_scores` with the same session/symbol axes as
other predictions. Fit records include training pattern quantiles. Diagnostics
use the pattern values actually computed under each chronological fit, including
when fitted normalization changes across refits.

## Try the supplied recipes

After initializing a new program using the engine guide:

```bash
/path/to/program/research.sh run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/compression-breakout.example.json

/path/to/program/research.sh run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/path-recovery-participation.example.json

/path/to/program/research.sh run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/combined-patterns.example.json
```

The optional model proposer receives the expanded schema and research controls.
Provider availability and output quality remain external constraints; invalid
recipes fail over or pause rather than entering a backtest. The worker never
executes hypothesis prose or model-generated Python.
