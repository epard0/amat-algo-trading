# Promotion gates

Promotion means “eligible for shadow/paper operational testing,” not “profitable” or “safe for live capital.”

## Engineering gates

- All relevant unit, integration, property, accounting, leakage, and parity tests pass.
- Bundle/configuration validation passes.
- No unexplained ledger variance.
- No position change lacks fill/action provenance.
- No missing execution data is silently imputed.
- Re-running identical inputs produces identical core artifacts.
- Every report traces to a run manifest and immutable inputs.
- Secret and destructive-action scans pass.

## Research gates

Suggested minimums for the frozen canonical strategy:

- net out-of-sample Sharpe at least 0.50;
- positive locked-holdout cumulative return;
- at least 60% positive annual out-of-sample folds;
- maximum drawdown no worse than 20%;
- positive cumulative result under triple baseline costs;
- best symbol contributes less than 25% of total P&L;
- best year contributes less than 40% of total P&L;
- nearby parameters form a stable region rather than an isolated optimum;
- trend signal adds measurable value versus the signal-free allocator;
- cost break-even materially exceeds baseline assumptions.

## Automatic rejection conditions

- any look-ahead or same-bar execution defect;
- holdout-driven parameter change;
- unresolved corporate-action double count;
- materially incomplete or untraceable data;
- positive result disappears under baseline realistic costs;
- result depends primarily on one instrument, year, or handful of trades;
- research and event runners disagree beyond tolerance;
- report omits failed experiments or changes benchmark/sample definitions.

A rejection should identify whether the failure is data, implementation, execution, allocation, concentration, or absence of signal. Do not respond by adding arbitrary indicators or optimizing more parameters.
