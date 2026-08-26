# Closing report — validation campaign through 2026-08-14

This report closes the campaign that ran 2026-08-12 → 2026-08-14: the
falsification/persistence work, walk-forward + locked holdout, the
adversarial audit of the validation layer, regime/risk:reward experiments,
Monte Carlo, registered exit-rule experiment 0003, the paper-trading
journal extension, the engine performance rework, and the documentation
pass. It states what was done, what the evidence says, exactly which checks
ran, and what remains open. Nothing here is a profitability claim.

## Verdict in one paragraph

amat-etf v1.0.0 remains the **current candidate, not promoted**. On the
burned 2016-12-30 → 2026-08-10 sample it returns +85.24% total (+6.65%
annualized, Sharpe 1.01, max drawdown −8.69%, net of 2 bps costs, corporate
actions applied, accounting reconciled every session). Every consistency
harness it has been put through — walk-forward (9/10 positive folds),
Monte Carlo (observed history an unremarkable draw from its own resampled
process), trade-quality (profit factor 1.81, costs cleared 14.5×), and four
registered/controlled experiments — supports *internal coherence*, and
none of it is out-of-sample evidence, because every session of the sample
was inspected before the holdout was declared. Genuine out-of-sample
evidence begins only as post-2026-08-10 data accrues against the locked
holdout. The honest summary of the experiment program: **every attempt to
improve the frozen core — news conditioning (v3), stance switching (v4),
universe expansion (v5), volatility-state conditioning (0002), explicit
stops/take-profits (0003) — has failed its controls or its validation
window**, which is precisely why the frozen core is still the candidate.

## What closed in this campaign

### 1. Registered experiment 0003 — exit rules (stop-loss / take-profit)

Registered before running, with the failure prior written into the
registration. Result, 2026-08-14: **falsified exactly as the prior
predicted**. Take-profit overlays dominated training (Sharpe 1.18 vs 0.89)
and every one of them lost to the no-exit core on the ranking-blind
validation window (+8.41%/1.19 for the core vs +6.94%/1.13 for the best
overlay); the top training fold scores tied within 1e-09 and the verdict
machinery declared no winner. Stop-loss-only rows were near no-ops — the
volatility target de-risks before a 5–15% stop is touched. The v1 answer to
"where is the stop loss / take profit?" is therefore *in the sizing,
continuously*; a price-level exit grid measurably hurt here, and only the
validation window showed it. Run record
`outputs/runs/sweep-exit-rules-amat-etf-20260814T014415.823351Z`; outcome
recorded in `configs/experiments/0003-exit-rules-sweep.toml`, the registry,
README, and the strategy guide.

The supporting machinery added for 0003 — `amat.backtest.exits.ExitRules`
(adjusted-basis stop/TP with a fixed re-entry embargo, applied as the third
runner overlay), `amat sweep-exit-rules`, and 18+ unit/integration tests —
remains available for future registered experiments only.

### 2. Monte Carlo robustness

200 stationary-bootstrap paths (mean block 21 sessions, base seed
20260812), the full engine on every path. The observed history lands at the
60th percentile of resampled total return, 70th of Sharpe, and its −8.69%
max drawdown is *shallower than 84% of paths* (resampled median −11.60%,
p05 −17.45%) — plan on deeper drawdowns than history shows. 0.5% of paths
end below initial funding; 2.5% breach −20% drawdown. Artifacts:
`outputs/reports/amat_v1_montecarlo.pdf` (equity fan, histograms, summary
table — verified rendered as real tables), run record
`outputs/runs/montecarlo-amat-etf-20260814T014412.934963Z`. All
probabilities are within-resampling frequencies, not forecasts.

### 3. Engine performance rework (bit-identical)

Root cause of the 8–9 minute studies: `decide()` recomputed every feature
panel from the visible prefix at each decision — O(T²), with the
pure-Python EWMA recursion profiled at ~91% of runtime. Features are now
computed **once per run** (`precompute_decision_inputs`,
`src/amat/portfolio/__init__.py`) and each decision reads its row; valid
because every feature recursion is strictly causal, so row *t* of the full
pass equals the prefix pass bit-for-bit. Full 2,665-session study: **8–9
minutes → ~8 seconds**; the 800-session golden run reproduces its
pre-change equity-curve SHA-256 exactly (565 fills, final equity identical
to the last Decimal digit).

An independent four-lens adversarial review (causality, bit-identity,
behavior drift, coverage) then attacked the change. It **confirmed
byte-level identity at all 269 legal decision indices** of its probe panel
(including the LedoitWolf covariance, whose memory-layout bit-sensitivity
it probed directly and found unexercised), and surfaced real coverage
gaps, all closed the same day: the runner-level future-mutation test had a
lag-sized blind spot (decision diagnostics and fills are now asserted, not
just equity/cash); the cache row-vs-prefix property is now asserted
directly across a dense index sweep; a vacuity guard ensures the
bit-identity test compares decisions that actually hold positions; and one
deliberate semantic tightening is documented and pinned by a regression
test — a panel with a feature-rejected defect (e.g. a non-positive close)
now fails the run at start instead of completing with per-session warnings
(the fail-closed direction; loaders already refuse such bars at snapshot
load). Recorded results are unaffected — the cache is a cache, not a new
convention.

