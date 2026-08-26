# AMAT strategy guide — the frozen v1 core, its rules, and its evidence

This is the answer-first consolidation of what the strategy *is*, where every
rule is defined with zero ambiguity, what evidence exists for it, and what has
deliberately **not** been claimed. Normative definitions live in
`.claude/references/strategy-contract.md`; this guide restates them for a
reader and links every number to a persisted run record under `outputs/runs/`.

Nothing in this document is a profitability claim. Every measured figure is a
property of one burned historical sample under stated assumptions.

---

## 1. Identity

| | |
|---|---|
| Strategy | `amat-etf` v1.0.0 (`configs/strategy/amat_v1.toml`) |
| Style | Daily, long-or-cash, volatility-targeted multi-asset trend |
| Universe | SPY QQQ IWM EFA EEM TLT IEF GLD SLV DBC USO UUP (12 ETFs, 5 clusters) |
| Frozen | 2026-08-10; every parameter below is fixed, none is fitted at runtime |
| Data | Immutable snapshot `alpaca-sip-2026-08-11` (2016-01-04 → 2026-08-10, 2,665 sessions) |
| Holdout | Sessions after 2026-08-10 locked (`outputs/holdouts/holdout-declaration-2026-08-13/`) |

## 2. The rules, with zero ambiguity

Every rule below is a pure function of configuration constants and data
available at the decision timestamp. There is no discretionary input, no
fitted parameter, and no rule that requires interpretation.

**Signal (per symbol, at the close of session `t`):**

1. Compute trailing total returns over 21, 63, and 252 *completed* sessions
   on the adjusted (total-return) close.
2. Normalize each by the EWMA realized volatility (20- and 60-session
   estimates, floored) so a 5% move in a quiet asset and a 5% move in a wild
   one do not read the same.
3. Clip each normalized trend, average the three horizons, pass through
   `tanh`, and apply the dead-band: a signal inside the band is exactly zero.
   The result is a conviction in [0, 1] — the strategy never shorts.

**Entry:** a symbol enters when its dead-banded signal becomes positive.
There is no confirmation rule, no discretionary filter, and no partial-entry
schedule beyond what the sizing below produces.

**Sizing:** signal-weighted allocations are scaled so the *portfolio's*
forecast annualized volatility is 8%, then clipped by three hard caps —
per-symbol, per-cluster, and 100% gross exposure (no leverage). Residual
weight is cash at 0% (a Treasury-yield cash rate exists only as a
sensitivity).

**Exit — where profit is taken and losses are cut.** The frozen core has no
per-trade stop-loss or take-profit. Exits are continuous, through three
mechanisms, all of them objective:

- *Signal exit:* the dead-banded signal returning to zero (trend gone) takes
  the target weight to zero — the position is fully closed at the next
  execution window.
- *Volatility exit:* rising realized volatility shrinks every position
  through the 8% portfolio target — losses in turbulent stretches are cut by
  *sizing*, before any stop could act.
- *Cap exit:* a position that grows past its symbol/cluster cap is trimmed
  back to the cap.

