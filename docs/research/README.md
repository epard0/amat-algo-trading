# AMAT — Adaptive Multi-Asset Trend

This guide moved to `docs/research/`. Run component-relative commands from `amat/` at the repository root.

For current cross-system installation, API contracts and contribution guidance, start with the [workspace documentation](../../README.md#table-of-contents). Dated reports and deployment observations below require their original local evidence and do not describe a fresh clone.

A reproducible, adversarially validated **backtesting** platform for a daily,
long-or-cash multi-asset ETF trend strategy.

> **Status: running on real market data.** As of 2026-08-11 the engine runs
> against immutable Alpaca SIP snapshots — the current one covers **2,665
> sessions (2016-01-04 → 2026-08-10) across 30 ETFs** with 100%
> execution-window coverage. Every strategy opens with **$100,000**, spread
> across assets by the allocator. The synthetic generator remains, for engine
> verification only.

This repository is a research and backtesting system. It is **not** a
live-trading application. It has no capability to reach a trading account, and
must not acquire one.

For autonomous feature engineering and signal development, see the
[strategy-development engine guide](strategy-development-engine.md).
The simplest workflow is `./research 5` to propose and backtest five strategies,
then `./research view` to reopen the browser report. These commands work from
either this directory or the workspace root. See [simple research](simple-research.md).
`./research status` shows live progress; [metrics and recording](research-metrics.md)
documents the analysis tables generated with each session.
The [trade performance monitor](trade-performance-monitoring.md) joins model intent,
execution and subsequent price outcomes for backtests and paper trading. New daily research
reports include an interactive viewer; `./research trades FILE...` combines recorded evidence.
`autoresearch` builds composable causal features, weighted or fitted forecasts and
independent trading policies. Its bounded search runs in a frozen workspace outside
the executor-mounted checkout and exports diagnostics and matched review evidence.
The [agent pattern research guide](agent-pattern-research.md) covers experimental
parent selection, temporal sequences and agent-authored price-path templates.

---

## Table of contents

1. [Non-negotiable guardrails](#non-negotiable-guardrails)
2. [Quick start](#quick-start)
3. [How the strategy works, in exact detail](#how-the-strategy-works-in-exact-detail)
4. [How the backtest engine works](#how-the-backtest-engine-works)
5. [Strategy versions and the experiment record](#strategy-versions-and-the-experiment-record)
6. [Measured results — real data](#measured-results--real-data)
7. [Validation: walk-forward, Monte Carlo, regimes, risk:reward](#validation-walk-forward-monte-carlo-regimes-riskreward)
8. [The PDF reports](#the-pdf-reports)
9. [Command reference](#command-reference)
10. [Run records, holdout, and reproducibility](#run-records-holdout-and-reproducibility)
11. [Repository structure — what lives where, and why](#repository-structure--what-lives-where-and-why)
12. [Data: snapshots, corporate actions, news](#data-snapshots-corporate-actions-news)
13. [Backtest fidelity: how close is this to live?](#backtest-fidelity-how-close-is-this-to-live)
14. [Development and quality gates](#development-and-quality-gates)
15. [Known defects and audit trail](#known-defects-and-audit-trail)
16. [What has *not* been done](#what-has-not-been-done)
17. [Normative references](#normative-references)

---

## Non-negotiable guardrails

Reproduced in full in `.claude/CLAUDE.md`.

1. **Backtest only.** No order submission, no live-execution entry points.
2. **No profitability claims.** Report measured results, uncertainty, failure
   modes, and data coverage. A positive backtest is not proof of future profit.
3. **No look-ahead.** A signal formed after session `t` closes may execute no
   earlier than the configured window in session `t+1`.
4. **Immutable evidence.** `data/snapshots/`, `data/raw/`, and
   `outputs/validated/` are append-only, never rewritten in place.
5. **One strategy core.** Every runner calls the same signal, allocation, and
   constraint functions.
6. **Accounting must reconcile.** `equity == cash + Σ(quantity × mark)` every
   session; every position change traces to a fill or a corporate action.
7. **Costs are mandatory.** Zero-cost runs are diagnostic only.
8. **Holdout discipline.** No optimizing against a locked holdout.
9. **No silent fallbacks.** Missing execution bars, ambiguous corporate actions,
   invalid schemas, and partial API pagination fail closed or are recorded.
10. **No embedded secrets.** Credentials live in the operator's shell — never in
    source, settings, reports, notebooks, or fixtures.

---

## Quick start

```bash
uv sync --locked

# Sanity: validate a configuration (loader rejects unknown keys)
uv run amat validate-config configs/strategy/amat_v1.toml

# Full backtest on real data, PDF report out. ~5 minutes with sleeves.
uv run amat study configs/strategy/amat_v1.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-11 \
  --pdf outputs/reports/amat_v1_study.pdf
```

The combined portfolio opens with $100,000 (`--initial-cash` to change), each
per-asset sleeve opens with its own $100,000 (`--sleeve-equity`), and the PDF
is the primary artifact — see [The PDF report](#the-pdf-report).

---

## How the strategy works, in exact detail

The frozen v1 contract (`.claude/references/strategy-contract.md` is normative;
`configs/strategy/amat_v1.toml` is its machine-readable form). Everything below
names the code that implements it, so a claim can be checked in minutes.

### Step 0 — Universe and clusters

12 ETFs in 6 clusters, declared in `amat_v1.toml` `[[universe.clusters]]`:

| Cluster | Cap | Symbols |
|---|---:|---|
| us_equity | 40% | SPY QQQ IWM |
| international | 25% | EFA EEM |
| rates | 35% | TLT IEF |
| metals | 20% | GLD SLV |
| commodities | 20% | DBC USO |
| us_dollar | 15% | UUP |

Cluster declaration order fixes the column order of every downstream matrix
(`amat/config/models.py` → `StrategyConfig.to_universe`). Long-or-cash only:
no leverage, shorting, inverse products, options, or futures.

v5 (`amat_v5.toml`) extends this to 17 symbols in 8 clusters with the **same
core**; see [Strategy versions](#strategy-versions-and-the-experiment-record).

### Step 1 — Per-asset volatility (`src/amat/features/__init__.py`)

From **adjusted closes** of completed sessions only:

```
r_t     = ln(P_t / P_{t-1})                      log_returns:53
sigma20 = EWMAStd(r, half_life=20) * sqrt(252)   ewma_std:92, annualized_volatility:158
sigma60 = EWMAStd(r, half_life=60) * sqrt(252)
sigma   = max(sigma20, 0.75 * sigma60, 0.05)     the 5% floor is absolute
```

`min_periods_sessions = 60` refuses to report an unconverged slow estimate.
The EWMA convention is pinned by hand-calculated fixtures in
`tests/unit/test_features.py`.

### Step 2 — Trend signal (`src/amat/features/__init__.py`)

For horizons h ∈ {21, 63, 252} sessions (`trend_scores:195`):

```
r_h = ln(P_t / P_{t-h})                          horizon_log_returns:74
z_h = clip( r_h / (sigma * sqrt(h/252)), -2, +2 )
s   = tanh( mean(z_h over the three horizons) )
s   = 0        where |s| < 0.20                  (deadband)
long_score = max(s, 0)                           long_scores:240 (long_only)
```

The deadband is the strategy's whole entry/exit rule: a symbol is held while
its score is nonzero and the allocator gives it weight. There are no stops,
no profit targets, and no discretionary exits.

### Step 3 — Covariance (`src/amat/portfolio/__init__.py`)

120-session annualized Ledoit-Wolf shrunk covariance
(`ledoit_wolf_covariance:73`), estimated from rows **through the decision
session only**. Complete rows are required — this is why the universe demands
continuous coverage.

### Step 4 — Volatility targeting (`src/amat/portfolio/__init__.py`)

```
q            = long_score / sigma                raw_exposure:111
forecast_vol = sqrt(q' C q)                      forecast_volatility:125
w            = q * 0.08 / forecast_vol           volatility_scaled_weights:131
```

The portfolio targets **8% annualized volatility**, not a return.

### Step 5 — Constraint stack (`apply_constraints:150`, fixed order)

1. symbol cap — no name above 20% (`max_symbol_weight`);
2. cluster gross caps — the table above;
3. gross cap — total exposure ≤ 100% (`max_gross_exposure`);
4. *(risk-contribution limit — unset in v1; enabling it is a new version)*;
5. forecast risk is recomputed after clipping;
6. the residual stays in **cash**. `allow_releverage_after_clipping = false`
   is pinned: the book is never re-levered to recover the 8% target after a
   cap binds. When every score is zero the portfolio is 100% cash.

`decide:251` composes steps 1–5 into the one function every runner calls —
the "one strategy core" guardrail.

### Step 6 — Rebalance discipline (`src/amat/execution/__init__.py`)

`build_instructions:49` emits a trade only for a contract-permitted reason:
a weight delta ≥ 2 percentage points (`min_weight_delta`), or a zero/non-zero
transition (`TradeReason.POSITION_TRANSITION` vs `WEIGHT_DELTA` — the reason
rides on the fill and is printed on the PDF's per-asset pages; a third
reason, `CORPORATE_ACTION`, marks in-flight instructions rewritten across a
split, `src/amat/backtest/runner.py:280`). Trades under
$25 notional are recorded as withheld, never silently dropped. Share counts
are exact fractional Decimals sized at the decision session's raw close.

### Step 7 — Execution timing (`src/amat/execution/__init__.py`)

A decision formed after the close of session `t` fills **no earlier than the
09:30–09:35 America/New_York window of session `t+1`**, at that window's
raw-price VWAP plus a side-adjusted 2 bps one-way cost (`one_way_cost:38`,
`SimulatedExecutionModel.execute:169`). `signal_to_execution_lag_sessions`
has a hard floor of 1 — lag 0 would execute on the signal bar, the canonical
look-ahead defect. A missing or zero-volume window records a **missed fill**
naming its reason; a price is never imputed, carried forward, or borrowed.
Sells settle before buys; a buy the running cash balance cannot fund is
recorded `INSUFFICIENT_CASH` rather than overdrawing.

### Step 8 — Costs and cash

2.0 bps one-way baseline with a mandatory 0/4/6/10 bps stress ladder
(`[costs]`). Cash earns 0% in the primary result; a Treasury-yield assumption
is a sensitivity that must set `is_primary = false` — and since 2026-08-11 the
runner **fails closed** on a cash source it cannot price rather than silently
crediting 0% (`src/amat/backtest/runner.py`, `test_backtest_invariants.py::
TestCashYieldSource`).

### Step 9 — Warm-up

252 completed sessions before the first decision (`[warmup]`), covering the
covariance lookback and volatility minimum period. **Warm-up happens inside
your `--start` window** — see the caveat in the command reference.

---

## How the backtest engine works

`src/amat/backtest/runner.py` — an explicit, ordered session loop. Feature
computation is vectorized; portfolio state advances one session at a time so
the causality claim stays checkable.

Per session `t`, in order:

1. **Settle**, in the accountant's fixed order (`SessionAccountant.advance`,
   `src/amat/backtest/accounting.py:69` — "deliberate and must not be
   permuted"): accrue cash yield on the *opening* balance; apply corporate
   actions dated `t` to opening positions (splits rescale quantity, dividends
   credit cash); apply the instructions scheduled *for* `t` against `t`'s
   execution window (sells before buys; fills, missed fills, costs); then
   mark holdings at `t`'s **raw closes**.
2. **Reconcile**: `equity == cash + Σ(quantity × mark)` within
   `DEFAULT_TOLERANCE`; a break raises, never warns.
3. **Decide** from data through `t`: `decide()` over **adjusted closes**
   produces target weights; the optional v4 stance blend and the optional v3
   weight-scalar multiply apply here, in that documented order.
4. **Schedule** the resulting instructions for session `t + 1`.

Every session appends a `SessionRecord` (equity, cash, gross exposure,
forecast vol pre/post-constraints, fills with reasons, missed fills,
attribution, binding constraints). Reports **read** these records; nothing
downstream recomputes strategy state. The final session's decision has no next
session inside the panel; `amat plan` exists to emit exactly that decision as
an inert artifact (`src/amat/execution/intent.py`).

Leakage is pinned by the decisive test: mutate every observation after a cut
and re-run — sessions through the cut must be **bit-identical**
(`tests/integration/test_backtest_invariants.py::TestCausality`, and
`tests/unit/test_v5_universe.py::TestCausality` for the expanded universe).

**Feature caching (2026-08-13).** The loop originally recomputed every feature
panel from the visible prefix at each decision — `O(T²)` for a run, with the
pure-Python EWMA recursion measured at ~91% of runtime (a full 2,665-session
study took 8–9 minutes; it now takes ~8 s, the residual being the per-decision
Ledoit–Wolf estimate). Because every feature recursion is strictly causal
(row `t` reads rows ≤ `t` only), row `t` of one full-panel pass equals the
last row of a prefix pass **bit-for-bit**, so `run_backtest` now computes the
panels once (`precompute_decision_inputs`,
`src/amat/portfolio/__init__.py`) and each decision reads its row. Verified
three ways: an 800-session golden run reproduces its pre-change equity-curve
SHA-256 exactly (565 fills, final equity to the last Decimal digit, 14.8 s →
1.0 s); `tests/unit/test_portfolio.py::TestPrecomputedDecisionInputs` locks
cached-vs-recomputed equality with `==`, not tolerances; and the
future-mutation tests above now exercise the cached path, since the runner
always uses it. Recorded results are unaffected — the change is a cache, not
a new convention. One deliberate semantic tightening: a panel defect that
feature computation rejects (e.g. a non-positive close) now fails the run at
start instead of degrading into per-session "no decision formed" warnings;
loaded and synthetic panels already exclude such defects upstream (the
snapshot loader refuses non-positive closes at load), and the behavior is
pinned by `test_a_non_positive_close_fails_the_run_at_start`. An independent
four-lens adversarial review (causality, byte-level identity at all 269
legal decision indices, behavior drift, coverage) confirmed the identity
claim and produced the tightened leakage assertions now in
`tests/integration/test_backtest_invariants.py`.

---

## Strategy versions and the experiment record

Every measured configuration lives in **`docs/experiment-registry.md`** with
its digest, funding, control tables, and verdict — including failures. A
version is a frozen config file; editing values in place invalidates every
prior result, and verdicts are recorded in the registry, never annotated into
config files (that would change the digest and orphan cited results).

| Version | Config | What it is | Verdict |
|---|---|---|---|
| **v1** | `amat_v1.toml` | The frozen 12-symbol core above | **Candidate.** Not promoted: no walk-forward, no holdout |
| v2 | `amat_managed_v2.toml` | 29-symbol managed risk overlay (grades, Kelly, heat, regime floor) | **REJECTED 2026-08-10.** Year gate 71.9% vs 40%; realizes 16% of its own vol target. Implementation deleted; config + record retained |
| v3-regime | *(no config — never a candidate)* | v1 conditioned on a regime-model scalar | **REJECTED 2026-08-10.** Beat shuffled/random controls, lost to its own static mean (1.04 vs 0.93) |
| **v3** | `amat_v3.toml` | v1 core conditioned on point-in-time **news** scalars (`newsdesk export-scalars`) | Wired, structurally v1 until forward news accrues. No independent result possible yet |
| v4 | `amat_v4.toml` | Convex blend of v1 with a defensive stance of the same core, λ from an unfitted breadth rule | **REJECTED 2026-08-11.** Timing genuinely informative (beat shuffled by +0.28 Sharpe on the first snapshot, +0.15 on the current one — the margin is seed/sample-sensitive), still lost to its own mean (1.03 vs 0.98 at $100k). Mechanism retained |
| v5 | `amat_v5.toml` | **Breadth expansion**: the byte-identical v1 core on 17 symbols / 8 clusters (adds FXI, TIP, HYG, EMB, VNQ) | **REJECTED 2026-08-11.** Underperforms v1 (0.88 vs 1.01); leave-one-out shows every addition diluted the book. Harness, config, and tests retained |

**The recurring lesson**, written into `.claude/skills/strategy-experiment`:
every conditioning/switching overlay measured so far kept rediscovering that
its *mean* was doing the work — a constant exposure scaling beat the timing.
v5 was designed so that critique cannot apply: it changes the opportunity set,
not a per-session series. Its falsification battery is instead **leave-one-out**
(`amat study --falsify-universe`): the candidate must beat v1 on identical
data/costs/funding, no smaller universe may match it, and the improvement must
not be carried by one lucky symbol. The harness refuses to run if anything but
the universe differs between candidate and baseline
(`src/amat/validation/falsification.py::falsify_universe`).

**v5 exclusions, with evidence** (data audit 2026-08-11): SHY (two genuine
distributions share ex-date 2021-12-01, unrepresentable in the
`(symbol, ex_date, kind)` schema — the loader fails closed, and re-ingestion
returns the same rows; also its 1.55% realized vol sits under the 5% floor);
VTI/VEA/VWO/IYR/AGG/XLK (correlation 0.97–0.99 twins of held symbols);
XLF/XLV/XLI/XLE/EWJ/LQD (sector rotation and residual overlap are not this
design). EMB was the one genuinely new asset worth downloading — EM sovereign
USD debt, the macro sleeve the previous 29 symbols did not span — and was
ingested into `alpaca-sip-2026-08-11`.

---

## Measured results — real data

<!-- RESULTS-START -->

Sample **2016-12-30 → 2026-08-10**, 2,414 measured sessions, snapshot
`alpaca-sip-2026-08-11`, **$100,000 starting capital** (combined portfolio and
each sleeve alike), 2 bps one-way, 0% cash, execution at the next session's
09:30–09:35 America/New_York raw VWAP, corporate actions applied (returns are
total-return). Full provenance and every superseded number:
`docs/experiment-registry.md`.

| | **v1 frozen (candidate)** | v5 breadth (rejected) | SPY buy-and-hold | 60/40 SPY/IEF monthly |
|---|---:|---:|---:|---:|
| Total | +85.24% | +66.77% | +299.83% | +148.52% |
| Annualized | +6.65% | +5.49% | +15.57% | +9.97% |
| Volatility | 6.59% | 6.22% | 17.95% | 10.65% |
| Sharpe | **1.01** | 0.88 | 0.87 | 0.94 |
| Max drawdown | **−8.69%** | −9.33% | −33.79% | −21.18% |

v1: $100,000 → $185,242; concentration gates PASS (symbol: GLD 23.1% of 25%;
year: 2026 at 21.0% of 40%); 11/12 sleeves profitable, median +23.30%, only
TLT loses. Benchmarks are frictionless while the strategies pay costs, so the
comparison tilts toward the benchmarks.

**How to read this, in four sentences.** v1 posts the highest Sharpe of
anything measured here at a quarter of SPY's drawdown, and still trails badly
on absolute return because it runs at 37% of SPY's volatility with
re-levering forbidden. None of the Sharpe differences is statistically
significant (standard error over 9.6 years ≈ ±0.38); the drawdown difference
is the difference of kind. The sample is one overlapping decade dominated by
a US equity bull market. This is a measurement, not evidence of future
profitability.

**v5 was measured and REJECTED on 2026-08-11.** The 17-symbol breadth
expansion of the identical core underperforms the 12-symbol v1 (0.88 vs 1.01),
and the leave-one-out battery shows the damage is monotonic: every 16-symbol
variant beats the 17-symbol candidate, none recovers the baseline. Four of
the five added symbols are profitable *in isolation* (per-asset sleeves:
EMB +21.0%, FXI +20.5%, HYG +18.3%, TIP +6.3%; VNQ loses −0.4%) yet every
one of them subtracts at the portfolio level —
the volatility-normalized allocator loads low-vol, thin-premium additions
heavily and crowds out the sleeves that carry the return. Full control table
and interpretation in the registry.

**v3 re-verified the same day**: with the news store still empty, the
conditioned run reproduces v1 byte-for-byte (+85.24% / 1.01), as designed.
**v4 re-measured at this funding**: rejection reproduces (0.98 vs 1.01, its
own static mean at 1.03).

Reports (all regenerated 2026-08-13 with real-table pages and run records):
`outputs/reports/amat_v1_study.pdf`, `amat_v3_study.pdf`,
`amat_v4_study.pdf`, and `amat_v5_study.pdf` (the latter carrying the
labelled Gemini advisory pages). Earlier generations:
`outputs/reports/archive/`. Results measured before 2026-08-11
(at $1,200,000 on `alpaca-sip-2026-08-08`, e.g. v1's +84.82%) remain recorded
in the registry; the funding and snapshot change moved every number by
hundredths and no conclusion.

> These are backtest measurements on a survivorship-biased universe over one
> overlapping decade, with no locked holdout and no walk-forward. They are not
> evidence of future profitability.

<!-- RESULTS-END -->

---

## Validation: walk-forward, Monte Carlo, regimes, risk:reward

Everything in this section was measured on 2026-08-13 on snapshot
`alpaca-sip-2026-08-11` at $100,000 and persisted as a run record under
`outputs/runs/` (see [Run records](#run-records-holdout-and-reproducibility)).
One sentence of scope first: **the entire historical sample is burned** — the
v1–v5 studies inspected all of it before any of this ran — so nothing below
is out-of-sample evidence. What it measures is *consistency* (was the
aggregate earned across years?), *robustness* (does the result survive
resampled markets?), and *structure* (how do risk and reward trade against
each other?).

### Walk-forward consistency (`amat walkforward`)

The frozen core fits nothing across runs, so the expanding walk-forward is
one full-history run sliced into calendar folds
(`src/amat/validation/walkforward.py`). Result: **9 of 10 folds positive
(90%)**; the single loser is 2018 at −1.09% (Sharpe −0.15); best full-year
Sharpe 2.25 (2016+2017); no fold carries the result. The three research
gates a walk-forward can measure all pass (Sharpe 1.01 ≥ 0.50, positive
folds 90% ≥ 60%, max DD −8.69% ≥ −20%) — necessary, never sufficient, and
consistency-only on burned data. Full fold table:
`docs/experiment-registry.md`.

### Performance by market regime (`amat walkforward`, same record)

Sessions labelled causally from SPY's own past (63-session trend sign ×
21-session realized vol vs its expanding median,
`src/amat/validation/regimes.py`), with session `t`'s return booked under
session `t−1`'s label — the contemporaneous convention manufactures regime
separation out of noise, which the 2026-08-13 audit caught (see Known
defects). The corrected table is flat: Sharpe 1.00 in bull-quiet (38% of
sessions), 1.29 in bull-volatile (40%), and a modest +3.8% annualized
(Sharpe 0.61) through bear-volatile (21%). **This sample shows no sharp
regime differentiation** — an honest finding that replaced a first-draft
table whose bull-quiet 1.80 / bear-volatile −0.14 split was partly the
attribution artifact. (The bear-quiet slice is 15 interleaved sessions —
noise, shown so thin slices aren't hidden.)

### The risk:reward frontier (`amat sweep-target-vol`, experiment 0001)

The volatility target is the strategy's one explicit risk:reward dial.
Swept over {5, 6, 8, 10, 12}% with the split fixed in advance (training
through 2022-12-30 ranks by fold consistency; validation 2023→2026 is
measured ranking-blind): annualized return rises almost linearly with the
target (validation +5.95% → +10.37%) while max drawdown worsens (−4.28% →
−7.57%) and consistency falls monotonically. The 6% target ranks first on
training consistency and holds on validation (Sharpe 1.27 vs the frozen
8%'s 1.19), a smooth plateau rather than a lucky spike — but the validation
Sharpes cluster inside one standard error, so this is a *structure* finding,
not an edge finding. **"Optimal" is a choice of objective**: per unit of
risk the evidence mildly favours 6%; per absolute annual profit it favours
the highest target your drawdown tolerance allows. Per the registered rule
the frozen 8% is unchanged; 6% earned eligibility for future registration
under the full protocol. Registration + outcome:
`configs/experiments/0001-target-volatility-sweep.toml`.

### Regime conditioning, third attempt (`amat regime-experiment`, experiment 0002)

Halve position size while SPY's 21-session realized vol exceeds its own
expanding 80th percentile — causal, zero fitted parameters, judged by the
same falsification battery that killed v3's KMeans regimes and v4's breadth
blend. It is the first conditioning candidate to nominally **survive** every
control (Sharpe 1.0494 vs shuffled 0.9446, random 1.0052, static-mean
1.0482) — and the honest reading is in that last number: the margin over
simply *always* running at the series' mean size is **0.0012 Sharpe**. The
timing is unproven; the real effect is the static risk transformation (max
DD −8.69% → −6.31% for 5.9pp of return), which the target sweep expresses
more directly. Not promoted; recorded so the third regime idea is not
re-derived a fourth time.

### Explicit exits: stop-loss / take-profit (`amat sweep-exit-rules`, experiment 0003)

Does an explicit per-position exit overlay — stop-loss and/or take-profit on
the adjusted entry basis, 21-session re-entry embargo — beat the frozen
core's implicit exits (deadband, volatility target)? Registered **before**
running, with the failure prior stated in advance: for a volatility-targeted
trend design, stops realize losses the sizing already sized for, and
take-profits truncate the right tail the positive skew depends on. The grid
(4 stop levels × 3 take-profit levels, embargo frozen at registration) ran
2026-08-14: on the training window every take-profit overlay looked better
(Sharpe 1.18 vs 0.89, shallower drawdowns), the top two fold scores tied
within 1e-09 — and on the ranking-blind validation window **every overlay
lost to the no-exit core** (+8.41% ann / 1.19 Sharpe vs the best overlay's
+6.94% / 1.13). Verdict machinery: tied ranking + no validation edge →
**falsified; the frozen no-exit core stands**. This is what "the optimizer
found the ideal stop-loss/take-profit" honestly looks like here: the ideal
is *none*, and the training window would have told you otherwise.
Registration + outcome: `configs/experiments/0003-exit-rules-sweep.toml`;
run record `outputs/runs/sweep-exit-rules-amat-etf-20260814T014415.823351Z`.
A 20-draw **randomized stop/TP control** (2026-08-15, `--random-control`)
confirmed the conclusion is not grid-shaped: zero random rules beat the
core's validation return; a third nudge past its Sharpe within noise by
cutting volatility — the trade the volatility-target dial prices directly.

### The stress battery (`amat stress`) — measured 2026-08-15

The entire former "Not yet run" list, one recorded run per strategy. v1
survives everything: 5× costs (Sharpe 1.01 → 0.77), execution delayed to
lag 3 (0.97), 10% of fills randomly withheld (1.03), session-close fills
(1.01). Leave-one-out over all twelve symbols — the measurable
survivorship bound — spans Sharpe 0.89–1.04: no single listing carries the
result. Top-10 trades carry 33.8% of net profit; the best year removed
leaves +66%. Correlation to SPY is +0.36. And the **signal-free allocator
control** delivers the honest headline: pinning every score to 1
out-returns the strategy on this sample (+93.6% vs +85.2%) at double the
drawdown (−16.9% vs −8.7%) — on this burned decade the signal's measured
contribution is *drawdown control, not raw return*. Full tables in the
registry; records `stress-amat-etf-20260815T063809.490261Z` (v1) and
`stress-amat-etf-regime-quality-20260815T063653.149460Z` (v6).

### Heavy-tailed regime stress (`amat stress-heavytail`) — measured 2026-08-15

**Synthetic. Not a backtest result and not a forecast.** Every other harness
resamples observed history and so cannot produce a move the sample never
contained. This one generates markets from an explicit process — a persistent
four-state regime chain (calm/normal/stressed/crisis) plus **Pareto**
jumps, market-wide and propagated by each symbol's measured beta — and runs the
unmodified engine on them.

Prices stay relevant because every symbol is calibrated from the snapshot: its
own last close as the starting level, its own realized volatility, its own
market beta, and the full empirical correlation matrix (PSD-repaired). Drift is
zero by default, so a long-only book cannot inherit the sample's bull market —
which is why the passive control loses money too and is the right comparison.

24 scenarios × 756 sessions, $100,000, 2 bps:

| | v1 | v6 | equal-weight buy-and-hold |
|---|---:|---:|---:|
| Median total return | −5.2% | +0.3% | −13.6% (v1 markets) |
| Median max drawdown | −10.1% | −7.5% | −29.6% |
| Worst drawdown seen | **−20.2%** | −13.1% | −43.8% |
| Losing scenarios | 19/24 | 11/24 | — |
| Smaller drawdown than passive | 23/24 | 24/24 | — |

**The historical −8.7% max drawdown is not a bound.** v1 drew down −20.2% in
one of these markets — 2.3× its worst observed drawdown — and lost money in 19
of 24. What holds up is the drawdown-control claim: it roughly halved the
passive book's drawdown in almost every scenario. At `--tail-index 2.0`, where
theoretical variance is infinite, results barely move: isolated extreme
sessions are absorbed by the deadband and volatility cap, and it is *sustained*
regime shifts that cost money.

### Forward projections (`amat project`) — measured 2026-08-15

The run's own net session returns, resampled forward from any funding.
For v1 from $100k: 1-year outcomes span −2.8%…+16.7% (p05–p95) with an
11.8% chance of a losing year; the 10-year median is +90.5% total with
drawdowns that run deeper than history's (p05 −14.6%). **Within-resampling
frequencies, never forecasts** — the caveat travels on every table, page,
and record. PDFs: `outputs/reports/amat_v1_projection.pdf`,
`outputs/reports/amat_v6_projection.pdf`.

### Trailing windows — 6m/1y/2y/5y/10y ending 2026-08-10

Produced by `scripts/horizon_sweep.py` (artifact
`outputs/campaigns/20260815T161255Z/horizon-sweep.json`, reproducing
`outputs/experiments/trailing-horizons-20260815.json` exactly for the four
windows they share).

Method, because it decides whether the rows are comparable at all: each
version runs **once** over the full snapshot and every window is a trailing
slice of that single equity curve, measured by one function
(`amat.validation.optimize.window_statistics`). Warm-up and open positions
carry into every window. All windows end on the snapshot's last completed
session, 2026-08-10; each start is that end minus a fixed number of calendar
months. No start date is searched over and no version gets its own window.

Total return / Sharpe / max drawdown, $100,000 funding, 2 bps one-way:

| Window | v1 | v4 | v5 | v6 (12 sym) | v6 (11 sym, no TLT) |
|---|---|---|---|---|---|
| 6m | +5.6% / 1.56 / −3.1% | +5.4% / 1.51 / −2.8% | +3.3% / 1.03 / −3.8% | +5.1% / 1.76 / −2.3% | +5.3% / 1.85 / −2.0% |
| 1y | +20.0% / 2.40 / −4.3% | +19.7% / 2.39 / −4.3% | +14.9% / 2.07 / −4.4% | +16.4% / 2.41 / −3.1% | +17.4% / 2.50 / −3.4% |
| 2y | +24.0% / 1.54 / −5.3% | +23.6% / 1.54 / −4.9% | +20.3% / 1.47 / −5.4% | +19.7% / 1.58 / −4.2% | +22.3% / 1.75 / −3.6% |
| 5y | +36.7% / 0.92 / −8.0% | +34.6% / 0.96 / −4.9% | +28.9% / 0.79 / −9.3% | +32.9% / 1.03 / −5.9% | +35.5% / 1.09 / −5.9% |
| 10y | +85.2% / 1.01 / −8.7% | +75.1% / 0.98 / −8.7% | +66.8% / 0.88 / −9.3% | +78.0% / 1.14 / −8.0% | +77.1% / 1.14 / −7.9% |

**v6 dropped TLT on 2026-08-15** at operator instruction (digest
`a27689fbe4ca07ab` → `2712f694227321e4`); the last two columns are the same
strategy before and after that one edit. Note the 10-year row: the deletion
*lowered* total return and left Sharpe unchanged, and the Monte Carlo
percentiles fell on all three dimensions. The gains are confined to the 2y/5y
windows, which is exactly where TLT's whipsaw losses were — the signature of
removing a known past loss, not of a structural improvement. It is hindsight
selection, measured and recorded as such in the registry. v1–v5 still carry
TLT.

The "10y" row is 2,414 sessions from 2016-12-30, not ten full years: the
snapshot's history begins there and the artifact flags the window as
truncated rather than annualizing a shorter span under a 10-year label.

v3 is absent from the table because it *is* its scalar series and the only
export in the repository, `outputs/scalars/news_scalars_2016-2026.csv`, is
constant 1.0 on all 2,665 sessions — it de-risks nothing, so a v3 run is
bit-identical to v1 by construction. See the registry entry.

The trailing year's Sharpe 2.4 is the kind of stretch the Monte Carlo
distribution produces *sometimes*; extrapolating it is exactly the error
the projection tables exist to prevent. A 6-month Sharpe is dominated by
its own noise and is reported for completeness, not because it informs.

### Monte Carlo robustness (`amat montecarlo`)

Stationary block bootstrap (`src/amat/validation/montecarlo.py`): resample
the panel's own *joint* session moves in geometric blocks (mean 21
sessions), rebuild 200 alternative markets on the same calendar, run the
full engine on each — warm-up, next-open VWAP fills, costs, accounting —
and place the observed result inside the resampled distribution. Cross-
sectional correlation survives exactly (a resampled session is one whole
row); dependence longer than the blocks is destroyed, which is conservative
for a trend strategy, whose premium (if any) *is* long memory.

Measured 2026-08-14 (200 paths, base seed 20260812, snapshot
alpaca-sip-2026-08-11; run record
`outputs/runs/montecarlo-amat-etf-20260814T014412.934963Z`):

| metric | p05 | p50 | p95 | observed | percentile |
|---|---|---|---|---|---|
| total return | +19.40% | +72.52% | +158.29% | +85.24% | 60% |
| annualized return | +1.87% | +5.86% | +10.42% | +6.65% | 60% |
| annualized volatility | 6.34% | 6.95% | 7.67% | 6.59% | 14% |
| Sharpe | 0.27 | 0.83 | 1.41 | 1.01 | 70% |
| max drawdown | −17.45% | −11.60% | −7.71% | −8.69% | 84% |

The observed history is an unremarkable draw from its own resampled
distribution — return and Sharpe near the middle, drawdown shallower than
84% of paths (the realized ordering of sessions was kinder than most
reshuffles, so expect *worse* drawdowns than history at the same process).
0.5% of paths end below initial funding; 2.5% draw down worse than −20%.
The equity fan, metric histograms, and this table are in
`outputs/reports/amat_v1_montecarlo.pdf`. Read every probability as a
within-resampling frequency, never as a forecast.

The same machinery is drivable interactively:
`notebooks/monte_carlo_study.ipynb` and
`notebooks/walk_forward_and_regimes.ipynb` (generated by
`scripts/build_notebooks.py`, executed headlessly by
`scripts/execute_notebooks.py`; `uv sync --group notebooks` first).

---

## The PDF reports

`amat study … --pdf out.pdf` writes the primary artifact
(`src/amat/reporting/pdf.py`). Every tabular page is drawn as a real table —
header row, gridlines, striping, right-aligned numerals — never as
monospaced text with ASCII borders (`_draw_table`; pinned by
`tests/unit/test_insights_and_pdf.py::TestTablesAreRenderedNotTyped`).
Pages, in order:

1. **Headline** — sample, funding, return/vol/Sharpe/drawdown, both
   concentration gates (symbol and year), and the caveat block. A FAIL gate is
   a rejection condition, not a footnote.
2. **All-asset comparison** — every asset's total-return path and the strategy,
   log scale, one fixed color per symbol (identity never rides on color alone:
   direct end labels + legend), strategy bold black.
3. **Cumulative growth** — strategy vs frictionless SPY and equal-weight
   reference baskets, with the drawdown track below. References bear no costs;
   the comparison is biased in their favor and labelled so.
4. **Total portfolio value over time** — the account in dollars: invested
   (marked holdings) and cash stacked to the equity line (the accounting
   identity, drawn), initial-funding reference line, final/peak annotations.
5. **One page per asset** — the adjusted price path with every executed trade
   marked (▲ buy / ▼ sell; filled = position transition, hollow = weight
   rebalance) and its recorded `TradeReason` annotated; sleeve statistics for
   the same symbol alongside. Annotated prices are raw execution VWAPs; the
   line is the adjusted series.
6. **Individual statistics** — the per-asset table: round trips, win rate,
   net P&L and share, average hold, time in market, buy-and-hold growth, and
   the independent sleeve result.
7. **Episode diagnostics (two pages)** — replayed from the run's own fills
   and reconciled against the ledger before anything is reported
   (`src/amat/reporting/insights.py`). Page one: captured trends (≥ +8% net)
   and whipsaws (loss held ≤ 21 sessions). Page two: give-backs
   (surrendered ≥ 50% of a ≥ 5% peak; partial exits coalesced so a peak
   counts once) and missed rallies (≥ +20% over 63 sessions at < 2% mean
   weight). If ledger reconciliation fails the report refuses to render —
   that is a data problem to investigate, never suppress.
8. **Advisory (optional, `--advisory`)** — a clearly labelled Gemini
   commentary page (`src/amat/reporting/advisory.py`). Input is aggregate
   statistics only; the output feeds nothing — no signal, weight, fill, or
   accounting entry derives from it. Uses the shared model policy and configured provider credentials;
   degrades to "no page, reason recorded on stderr" on any failure.

`amat montecarlo … --pdf out.pdf` writes a second, three-page document
(`src/amat/reporting/montecarlo.py`): the equity fan (resampled percentile
bands with the observed curve overlaid), the metric distributions with the
observed value marked and percentile-ranked, and the summary tables with
exceedance probabilities and the study's own caveat.

---

## Command reference

The complete surface: three console scripts (`amat`, `newsdesk`,
`deskreview`), five standalone scripts, and the quality gates. Every command
and every argument below is generated from the parsers themselves, so this
section is exhaustive, not illustrative. All commands run from the repository
root after `uv sync --locked`.

### Conventions shared across commands

- **`uv run amat --version`** prints the version; the top-level program
  description states the security posture: *"reads historical market data and
  cannot reach an account."*
- **Exit codes**: `0` success; `1` configuration, data, or accounting error;
  `2` usage error or a refused environment.
- **Environment refusal**: every `amat` command refuses to run when
  `AMAT_EXECUTION_MODE` is anything but empty/`backtest`, or when
  `AMAT_LIVE_TRADING_ENABLED` is truthy. Defence in depth — there is no
  execution path to enable.
- **`--json`** on most commands emits machine-readable JSON instead of the
  text tables; both views come from the same computed objects.
- **`--snapshot DIR`** names an immutable snapshot under `data/snapshots/`;
  its digests are verified at load and tampering fails the run.
- **`--start` / `--end`** (inclusive session dates) slice the sample.
  *Warm-up caveat*: 252 completed sessions must pass inside the window before
  the first decision, so `--start 2019-01-02` begins **measuring** around
  2019-12-31 — set `--start` ~13 months before the date you want measured.
- **Run records** (`study`, `study-v4`, `walkforward`, `montecarlo`,
  `regime-experiment`, `sweep-target-vol`, `sweep-exit-rules`): every run
  writes `outputs/runs/<run_id>/{manifest.json,results.json}` by default.
  `--run-root DIR` moves it (immutable evidence roots are refused);
  `--no-run-record` skips it — an unrecorded result cannot be cited.
- **Holdout enforcement** (same command list): after a declaration exists,
  any panel that crosses the locked boundary fails closed;
  `--allow-holdout REASON` authorizes the read and records the reason
  verbatim in the manifest. Resolution anchors to the repository root by
  marker, not the shell's working directory.

### `amat` — inspection (no market data read)

| Command | Arguments | What it does |
|---|---|---|
| `validate-config CONFIG` | positional config path | Parses and validates a strategy TOML (`extra="forbid"` — unknown keys are errors); prints the resolved identity and content SHA-256 |
| `show-config CONFIG` | positional | Prints every resolved frozen parameter of the configuration |
| `show-universe CONFIG` | positional | Prints the cluster/symbol table with caps and column order |
| `show-layout` | `--root` (default: cwd) | Prints the expected repository directory layout and which paths are immutable |
| `show-environment` | — | Prints the backtest-only environment verdict (the same check every command runs) |

### `amat` — engine verification on synthetic data

Never a market result; these exist so engine changes are testable without
touching real data.

**`amat backtest-synthetic CONFIG`**

| Argument | Default | What it does |
|---|---|---|
| `--sessions` | `1500` | Synthetic sessions to generate |
| `--seed` | `20260808` | Reproducibility seed for the generator |
| `--trend-strength` | `0.0` | Planted trend amplitude; `0` is a driftless random walk (the null-hypothesis panel) |
| `--initial-cash` | `100000` | Opening cash balance |
| `--cost-ladder` | off | Also run the mandatory 0/4/6/10 bps cost-stress ladder |
| `--json` | off | JSON instead of a table |

**`amat report CONFIG`** — synthetic run + full report pipeline (the report
renderer's own test-bed).

| Argument | Default | What it does |
|---|---|---|
| `--sessions` | `1200` | Synthetic sessions |
| `--seed` | `20260808` | Generator seed |
| `--trend-strength` | `0.35` | Planted trend amplitude |
| `--initial-cash` | `100000` | Opening cash |
| `--engine` | `v1` | Which runner to report on (v2 stays selectable only so its rejection is reproducible) |
| `--html PATH` | — | Also write a self-contained HTML report |
| `--json` | off | JSON instead of text |

### `amat backtest` — the one-line real-data backtest

**`amat backtest -v {v1,v2,v3,v4,v5,v6}`** — a thin front end over the
study pipeline: every guardrail, holdout check, and run record is identical
to the long-form commands, and this command adds no measurement code of its
own (one code path per version, so the shortcut cannot drift). v2 refuses
with the registry as the record (its runner was removed on rejection); v3
without `--scalars` is loudly noted as numerically identical to v1; v6 runs
the research core with its regime overlay honoring the configuration's
`capital_control_enabled` (false in the canonical file → static 6% core).

| Argument | Default | What it does |
|---|---|---|
| `-v/--strategy-version` | required | `v1`–`v6`; picks the checked-in config `configs/strategy/amat_<v>.toml` |
| `--out PATH` | `outputs/reports/<v>-<YYYYMMDD>.pdf` | The PDF report (dated with today's UTC date) |
| `--start YYYY-MM` | — | First session: a month (resolved to its first day) or a full date. Warm-up happens *inside* the window: the first ~252 sessions of any window warm the signal before the first trade |
| `--end YYYY-MM` | — | Last session: a month (resolved to its last day) or a full date |
| `--snapshot PATH` | newest under `data/snapshots/` | Snapshot directory |
| `--initial-cash` | `100000` | **Starting funds** for the combined portfolio |
| `--no-sleeves` | off | Skip the 12 per-asset diagnostic sleeves |
| `--scalars PATH` | — | v3 only: the news-conditioning CSV |
| `--advisory` | off | Labelled LLM advisory page (v1/v3/v4/v5) |
| `--run-root`, `--no-run-record`, `--allow-holdout`, `--json` | as everywhere | Standard record/holdout/output options |

```bash
amat backtest -v v1                                   # full history, dated PDF
amat backtest -v v6 --start 2020-07 --initial-cash 250000
amat backtest -v v1 --start 2019-06 --end 2022-12 --out outputs/reports/gfc-era.pdf
```

### `amat study` — the full backtest on real data

Runs the combined portfolio plus one independently funded per-asset sleeve
per symbol on the v1-frozen engine; writes the primary PDF artifact and the
run record.

| Argument | Required | Default | What it does |
|---|---|---|---|
| `CONFIG` | yes | — | Strategy TOML (v1, v3, or v5) |
| `--snapshot DIR` | yes | — | Immutable snapshot to run on |
| `--engine` | | `v1` | Runner choice (currently only `v1`) |
| `--initial-cash` | | `100000` | Combined-portfolio funding; the allocator spreads it, nothing is pre-assigned per symbol |
| `--sleeve-equity` | | `100000` | Opening equity of each per-asset sleeve |
| `--start` / `--end` | | full sample | Inclusive session bounds (warm-up caveat above) |
| `--no-sleeves` | | off | Combined portfolio only — much faster |
| `--html PATH` | | — | Self-contained HTML report |
| `--pdf PATH` | | — | The full PDF report (see [The PDF reports](#the-pdf-reports)) |
| `--scalars CSV` | | — | Per-session weight scalars in [0,1] (v3's news conditioning, from `newsdesk export-scalars`). Alignment is fail-closed: every run session must appear in the file |
| `--advisory` | | off | Append the clearly-labelled model commentary page (requires `--pdf`, configured model credentials, network); degrades to "no page, reason recorded" on any failure; nothing in the run derives from it |
| `--falsify-universe BASELINE` | | — | Run the leave-one-out universe battery against BASELINE (identical non-universe core enforced); how v5 was judged |
| `--run-root` / `--no-run-record` | | `outputs/runs` / write | Run-record persistence (conventions above) |
| `--allow-holdout REASON` | | fail closed | Authorize and record a read past the holdout boundary |
| `--json` | | off | JSON instead of text |

### `amat study-v4` — the stance-switching study

The v4 blend of the v1 core with a defensive stance of the same core
(REJECTED 2026-08-11; kept reproducible).

| Argument | Required | Default | What it does |
|---|---|---|---|
| `CONFIG` | yes | — | v4 switching TOML (`amat_v4.toml`) |
| `--snapshot DIR` | yes | — | Snapshot to run on |
| `--initial-cash` | | `100000` | Opening cash |
| `--start` / `--end` | | full sample | Session bounds |
| `--falsify` | | off | Run the blend controls: shuffled λ, turnover-matched random λ, static-mean λ, defensive-only. A v4 that does not beat every control is not a candidate |
| `--pdf PATH` | | — | Full PDF report |
| `--advisory` | | off | Gemini page (requires `--pdf`) |
| `--run-root` / `--no-run-record` / `--allow-holdout` / `--json` | | | As above |

### `amat` — validation and registered experiments

**`amat walkforward CONFIG`** — one expanding full-history run sliced into
calendar folds, with per-regime slices and the three measurable research
gates (printed as *in-sample analogues* of the promotion gates; consistency
evidence, never out-of-sample proof).

| Argument | Required | Default |
|---|---|---|
| `--snapshot` | yes | — |
| `--start` / `--end` / `--initial-cash` | | full sample / `100000` |
| `--allow-holdout` / `--run-root` / `--no-run-record` / `--json` | | conventions above |

**`amat declare-holdout`** — freeze the locked-holdout boundary at a
snapshot's last session. Immutable and append-only; a later declaration may
never move the boundary earlier.

| Argument | Required | What it does |
|---|---|---|
| `--snapshot DIR` | yes | The boundary becomes this snapshot's last session |
| `--reason TEXT` | yes | Why the boundary is being frozen now; recorded verbatim |

**`amat montecarlo CONFIG`** — stationary block-bootstrap Monte Carlo: the
snapshot's own joint session moves resampled into N alternative markets, the
full engine run on each, the observed result placed inside the distribution.
Bit-identical at any worker count.

| Argument | Default | What it does |
|---|---|---|
| `--snapshot` (required) | — | Snapshot to resample |
| `--paths` | `200` | Resampled paths |
| `--seed` | `20260812` | Base seed; path *i* uses `seed+i`, so any path regenerates alone |
| `--block-sessions` | `21` | Mean geometric block length (one month) |
| `--workers` | cores−2 | Process count; result identical at any count |
| `--pdf PATH` | — | The three-page Monte Carlo PDF |
| `--start`/`--end`/`--initial-cash`/`--allow-holdout`/`--run-root`/`--no-run-record`/`--json` | | Conventions above |

**`amat project CONFIG`** — forward performance-metric projection: runs the
strategy on the snapshot, then stationary-bootstraps its **own net session
returns** out to the requested horizons and reports outcome distributions
from a chosen funding. Every number is a within-resampling frequency,
never a forecast; the caveat is printed, recorded, and drawn on the PDF.

| Argument | Default | What it does |
|---|---|---|
| `--snapshot` (required) | — | Snapshot to run and resample |
| `--funding` | `--initial-cash` | **Starting funds** every projected path compounds from |
| `--horizon-years` | `1 2 5 10` | Forward horizons in years (space-separated) |
| `--paths` | `2000` | Resampled paths; percentile resolution is 1/paths |
| `--seed` | `20260815` | Base seed; path *i* uses `seed+i` |
| `--block-sessions` | `21` | Mean geometric block length |
| `--pdf PATH` | — | Two-page projection PDF (wealth fan + horizon tables) |
| `--start`/`--end`/`--initial-cash`/`--allow-holdout`/`--run-root`/`--no-run-record`/`--json` | | Conventions above |

**`amat stress CONFIG`** — the whole robustness battery in one recorded
run: the configured cost ladder, execution delays (lag 2/3), deterministic
5%/10% missed-fill injection, session-close fills, leave-one-out over every
universe symbol (the measurable survivorship bound), top-5/10 trade and
best-year removal restatements, the signal-free allocator control, and
SPY / 60-40 correlations. Surviving stress is necessary, never sufficient.

| Argument | Default | What it does |
|---|---|---|
| `--snapshot` (required) | — | Snapshot to stress on |
| `--start`/`--end`/`--initial-cash`/`--allow-holdout`/`--run-root`/`--no-run-record`/`--json` | | Conventions above |

**`amat stress-heavytail CONFIG`** — the **synthetic** counterpart, and the
only harness here that can produce a move the sample never contained. Generates
markets from a four-state regime chain plus Pareto jumps, calibrated per symbol
to the snapshot's own levels, volatilities, betas and correlation, then runs the
unmodified strategy on them. Output is never a backtest result.

| Argument | Default | What it does |
|---|---|---|
| `--snapshot` (required) | — | Snapshot to **calibrate** from; no observed return sequence reaches the strategy |
| `--scenarios` | `24` | Independent generated markets |
| `--sessions` | `756` | Sessions per market; must exceed the 252-session warm-up |
| `--tail-index ALPHA` | `2.5` | Pareto tail index. Lower is wilder; below 2.0 the theoretical variance is infinite |
| `--drift-annual` | `0.0` | Baseline annual drift. Zero so the strategy cannot inherit the sample's bull market |
| `--seed` | `20260815` | Base seed; scenario *i* uses `seed+i`, so any single scenario regenerates alone |
| `--start`/`--end`/`--initial-cash`/`--allow-holdout`/`--run-root`/`--no-run-record`/`--json` | | Conventions above |

All three commands (and every measuring command that loads a strategy
configuration) also accept the v6 research file: its extra tables are
routed through the v6 loader and the *strategy core* is measured — which
for canonical v6 is the entire candidate, since its overlay is disabled.
Since 2026-08-15 `validate-config`, `show-config` and `show-universe` accept
every checked-in schema too (plain, v6 superset, and the v4 switching file),
and print which one matched; previously they used the plain loader alone and
reported every v4 and v6 file as invalid.

**`amat regime-experiment CONFIG`** — registered experiment 0002: halve
position size while the reference symbol's realized volatility exceeds its
own expanding quantile, judged by the conditioning falsification battery
(shuffled / turnover-matched random / static-mean controls).

| Argument | Default | What it does |
|---|---|---|
| `--snapshot` (required) | — | Snapshot to run on |
| `--reference` | `SPY` | Reference symbol for the volatility state |
| `--vol-window` | `21` | Trailing realized-volatility window (sessions) |
| `--quantile` | `0.80` | Expanding quantile above which volatility counts as elevated |
| `--reduced-scalar` | `0.50` | Sizing multiplier during elevated-volatility sessions |
| `--seed` | `0` | Seed for the matched worthless controls |
| window/record/holdout/`--json` | | Conventions above |

**`amat sweep-target-vol CONFIG`** — registered experiment 0001: rank nearby
volatility targets by training-window fold consistency; validation measured
ranking-blind; a winner earns a registry entry, never an in-place change.

| Argument | Required | Default | What it does |
|---|---|---|---|
| `--snapshot` | yes | — | Snapshot to run on |
| `--train-end DATE` | yes | — | Last training session, inclusive. Choose before looking at results; never move it |
| `--targets` | | `0.05,0.06,0.10,0.12` | Comma-separated annualized volatility targets; the config's own target is always the baseline row |
| window/record/holdout/`--json` | | | Conventions above |

**`amat sweep-exit-rules CONFIG`** — registered experiment 0003: does any
stop-loss/take-profit overlay beat the frozen no-exit core? Full grid of
`{none, stops} × {none, take-profits}` on the adjusted entry basis, same
split discipline; unmeasured or tied rankings produce explicit no-winner
verdicts.

| Argument | Required | Default | What it does |
|---|---|---|---|
| `--snapshot` | yes | — | Snapshot to run on |
| `--train-end DATE` | yes | — | Split date; choose in advance, never move |
| `--stop-losses` | | `0.05,0.10,0.15` | Stop fractions from the adjusted entry basis; "no stop" always included |
| `--take-profits` | | `0.10,0.20` | Take-profit fractions; "no take-profit" always included |
| `--embargo-sessions` | | `21` | Re-entry embargo after a breach, in decisions — fixed at registration, not part of the grid |
| `--random-control N` | | — | Also measure N seeded uniform-random stop/TP pairs (stop 3–30%, TP 5–60%, same embargo/split) beside the no-exit core: a robustness control on the grid's conclusion, never a search; draws and seed recorded, nothing promotable |
| `--control-seed` | | `20260815` | Seed for the random draws |
| window/record/holdout/`--json` | | | Conventions above |

### `amat` — paper account and execution plan

**`amat simulate CONFIG`** — the paper account: replays from inception,
appends live Alpaca *market data* (read-only) past the snapshot, journals
every observation **and every paper-period trade** (`live_fills`: symbol,
side, exact quantity/price/cost, recorded reason). No account is connected;
no order is submitted, paper or otherwise.

| Argument | Required | Default | What it does |
|---|---|---|---|
| `--snapshot DIR` | yes | — | Base snapshot the live tail extends |
| `--equity` | | `100000` | Opening paper equity |
| `--offline` | | off | Replay the snapshot only; fetch nothing |
| `--journal DIR` | | `outputs/paper` | JSON-lines journal directory (one dated file per session) |
| `--json` | | off | JSON instead of text |

**`amat plan CONFIG`** — emit the decision the backtest's final session would
discard, as an inert, versioned JSON artifact. Submits nothing; equity and
positions are supplied by the caller, never fetched.

| Argument | Required | Default | What it does |
|---|---|---|---|
| `--snapshot DIR` | yes | — | Data through the decision session |
| `--equity CASH` | yes | — | Account equity the plan is sized against |
| `--positions FILE` | | flat | JSON mapping symbol → share count (e.g. `{"SPY": "120.5"}`); omit only when genuinely flat |
| `--engine` | | `v1-frozen` | Decision engine |
| `--offline` | | off | Use the snapshot as-is; fetch nothing |
| `--out PATH` | | stdout | Write the plan JSON here |
| `--json` | | off | JSON instead of text |

### `newsdesk` — the point-in-time news store

Isolated package (no imports in either direction with `amat`; a CSV is the
only bridge). See `docs/news-pipeline.md`.

| Command | Arguments | What it does |
|---|---|---|
| `ingest-marketaux` | `--symbols` (req, comma-sep), `--published-after` (req, UTC), `--published-before`, `--language` (`en`), `--force` | Pull vendor articles into the append-only store; windows deliberately overlap; a provider suspended by the quota circuit-breaker needs `--force` after the cause is fixed |
| `ingest-adanos` | `--ticker` (req), `--start` (req), `--end` (req), `--force` | Same, for the Adanos vendor |
| `coverage` | — | Report what date/symbol ranges the store covers, per vendor |
| `knowable` | `--as-of` (default now, ISO-8601 with tz), `--symbol`, `--limit` (`25`) | What was knowable at instant T **on our own ingestion clock** — `published_at` is never a knowability filter |
| `export-scalars` | `--start` (req), `--end` (req), `--out` (req, CSV), `--symbols`, `--baseline-sessions` (`63`), `--spike-multiple` (`3.0`), `--min-baseline-articles` (`10`), `--derisk-scalar` (`0.5`) | One row per calendar date: the point-in-time de-risk scalar from attention spikes (article count vs its trailing baseline). This CSV is v3's only input |

### `deskreview` — scheduled observation passes

`uv run deskreview {morning|midday|evening} CONFIG` — one review pass:
observation only; forms no decision, sizes nothing, submits nothing.

| Argument | Required | Default | What it does |
|---|---|---|---|
| `pass_name` | yes | — | `morning`, `midday`, or `evening` |
| `CONFIG` | yes | — | Strategy TOML (for universe/context) |
| `--snapshot DIR` | yes | — | Snapshot to review against |
| `--weights FILE` | | — | JSON mapping symbol → fraction of equity (operator-supplied) |
| `--news-db PATH` | | `data/news/news.sqlite3` | Point-in-time news store; absent/empty is *reported*, not assumed benign |
| `--journal DIR` | | `outputs/reviews` | JSON-lines output directory |
| `--json` | | off | JSON instead of text |

### Standalone scripts

| Script | Arguments | What it does |
|---|---|---|
| `scripts/ingest_market_data.py` | `--snapshot-id` (req), `--start` (`2016-01-04`), `--end` (exclusive, default today — the current session is never ingested), `--feed` (`sip`) | **The only network-touching entry point.** Requires `ALPACA_MARKET_API_KEY`/`ALPACA_MARKET_API_SECRET` in the shell; reaches exactly one host (`data.alpaca.markets`), GET-only, checkpointed. Adding an asset = adding it to the script's `UNIVERSE` tuple and pulling a **new** snapshot |
| `scripts/benchmarks.py` | `--snapshot` (req), `--symbols` (default `SPY QQQ IEF TLT GLD`), `--start`, `--end`, `--json` | Passive baselines (buy-and-hold, 60/40, equal-weight) on the identical session axis. Benchmarks are frictionless while the strategy pays costs — say so whenever comparing; match `--start/--end` to the run being compared |
| `scripts/trade_quality.py` | `--config` (default `amat_v1.toml`), `--snapshot` (req), `--initial-cash` (`100000`) | Round-trip trade quality: profit factor, net win rate, average/median net trade vs its own costs, average hold — the backtest-checklist numbers, from the same engine and round-trip coalescing as the study reports |
| `scripts/asset_audit.py` | `--symbol` (req), `--config`, `--snapshot` | Per-asset attribution: the standalone sleeve, every coalesced round trip inside the combined portfolio with per-year P&L, whipsaw share, signal occupancy, and the buy-and-hold comparison on the identical adjusted series |
| `scripts/horizon_sweep.py` | `--versions`, `--horizons` (`6m 1y 2y 5y 10y`), `--snapshot`, `--initial-cash` (`100000`), `--scalars`, `--out` | **The comparable trailing-horizon table.** Runs each version once over full history and slices that one equity curve per window, so warm-up and open positions carry in and every row uses one metric implementation. All windows end on the snapshot's last session; starts are that end minus calendar months |
| `scripts/build_notebooks.py` | — | (Re)generate `notebooks/*.ipynb` programmatically |
| `scripts/execute_notebooks.py` | — | Execute the notebooks headlessly so committed outputs are real (`uv sync --group notebooks` first) |

### Market distributions and scenarios

New short research sessions use **25 paired robustness cases** by default: 17
synthetic market/distribution scenarios, two joint block bootstraps, and six
historical execution stresses. Coverage includes Student's t and Laplace tails,
negative skew, clustered volatility, correlation breaks, gaps, flat markets, and
liquidity crises. Each case recomputes the candidate and baseline through the same
strategy/accounting engine and reports 95%/99% empirical tail losses.

Run `./research 1 --scenario-synthetic-paths 2 --no-open` to test one candidate with
two seeds per synthetic recipe. See the [market-condition testing guide](backtest-market-conditions.md)
for all assumptions, calibration boundaries, execution stresses, coverage gates,
and report fields. Existing frozen research sessions retain their original suite.

### The comprehensive campaign (`scripts/comprehensive_backtest.sh`)

One command runs the CLI strategy validation measurements against the registered
strategy versions on a single consistent basis and writes a self-describing campaign
directory:

```bash
bash scripts/comprehensive_backtest.sh                  # everything (~36 min)
bash scripts/comprehensive_backtest.sh --tier fast      # skip the slow phases
bash scripts/comprehensive_backtest.sh --versions "v1 v6" --initial-cash 250000
bash scripts/comprehensive_backtest.sh --dry-run        # print the plan, run nothing
```

| Flag | Default | Meaning |
|---|---|---|
| `--versions` | `v1 v3 v4 v5 v6` | Allowlisted; `v2` is refused with the registry as the record |
| `--horizons` | fixed `6m 1y 2y 5y 10y` | |
| `--snapshot` | newest under `data/snapshots/` | Must resolve inside that directory; `..` refused |
| `--initial-cash` | `100000` | Identical for every version and command |
| `--scalars` | `outputs/scalars/news_scalars_2016-2026.csv` if present | v3 conditioning series |
| `--tier` | `full` | `fast` skips Monte Carlo, the registered experiments, and most gates |
| `--out-dir` | `outputs/campaigns/<UTC stamp>` | |
| `--workers` | `min(nproc, 8)` | Monte Carlo only |

Phases: (1) the horizon sweep above; (2) one windowed PDF per version per
horizon; (3) stress battery, walk-forward, Monte Carlo and projections; (4)
the registered experiments and controls; (5) per-asset audits, trade quality
and passive benchmarks; (6) engine diagnostics plus `plan`/`simulate`
`--offline`; (7) the full quality gate.

Held fixed across all of it: one snapshot, one funding figure, one **end
session** (the snapshot's own last, never a chosen date), configured costs on
both sides of every fill, and the frozen 2022-12-30 experiment split. Window
starts are derived from the end session, never searched over. `--allow-holdout`
is deliberately unavailable, so a panel crossing the boundary fails the run
closed instead of quietly widening the sample.

A failed step never aborts the campaign and is never hidden: every step's exit
code and duration land in `steps.tsv` and `manifest.json`, and the script exits
non-zero if any failed. Skips carry their reason inline — `stress`/`walkforward`/
`montecarlo`/`project` are skipped for **v4** (its switching config carries
`[trend]`/`[defensive]`/`[switching]` tables the plain loader rejects) and for
**v3** (those commands take no scalars series, so they would run the identical
v1 core under a v3 heading).

The phase-2 windowed PDFs are **documents, not the comparable table**: each is
padded 14 calendar months so the engine can finish its 252-session warm-up
inside the run, and that pad sits inside the reported span. Phase 1 is the
table.

### Quality gates

```bash
uv run pytest -q                            # full unit + integration suite
uv run ruff check . && uv run ruff format --check .
uv run mypy src
bash .claude/scripts/quality_gate.sh fast   # or: full
uv run python3 .claude/scripts/validate_bundle.py
```

---

## Run records, holdout, and reproducibility

**Every report traces to a run manifest** is a promotion gate, and since
2026-08-13 it is machinery rather than aspiration
(`src/amat/reporting/artifacts.py`). Each measuring command — `study`,
`study-v4`, `walkforward`, `montecarlo`, `regime-experiment`,
`sweep-target-vol` — writes `outputs/runs/<run_id>/` containing:

- `manifest.json` — the `RunManifest` (`src/amat/provenance.py`): strategy
  name/version, configuration SHA-256 and source path, git revision (with a
  dirty flag; an undetected revision is recorded as undetected, never as
  clean), Python version, a dataset reference per snapshot (provider, feed,
  as-of, a combined digest over the partition checksums, session coverage),
  execution- and cost-model identifiers, every random seed, UTC start/end,
  and — when relevant — whether the locked holdout was read and why.
- `results.json` — the measured sections (study summary, falsification
  battery rows and verdict, walk-forward folds and gates, regime slices,
  Monte Carlo percentiles), bound to the manifest by its SHA-256. `Decimal`
  cash values serialize as strings so recording cannot re-round the ledger.

The run directory is created exclusively and never rewritten — a collision
fails loudly, and `outputs/runs` is treated as append-only evidence even
though only `outputs/validated`, `outputs/holdouts`, `data/raw`, and
`data/snapshots` are mechanically immutable (`src/amat/paths.py`).

**The locked holdout** (`amat declare-holdout`,
`src/amat/validation/walkforward.py`): a declaration freezes a boundary
session into `outputs/holdouts/holdout-declaration-<date>/` (immutable,
append-only). The declaration made on 2026-08-13 locks sessions after
2026-08-10 and records `history_burned: true` — everything at or before the
boundary was inspected by full-sample studies first, so no pre-boundary
window can honestly be called out-of-sample. After a declaration, every
panel-measuring command (`study`, `study-v4`, `walkforward`, `montecarlo`,
`regime-experiment`, `sweep-target-vol`) refuses a panel that crosses the
boundary unless the operator supplies a reason, which the manifest records
verbatim; enforcement lives in the shared panel loaders, so no measuring
command can skip it. A later declaration may never move the boundary
*earlier* — that would unlock sessions the standing declaration froze. A
configuration change motivated by anything seen past the boundary is an
automatic rejection.

---

## Repository structure — what lives where, and why

Three packages under `src/`, strictly separated: `amat` (the strategy and
backtest), `newsdesk` (point-in-time news intake; **no imports in either
direction** with `amat` — a CSV file is the only bridge, enforced by
`tests/unit/test_newsdesk_isolation.py`), and `deskreview` (read-only market
review passes).

### `src/amat/` — layered, and the layering is enforced

`tests/unit/test_dependency_direction.py` parses every module's AST and fails
the build on an import pointing downward. Domain is stdlib-pure by test.

| Layer | Module | What it holds |
|---|---|---|
| 0 | `errors.py` `paths.py` `provenance.py` | Domain exceptions (`CausalityError`, `DataIntegrityError`, …), repository layout, code-revision detection |
| 1 | `domain/types.py` | `Symbol`, `Price`, `Cash`, `SessionDate`, `Side` — validated primitives |
| 1 | `domain/bars.py` `fills.py` `orders.py` `positions.py` `actions.py` `ledgers.py` `instruments.py` | Frozen dataclass records: bars, fills (each carrying its `TradeReason`), simulated orders, positions, corporate actions, ledger snapshots, universe/clusters |
| 2 | `config/models.py` | Pydantic `StrategyConfig` — every frozen parameter, `extra="forbid"` |
| 2 | `config/loading.py` | TOML → validated config + content SHA-256 (the digest results cite) |
| 2 | `config/switching.py` | v4's two-stance `SwitchingStrategyConfig` |
| 3 | `boundaries.py` | The protocol interfaces the layers meet at |
| 4 | `features/` | Log returns, EWMA volatility, horizon z-scores, tanh + deadband (steps 1–2 above) |
| 4 | `data/alpaca.py` | The **only** network client: `data.alpaca.markets`, GET-only, two path prefixes |
| 4 | `data/ingest.py` `snapshot.py` | Snapshot writing/loading: digest verification, calendar checks, corporate-action conflict rejection, panel assembly |
| 4 | `data/scalars.py` | v3 weight-scalar CSV loader — fail-closed session alignment |
| 4 | `data/synthetic.py` | Deterministic synthetic panels for engine verification |
| 4 | `context/` | Causal multi-timeframe market-structure features (built for the rejected v2 overlay; retained) |
| 4 | `portfolio/` | Ledoit-Wolf covariance, vol targeting, the constraint stack, `decide()` (steps 3–5) |
| 4 | `execution/` | Weight→instruction conversion, simulated fills at window VWAP + costs (steps 6–7) |
| 4 | `execution/intent.py` | `ExecutionPlan` — the inert, versioned decision artifact |
| 4 | `backtest/runner.py` | The session loop; `StanceBlend` (v4 hook); `weight_scalars` (v3 hook); `exit_rules` (experiment 0003 hook) |
| 4 | `backtest/exits.py` | Stop-loss/take-profit overlay on the adjusted entry basis, with re-entry embargo (never part of the frozen core) |
| 4 | `backtest/accounting.py` | `SessionAccountant` — cash yield, dividends, splits, fills, the reconciliation identity |
| 4 | `backtest/panel.py` | `MarketPanel` — sessions × symbols matrices, three price bases kept distinct |
| 4 | `backtest/sleeves.py` | Per-asset sleeve studies (one independent $100k account per symbol) |
| 4 | `backtest/attribution.py` | Fill stream → round trips (average-cost, MFE/MAE) |
| 4 | `backtest/stances.py` | v4 stance assembly from a switching config |
| 4 | `regime/__init__.py` `switching.py` | `CausalRegimeModel` (rejected v3-regime experiment) and the v4 breadth series (EWMA-smoothed fraction of universe above its 63-sessions-ago close) |
| 4 | `validation/falsification.py` | The control batteries: `falsify_conditioning` (scalars), `falsify_blend` (stances), `falsify_universe` (leave-one-out) |
| 4 | `validation/conditioning.py` | `session_features`/`regime_scalars` — the scalar pipeline behind the rejected v3-regime experiment |
| 4 | `validation/optimize.py` | Train/validation split, window statistics, fold scoring, grid protocol |
| 4 | `validation/walkforward.py` | Expanding walk-forward folds, the measurable research gates, and the locked-holdout declaration/enforcement |
| 4 | `validation/montecarlo.py` | Stationary block bootstrap of the market panel; the Monte Carlo study (deterministic per-path seeds, process-pool parallel) |
| 4 | `validation/regimes.py` | Causal SPY trend/vol regime labels, per-regime performance slices, and the vol-state conditioning candidate (experiment 0002) |
| 4 | `validation/sweep.py` | The volatility-target sweep under split discipline (experiment 0001) |
| 4 | `validation/exitsweep.py` | The exit-rule sweep under the same discipline (experiment 0003) |
| 5 | `metrics/` | `summarize()` — return/vol/Sharpe/drawdown conventions, defined once |
| 5 | `reporting/` | `build_report`, HTML/text renderers, `insights.py` (episode diagnostics), `pdf.py` (the study report; `_draw_table`), `montecarlo.py` (the MC report), `advisory.py` (Gemini page), `artifacts.py` (run-record persistence), `sleeves.py` |
| 5 | `simulation/` | Paper account journalled to `outputs/paper/`, including the paper-period trade log (`live_fills`) |
| 5 | `planning/` | Assembles the `amat plan` artifact from a formed decision |
| 6 | `cli/main.py` | Argument parsing and command dispatch; no strategy math |

### Everything else

| Path | What it holds |
|---|---|
| `src/newsdesk/` | Append-only SQLite news store (`store.py` — UPDATE/DELETE refused by trigger), vendor clients (Marketaux, Adanos; quota circuit breaker in `ingest.py`), point-in-time queries keyed **only** on the store's own `ingested_at` clock, `features.py` (attention-spike scalar rule + CSV export), `cli.py` |
| `src/deskreview/` | Observation-only review passes over a snapshot + operator-supplied weights |
| `configs/strategy/` | One frozen TOML per strategy version (v1, v2 + sensitivity, v3, v4, v5) |
| `configs/experiments/` | Machine-readable experiment registrations, written **before** each run (0001 sweep, 0002 conditioning) |
| `configs/data/` | Data-source declarations |
| `data/snapshots/` | Immutable, digest-verified Parquet snapshots (manifest + daily + windows + actions) |
| `outputs/reports/` | Generated study artifacts (older generations in `archive/`) |
| `outputs/runs/` | One directory per run: `manifest.json` + `results.json`, created exclusively, never rewritten |
| `outputs/holdouts/` | Immutable locked-holdout declarations, one dated directory each |
| `outputs/scalars/` | Exported news-scalar series (self-describing headers) |
| `notebooks/` | Generated research notebooks (Monte Carlo; walk-forward + regimes) — reduced-window demos of the library API |
| `scripts/ingest_market_data.py` | The one network entry point (see above) |
| `scripts/benchmarks.py` | Passive baselines on the identical session axis |
| `scripts/build_notebooks.py` `execute_notebooks.py` | Author the notebooks programmatically; execute them headlessly so committed outputs are real |
| `docs/strategy-guide.md` | **The consolidated strategy guide**: every rule with zero ambiguity, exit semantics, the risk:reward frontier, the backtest checklist answered, paper trading, anti-overfitting posture |
| `docs/experiment-registry.md` | **Every measured configuration and verdict, including failures** |
| `docs/news-pipeline.md` | The news architecture: two clocks, breaker, export |
| `docs/market-data-and-simulation.md` | Data provenance and timing semantics |
| `docs/execution-repo.md` | The contract a *separate* execution repository would have to satisfy |
| `docs/audit-2026-08-08.md`, `docs/results-2026-08-08.md` | Adversarial audit record; v2 post-mortem |
| `tests/unit/`, `tests/integration/` | ~950 tests; the invariant tests are the spec |
| `.claude/references/` | The normative strategy contract, validation protocol, promotion gates |
| `.claude/skills/` | Operational runbooks: `amat-study-reports`, `strategy-experiment`, `news-scalars` |

### Tests worth reading first

- `tests/integration/test_backtest_invariants.py` — causality (future-mutation),
  accounting identity, reproducibility, cost monotonicity, the driftless-noise
  null hypothesis, cash-source fail-closed.
- `tests/unit/test_read_only_data_access.py` — no module but `data/alpaca.py`
  may import a transport, name a trading host, or contain a state-changing
  HTTP verb.
- `tests/unit/test_v5_universe.py` — v5's null-case bit-identity and causality.
- `tests/unit/test_universe_falsification.py` — the leave-one-out battery's
  guard, null case, and verdict logic.

---

## Data: snapshots, corporate actions, news

| Field | Value |
|---|---|
| Provider / feed | Alpaca Market Data v2, SIP consolidated tape |
| Current snapshot | `data/snapshots/alpaca-sip-2026-08-11` (30 symbols, 2016-01-04 → 2026-08-10, 2,665 sessions, 79,950 daily rows, window coverage **1.0000**) |
| Prior snapshot | `data/snapshots/alpaca-sip-2026-08-08` (29 symbols, → 2026-08-07; results before 2026-08-11 cite it) |
| Credentials | `ALPACA_MARKET_API_KEY` / `ALPACA_MARKET_API_SECRET`, read by name, never rendered or persisted |

Three price bases are kept distinct and structurally hard to confuse:
**adjusted close** for features, **raw close** for valuation, **raw
09:30–09:35 VWAP** for execution. Snapshots are immutable and digest-verified
at load (`data/snapshot.py`); tampering fails the load.

**Corporate actions** are applied to the ledger (splits rescale quantities,
dividends credit cash on the ex-date), so results are **total-return** and
comparable to total-return benchmarks. Ambiguity fails closed: the known SHY
2021-12-01 conflicting dividend (two genuine distributions, one ex-date — the
vendor re-serves both on re-ingest) raises `DataIntegrityError` for any run
that requests SHY, which is why v5 excludes it.

**News** (`docs/news-pipeline.md`): `newsdesk` answers exactly one question —
*what was knowable at instant T on our own ingestion clock?* `published_at` is
the vendor's revisable claim and is never a knowability filter; backfilled
archives can never produce valid point-in-time features here by construction.
The store began ingesting 2026-08-11, so every v3 scalar is currently 1.0 and
v3 is numerically v1 — the designed degeneration, not a bug. The scalar enters
the runner as a per-session multiplier on the combined portfolio's targets
*after* allocation (shrinking a cap-satisfying book keeps every cap
satisfied), and sleeves stay unconditioned diagnostics.

> **This universe is survivorship-biased.** Every symbol was chosen in 2026
> for having persisted with continuous SIP coverage since 2016. That biases
> every result upward by an amount this project has not measured.

---

## Backtest fidelity: how close is this to live?

An honest ranking of the gaps between this simulation and a live account
(full audit 2026-08-11), what the repo already does about each, and what it
cannot do. This section is the answer to "will it perform similarly live?" —
and the summary answer is: *the accounting and causality are tight; the
execution-price realism and the absence of out-of-sample validation are the
two places live results will differ first.*

1. **Opening-window fill realism (largest gap).** Every fill prices at the
   09:30–09:35 raw VWAP + 2 bps. A real small order crosses the spread or
   takes the opening auction print in the widest-spread part of the day; only
   a VWAP algo participating across the whole window approaches the modelled
   price. *Feasible improvement:* keep the window's minute OHLC at ingest
   (currently discarded, `data/alpaca.py`) and add a pessimistic-fill
   sensitivity (e.g. worst-of-window, or open + spread). *Irreducible:* queue
   position, auction imbalance, and the order's own impact.
2. **Flat cost model.** One 2 bps figure for SPY (true spread ~0.3 bp) and
   DBC/USO/UUP alike (multiples of that, wider at the open). The stress
   ladder (0/4/6/10 bps) tests the level, not the cross-section. *Feasible:*
   per-symbol cost calibration from observed spreads — a new registered
   experiment, since it changes every number. The `slippage` attribution
   field exists and is currently hardwired zero (`backtest/accounting.py`).
3. **No participation constraint.** Fills are all-or-nothing at the window
   VWAP regardless of size vs window volume. At $100,000 funding this is
   realistic (a full cluster-cap DBC position is ~0.03% of the median DBC
   window's notional); at institutional size it would not be. The funding
   change to 100k made this gap small — honestly, that is the main fidelity
   argument for the 100k default.
4. **Fractional shares.** The backtest sizes exact fractional Decimals. Live,
   opening-window orders at most brokers are whole-share; at $100,000 the
   worst-case whole-share rounding drift is roughly 1.4% of gross exposure,
   unmodelled. The plan artifact says this out loud in its caveats
   (`execution/intent.py`).
5. **Cash yield.** The primary result pins cash at 0% by contract. Long
   100%-cash stretches at 4–5% bill yields make live results *better* than
   backtest on that leg; the Treasury sensitivity now fails closed instead of
   silently running at 0% (fixed 2026-08-11).
6. **Dividend timing.** Credited on the ex-date and spendable same-session;
   live cash arrives on the payment date weeks later. Alpaca's `payable_date`
   is dropped at ingest — feasible to keep and defer crediting.
7. **Calendar and halts.** No exchange calendar is held: the session axis is
   the intersection of complete vendor coverage, execution dates are
   lower-bounded and resolved by the (hypothetical) executor, intraday halts
   appear only as zero-volume windows → recorded missed fills, never retried
   (`RETRY_MISSED_FILL` exists in the domain but is never emitted).
8. **Data revisions.** Snapshots are immutable and as-of-stamped — strong —
   but the `simulate` command's appended live tail is revisable vendor data,
   and no process measures the vendor's revision rate between snapshots.
9. **Survivorship.** The universe was chosen in 2026 for continuous coverage
   over this sample, so it contains only instruments that persisted. The
   bias here operates at the *asset-class-menu* level, its mildest form:
   these are broad asset-class ETFs with 15+ year histories, not
   hand-picked stocks, and the per-session signals are causal — what the
   selection flatters is the availability and health of the menu itself
   (no failed ETN, no delisted niche product, no asset class whose vehicles
   died is on it). Measuring the effect needs delisted-ETF histories, a
   vendor product this repository does not ingest; until then it is
   **documented and unmeasured**, and every study report carries the caveat.
   The worst-case reading — that a 2026-chosen menu of major asset classes
   over 2016–2026 embeds a mild optimism that cannot be quantified from this
   data — should be assumed, not argued away.
10. **Sizing-timing gap.** Share counts fix at the decision close and fill at
    the next morning's VWAP; the overnight gap is real weight error, modelled
    honestly (including `INSUFFICIENT_CASH` misses).

**What must exist before any live-transfer claim:** genuinely out-of-sample
evidence — the holdout boundary was declared on 2026-08-13 and the
walk-forward consistency battery now runs (`amat walkforward`), but every
session at or before 2026-08-10 is burned, so real out-of-sample evidence
only begins accruing with post-declaration data; the pessimistic-fill
sensitivity (1); and a paper-trading period reconciled daily against
`amat plan` artifacts. In that order.

---

## Development and quality gates

```bash
uv run pytest -q                       # ~950 tests, ~4 min
uv run ruff check .
uv run ruff format --check .
uv run mypy src
bash .claude/scripts/quality_gate.sh fast   # or: full
python3 .claude/scripts/validate_bundle.py  # agent/skill/rule bundle
```

House rules that shape the code (`.claude/rules/`): every defect fix ships a
regression test that fails before the fix; hand-calculated fixtures for money
math; `Decimal` for cash, floats only at declared boundaries; timezone-aware
datetimes; no mutable module state; domain-specific errors naming dates and
invariants; and no test may be weakened to make an implementation pass.

---

## Known defects and audit trail

**Multi-session-lag double-sell — found and fixed 2026-08-15.** Building
the stress battery's execution-delay rows exposed a real engine gap: at a
signal-to-execution lag of 2 or more, consecutive decisions overlap in
flight, and the instruction builder — computing deltas from settled
holdings only — scheduled the same exit twice (measured: an
`AccountingError` short on the real panel, first at UUP 2017-04-13).
Two-part fix, both provably inert at the baseline lag of 1 where nothing is
ever in flight at decision time (golden-digest identity re-verified):
decisions now net in-flight scheduled quantities into their deltas, and a
sale whose backing evaporates (a netted buy misses at execution) is clamped
to settled holdings with the excess recorded as an `UNBACKED_SALE` missed
fill — rejected like a cash brokerage would, never shorted, never silent.
The baseline configuration was never affected; every recorded primary
result is unchanged. Two research-control hooks were added in the same
change, both refused on primary runs by their own validation:
`drop_every_nth_fill` (deterministic missed-fill injection, reason
`STRESS_DROPPED`) and `signal_override` (the signal-free allocator control,
availability-masked so a control can never trade before the strategy
could).

The adversarial audit record lives in `docs/audit-2026-08-08.md` (33 raw
claims merged into 24 verified findings) and the registry. Highlights caught
by that audit: the `is`-vs-`==` StrEnum comparison that silently inverted a
fill's sign; the diversification floor's floating-point knife edge; Kelly
being dimensionally inert in v2. The v2 risk overlay's own defect history
(the position cap liquidating the positions it protected, heat leaks,
R-multiple corruption) was fixed test-first before the overlay's rejection
and left the repository with the overlay's deletion; the narrative survives
in the registry and `docs/results-2026-08-08.md`. Later data-layer fixes —
vendor `end`-inclusivity requesting in-progress sessions, truncated
multi-megabyte responses retried incorrectly — are recorded in
`docs/market-data-and-simulation.md`.

Fixed on 2026-08-11 during this build:

- **Silent 0% cash fallback** — a `treasury_3m` cash source ran at 0% without
  saying so; now `ConfigurationError`, with a regression test.
- **DST window labels** — `panel.windows_at` stamped 14:30 UTC year-round, an
  hour late all summer (labels only; prices were always the stored VWAPs).
- **Study funding coupling** — combined-portfolio funding was
  `sleeve_equity × N`; now flat $100,000, independent of the sleeve list.
- **Give-back inflation** (2026-08-11, earlier session) — partial exits each
  carried the position's full peak, inflating "total surrendered" ~8×;
  round trips are now coalesced per `(symbol, entry_session)`.

Fixed on 2026-08-13 during this build:

- **Run records now exist** — every measuring command persists
  `manifest.json` + `results.json` under `outputs/runs/` (the "no run
  manifests are persisted" item below is closed), including the
  falsification batteries that previously lived only on stdout.
- **`outputs/validated` masking** — in the sandboxed environment that root
  is shadowed by a read-only placeholder, so holdout declarations write to
  the parallel immutable root `outputs/holdouts/` (same append-only
  guarantees; mirrors the existing `data/raw` → `data/snapshots` split).

Fixed on 2026-08-13 after the same-day adversarial audit of the new
validation layer (each with a regression test that fails on the old code):

- **Holdout enforcement had one caller** — only `walkforward` checked the
  declared boundary; `montecarlo`, `sweep-target-vol`, `regime-experiment`,
  and the studies could read past it while writing an affirmative
  `holdout_accessed: false` into their manifests. Enforcement now lives in
  the shared panel loaders; every panel command carries `--allow-holdout`.
- **Sweep verdicts from unmeasured fields** — a field of below-minimum-trade
  candidates (fold score −inf) "ranked" by input order and could emit a
  promotion-shaped verdict; exact score ties broke silently by ascending
  target. The verdict now declares unmeasured/tied fields outright, and the
  sweep carries a burned-history caveat into its record.
- **Contemporaneous regime attribution** — booking session `t`'s return
  under `t`'s own label let large moves select themselves into the volatile
  buckets (a ~1.27× quiet/volatile separation manufactured on pure iid
  noise). Returns now book under the prior session's label, and slice
  annualization uses the platform's realized-CAGR convention instead of
  compounding the arithmetic mean (which overstated the volatile rows).
- **Gate names overclaimed** — the walk-forward research gates dropped the
  "out-of-sample" qualifier from the promotion gates they mirror; names now
  say "in-sample analogue", and the fold gate refuses to decide below 4
  folds instead of passing 60% on two observations.
- **Inert immutable-root guard** — `persist_run_record`'s check compared
  paths against the wrong base, so `--run-root outputs/validated` would have
  complied; it now resolves the owning repository root and refuses.
- **Holdout anchoring and monotonicity** — declarations resolved against the
  shell's cwd (a subdirectory invocation saw "no declaration") and nothing
  stopped a later declaration from moving the boundary *earlier*; both now
  fail closed.
- **Monte Carlo universe mismatch** — `run_monte_carlo` accepted a
  caller-supplied universe different from the strategy's own, which would
  have placed an observed run of one symbol set inside a bootstrap
  distribution of another; now a hard error.

Known and open (also listed in the registry):

- The config digest hashes comments, so a comment edit changes identity.
- `detect_code_revision` can report a foreign repository's commit as clean;
  `RunManifest` accepts zero datasets as `passed`; `ensure_writable` is
  check-then-open (TOCTOU).
- Intraday (4H/1H) bars are not ingested; the strategy is daily-only.
- SHY's 2021-12-01 conflicting dividend blocks any study that requests SHY.

---

## What has *not* been done

- **No out-of-sample evidence.** The walk-forward, regime slices, Monte
  Carlo intervals, and holdout *machinery* all exist and have run (see
  [Validation](#validation-walk-forward-monte-carlo-regimes-riskreward)),
  but every session at or before the 2026-08-10 holdout boundary is burned —
  so no promotion decision is possible for any version, v5 included, until
  post-declaration data accrues and the holdout protocol runs on it.
- No event-driven runner (NautilusTrader adapter), therefore no parity gate.
- No per-symbol cost calibration; no pessimistic-fill sensitivity; the
  remaining robustness battery (cost stresses on real data, execution
  delays, deterministic missed fills, trade-removal, signal-free allocator)
  is listed in the registry's "Not yet run".
- Survivorship bias is documented and unmeasured (fidelity item 9).
- The news store has days of forward history; v3 is structurally v1 until
  months of collection accrue.

---

## Normative references

| File | Defines |
|---|---|
| `.claude/CLAUDE.md` | Mission, guardrails, working method, completion standard |
| `.claude/references/strategy-contract.md` | Normative v1 formulas, timing, caps |
| `.claude/references/validation-protocol.md` | Required validation sequence |
| `.claude/references/promotion-gates.md` | Gates and rejection conditions |
| `docs/experiment-registry.md` | Every measured configuration and verdict |
| `docs/market-data-and-simulation.md` | Data provenance, sleeves, simulation |
| `docs/news-pipeline.md` | Point-in-time news architecture |
| `docs/execution-repo.md` | The contract for any future executor repository |

---

## Disclaimer

This software is for research and backtesting. It produces no investment
advice and makes no claim of profitability. Results describe a historical
simulation on a survivorship-biased universe with modelled costs and idealized
fills, and are not evidence about future returns.

## License

Proprietary. Private — do not upload.

See [strategy flexibility and the failure library](strategy-flexibility-and-failure-library.md) for broader exploration,
research briefs, and analysis of every unsuccessful attempt.

### Shared model policy (September 2026)

Normal research uses `./research run [N]`, `./research status`, and
`./research report`; proposals, evaluations and result reports run automatically.
Detailed diagnostics remain under `./research advanced --help`.

Default report commentary and automatic committee reviews
prefer Groq `openai/gpt-oss-120b`, then Gemini `gemini-3.8-flash`, then OpenRouter
`stealth/space-bunny-alpha` when earlier routes are missing, unavailable or invalid. Groq's
free allowance depends on the account plan; its paid tier is not a zero-price
endpoint. Space Bunny is currently a free preview; the
actual served model is recorded separately from the requested router name.

Coding requests prefer OpenAI `gpt-5.6-luna`, then Gemini, Groq, and Space Bunny.
Shared complex requests and committee chief adjudication remain Luna-only. For the Luna-only route, a missing key, rate limit or invalid output remains an
explicit unavailable result. These routing choices are not model-quality certification.

Credentials are `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY` and
`OPENAI_API_KEY`; the research wrapper loads `amat/.env`. An optional
`AMAT_MODEL_CHAIN=/absolute/path/models.toml` applies a shared TOML policy to new
research, automatic committee reviews and default commentary. The example is
`configs/autoresearch/llm-chain.example.toml`; `targets` covers routine calls and
`complex_targets` covers complex calls; optional `coding_targets` takes precedence for coding.
Research uses its separate [research chain](research-model-fallbacks.md). Explicit `--chain` overrides and
existing frozen session policies stay authoritative. Existing Gemini/OpenAI
committee options remain explicit overrides; `--provider fake` is the offline
fixture provider, never a remote-model fallback.

Committee fallback attempts reserve and settle usage independently through the
existing store. Research attempts use the existing ledger. Commentary returns
per-attempt usage metadata in its provenance. This does not yet constitute one
global cost ledger or completed research quality evaluation.

Newsdesk currently ingests vendor sentiment; it has no local LLM sentiment stage.
An LLM news adapter must use this policy when that evaluated stage is implemented.

A separate fixed-recipe return-target study can run through registration,
development screening and one selected candidate's historical validation:

```bash
./research returns run --target-cagr 0.20 --output outputs/return-target-20pct-01
```

Use a new output directory for each registered study. `0.20` means 20% annualized
net return. This command tests the frozen target at normal and stressed costs;
it does not iterate indefinitely until passing or change the default research
score. It uses the existing v6-based recipe set and historical data contract,
not the LLM proposal loop. Its decision explicitly distinguishes a historical
threshold pass from future returns. `./research run 20` instead requests twenty
candidate strategies under the normal research score.

New daily research sessions now start from the selected **v13** allocator and freeze
its return-target reference evidence. Existing E73/v6 sessions retain their saved
programs. Continue to use `./research run 100`; its argument requests candidates,
not a return percentage. New sessions preserve best returns and best eligible
research scores separately, grouped by comparable evaluation contracts. Completed
recipes and their evidence remain in the session ledger and shared
`~/Documents/amat-research/best-strategies.sqlite3` archive.

For the separate training-through-2025 program:

```bash
./research run 100 --program training-2025
```

That creates a new frozen program. It does not retrain or rewrite older sessions,
or treat previously inspected 2026 history as pristine validation.

New daily sessions also verify market-session dates against a version-pinned XNYS
calendar and freeze that evidence. After an unsuccessful batch of at least three
attempts, a research engineer can propose bounded code changes using GPT-5.6-luna.
Its default is one engineering request per session, configured in
`configs/autoresearch/engineering.json`. `--built-in` runs do not request code.

Proposed replacements and a diff appear under the session's `engineering/` folder;
`./research status` and the report show their state. Code remains staged pending
isolated validation: it is not applied to the running strategy or imported by the
research worker. See [the autonomy implementation plan](research-autonomy-2026-09-27.md)
for the next steps toward automatic code evaluation and strategy promotion.


### Controller-led research

`./research run [N]` and `./research intraday [N]` start Controller, which reads
research evidence, supplies a brief and batch size, and supervises follow-ups in
the same frozen session. The research ledger is read-only to Controller; its own
plans and events live in an adjacent audit directory. See the
[Controller guide](research-controller.md) for limits, plan previews and resume.
