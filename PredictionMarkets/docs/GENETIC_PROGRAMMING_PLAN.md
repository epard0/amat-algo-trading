# Genetic Programming optimization — implementation plan

Status: **plan only, nothing implemented.** Read the data section before writing code.

---

## 1. Verdict up front

Genetic programming is a reasonable fit for this problem — the predictor is a small
closed-form expression, the search space is a handful of scalar transforms, and the
objective (money) is measurable. But **the repository does not currently hold enough
labelled data to fit anything without overfitting it catastrophically**, and running
GP against today's dataset would produce a confident, worthless model.

Measured on the current state of the repo:

| Quantity | Count | Consequence |
| --- | ---: | --- |
| Live settled trades | 13 | ~4 effective bits of signal. Unusable as a fitness set. |
| Paper settled trades | 4 | Same. |
| Decision records total | 19,033 | Mostly not usable directly — see below. |
| Decisions carrying full model output | **2,537** | The real candidate training set. |
| Calendar span of all data | **~3 days** | One volatility regime. Zero seasonality coverage. |
| Assets | 5 | Cross-sectionally correlated; not 5× independent. |

16,496 of the 19,033 decisions were rejected at the `entry_window` gate *before* the
model ran, so they carry no features and no probability. They are useful for auditing
the schedule, not for fitting.

**A GP run on 2,537 samples from 3 days, selecting over thousands of candidate
programs, will find noise.** The number of effectively independent 15-minute windows in
3 days is ~288 per asset, and adjacent windows share overlapping candle history, so the
independent sample count is smaller still. Phase 0 is therefore not optional throat-clearing;
it is the majority of the work.

---

## 2. Phase 0 — earn the right to run GP

### 2.1 Counterfactual settlement labelling (the unlock)

The decision log already stores, for every evaluation that reached the model:
`target_price`, `close_time`, the full 15-field `regime_features` vector, the model's
`probability_yes`, and the market's `yes_bid`/`yes_ask` at evaluation time.

That means **the outcome of a market the bot never traded is reconstructable after the
fact**: fetch the spot series around `close_time`, compare to `target_price`, and derive
the YES/NO label. Every evaluation becomes a labelled sample, not just the 13 that were
traded — and the recorded book prices let you score what a *different* strategy would
have paid, not merely whether it would have been right.

```python
# monitoring/../research/labeling.py  (new)
def counterfactual_label(record, settlement_series) -> Label:
    """Reconstruct the YES/NO outcome of an evaluated-but-untraded market."""
```

**The caveat that must be written into the code, not just the docs:** Kalshi settles on a
CF Benchmarks one-minute index, while the only series available here is Kraken spot.
`CLAUDE.md` already identifies this gap as the reason the divergence gate exists. A label
reconstructed from Kraken is therefore *approximate*, and it is systematically wrong
exactly where the two references diverge — which is exactly where the model loses money.

Mitigations, in order of preference:
1. Source the CF Benchmarks BRTI/ETHUSD_RR index directly and label from it. Correct, and
   worth the integration cost before spending weeks of compute on GP.
2. Failing that, label from Kraken but **discard samples whose settlement was within
   `epsilon` of the strike**, where `epsilon` is calibrated from observed Kraken↔Kalshi
   disagreement on the 13 trades with known ground truth. Near-strike markets are both the
   most label-noisy and the most numerous, so this discards a lot — report how much.
3. Carry a `label_confidence` field through to fitness and down-weight uncertain labels.

Validate the labeller against the 13 live + 4 paper trades where the true Kalshi result is
known. **If it cannot reproduce 17/17, it is not fit to generate 20,000 labels.** That check
is cheap and non-negotiable.

### 2.2 Historical backfill

Counterfactual labelling still only covers markets the bot *evaluated*. To reach a defensible
sample, replay history:

- Kraken OHLC gives 720 rows per request; at 1-minute granularity that is 12 hours, so a
  long backfill needs paged requests against `KrakenRestSource.get_ohlc` (already
  interval-parameterised) or a bulk OHLCVT dump from Kraken's historical data export.
- Reconstruct synthetic 15-minute markets on the real Kalshi schedule (`:00/:15/:30/:45`),
  derive each strike the way Kalshi does, and run `extract_features` + `classify` over the
  history to produce the same feature vector the live bot would have seen.