Whether an explicit stop-loss/take-profit overlay *improves* on this was a
registered, falsifiable question — experiment
`configs/experiments/0003-exit-rules-sweep.toml`, measured with the
`amat sweep-exit-rules` command under split discipline (grid: stop ∈ {none,
5%, 10%, 15%} × take-profit ∈ {none, 10%, 20%} on the adjusted entry basis,
21-session re-entry embargo fixed at registration). The registered prior was
failure: stops realize losses the volatility targeting already sized for,
and take-profits truncate the right tail a trend follower's positive skew
depends on. **Measured 2026-08-14: the prior held.** Take-profit overlays
beat the core on the training window (Sharpe 1.18 vs 0.89) and *every one of
them lost to it* on the ranking-blind validation window (best overlay +6.94%
ann / 1.13 Sharpe vs the core's +8.41% / 1.19); the top training scores tied
within 1e-09, so the verdict machinery declared no winner. Stop-loss-only
rows were near no-ops — the volatility target de-risks before a 5–15% stop
is touched. Falsified; the frozen no-exit core stands, and the answer to
"where is the stop loss?" remains: *in the sizing, continuously*, not at a
price level. Run record
`outputs/runs/sweep-exit-rules-amat-etf-20260814T014415.823351Z`.

**Timing:** a signal formed at session `t`'s close executes in session
`t+1`'s configured opening window (09:30–09:35 ET VWAP), never on the signal
bar. The lag is enforced (`CausalityError` below 1), and the fill price is
the *observed* window VWAP — a window with no volume records a missed fill,
never an imputed one.

**Costs:** 2 bps one-way on every fill, applied at execution. This is the
*primary* assumption (liquid ETFs, zero-commission brokerage, ~1 bp half
spreads). The full ladder ran 2026-08-15 (`amat stress`): at 2× the result
is +78.9% / Sharpe 0.95, at 5× still +60.8% / 0.77 — the strategy's economics
do not depend on the cost assumption. Zero-cost runs remain diagnostic only.

**Accounting:** exact-`Decimal` ledger; every position change traces to a
fill or a corporate action; cash + marked holdings must reconcile to equity
within one cent per session or the run fails.

## 3. Risk : reward, measured not asserted

The volatility target is the strategy's one explicit risk:reward dial.
Registered experiment 0001 (`amat sweep-target-vol`) swept it over
{5, 6, 8, 10, 12}% with the split fixed in advance:

- **The frontier is real and monotone**: validation annualized return rises
  from +5.95% (5% target) to +10.37% (12% target) while max drawdown worsens
  from −4.28% to −7.57% and fold consistency falls.
- **"Optimal" is a choice of objective.** Per unit of risk the evidence
  mildly favours the 6% target (validation Sharpe 1.27 vs the frozen 8%'s
  1.19, a smooth plateau); per absolute annual profit it favours the highest
  target the investor's drawdown tolerance allows. The validation Sharpes
  cluster within one standard error — this is structure, not edge.
- Per the registered rule, the frozen 8% is unchanged; 6% earned only
  *eligibility* for future registration under the full protocol.

Measured at the frozen 8% target on the full burned sample (run record
`study-amat-etf-20260813T045106.720526Z`): +85.24% total, +6.65% annualized,
6.59% realized volatility, Sharpe 1.01, max drawdown −8.69%, 3,643 fills, all
net of costs.

At the round-trip level (`scripts/trade_quality.py`, same deterministic
inputs): 860 completed round trips, profit factor 1.81, win rate 32.7% net of
costs, average net trade +$78.81 against a −$38.05 *median* — the strategy
loses small more often than it wins, and the few large wins pay for
everything, which is the return profile a trend follower is supposed to have
and the reason a take-profit rule is expected to hurt it (experiment 0003).
Average round-trip cost is $5.45, so the average trade clears its own costs
14.5 times over; average hold is 19 sessions.

## 4. The validation evidence, and what each piece can and cannot say

All history at or before 2026-08-10 is **burned** — inspected before the
holdout was declared — so *nothing below is out-of-sample evidence*. Each
harness measures a different failure mode; run records persist every number.

- **Walk-forward** (`amat walkforward`): 9/10 calendar folds positive (90%);
  the single loser is 2018 at −1.09%; no fold carries the result. The three
  measurable gates pass as *in-sample analogues* of the promotion gates.
- **Regimes** (same record): under lag-honest attribution the table is flat —
  Sharpe 1.00 bull-quiet, 1.29 bull-volatile, 0.61 bear-volatile. This
  sample shows no sharp regime differentiation; an earlier draft that showed
  one was partly an attribution artifact, caught by the 2026-08-13 audit.
- **Monte Carlo** (`amat montecarlo`): 200 stationary-block-bootstrap
  resamplings of the panel's own joint session moves, full engine on each;
  places the observed result inside a resampled distribution. Destroys
  dependence longer than ~21 sessions, which is conservative for trend.
  Measured 2026-08-14: the observed run sits at the 60th percentile of
  resampled total return, 70th of Sharpe, and its −8.69% drawdown is
  *shallower than 84% of paths* — plan on deeper drawdowns than history
  shows. 0.5% of paths end below initial funding; 2.5% breach −20%
  drawdown. `outputs/reports/amat_v1_montecarlo.pdf`, run record
  `montecarlo-amat-etf-20260814T014412.934963Z`.
- **Exit rules** (experiment 0003, 2026-08-14): the registered stop/TP grid
  was falsified exactly as its prior predicted — training loved take-profits,
  validation punished them; no winner declared; the no-exit core stands
  (§2). A 20-draw randomized stop/TP control (2026-08-15) confirmed the
  conclusion is not grid-shaped: zero random rules beat the core's
  validation return.
- **Stress battery** (2026-08-15, `amat stress`): survives 5× costs, lag-3
  execution delay, 10% injected missed fills, and session-close pricing;
  leave-one-out Sharpe 0.89–1.04 (no single listing carries it); top-10
  trades are 33.8% of profit; correlation +0.36 to SPY. The signal-free
  control shows the signal's contribution on this sample is *drawdown
  control, not raw return* (+93.6% at −16.9% DD without the signal vs
  +85.2% at −8.7% with it).
- **TLT and the rates sleeve** (audit + experiment 0004, 2026-08-15): TLT's
  −$3.6k is whipsaw in 2024–26 chop, not the 2022 shock (which the deadband
  sat out); even so the TLT sleeve beat holding TLT. Registered variants:
  removing TLT wins training and validation by a within-noise margin while
  the without-IEF control ranks last — eligibility recorded, hindsight
  caveat attached, frozen universe unchanged.
- **Parameter neighbourhood** (experiment 0005 + sensitivities,
  2026-08-15): the rebalance-band grid is falsified (frozen 2% ranks first
  on training); horizons/deadband/lookback perturbations all land within
  one standard error with train/validation orderings disagreeing — the
  frozen configuration sits on a plateau.
- **Forward projections** (`amat project`, 2026-08-15): resampled outcome
  distributions from any funding at 1/2/5/10 years — e.g. an 11.8% chance
  of a losing first year for v1 — always labelled within-resampling
  frequencies, never forecasts.
- **Falsification batteries**: every conditioning idea (v3 news scalars, v4
  breadth blend, vol-state conditioning 0002) is judged against shuffled,
  random-turnover-matched, and static-mean controls; universe changes (v5)
  against leave-one-out. Three regime mechanisms have now failed the
  static-mean control — the recurring lesson is that apparent timing skill
  keeps reducing to "hold less on average".
- **Locked holdout**: declared, never opened. Every panel-measuring command
  fails closed past the boundary; access requires a recorded reason.

## 5. The backtest checklist, answered

| Item | Status | Evidence |
|---|---|---|
| Strategy rules 100% objective | **Yes** | §2; `strategy-contract.md`; zero discretionary inputs |
| Realistic commission & slippage | **Yes, stressed** | 2 bps one-way at observed window VWAP (fills also verified at session close: no sensitivity); missed fills on zero volume plus 5%/10% injection stresses; survives the full 2×/3×/5× ladder |
| Multiple years of data | **Yes** | 10.6 years, 2,665 sessions, 2016–2026 |
| Different market conditions | **Yes** | 2018 (loser), 2020 crash, 2022 rates shock in-fold; regime slices measured (and honest: flat) |
| Out-of-sample / walk-forward | **Walk-forward yes; OOS no** | Expanding walk-forward run; every session burned, so OOS begins only after 2026-08-10 as data accrues |
| Max drawdown acceptable | **Measured: −8.69%, with a warning** | At the 8% vol target; the 0001 frontier maps DD vs return for other targets. Monte Carlo context: 84% of resampled paths drew down deeper (median −11.60%, p05 −17.45%) — history's figure is near the lucky edge of the process, not its expectation |
| Profit factor above 1.5 | **Yes: 1.81** | 860 coalesced round trips (`scripts/trade_quality.py`, identical deterministic inputs to record `study-amat-etf-20260813T045106.720526Z`) |
| Average trade exceeds costs | **Yes: 14.5×** | Average net round trip +$78.81 vs $5.45 average round-trip cost; win rate 32.7% with a −$38 median — the classic trend profile of many small losses paid for by few large wins (avg hold 19 sessions) |
| No lookahead / survivorship bias | **Lookahead: tested. Survivorship: measured bound + unmeasurable residue** | Causality + future-mutation tests. Leave-one-out over all 12 symbols (2026-08-15): Sharpe 0.89–1.04, no single listing carries the result. What no test can bound: ETFs that delisted before the 2026 universe selection could never be chosen — stated, not approximated |
| Paper traded before going live | **Machinery exists; period not yet served** | `amat simulate` steps a simulated paper account against live Alpaca *market data* and journals every observation and every paper-period trade (`live_fills`) |

## 6. Paper trading, exactly as far as the guardrails allow

`amat simulate` replays the account from inception through the latest
completed session, appending live Alpaca market data (read-only, one host) to
the immutable snapshot, and journals to dated JSON-lines files: equity, cash,
positions, next-session targets, and — since 2026-08-13 — `live_fills`, the
complete simulated trade log for sessions past the snapshot boundary (symbol,
side, exact quantity/price/cost, and the recorded reason for the trade).
Review that journal to judge the strategy's live behavior before trusting it
with anything.

What this deliberately is **not**: an Alpaca paper-*order* integration.
Guardrail #1 ("do not place broker orders, call order-submission endpoints,
or add live-execution entry points") covers paper endpoints too, and the
session contract states it as "never submit paper or live broker orders".
Submitting to Alpaca's paper API would put an order-submission code path in a
repository whose security posture is built on not having one. If that
guardrail is ever consciously lifted, the path runs through: a written
change to `CLAUDE.md` and the strategy contract, a separate execution module
behind an explicit environment gate, and the `amat plan` artifact (an inert
JSON decision file) as its only input.

## 7. The anti-overfitting posture

- **One frozen core.** Research and any future event-driven runner call the
  same signal/allocation/constraint functions; nothing tunes at runtime.
- **Registered experiments only.** Every parameter question becomes a
  numbered registration (`configs/experiments/`) *before* results exist,
  with the split fixed in advance, ranking on training folds only
  (`fold_score` = 0.5·median + 0.5·minimum — the aggregate Sharpe cannot
  tell a steady result from one lucky year), validation measured
  ranking-blind, and the candidate count recorded for multiple-testing
  discount.
- **A winner never promotes itself.** Surviving a sweep or a falsification
  battery earns a registry entry and the *full* protocol as a new version —
  the frozen configuration changes through that door or not at all. (0002
  survived nominally by 0.0012 Sharpe over its static-mean control and was
  still not pursued; that margin is the system working.)
- **Controls before applause.** Shuffled, turnover-matched, static-mean, and
  leave-one-out controls exist because three separate regime mechanisms have
  each looked good until the control that revealed the mean was doing the
  work.
- **The verdict machinery refuses non-answers.** Unmeasured candidates
  (below minimum trade count) and tied rankings produce explicit no-winner
  verdicts, persisted in the run record.

## 8. Known limitations, restated

1. No out-of-sample evidence exists; it begins only as post-2026-08-10 data
   accrues against the locked holdout.
2. Survivorship bias is bounded and documented, not eliminated: the menu of
   12 ETFs was chosen knowing they all survived to 2026.
3. One vendor's data (Alpaca SIP), one decade, one calendar.
4. Cost stresses, execution delays, deterministic missed-fill stresses, and
   trade-removal batteries are implemented but not yet run on the real
   snapshot (registry "Not yet run").
5. No event-driven runner yet, therefore no engine-parity gate.
6. The Monte Carlo's block bootstrap destroys long memory — its bands are a
   robustness floor for trend, not a distribution of futures.
