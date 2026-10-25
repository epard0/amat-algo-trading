# Strategy development v2: implementation contract

Goal: develop causal features and continuous long-or-cash signals independently
of portfolio risk sizing, and explain their incremental contribution after costs.
The operator owns the research policy; the autonomous loop owns bounded proposals.
The executor consumes no research output automatically.

The data grain is completed exchange session × ETF. Features use adjusted prices
and full-session volume. Diagnostic/learning labels begin at the next available
configured execution-window VWAP, use the adjusted/raw conversion consistently,
and mature only after their exit window. Missing executable windows produce
missing labels, never filled-forward prices. Horizons are frozen at 5, 20 and 60
sessions. All assets share chronological split boundaries.

Baseline: unchanged v6 strategy and fixed universe, costs, lag and risk limits.
Candidate signals: bounded feature registry, causal transformations, weighted or
regularized linear combinations, limited pair interactions, and entry/exit
hysteresis. The existing runner's signal override feeds the existing allocator.
Legacy attenuation recipes remain supported.

Evaluation: allow an initial training period before a common measured anchor;
refit at frozen chronological boundaries. Learned preprocessing, feature selection
and ridge calibration use only matured training labels, with a purged inner
chronological split. Outer development folds feed the research search and are
explicitly adaptive development evidence. The later explicit checkpoint remains
excluded from selection and is already-inspected historical evidence.

Diagnostics: feature availability, training-defined buckets, forward returns and
rank correlations by horizon/year/asset, redundancy with baseline and other
features, persistence, delayed execution, ablations, nearby-parameter checks,
and an explicitly retrospective exposure-matched benchmark. A block-shuffled
timing control is a falsification check, not a tradeable strategy or a p-value.

Selection: retain the existing accounting/drawdown/exposure/cost gates, add paired
block-bootstrap uncertainty against the incumbent and record sensitivity/control
results. Bound all trials, including failed ones. Maintain a small diverse
frontier for experimentation without treating frontier membership as a keep.

Unacceptable mistakes: future labels in a fit, preprocessing fit on validation,
changed risk/execution assumptions, silent missing inputs, comparisons with
different measured windows, and claiming adaptive development scores are pristine
out-of-sample evidence. Acceptable outcomes include no retained candidate.

Artifacts include feature metadata, recipes, model/scaler states and cutoffs,
prediction arrays, diagnostics and full accounting for control runs. Checks use
synthetic prefix mutation, hand-computed labels/policy, split-boundary and cache
tests, a real frozen-workspace run, and both projects' existing quality gates.
Existing workspaces retain their frozen v1 runtime; new programs use v2.