- **Target: ≥ 12 months, ≥ 30,000 independent windows per asset**, covering at least one
  sustained trend, one chop regime, and one volatility shock.

What backfill *cannot* give you is the order book. Fills, depth, and spread are not
reconstructable from OHLC. Model them explicitly and pessimistically — see §4.3.

### 2.3 Data contract

Freeze one immutable Parquet/SQLite dataset with a content hash, split **chronologically**:

```
train:      oldest 60%
validation: next 20%      <- GP selection pressure applies here
holdout:    newest 20%    <- opened exactly once, at the end, ever
```

Record the hash in every experiment's metadata. A result that cannot name its dataset hash
is not a result.

---

## 3. What to evolve — and what must never be evolvable

### 3.1 Evolvable

Keep the normal-CDF skeleton in `crypto_15min_predictor.py`. It encodes a real
structural prior (terminal price ≈ current spot + drift, dispersed by realized
volatility over the remaining horizon), and GP is bad at rediscovering structure it
can be handed for free. Evolve the *terms feeding it*:

| Target | Current form | GP search space |
| --- | --- | --- |
| Drift estimator | momentum × regime `drift_multiplier`, integrated as `tau·(1−e^{−T/τ})` | expression tree over the 15 regime features |
| Volatility estimator | `realized_vol` × `volatility_multiplier` | expression tree, constrained strictly positive |
| Shrinkage | `extra_shrinkage` per regime | expression tree mapping to [0, 1] |
| Regime → multipliers | hand-tuned constants in `market_regime.classify` | evolvable constants, sign-constrained |

Grammar (typed GP, so only dimensionally sane programs are generated):

```
Expr    := Feature | Const | Unary(Expr) | Binary(Expr, Expr)
Unary   := tanh | clip01 | sqrt_abs | neg | exp_neg
Binary  := + | − | × | protected_÷ | min | max
Feature := realized_vol_fast | realized_vol_slow | vol_ratio | efficiency_ratio
         | return_autocorrelation | range_compression | impulse_sigma
         | minutes_since_impulse | reversal_retracement | close_location
         | wick_rejection | travel_direction | recent_travel_sigma
         | minutes_remaining | distance_return
```

Use `tanh`/`clip01` as the only routes to a bounded output so multipliers cannot explode,
and `protected_÷` (returns 1.0 on |denominator| < 1e-9) so division cannot produce NaN
mid-evaluation.

### 3.2 Hard constraints — enforced in code, not by fitness penalty

`CLAUDE.md` states the invariant plainly: *"Regime multipliers never flip a sign… a
classifier that can reverse the model's direction is a second, untested strategy hiding
inside the first."* A fitness penalty is the wrong enforcement mechanism, because a
sufficiently profitable sign-flipping program will simply pay the penalty. Reject at
generation time:

```python
def admissible(program) -> bool:
    return (
        program.drift_multiplier_range() >= 0        # never inverts measured drift
        and program.volatility_multiplier_range() > 0
        and 0.0 <= program.shrinkage_range() <= 1.0
        and program.depth <= MAX_DEPTH                # parsimony, hard bound
        and program.uses_no_future_bar()              # see §4.2
    )
```

**Never evolvable — these are safety, not strategy:**

- `MAX_MODEL_MARKET_DIVERGENCE`. It is the guard against the exact Kraken/CF-Benchmarks
  mismatch that the training labels inherit. Letting GP tune the gate that protects
  against the flaw in GP's own training data is circular, and it will widen it.
- Circuit breakers: `MAX_CONSECUTIVE_LOSSES`, `MAX_DRAWDOWN_FRACTION`.
- `LIVE_CAPITAL_LIMIT_USD`, live-mode gating, the one-open-trade invariant.
- The IOC-not-FOK order type and the re-quote-before-sizing logic.
- Kelly sizing *formula* (the fraction may be tuned; the formula stays).

### 3.3 Second target: thresholds (GA, not GP)

`MIN_MODEL_PROBABILITY`, `MIN_EXECUTABLE_EDGE`, `ENTRY_WINDOW_SECONDS`,
`KELLY_FRACTION`, `MAX_SLIPPAGE` are scalars — a plain genetic algorithm or even CMA-ES
over a 5-dimensional box is more appropriate and far less overfit-prone than GP.

