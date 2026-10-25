# Simple research batches

Goal: `./research 5` starts a new session, measures five distinct candidate recipes
plus the baseline, and opens one comparison report. The researcher supplies only
the batch size. Existing advanced commands remain available.

Decision: escalation means replacing the research incumbent, not placing trades.
Compare every candidate with the best retained strategy under the frozen score,
minimum improvement, eligibility, and uncertainty checks. Explain failed checks
alongside returns, Sharpe, costs and drawdown; a higher return alone is insufficient.

Counting: baseline, duplicates and failed measurements do not consume a completed
strategy slot. Bound replacement attempts and preserve partial reports when a
budget, data failure or exhausted search prevents completion. Never label a short
batch complete. Prefer configured LLM proposals, with disclosed built-in search
fallback if providers are unavailable. LLM reviews retain their separate budget
and availability reporting.

Evidence: use the same frozen data, backtest runner, costs, chronological split
and controls. Preserve per-strategy measurements and reviews. Generate an offline
HTML report with a comparison table and expandable strategy details; no remote
assets or model-supplied executable HTML. Maintain a latest-session pointer for
`./research view`, without modifying old frozen sessions.

Verification: test distinct counting, replacement after failures, incumbent
escalation, partial batches, HTML escaping and launcher arguments. Run an actual
five-candidate batch and inspect the generated report in a browser. Existing
advanced commands and frozen sessions are the fallback.

Metrics extension: record UTC phase transitions, experiment outcomes, provider
attempt durations and known token usage in an append-only ledger event table.
Expose read-only status while the writer is active. Export comparable strategy,
year-fold, scenario, daily-account, fill and missed-fill records with identifiers,
input/output hashes and metric definitions. Add downside, drawdown-duration and
turnover diagnostics without changing the selection score. Missing observations
remain missing; unknown token usage is separate from conservative budget charges.
Preserve forecast volatility and binding constraints already available in the
runner's daily records. Support analysis export from existing recorded batches
without rerunning their backtests or inferring historical timings.