### 4. Paper trading, within the guardrails

`amat simulate` journals the complete simulated trade log for sessions past
the snapshot boundary (`live_fills`: symbol, side, exact quantity, price,
cost, `TradeReason`) alongside equity, positions, and next-session targets,
using live Alpaca market data read-only. An Alpaca paper-*order*
integration was deliberately **not** built: the session contract's "never
submit paper or live broker orders" covers it, and the strategy guide §6
records both the refusal and the gate a future lift would have to pass.

### 5. Documentation

- `README.md` — full command reference (every command and argument of
  `amat`, `newsdesk`, `deskreview`, the scripts, and the gates), measured
  results with provenance, known defects and audit trail, the perf note.
- `docs/strategy-guide.md` — the strategy with zero ambiguity: exact
  rules, exits, timing, costs; the 10-item backtest checklist answered
  honestly row by row; anti-overfitting posture; limitations.
- `docs/experiment-registry.md` — every configuration ever measured,
  including failures and superseded drafts, now including Monte Carlo and
  0003.
- Notebooks (`notebooks/monte_carlo_study.ipynb`,
  `notebooks/walk_forward_and_regimes.ipynb`) executed headlessly against
  repo methods.

## Checks run at close (2026-08-14, all fresh)

| Check | Command | Result |
|---|---|---|
| Full test suite | `uv run pytest -q` (via quality gate) | **1160 passed, 1 skipped** (pre-existing layer-declaration skip) |
| Lint | `uv run ruff check .` | **All checks passed** |
| Format | `uv run ruff format --check .` | **130 files already formatted** |
| Types | `uv run mypy src` | **no issues in 78 source files** |
| Quality gate | `bash .claude/scripts/quality_gate.sh full` | **7 passed, 0 failed, 2 skipped** (Pyright unconfigured; skips are pre-existing) |
| Bundle | `python3 .claude/scripts/validate_bundle.py` | **PASS** (16 agents, 23 skills, 7 rules) |
| Golden digest | 800-session seed-7 run | **SHA-256 match**, 565 fills, final equity exact |

## Remaining risks and open items, stated plainly

1. **No out-of-sample evidence exists.** The entire sample is burned; the
   locked holdout (boundary 2026-08-10) has never been opened and must
   accrue post-declaration data before it can say anything. Every positive
   number above is in-sample or ranking-blind at best.
2. **Survivorship bias is bounded, not eliminated.** The universe is 12
   currently-listed ETFs chosen in 2026. The bias is at the
   asset-class-menu level (broad-index ETFs, not stock picking), documented
   in README fidelity item 9 — but it is real and unmeasured.
3. **Cost stresses on real data are pending.** 2×/3×/5× cost ladders,
   execution delays, deterministic missed fills, next-open/next-close
   alternatives, and trade/symbol/year-removal stresses are listed under
   "Not yet run" in the registry so their absence is not mistaken for a
   pass.
4. **No engine parity yet.** The event-driven (NautilusTrader) runner has
   not been built; all results come from the single research runner. The
   one-strategy-core design exists to make future parity checkable.
5. **Nearby-parameter stability is partial.** The volatility-target
   neighbourhood is measured (a smooth plateau); horizons, deadband,
   covariance lookback, and rebalance thresholds are not.
6. **The paper period has not been served.** The simulate journal exists;
   judgment requires letting it accumulate observations.
7. **Multiple-testing debt.** Four registered experiments and several
   studies have consumed surprise capacity on one dataset; the registry
   records candidate counts so a future reader can discount accordingly.

## Changed files (this closing pass)

Engine: `src/amat/portfolio/__init__.py`, `src/amat/backtest/runner.py`,
`src/amat/backtest/exits.py` (new). Validation:
`src/amat/validation/exitsweep.py` (new), `src/amat/validation/sweep.py`,
CLI wiring in `src/amat/cli/main.py`, `src/amat/simulation/__init__.py`
(`live_fills`). Tests: `tests/unit/test_exit_rules.py` (new),
`tests/unit/test_portfolio.py`, `tests/integration/test_backtest_invariants.py`,
plus the audit-driven additions across the validation test files. Records
and documentation: `configs/experiments/0003-exit-rules-sweep.toml`,
`docs/experiment-registry.md`, `docs/strategy-guide.md`, `README.md`,
`scripts/trade_quality.py` (new), this report. Run artifacts (immutable):
`outputs/runs/montecarlo-amat-etf-20260814T014412.934963Z`,
`outputs/runs/sweep-exit-rules-amat-etf-20260814T014415.823351Z`,
`outputs/reports/amat_v1_montecarlo.pdf`.