The dashboard already argues for one of these: the **60–65% probability bucket is 0-for-3
and −$14.54**, i.e. trades sitting right at the `MIN_MODEL_PROBABILITY = 0.62` floor are
the money-losers. Test raising the floor as a one-line experiment *before* building any of
this. If a scalar change captures most of the gain, the GP project may not be worth it.

---

## 4. Fitness — the part that decides whether this works

### 4.1 Optimise money, not accuracy

Accuracy and log-loss are the wrong objective: a model can be well-calibrated and still
lose after fees, and it can be poorly calibrated in bands it never trades and lose nothing.
Fitness must run the **actual decision pipeline**.

```python
def fitness(program, fold) -> float:
    trades = simulate(program, fold)      # applies the real gates in risk_gate.py
    if len(trades) < MIN_TRADES:          # too few to be evidence
        return -inf
    pnl_after_fees = sum(t.pnl for t in trades)     # Kalshi quadratic taker fee, ceiled
    return (
        pnl_after_fees / fold.capital_at_risk
        - LAMBDA_BRIER * brier(trades)              # calibration matters independently
        - LAMBDA_SIZE  * program.node_count         # parsimony
        - LAMBDA_TURN  * turnover(trades)           # fee drag is 3.5% of stake today
        - LAMBDA_DD    * abs(max_drawdown(trades))
    )
```

Reuse `monitoring/performance_tracker.py` for `brier`, `max_drawdown`, and the summary
statistics — the metric that selects the model must be the same code that reports on it in
production, or the two will silently diverge.

Fee modelling is not optional: measured fee drag is **3.5% of every dollar staked**, and
Kalshi's quadratic taker fee peaks at a 50c price, which is precisely where the model
finds most of its edge. A fitness function without fees will select for exactly the trades
that lose money.

### 4.2 Lookahead is the failure mode that will get you

Three specific leaks to guard, all of which this codebase makes easy to hit:

1. **The forming bar.** `extract_features` deliberately reads shape from `candles[:-1]`
   because Kraken's last row is the minute in progress. A backtest that hands the model
   completed bars is giving it information the live bot does not have. The simulator must
   reproduce the partial-bar behaviour exactly.
2. **Rolling windows.** Every feature must be computed from a strict backward slice.
   `monitoring/chart.rolling_indicators` already demonstrates the pattern.
3. **Normalisation.** Any scaling constant must be fit on the training fold only. Fitting a
   z-score over the full dataset leaks the future into the past — the classic silent leak.

Write a `test_no_lookahead` that shifts all future data to NaN after each decision point and
asserts the produced features are bit-identical. If they are not, the simulator is lying.

### 4.3 Execution realism

The backfill has no order book, so assume the worst rather than the average:
- Fill at the far touch plus `MAX_SLIPPAGE`, never at mid.
- Cap size at a fixed fraction of a conservatively assumed depth.
- Model partial fills (the bot uses IOC, which accepts them).
- Charge the fee on the filled quantity, ceiled the way `calculate_paper_fill` does.

A strategy that is only profitable under optimistic fills is not profitable.

### 4.4 Validation protocol

- **Purged, embargoed walk-forward.** Train on window *n*, validate on *n+1*, with an
  embargo gap ≥ the feature lookback (45 minutes) so overlapping candle history cannot
  bridge the split.
- **Parsimony**: hard depth cap plus the size penalty. Prefer the simplest program within
  one standard error of the best.
- **Multiple-comparison correction.** Evaluating 10,000 programs and keeping the best is a
  guaranteed way to find noise. Apply White's Reality Check or a deflated Sharpe ratio that
  accounts for the number of trials. Log the trial count in every experiment record.
- **Null baselines that must be beaten**, by a margin, on validation:
  1. the current hand-tuned model,
  2. a constant 50% probability (i.e. never trade),
  3. always fade the market's implied probability,
  4. a random program of equal complexity.

Beating (1) but not (4) means you have measured search effort, not skill.

---

## 5. Architecture

