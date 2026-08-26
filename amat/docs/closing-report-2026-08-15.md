# Closing report — stress, projections, TLT, and the finished v6 (2026-08-15)

This report closes the campaign that ran on 2026-08-15: the complete stress
battery (everything on the registry's "Not yet run" list), a slippage/cost
application audit, forward performance projections, the TLT investigation
with its registered experiment, the randomized stop-loss control, the
finished v6 evidence program, multi-horizon backtests, and the simplified
`amat backtest` CLI. Every number below is measured on burned history and
none of it is out-of-sample evidence or a profitability claim.

## Verdict in one paragraph

v1 remains the candidate and survived everything thrown at it: 5× costs
(Sharpe 1.01 → 0.77), three-session execution delay, 10% of fills randomly
withheld, session-close pricing, deletion of any single universe symbol
(Sharpe 0.89–1.04), its best year removed, its top-10 trades removed
(33.8% of profit). Two experiments registered before running were resolved
without changing anything: 0004 (TLT variants — the winner earns
eligibility only, with the hindsight caveat, and the without-IEF control
proves the effect is TLT-specific) and 0005 (rebalance band — falsified;
the parameter neighbourhood is a plateau). The single most honest new fact
in the repository comes from the signal-free control: **on this decade the
trend signal's measured contribution is drawdown control, not raw
return** — a score-of-1 allocator out-returned the strategy (+93.6% vs
+85.2%) at double the drawdown (−16.9% vs −8.7%). Any future promotion
argument must be made in exactly those terms.

## What was asked, and what was done

**"Ensure slippage is being applied."** Audited end-to-end with file:line
evidence: every fill bears `cost_cash = |notional|·bps/10⁴` on both sides
(`src/amat/domain/fills.py:113` — buy pays notional+cost, sell receives
notional−cost), the accountant books it (`accounting.py`), and per-session
reconciliation would break if any cost went missing. Price slippage proper
is structural: sizing at decision marks, filling at the next session's
window VWAP. The `PeriodAttribution.slippage` field is 0 *by design*
(the one-way bps books under fees) — now documented in the code so it
cannot be misread as "slippage unmodeled". Additionally stressed: 2×/3×/5×
costs and session-close fills (no sensitivity).

**"Run the remaining tests, including survivorship bias."** `amat stress`
now runs the whole battery as one recorded artifact per strategy; the
registry's "Not yet run" list is reduced to the three items only new
machinery or calendar time can produce (engine parity, holdout accrual,
paper period). Survivorship: leave-one-out over all twelve symbols is the
measured bound (no single listing carries the result); the unmeasurable
residue — ETFs that delisted before the 2026 selection — is stated,
not approximated.

**"Randomize stop losses."** 20 seeded uniform stop/TP draws under the
0003 split: zero beat the no-exit core's validation return; the third that
edged its Sharpe did so within noise by cutting volatility — the trade the
volatility-target dial already prices. The 0003 falsification is not an
artifact of its grid.

**"Investigate and audit TLT."** The loss (−$3,628 across 91 round trips,
PF 0.62) is *not* the 2022 rates shock — the deadband sat 2022 out at
−$64. It is whipsaw: 61 losing trades held ≤ 21 sessions costing −$8,149,
concentrated in 2024–26 chop; the signal is long TLT just 35% of sessions
against a persistently negative raw score. Even so, the TLT sleeve
(−7.04%) beat holding TLT (−9.28%). Registered experiment 0004 tested the
risk:reward variants: removing TLT wins training and validation by a
within-noise margin (validation Sharpe 1.25 vs 1.19), the without-IEF
control ranks last (the effect is TLT-specific), and per registration the
winner earns **eligibility only** — the frozen universe is unchanged,
because deleting an asset after watching it lose is hindsight selection.

**"Accurate performance metrics projections."** `amat project`: the run's
own net returns, stationary-bootstrap resampled forward from any funding
(`--funding`), at any horizons, with percentile tables, exceedance
probabilities, a wealth-fan PDF, and a run record. For v1 from $100k: 1y
spans −2.8%…+16.7% (11.8% of resampled years lose), 10y median +90.5%
with p05 drawdown −14.6%. Every artifact carries the caveat verbatim:
within-resampling frequencies, never forecasts.

**"Finish the planned v6."** All "required next evidence" items measured:
stress battery (survives; LOO 1.00–1.15), Monte Carlo (observed at the
74th/78th/72nd percentiles), projection (10y median +83.2%), trailing
windows, and paper capability (`amat simulate` accepts the v6 file). The
one admissible return lever the v6 audit named — the wider rebalance
band — was registered (0005) and falsified. No re-tuning on burned data
was attempted, per the audit's own instruction.

**"Full comprehensive backtest at 1/2/5/10 years; specify starting
funds."** Eight dated PDFs (`v{1,6}-horizon{1y,2y,5y,10y}-20260815.pdf`)
plus the exact trailing-window table (positions carried in, no ramp):

| Window | v1 total / Sharpe / DD | v6 total / Sharpe / DD |
|---|---|---|
| 1y | +20.0% / 2.40 / −4.3% | +16.4% / 2.41 / −3.1% |
| 2y | +24.0% / 1.54 / −5.3% | +19.7% / 1.58 / −4.2% |
| 5y | +36.7% / 0.92 / −8.0% | +32.9% / 1.03 / −5.9% |
| 10y | +85.2% / 1.01 / −8.7% | +78.0% / 1.14 / −8.0% |

Starting funds: `--initial-cash` on `amat backtest` (and everywhere), plus
`--funding` on projections.

**"Simplify the CLI."** `amat backtest -v v1` runs the whole pipeline with
defaults for everything (newest snapshot, dated PDF under
`outputs/reports/`, checked-in config), `--start`/`--end` in `YYYY-MM`,
and month-edge resolution. It is a pure dispatcher over the existing
handlers — one code path per version, so the shortcut cannot drift. v2
refuses with the registry as the record.

## Engine changes, disclosed

1. **In-flight netting + unbacked-sale clamp** (`runner.py`,
   `execution/__init__.py`): at lag ≥ 2 consecutive decisions overlap and
   previously double-sold (a measured `AccountingError` short). Decisions
   now net scheduled quantities; a sale that loses its backing is clamped
   and recorded (`UNBACKED_SALE`), never shorted. Provably inert at lag 1:
   the golden-digest identity was re-verified after each change, and unit
   fixtures pin both behaviors.
2. **Research-control hooks**: `drop_every_nth_fill` (deterministic
   missed-fill injection, `STRESS_DROPPED`) and `signal_override`
   (signal-free control, availability-masked, [0,1]-validated). Neither is
   reachable from a primary run's configuration.
3. **v6 config acceptance**: measuring commands route the v6 superset file
   through its own loader and measure the strategy core (canonical v6 *is*
   that core).

## Checks run at close (2026-08-15, all fresh)

| Check | Result |
|---|---|
| `uv run pytest -q` | **1215 passed, 1 skipped** (pre-existing layer skip) |
| `uv run ruff check .` | clean |
| `uv run ruff format --check .` | clean (139 files) |
| `uv run mypy src` | clean (81 files) |
| `bash .claude/scripts/quality_gate.sh full` | **7 passed, 0 failed, 2 skipped** |
| `python3 .claude/scripts/validate_bundle.py` | PASS |
| Golden digest (lag-1 bit-identity) | **MATCH** after every engine edit |

## Remaining risks, stated plainly

1. **Still no out-of-sample evidence.** Every measurement above is burned
   history; the holdout stays locked and unopened until post-declaration
   data accrues. The trailing year's Sharpe 2.4 is a favorable draw, not a
   property.
2. **The signal-free finding cuts both ways.** The signal earns its keep
   as risk shaping; a reader who wants maximum return from these twelve
   assets would historically have preferred the signal-free book and its
   −17% drawdowns. That is a framing risk for any future presentation of
   this strategy, and it is now impossible to miss in the registry.
3. **Survivorship residue is unmeasurable from this data** (pre-2026
   delistings), and the universe remains a 2026 choice.
4. **Engine parity does not exist** (single research runner; the
   event-driven adapter is unbuilt), and the paper period has not been
   served.
5. **Multiple-testing debt keeps growing** (five registered experiments,
   several controls, many sensitivities on one dataset); every count is
   recorded so future readers can discount accordingly.

## Changed files (this campaign)

Engine: `src/amat/backtest/runner.py`, `src/amat/execution/__init__.py`,
`src/amat/domain/fills.py`, `src/amat/backtest/sleeves.py`,
`src/amat/backtest/accounting.py` (doc note). New modules:
`src/amat/validation/stress.py`, `src/amat/validation/projection.py`,
`src/amat/reporting/projection.py`. CLI: `src/amat/cli/main.py`
(`backtest`, `project`, `stress`, `--random-control`, v6-aware loading).
Validation: `src/amat/validation/exitsweep.py` (random control). Scripts:
`scripts/asset_audit.py`, `scripts/experiment_0004_tlt_variants.py`,
`scripts/parameter_neighborhood.py` (all new). Registrations + outcomes:
`configs/experiments/0004-…toml`, `0005-…toml`. Tests:
`tests/unit/test_stress.py`, `test_projection.py` (new);
`test_execution_and_accounting.py`, `test_cli_engine_surface.py`,
`test_cli_validation_surface.py` (extended). Documentation: `README.md`,
`docs/experiment-registry.md`, `docs/strategy-guide.md`, this report.
Immutable artifacts: run records under `outputs/runs/` (stress ×2,
montecarlo, project ×2, sweep-exit-rules, backtest ×4, study ×4), JSON
artifacts under `outputs/experiments/` (TLT audit, 0004, neighborhood,
trailing horizons), and twelve PDFs under `outputs/reports/`.
