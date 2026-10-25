# Next strategy reference and return-improvement backlog

Reviewed FinRL-Trading at commit `4409abe925c904e570be78ebfb5e77ac3491dff8` on
2026-10-19. This is a research design, not a newly implemented or promoted trading
strategy. Machine-readable scope: `configs/experiments/adaptive-rotation-next.json`.

Sources are the upstream [adaptive configuration](https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/4409abe925c904e570be78ebfb5e77ac3491dff8/src/strategies/AdaptiveRotationConf_v1.2.1.yaml),
[group-strength calculation](https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/4409abe925c904e570be78ebfb5e77ac3491dff8/src/strategies/adaptive_rotation/group_strength.py),
[intra-group ranking](https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/4409abe925c904e570be78ebfb5e77ac3491dff8/src/strategies/adaptive_rotation/intra_group_ranking.py),
and [regime logic](https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/4409abe925c904e570be78ebfb5e77ac3491dff8/src/strategies/adaptive_rotation/market_regime.py).
These sources inform mechanisms; their published results are not evidence that
those mechanisms will improve AMAT's returns.

## What already exists

AMAT v13 already rotates across assets with positive multi-horizon trend
eligibility, a four-position limit and five-session ranking refresh. It checks
eligibility and sizes exposure daily. It uses shrinkage covariance, a 20% volatility
target, symbol/cluster caps, no leverage, and minimum rebalance weight/notional
thresholds. Its `allocation.volatility_power` is **0**, so inverse-volatility
allocation is available but is not the current v13 setting.

Structure filters, trailing signal-payoff risk budgets, and learned regimes exist
in the codebase but are disabled in v13. The signal-payoff information ratio is
not the same quantity as a group's performance relative to QQQ. Research features
already include relative strength, breadth, efficiency, and volatility shocks;
feature availability is not proof those features affect the active allocator.
The separate active-risk policy supports account drawdown/loss triggers and a
cooldown. This is not equivalent to a market-based slow/fast rotation regime.

## Eight experiments worth prioritizing

1. **Group selection before asset selection.** Add a causal economic-group ranking
   stage, then select assets within the strongest eligible groups. Existing cluster
   caps limit exposure but do not select a small set of groups. This may avoid
   filling all slots with nearly identical trades. First compare against v13 on
   its same eleven instruments; expanding the universe at the same time would
   confound the result. Do not require defensive groups to beat an equity benchmark
   in absolute terms before they can provide defense.

2. **Persistent group-adjusted momentum.** Rank an asset's strength relative to
   its economic group, retaining absolute trend eligibility. This may distinguish
   leadership from shared market exposure. Upstream residuals are asset returns
   minus group returns, not regression-estimated factor alpha. Its ranking code
   sorts the latest residual Z-score, while separately calculating compounded
   residual momentum. Test persistent residual strength explicitly rather than
   assuming the latest outlier is a sustained trend. Use robust dispersion floors,
   deterministic ties, and strict missing-data rules.

3. **Separate slow regime from fast risk reduction and recovery.** Use a slow
   market trend for normal risk budgets and an independent completed-session shock
   trigger for timely reductions. Add recovery persistence to reduce repeated
   selling and rebuying. Reuse the existing risk-state/cooldown boundary. The
   current provider snapshot does not contain a VIX series: obtain and validate
   one, or declare a realized-volatility arm with a different name and parameters.
   No same-close exit after a close-derived signal; simulate the next execution
   window, gaps, and missed fills.

4. **Turnover-aware entry/exit hysteresis.** Existing minimum trade sizes do not
   prevent two similarly ranked assets repeatedly swapping places. Keep incumbents
   until challengers exceed a predeclared rank/score margin. Compare fixed weekly,
   B1 daily refresh, and daily refresh with hysteresis at 2/6/10 bps. A trend score
   is not an expected return; any forecast of net benefit needs separate training.

5. **A fixed broader ETF opportunity set with overlap controls.** Use the expanded
   archive to select one tradable representative per redundant exposure, with
   frozen groups, adequate history and lagged liquidity checks. This may improve
   opportunity coverage without simply adding correlated equity ETFs. Current
   stocks in the archive are not a survivorship-free historical equity universe.
   Evaluate new stock selection separately with point-in-time membership and event
   risk; do not put all 509 discovery symbols into the trading allowlist.