```
research/                       # new; not imported by bot.py, ever
  __init__.py
  labeling.py                   # counterfactual settlement reconstruction
  backfill.py                   # paged Kraken/CF history -> immutable dataset
  dataset.py                    # splits, hashing, feature assembly
  simulator.py                  # replays the real gates + fee model
  gp/
    grammar.py                  # typed nodes, admissibility constraints
    population.py               # init, crossover, mutation, elitism
    fitness.py                  # §4.1, reuses performance_tracker
    evolve.py                   # the driver
    report.py                   # per-generation lineage + metrics
  experiments/
    <utc-timestamp>-<hash>/     # config, dataset hash, seed, results, champion
```

Non-negotiable boundary: **`bot.py` must not import `research/`**. The trading path stays
exactly as auditable as it is now. GP output crosses into production as *data* — a small
JSON of evolved constants/expressions loaded by the predictor behind a feature flag — not
as imported code. This mirrors how the repo already isolates the legacy tree.

Dependencies: GP needs none beyond stdlib if the grammar is hand-rolled (recommended,
~400 lines, and it keeps the constraint checks explicit). If you would rather not, `deap`
is the conventional choice — but add it to `pyproject.toml` as a dev/optional extra, never
to the runtime set the VPS installs.

---

## 6. Deployment gate

No evolved program reaches live capital without passing every stage in order:

1. **Holdout** — opened once. Fails ⇒ the entire experiment is discarded, not re-tuned.
2. **Shadow** — run alongside production for ≥ 2 weeks, logging what it *would* have
   done to `decisions.jsonl` under a `shadow_` record type. Zero capital. Compare
   realised calibration against its backtest; a shadow Brier materially worse than the
   backtest Brier means the simulator is wrong.
3. **Paper** — ≥ 200 settled paper trades, calibration within tolerance.
4. **Live, capped** — `LIVE_CAPITAL_LIMIT_USD` at its floor, ≥ 100 settled trades, with
   the existing circuit breakers untouched.
5. **Live, normal** — only on a documented decision, with the champion's dataset hash,
   seed, and holdout numbers recorded.

Automatic rollback if live Brier exceeds the paper Brier by more than a pre-registered
margin, or the drawdown breaker trips twice in a month.

---

## 7. Milestones

| Phase | Work | Exit criterion |
| --- | --- | --- |
| 0a | CF Benchmarks index source, or calibrated Kraken proxy | labeller reproduces 17/17 known outcomes |
| 0b | Backfill + immutable dataset | ≥ 12 months, ≥ 30k windows/asset, hashed |
| 0c | Simulator + `test_no_lookahead` | replays the 17 real trades to within fee rounding |
| 1 | Scalar GA over thresholds | beats current config on walk-forward |
| 2 | GP grammar + constraints | 100% of generated programs admissible |
| 3 | Evolution + parsimony + reality check | beats all four null baselines |
| 4 | Shadow deployment | 2 weeks, calibration matches backtest |
| 5 | Staged live | per §6 |

Phase 0 is realistically 60–70% of total effort. Budget accordingly; the temptation to
skip to Phase 3 is the single most likely cause of this project losing money.

---

## 8. Honest risks

- **The market is close to efficient at 15 minutes.** Kalshi's 15m crypto books are
  quoted by participants with the same public data. The current edge may be an artifact of
  a 17-trade sample. GP cannot manufacture an edge that is not there; it can only
  manufacture the appearance of one.
- **Label noise is correlated with the failure mode.** Kraken-derived labels are wrong
  precisely where Kraken and CF Benchmarks disagree, which is where the strategy loses.
  GP will happily learn to trade into that blind spot.
- **Regime change.** A program fit to 12 months of history is fit to that volatility
  environment. Re-validate on a schedule, and treat a champion as perishable.
- **Complexity as a liability.** The current model's virtue is that a human can read it
  and say why it traded. An evolved expression tree is far harder to audit at 3am while
  real money is open. Weight parsimony heavily; prefer a slightly worse program you can
  explain.

The intellectually honest framing: **Phase 0 has standalone value** — counterfactual
labelling and a trustworthy simulator would let you evaluate *any* change, GP or not, and
would already tell you whether raising `MIN_MODEL_PROBABILITY` off the losing 60–65%
band is worth doing. Build Phase 0 first, and decide about GP once you can measure.