6. **A measured defensive/cash-return alternative.** Compare idle zero-return cash
   with a permitted short-duration Treasury instrument or verified dated cash
   yield. AMAT currently assumes a constant zero cash return. Carry can improve
   idle-capital returns, but a bond fund has price risk, spread and execution costs.
   Respect actual inception dates; never backfill a newer fund or count fund income
   and a separate cash yield on the same money.

7. **Reuse the structure filters to avoid weak, choppy trends.** Test a small
   number of existing efficiency/persistence/structure features individually.
   Measure avoided losses and missed winners. These components already exist;
   the experiment is whether enabling them helps after costs. Do not add an RSI
   rule that automatically suppresses the strongest continuing trends without
   evidence. Avoid searching dozens of thresholds on already-inspected 2026 data.

8. **Control risk contribution and compare allocation rules.** Compare the current
   score-based weighting with existing volatility-aware allocation, positive risk
   contribution limits, and stable economic-group budgets at the same total risk.
   This may reduce concentration and improve compounded wealth, but could reduce
   upside. Reuse covariance, caps and available risk-policy components. Report
   return, drawdown, exposure and turnover together; lower exposure by itself is
   not proof of better timing skill. The legacy `constraints.risk_contribution_limit`
   setting is explicitly unsupported in the v1 allocator: do not enable it as if it
   were protection. The separate `ActiveRiskConfig.max_positive_variance_share`
   guard is implemented; use it only with matched policy replay, or implement and
   test a distinct allocation-budget layer.

## Next implementation sequence

Build an opt-in adaptive-group selector and member ranker that feed native target
weights; retain v13 as an exact regression reference. Reuse the existing sizing,
risk-state and executor boundary. Freeze the separate slow/fast overlay before
combining it with group selection. Limit the first experiment to the six arms in
the design file; count all variants as trials. The larger ETF universe and cash
instrument belong to later isolated experiments.

Keep weekly calculations to fully completed exchange weeks. Midweek data cannot
be used under a Friday label. Future mutation, missing histories, deterministic
selection, holiday schedules, delayed fills, and restart/partial-fill tests must
pass before measured comparisons. A fixed full panel of currently listed stocks
is not sufficient to establish historical tradability.

Use the retained v13 and B1 study paths as references, but label all previously
inspected historical periods accordingly. Exact broker-fill parity remains a
separate event-replay requirement. Freeze the implementation and objectives before
new prospective observations; do not declare a fresh holdout merely by changing
a date label on data that previous research has already inspected.

## Web autoresearch repair

The saved web failures identified two independent launch-contract violations:
workspaces were created inside the executor-mounted checkout, and the adapter
passed a one-token budget to a Program requiring at least 256. Web research now
uses an external project-specific directory, a valid budget, a registered verified
core snapshot by default, and the existing bounded built-in proposal mode. Built-in
start neither loads nor freezes a model chain, so model credentials and calls are
unnecessary. Model-assisted CLI research retains its existing chain behavior.

`TERMINAL_RESEARCH_ROOT` can override the external location. It must remain outside
the project checkout. Preview does not create a workspace; existing names and
symlink escapes are refused. The default is
`~/.local/share/amat/web-research/<project-hash>/`.

The simple dashboard exposes **Explore strategy improvements** with a unique
research name. The existing advanced Research launch remains available. Native
report JSON, Markdown and comparison HTML are registered in Activity. HTML exports
are downloads with a sandbox content policy. These actions do not promote a
strategy or place orders.


## Verification of the repair

A real launch through the simple web flow on October 19 completed one built-in
candidate plus the v13 baseline using the verified core snapshot. The native
result was `batch_completed`: one candidate completed, zero escalations, zero
failed attempts. The candidate was **discarded**, demonstrating successful research
execution rather than a newly profitable strategy. The frozen program had
`summary_chain: null`, a valid 256-token budget, the historical split unchanged,
and an external workspace. No model calls or orders were made.

The retained verification run is
`~/.local/share/amat/web-research/bc9d49962820/research-1792450607682/`.
Native `report.html`, `report.md`, `report.json`, frozen inputs and ledger remain
there for review. Frontend API regression tests include external-root validation,
registered defaults, path-escape refusal and sandboxed report downloads. Native
CLI regression tests confirm built-in mode skips a missing model-chain file while
model-assisted mode preserves its configured chain.
