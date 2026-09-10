# Experiment registry

Every configuration that has been run, its status, and why. Required by
`.claude/rules/30-quant-methodology.md` ("Record every tested configuration in
the experiment registry, including failures") and by
`.claude/references/validation-protocol.md` step 8.

**Nothing is removed from this file.** A repository that deletes its failed
experiments looks like one that only ever tried what worked, and
`.claude/references/promotion-gates.md` makes a report that "omits failed
experiments" an automatic rejection condition.

Decisions live here rather than in the configuration files themselves. A
strategy config is hashed into every run manifest, so editing one — even to add
a comment — changes its digest and orphans every result that referenced it. This
was demonstrated on 2026-08-10: annotating `amat_managed_v2.toml` with its own
rejection moved the digest from `72bff815afc9f513` to `dce36c9a76c3617c` and was
reverted. See "Known weakness" below.

Shared conditions unless an entry states otherwise: 2 bps one-way, 0% cash,
execution at the next session's 09:30–09:35 NY raw VWAP, corporate actions
applied. **Snapshot, sample, and funding are per-entry** — results before
2026-08-11 cite snapshot `alpaca-sip-2026-08-08` (sample 2016-12-30 to
2026-08-07, 2,413 sessions) with $1,200,000 for 12-symbol runs and $2,900,000
for 29-symbol runs; results from 2026-08-11 onward cite snapshot
`alpaca-sip-2026-08-11` (sample 2016-12-30 to 2026-08-10, 2,414 sessions) with
$100,000 combined regardless of universe size. (An earlier revision of this
preamble claimed one blanket funding for all results; that was wrong for the
12-symbol entries and is corrected here.)

---

## Candidates

### `amat_v1.toml` — `ea8cbd5ad7418a38` — amat-etf v1.0.0

**Status: current candidate. Not promoted.**

12 symbols, long-or-cash, 20% max symbol weight, 8% volatility target, no risk
overlay.

#### Measured 2026-08-11 — snapshot `alpaca-sip-2026-08-11`, $100,000

Sample 2016-12-30 → 2026-08-10 (2,414 measured sessions), $100,000 combined,
sleeves $100,000 each, total return, corporate actions applied:

| Total | Ann. | Vol | Sharpe | Max DD | Symbol gate | Year gate |
|---:|---:|---:|---:|---:|---|---|
| +85.24% | +6.65% | 6.59% | 1.01 | −8.69% | PASS (GLD 23.1% of 25%) | PASS (2026 21.0% of 40%) |

$100,000 → $185,242. 11/12 sleeves profitable, median +23.30%; only TLT loses
(−7.04%). Report: `outputs/reports/amat_v1_study.pdf` (includes the labelled
Gemini advisory page). Benchmarks on the identical axis
(`scripts/benchmarks.py --snapshot data/snapshots/alpaca-sip-2026-08-11
--start 2016-12-30`, frictionless): SPY +299.83% / 0.87 / −33.79%; 60/40
SPY/IEF monthly +148.52% / 0.94 / −21.18%. The interpretation of the
2026-08-10 row below carries over unchanged: the Sharpe differences are inside
one standard error (~±0.38 over 9.6y); the drawdown difference is the
difference of kind.

The move from the 2026-08-10 row (+84.82% at $1,200,000 on
`alpaca-sip-2026-08-08`) decomposes into one additional measured session
(2026-08-10) and the funding change — at $100,000 the $25 minimum trade
notional binds very slightly more often. Neither changes any conclusion.

#### Walk-forward consistency and regime slices — measured 2026-08-13

`amat walkforward configs/strategy/amat_v1.toml --snapshot
data/snapshots/alpaca-sip-2026-08-11`; run record
`outputs/runs/walkforward-amat-etf-20260813T003356.065650Z`. One expanding
run (the frozen core fits nothing, so the expanding walk-forward IS the
full-history run), sliced into calendar folds:

| Fold | Sessions | Total | Ann. | Vol | Sharpe | Max DD | Trades |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2016+2017 | 252 | +11.37% | +11.42% | 5.09% | 2.25 | −1.81% | 348 |
| 2018 | 252 | −1.09% | −1.10% | 7.08% | −0.15 | −8.69% | 354 |
| 2019 | 253 | +9.37% | +9.37% | 4.96% | 1.89 | −4.07% | 398 |
| 2020 | 254 | +6.37% | +6.35% | 6.10% | 1.04 | −5.42% | 369 |
| 2021 | 253 | +5.75% | +5.75% | 7.58% | 0.76 | −4.76% | 368 |
| 2022 | 252 | +2.36% | +2.37% | 6.53% | 0.36 | −6.42% | 222 |
| 2023 | 251 | +2.19% | +2.21% | 5.56% | 0.40 | −6.24% | 428 |
| 2024 | 253 | +7.20% | +7.20% | 7.48% | 0.96 | −4.83% | 461 |
| 2025 | 251 | +9.98% | +10.07% | 6.72% | 1.50 | −5.26% | 431 |
| 2026 (partial) | 152 | +10.84% | +18.74% | 8.91% | 2.10 | −4.29% | 264 |

9/10 folds positive (90%); the single loser is 2018 at −1.09%. The three
research gates a walk-forward can measure all pass — aggregate Sharpe 1.01 ≥
0.50, positive folds 90% ≥ 60%, max drawdown −8.69% ≥ −20% — **and none of
them is out-of-sample evidence**: this configuration was frozen in 2026 with
this history known, so these are *consistency* measurements (the aggregate
was earned across years, not granted by one) and nothing more. Since the
2026-08-13 audit the gates carry that qualifier in their recorded names
("in-sample analogue of the out-of-sample gate") and the fold gate refuses
to decide below four folds. The result was not carried by a single stretch:
worst full-year Sharpe −0.15, best +2.25, no fold contributes more than a
quarter of the log return.

Per-regime slices from the same run, labels computed causally from SPY's own
past (63-session trend sign × 21-session realized vol vs expanding median).
Session `t`'s return is booked under session `t−1`'s label — the label under
which the position held through `t` was decided — and annualization is the
platform's realized-CAGR convention. Re-measured under those conventions on
2026-08-13 after the same-day audit (run record
`outputs/runs/walkforward-amat-etf-20260813T115414.026523Z`; per-regime
drawdowns are undefined because the sessions interleave):

| Regime | Sessions | Share | Ann. | Vol | Sharpe | Hit |
|---|---:|---:|---:|---:|---:|---:|
| bull-quiet | 924 | 38.3% | +6.79% | 6.78% | 1.00 | 57% |
| bull-volatile | 954 | 39.5% | +8.57% | 6.64% | 1.29 | 59% |
| bear-quiet | 15 | 0.6% | −19.47% | 4.25% | −4.58 | 47% |
| bear-volatile | 520 | 21.5% | +3.81% | 6.20% | 0.61 | 53% |

The honest table is flatter than the first draft it replaces (run record
`walkforward-amat-etf-20260813T003356.065650Z`, superseded). That draft
booked session `t`'s return under `t`'s own label, and a large move sits
inside the trailing volatility that labels its own session — so big returns
selected themselves into the volatile buckets, showing bull-quiet at Sharpe
1.80 and bear-volatile at −0.14 (the same mechanism manufactures a ~1.27×
quiet/volatile volatility separation on pure iid noise). Corrected, the
strategy earns across both bull states, keeps a modest positive drift
through bear-volatile stretches (+3.81% annualized, Sharpe 0.61), and shows
**no sharp regime differentiation in this sample**. The bear-quiet row is 15
interleaved sessions — noise, reported because hiding thin slices is how
regime tables flatter.

**Locked holdout declared 2026-08-13** (`outputs/holdouts/
holdout-declaration-2026-08-13/declaration.json`): sessions after 2026-08-10
are locked; everything at or before it is *burned* — inspected by the
v1–v5 full-sample studies before the declaration existed, which the
declaration records (`history_burned: true`). Reading past the boundary now
requires an explicit reason that the run manifest records. Genuinely
out-of-sample evidence for anything in this file begins with data ingested
after this declaration.

#### Round-trip trade quality — measured 2026-08-13

`scripts/trade_quality.py --snapshot data/snapshots/alpaca-sip-2026-08-11`,
the same deterministic engine and round-trip coalescing as the study reports
(inputs identical to record `study-amat-etf-20260813T045106.720526Z`):

| Round trips | Win rate (net) | Profit factor | Avg net trade | Median | Avg cost | Net/cost | Avg hold |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 860 | 32.7% | 1.81 | +$78.81 | −$38.05 | $5.45 | 14.5× | 19 sessions |

The profile is the trend-following signature: a *negative median* trade under
a positive mean — most round trips lose a little, a few long holds win a lot.
That is also the structural reason the exit-rule prior (experiment 0003)
expects take-profits to hurt: truncating the right tail attacks exactly the
trades that pay for the other two-thirds. (Outcome, 2026-08-14: the prior
held — see 0003 under "Registered experiments".)

#### Monte Carlo robustness — measured 2026-08-14

`amat montecarlo … --paths 200 --workers 8`, stationary block bootstrap
(mean block 21 sessions, base seed 20260812) over the full
`alpaca-sip-2026-08-11` panel; every path runs the complete engine —
warm-up, next-open VWAP fills, 2 bps costs, accounting. Run record
`outputs/runs/montecarlo-amat-etf-20260814T014412.934963Z`; PDF (equity
fan, metric histograms, summary table)
`outputs/reports/amat_v1_montecarlo.pdf`.

| metric | p05 | p25 | p50 | p75 | p95 | observed | %ile |
|---|---:|---:|---:|---:|---:|---:|---:|
| total return | +19.40% | +47.62% | +72.52% | +102.61% | +158.29% | +85.24% | 60% |
| annualized return | +1.87% | +4.15% | +5.86% | +7.65% | +10.42% | +6.65% | 60% |
| annualized volatility | 6.34% | 6.74% | 6.95% | 7.22% | 7.67% | 6.59% | 14% |
| Sharpe | 0.27 | 0.60 | 0.83 | 1.09 | 1.41 | 1.01 | 70% |
| max drawdown | −17.45% | −13.75% | −11.60% | −9.55% | −7.71% | −8.69% | 84% |

0.5% of paths end below initial funding; 2.5% draw down worse than −20%.
Reading: the observed run is an unremarkable draw from its own resampled
process on return and Sharpe, and *lucky* on drawdown — 84% of reshuffled
histories drew down deeper than −8.69%, so the realized max-DD figure
should be treated as near the favorable edge of what this process
produces, not as its expectation. The bootstrap breaks dependence longer
than ~21 sessions, which is conservative for a trend premium; every
probability is a within-resampling frequency, not a forecast.

#### Stress battery — measured 2026-08-15

`amat stress` (record `stress-amat-etf-20260815T063809.490261Z`), the whole
former "Not yet run" list in one recorded artifact. Baseline: +85.24%,
Sharpe 1.01, −8.69% DD.

| Stress | Total | Sharpe | Max DD | Reading |
|---|---:|---:|---:|---|
| costs 4 bps (2×) | +78.85% | 0.95 | −8.71% | survives |
| costs 6 bps (3×) | +72.55% | 0.89 | −9.14% | survives |
| costs 10 bps (5×) | +60.81% | 0.77 | −10.32% | survives; cost is not the edge |
| execution lag 2 | +84.59% | 0.99 | −7.99% | timing-insensitive |
| execution lag 3 | +83.23% | 0.97 | −9.52% | timing-insensitive |
| 5% fills missed | +88.50% | 1.04 | −8.67% | random skips are noise |
| 10% fills missed | +87.54% | 1.03 | −8.63% | random skips are noise |
| session-close fills | +85.62% | 1.01 | −8.94% | no open/close sensitivity |
| **signal-free allocator** | +93.64% | 0.93 | **−16.87%** | see below |

The **signal-free control** (same allocator/covariance/caps/costs, trend
score pinned to 1) is the honest headline: it *out-returns* the strategy on
this sample (+93.64% vs +85.24%) at nearly double the drawdown (−16.87% vs
−8.69%) and lower Sharpe. On this burned decade the trend signal's measured
contribution is **risk shaping, not raw return** — it spent about 8 points
of total return to halve the worst drawdown. Any promotion argument must be
made in those terms.

**Leave-one-out (the measurable survivorship bound):** twelve 11-symbol
re-runs; Sharpe spans 0.89 (without GLD) to 1.04 (without TLT), total
return +70.81% to +88.15%. No single listing carries the result, and the
worst deletion still leaves the qualitative picture intact. What this
cannot bound — assets that delisted before the 2026 universe selection and
so could never be chosen — remains unmeasured and unmeasurable from this
data, and stays a standing caveat.

**Concentration restatements** (additive, not re-runs): removing the top 5
round trips leaves +51.02% (they carry 22.6% of net profit); top 10 leaves
+34.16% (33.8%); removing the best calendar year (2017, +11.37%) leaves
+66.33%. No single trade or year is the result.

**Benchmark correlation** of session returns: +0.36 vs SPY, +0.39 vs
60/40 — the diversification property is real, not asserted.

Delay stresses required an engine correction, disclosed in README "Known
defects": at lag ≥ 2 consecutive decisions overlap, and the instruction
builder previously double-sold (an `AccountingError` short). Decisions now
net in-flight scheduled quantities, and a sale that loses its backing when
a pending buy misses is clamped and recorded as `UNBACKED_SALE` — never
shorted. Both changes are provably inert at the baseline lag 1 (the
golden-digest identity re-verified after each).

#### Forward projections — measured 2026-08-15

`amat project` (record `project-amat-etf-20260815T060059.126310Z`; PDF
`outputs/reports/amat_v1_projection.pdf`): the run's own net session
returns, stationary-bootstrap resampled forward from $100,000. **Every
number is a within-resampling frequency, never a forecast** — the resampled
worlds contain only regimes the burned sample contained.

| Horizon | ann. return p05–p95 | median terminal | max DD p05–p95 | P(end < funding) |
|---|---|---:|---|---:|
| 1y | −2.77% … +16.68% | 106,828 | −9.07% … −2.54% | 11.8% |
| 2y | +0.02% … +14.03% | 113,964 | −10.81% … −3.70% | 5.0% |
| 5y | +2.54% … +11.09% | 138,058 | −12.88% … −5.08% | 0.2% |
| 10y | +3.68% … +9.86% | 190,523 | −14.61% … −6.48% | 0.0% |

The tail worth reading: about one resampled year in nine loses money, and
long-horizon drawdowns run materially deeper than history's −8.69% —
consistent with the Monte Carlo finding that the realized drawdown was
lucky.

#### Trailing windows — 1y / 2y / 5y / 10y ending 2026-08-10

Slices of the canonical run (artifact
`outputs/experiments/trailing-horizons-20260815.json`; positions carried
in, no warm-up ramp inside the window). Dated horizon PDFs
(`outputs/reports/v1-horizon{1y,2y,5y,10y}-20260815.pdf`) re-run each
window independently with a warm-up lead and agree qualitatively.

| Window | Total | Ann. | Sharpe | Max DD |
|---|---:|---:|---:|---:|
| 1y | +20.04% | +20.21% | 2.40 | −4.29% |
| 2y | +23.98% | +11.47% | 1.54 | −5.26% |
| 5y | +36.69% | +6.48% | 0.92 | −7.98% |
| 10y (full) | +85.24% | +6.65% | 1.01 | −8.69% |

The recent windows flatter: a Sharpe-2.4 trailing year is exactly the kind
of stretch the Monte Carlo distribution says to expect *sometimes* and
never to extrapolate.

#### TLT audit — the losing sleeve, dissected 2026-08-15

`scripts/asset_audit.py --symbol TLT` (artifact
`outputs/experiments/asset-audit-TLT-20260815T063309Z.json`). Inside the
combined portfolio: 91 round trips, net **−$3,628**, win rate 28.6%, profit
factor 0.62. The mechanism is not the 2022 rates shock — 2022 cost only
−$64 because the deadband correctly kept the strategy out — it is
**whipsaw**: 61 of 91 trades were losses held ≤ 21 sessions, costing
−$8,149, concentrated in 2024–2026's choppy re-entries (2025: −$2,583;
2026: −$1,335). The signal is long TLT only 35.2% of sessions with a raw
mean score of −0.02 (persistent downtrend, long-only clamp). Context that
matters: the independent TLT sleeve (−7.04%) still *beat* buying and
holding TLT (−9.28%), and the portfolio's TLT position sizes were
vol-capped throughout. The registered response is experiment 0004 — not
in-place surgery.

#### All twelve sleeves audited — 2026-08-15, and TLT is not the only loser

The TLT audit above ran alone. Campaign `20260815T161255Z` ran
`scripts/asset_audit.py` across the entire universe, which corrects a
premise that had been carried through this file: **two symbols lose money
inside the combined portfolio, not one.** Coalesced round trips on
`alpaca-sip-2026-08-11`, $100,000, 2 bps one-way (logs under
`outputs/campaigns/20260815T161255Z/logs/asset-audit-*.log`):

| Symbol | Round trips | Net | Win rate | PF | Symbol | Round trips | Net | Win rate | PF |
|---|---|---|---|---|---|---|---|---|---|
| GLD | 68 | +16,922 | 29.4% | 3.12 | DBC | 85 | +4,370 | 31.8% | 1.45 |
| QQQ | 61 | +16,234 | 41.0% | 3.60 | USO | 86 | +3,966 | 36.0% | 1.56 |
| SPY | 50 | +11,185 | 36.0% | 3.01 | IWM | 59 | +2,476 | 30.5% | 1.47 |
| EEM | 65 | +7,037 | 40.0% | 2.29 | UUP | 68 | +1,170 | 35.3% | 1.27 |
| SLV | 82 | +5,412 | 24.4% | 1.61 | **IEF** | 78 | **−1,970** | 29.5% | **0.70** |
| EFA | 67 | +4,606 | 34.3% | 1.67 | **TLT** | 91 | **−3,628** | 28.6% | **0.62** |

Both losers are the *entire* rates cluster, so the effect is cluster-level,
not a TLT idiosyncrasy — TLT is merely the larger instance of it. Note the
earlier phrasing "11/12 sleeves profitable, only TLT loses" measured the
**independent sleeves**; this table measures round trips **inside the
combined portfolio**, where cluster caps and covariance change the sizing.
Both statements are true of different objects and neither should be read as
the other.

This does not reopen experiment 0004, and it slightly strengthens its
verdict: 0004's without-IEF control ranked *last* despite IEF's combined
round trips being net negative, which is diversification doing its job.
Deleting either symbol after watching it lose remains hindsight selection.

Portfolio-level trade quality on the same run (`scripts/trade_quality.py`):
860 coalesced round trips, win rate 32.7%, **profit factor 1.81**, average
net trade +$78.81 against an average round-trip cost of $5.45 (14.5×),
average hold 19 sessions.

#### Adversarial audit of the validation layer — 2026-08-13

A consolidated adversarial review of the walk-forward, Monte Carlo, regime,
sweep, and run-record code ran the same day these harnesses landed and
returned three major findings, all fixed with regression tests before the
numbers above were finalized: **(1)** holdout enforcement had exactly one
caller — every other measuring command could read past the declared
boundary while recording an affirmative `holdout_accessed: false` in its
manifest; enforcement now lives in the shared panel loaders and every
panel command carries `--allow-holdout REASON`. **(2)** the
target-volatility sweep could emit a promotion-shaped verdict from a field
whose candidates were unmeasured (below the minimum trade count, fold score
−inf, "ranked" by input order) or exactly tied; both cases now produce an
explicit no-winner verdict and `winner_holds_on_validation: false`, and the
sweep record carries a burned-history caveat. **(3)** regime attribution
was contemporaneous and its annualization compounded the arithmetic mean —
the regime table above is the corrected measurement, and the superseded
first-draft record is retained for the audit trail. Minor findings (inert
immutable-root guard on run records, cwd-anchored holdout resolution, no
boundary monotonicity, Monte Carlo accepting a mismatched universe, gate
names dropping the "out-of-sample" qualifier) are enumerated with their
fixes in README "Known defects and audit trail". Experiment 0001's and
0002's measured numbers were produced by code paths the audit did not
change and are unaffected; 0001's recorded *verdict text* predates the
tie/unmeasured guards, which did not alter its outcome (its scores were
finite and distinct).

#### Measured 2026-08-10 — snapshot `alpaca-sip-2026-08-08`, $1,200,000

$1,200,000 combined (12 × $100,000). Measured on total return, with corporate
actions applied:

| Total | Ann. | Vol | Sharpe | Max DD | Symbol gate | Year gate |
|---:|---:|---:|---:|---:|---|---|
| +84.82% | +6.63% | 6.59% | 1.01 | −8.69% | PASS (GLD 23.1% of 25%) | PASS (2026 20.6% of 40%) |

11/12 sleeves profitable, median +22.72%. Only TLT loses (−7.04%).
Report: `outputs/reports/amat_v1_total_return.html`.

Against passive alternatives over the identical 2,413 sessions, all on adjusted
closes, all total return, reproducible with `scripts/benchmarks.py`:

| | v1 contract | SPY | equal-weight v1 universe | 60/40 SPY/IEF monthly |
|---|---:|---:|---:|---:|
| Total | +84.82% | +299.95% | +166.91% | +149.00% |
| Annualized | +6.63% | +15.58% | +10.80% | +10.00% |
| Volatility | 6.59% | 17.96% | 12.83% | 10.65% |
| Sharpe | **1.01** | 0.87 | 0.84 | 0.94 |
| Max drawdown | **−8.69%** | −33.79% | −23.30% | −21.18% |

The benchmarks are **frictionless** — no spread, commission, or tax — while v1
pays 2 bps a side on roughly 18× annual turnover. The comparison is therefore
tilted toward the benchmarks, and v1's margin is if anything understated.

**v1 posts the highest Sharpe of anything measured here, at roughly a quarter
of SPY's drawdown** (−8.69% against −33.79%). It still trails badly on *absolute* return, because it runs at 37% of
SPY's volatility and the contract forbids re-levering past 100% gross. That is a
different vehicle, not a worse one.

**None of these Sharpe differences is statistically significant.** The standard
error of a Sharpe estimate over 9.6 years is roughly ±0.38. 1.01, 0.94, 0.87 and
0.84 are one cluster, and the honest statement is that v1 is not distinguishable
from a naive basket on risk-adjusted return — it is distinguishable on
*drawdown*, where −8.69% against −23% to −34% is a difference of kind.

A note on benchmark selection, because it cuts against a flattering reading: an
equal-weight basket of GLD/IEF/QQQ/SPY/TLT scores Sharpe **1.09** and beats v1.
That basket is not in this table because those five names were picked by looking
at which assets did well over this sample — hindsight selection in the benchmark
itself. The unbiased version is equal weight over the strategy's own declared
12-symbol universe, which is the 0.84 above. Both numbers are recorded here so
the choice is visible rather than quietly made.

Do not read this as a promotion case. The concentration margin is thin (GLD at
23.1% of a 25% cap), eight of ten research gates remain uncomputed, there is no
walk-forward and no locked holdout, and the universe was selected for continuous
coverage over this sample.

#### SUPERSEDED 2026-08-10 — measured without dividends

An earlier revision of this entry recorded +69.13% / +5.64% ann / 6.61% vol /
Sharpe 0.85 / −8.70% max DD, and reported the gap to SPY as "not material" at
0.85 against 0.87.

Those figures were correct arithmetic on an incomplete ledger. Corporate actions
were ingested into every snapshot and applied by nothing, so the run credited
none of the 436 distributions its positions earned while the SPY baseline it was
compared against was measured on adjusted closes that include SPY's. **A
price-return strategy was set against a total-return benchmark.** Crediting the
distributions adds 99 bps a year and moves Sharpe 0.85 → 1.01, which reverses the
ordering against SPY. See "Corporate actions were ingested and never applied"
below for the fix and its tests.

#### SUPERSEDED 2026-08-10 — a misattributed result, corrected

An earlier revision of this entry recorded +39.77% / +3.56% ann / 6.46% vol /
Sharpe 0.55 / −11.30% max DD against this configuration. **That attribution was
wrong and the row is withdrawn.**

Those figures were produced by `amat study configs/strategy/amat_managed_v2.toml
--engine both`. The `--engine` selection chooses the *runner*; the *configuration*
supplies the universe and constraints. So "v1-frozen" there means the v1 runner
driving the **v2 configuration** — 29 symbols, `max_symbol_weight = 0.10`, v2's
cluster caps — not the frozen 12-symbol v1 contract.

Verified directly: `outputs/reports/amat_full_backtest.v1-frozen.html` carries
the header `amat-managed v2.0.0`, the config digest `72bff815afc9f513`, a
$2,900,000 start (29 × $100,000), and per-asset sleeves for all 17 symbols
outside the v1 universe (VTI, XLK, XLF, XLE, XLV, XLI, VEA, VWO, EWJ, FXI, SHY,
AGG, TIP, LQD, HYG, VNQ, IYR).

What survives the correction and what does not:

- **Survives:** the v1-runner-vs-v2-overlay comparison. Both engines ran the same
  configuration, data, costs, and funding, so the overlay's effect is still
  attributable and the v2 rejection still stands.
- **Survives:** the SPY comparison. Both engines trail buy-and-hold SPY.
- **Does not survive:** any claim about the frozen 12-symbol v1 contract. It has
  never been backtested on real market data. The gate results (GLD 12.5%,
  2026 20.9%) describe a 29-symbol portfolio and say nothing about this config.

The lesson is a naming defect, not only a clerical one: `v1-frozen` names a
runner and reads as a strategy. Anything reported under it must state the
configuration digest beside it, which the report footer does and the summary
tables did not.

Not promoted regardless. Of the ten research gates in `promotion-gates.md`, only
the two concentration gates have ever been computed for anything, and those were
computed on the wrong universe for this row.

---

### `amat_v3.toml` — amat-etf-news v3.0.0 — mechanism wired, no independent result yet

**Status: wired and runnable 2026-08-11. Numerically identical to v1 until
news history accrues; no performance claim is possible or made.**

Every strategy parameter in `amat_v3.toml` is byte-identical to `amat_v1.toml`
(the config digest differs only through the header comments). What makes a run
"v3" is a conditioning series supplied at run time:

1. `newsdesk export-scalars` derives one scalar per calendar date from the
   attention-spike rule — a date whose new-article count reaches
   `spike_multiple` (3.0) times the trailing 63-day median exports the de-risk
   scalar (0.5); every other date exports 1.0. Parameters travel in the file
   header; counts filter on `ingested_at` (the store's own clock) as of
   23:59:59.999Z on the decision date, hours before the earliest execution.
2. `amat study configs/strategy/amat_v3.toml --scalars <file>` aligns the file
   to the panel **fail-closed** (a missing session is fatal, never defaulted
   to full size) and applies it through the same `weight_scalars` hook the
   regime experiment used. Sleeves stay unconditioned.

Verified 2026-08-11 on `alpaca-sip-2026-08-08`: with the store empty, the
exported series is all-1.0 and the v3 study reproduces v1's numbers exactly —
the wiring adds nothing until the store knows something, which is the honest
baseline for a point-in-time feature. Re-verified the same day on
`alpaca-sip-2026-08-11` at $100,000: v3 prints +85.24% / 1.01 / −8.69%,
byte-for-byte the v1 combined result on that snapshot and funding
(`outputs/reports/amat_v3_study.pdf`).

Re-verified 2026-08-15 in campaign `20260815T161255Z` against the larger
checked-in export `outputs/scalars/news_scalars_2016-2026.csv`: that file is
**constant 1.0 on all 2,665 panel sessions** (3,872 rows, zero below 1.0),
so the attention-spike rule has still never fired on any session the
strategy trades. v3's trailing-horizon rows are consequently bit-identical
to v1's at every window, and `scripts/horizon_sweep.py` now measures the
series and records `conditioned: false` rather than inferring conditioning
from the presence of a `--scalars` path. Two agreeing rows here corroborate
nothing. The four measuring commands (`stress`, `walkforward`,
`montecarlo`, `project`) take no scalars argument at all, so the campaign
skips them for v3 by name instead of printing the v1 core under a v3
heading.

A future v3 result is a candidate only if it survives
`amat.validation.falsification` (shuffled, turnover-matched-random,
static-mean) and the walk-forward protocol. The regime-conditioning experiment
below was rejected by exactly those controls; the news series inherits the
same bar. Backfilled news can never produce a valid result here: the store's
knowability filter is our own ingestion clock, which begins 2026-08-11.

---

## Rejected

### `amat_v5.toml` — `3968d08e264ccbfa` — amat-etf-breadth v5.0.0

**Status: REJECTED 2026-08-11, on the first measurement.**

The v5 hypothesis, stated in the config header before measurement: adding five
weakly correlated trend sleeves — FXI (China equity), TIP (inflation-linked
Treasuries), HYG (US high yield), EMB (EM sovereign USD debt), VNQ (US real
estate) — to the **byte-identical** v1 core increases the number of
independent trend bets under the same 8% volatility target and caps, raising
risk-adjusted return through breadth rather than timing. Deliberately designed
so the static-mean critique that killed v2/v3-regime/v4 could not apply: a
universe change has no per-session series whose mean could do the work.

Protocol compliance: null case pinned bit-identical
(`tests/unit/test_v5_universe.py` — v5 restricted to the v1 symbol list
reproduces v1 exactly), future-mutation causality test on the 17-symbol
universe, and the harness refuses any candidate/baseline pair differing
outside the universe (`falsify_universe`, `_identical_core`). SHY excluded on
its unresolvable 2021-12-01 conflicting dividend (re-ingestion on 2026-08-11
returned the same two rows, confirming the vendor re-serves them); EMB was
newly ingested for this experiment (snapshot `alpaca-sip-2026-08-11`, 30
symbols, coverage verified rectangular).

Measured on `alpaca-sip-2026-08-11`, sample 2016-12-30 → 2026-08-10 (2,414
sessions), 2 bps one-way, corporate actions applied, $100,000, identical data
and funding for every row (`amat study configs/strategy/amat_v5.toml
--falsify-universe configs/strategy/amat_v1.toml`):

| Run | Symbols | Total | Ann. | Vol | Sharpe | Max DD | Fills |
|---|---:|---:|---:|---:|---:|---:|---:|
| **baseline (amat-etf v1)** | 12 | +85.24% | +6.65% | 6.59% | **1.01** | **−8.69%** | 3,643 |
| candidate (amat-etf-breadth) | 17 | +66.77% | +5.49% | 6.22% | 0.88 | −9.33% | 4,628 |
| control: without FXI | 16 | +70.07% | +5.70% | 6.12% | 0.93 | −9.83% | 4,491 |
| control: without TIP | 16 | +69.87% | +5.69% | 6.33% | 0.90 | −8.67% | 4,431 |
| control: without HYG | 16 | +75.03% | +6.02% | 6.36% | 0.95 | −9.29% | 4,523 |
| control: without EMB | 16 | +73.06% | +5.90% | 6.30% | 0.94 | −9.02% | 4,494 |
| control: without VNQ | 16 | +72.03% | +5.83% | 6.16% | 0.95 | −8.82% | 4,381 |

**Verdict: REJECTED — the expanded universe does not improve on the
baseline** (0.88 against 1.01, with a deeper drawdown and 27% more fills).

The leave-one-out pattern is the finding. Every 16-symbol variant (0.90–0.95)
beats the 17-symbol candidate, and none reaches the 12-symbol baseline: the
damage is **monotonic in breadth** — each addition hurt, roughly additively —
not one bad pick. That is the strong form of rejection: the hypothesis is not
merely unproven on this sample, its direction is contradicted.

Why breadth diluted rather than diversified, mechanically: the signal is
volatility-normalized (`q = score / sigma`), so lower-volatility additions
with thin trend premia take large weight per unit of signal, crowding capital
away from the sleeves that carry the return (GLD/QQQ/SPY, realized 14–23%
vol) under the fixed 8% target and 100% gross cap. Measured on this sample's
adjusted closes: TIP 5.8%, HYG 8.4%, EMB 10.1% realized vol — all well below
the return-carrying sleeves, and under the strategy's own EWMA-with-floor
sigma they sit lower still. The sleeve evidence makes the contrast explicit:
**four of the five added symbols are profitable in isolation** (EMB +21.0%,
FXI +20.5%, HYG +18.3%, TIP +6.3%; VNQ loses −0.4%), yet **every one of them
subtracts at the portfolio level**. "Good asset" and "good addition to this
allocator" are different claims — the second is the one that was tested. This also converges with the hindsight-basket note
in the v1 entry: over this decade, *fewer, stronger* trend sleeves beat
breadth, and v1's 12 already sit past the point of diminishing returns.

What survives the rejection: the `falsify_universe` harness and its
identical-core guard, the leave-one-out control design, the v5 config and its
null-case/causality tests (all retained — a future universe candidate reuses
them unchanged), the 30-symbol snapshot with EMB, and the per-sleeve evidence
that EMB/FXI/HYG carry standalone trend premia — a future *cross-sectional*
design (rank-and-select rather than hold-everything) is the natural next
hypothesis, and it must be proposed with its own explanation of why the
allocator-dilution mechanism above would not simply recur. Following
precedent, the verdict lives here, not in the config file.

Two corrections of record, noted here rather than in the config because
editing it would change the digest this entry cites: the config header says
the non-universe sections are "byte-identical" to v1 — overstated; every key
and value is identical (enforced at run time by the harness's identical-core
guard), but v1's comment lines are stripped, so the bytes differ.

**Both provenance gaps closed 2026-08-13.** The full battery re-ran under the
run-record machinery and reproduced the rejection exactly — baseline 1.0091,
candidate 0.8823, every leave-one-out variant above the candidate (without
FXI 0.93 / TIP 0.90 / HYG 0.95 / EMB 0.94 / VNQ 0.95), `dominated_by` all
five additions — persisted as run record
`outputs/runs/study-amat-etf-breadth-20260813T191007.903987Z`. The
regenerated report `outputs/reports/amat_v5_study.pdf` (26 pages, real
tables) now carries the labelled Gemini advisory pages as well
(gemini-3.6-flash, 2026-08-13 19:38 UTC); the earlier quota degradation did
not recur.

---

### `amat_v4.toml` — `2b777041d92231d1` — amat-etf-switching v4.0.0

**Status: REJECTED 2026-08-11, on the first measurement.**

The v4 thesis was cooperative stance switching: the v1 trend core and a
defensive stance of the *same* core (rates/metals/dollar, 70% gross cap, 6%
vol target, otherwise the v1 contract verbatim), blended per session as

    target = lambda * trend + (1 - lambda) * defensive

with `lambda` from a declared, unfitted breadth rule — the EWMA(10) of the
fraction of the 12-symbol universe above its own close 63 sessions earlier,
mapped linearly from [0.35, 0.65] to [0, 1]. Zero fitted parameters, by design:
the prior regime-conditioning rejection showed a causally fitted model losing
to its own average, so v4 tested the simplest possible observable instead. The
blend is convex, so every cap either stance declares bounds the blended book.
Tests pin that `lambda = 1` is bit-identical to v1 and `lambda = 0` is
bit-identical to the defensive stance run alone.

Measured on `alpaca-sip-2026-08-08`, 2,413 sessions, 2 bps one-way, corporate
actions applied, $1,200,000, identical data and funding for every row
(`amat study-v4 configs/strategy/amat_v4.toml --falsify`):

| Run | Total | Ann. | Vol | Sharpe | Max DD | Fills | Mean λ | Flips |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **trend stance only (v1)** | +84.82% | +6.63% | 6.59% | **1.01** | −8.69% | 3,641 | 1.000 | 0 |
| v4 breadth-blended | +74.83% | +6.01% | 6.13% | 0.98 | −8.67% | 3,492 | 0.786 | 1,002 |
| control: lambda shuffled | +47.64% | +4.15% | 5.92% | 0.70 | −8.23% | 7,859 | 0.786 | 1,805 |
| control: random, matched turnover | +28.70% | +2.67% | 5.29% | 0.50 | −7.66% | 6,402 | 0.497 | 1,001 |
| **control: static mean** | +71.34% | +5.79% | 5.67% | **1.02** | **−6.84%** | 3,256 | 0.786 | 0 |
| control: defensive only | +21.63% | +2.07% | 3.87% | 0.53 | −10.47% | 1,312 | 0.000 | 0 |

**Verdict: REJECTED — does not improve on unconditioned v1** (0.98 against
1.01), and a **constant 0.786 of target size beats it too** (1.02) with zero
switching turnover and a smaller drawdown.

Two things are true at once, and recording both is the point:

*The breadth timing carries real information.* The candidate beats its
shuffled control by 0.28 Sharpe (0.98 vs 0.70) and a random series of matched
turnover by 0.48 — far larger margins than the regime model managed. When this
selector de-risks, it de-risks at genuinely worse-than-random moments to be
absent from. The defensive stance alone (0.53, −10.47% max DD) is also measured
and is worse than either stance-mix on every axis: the defensive basket without
trend timing on top of it is not a refuge.

*The information is still worth less than it costs.* Every point of the
blend's benefit is reproducible by holding a constant 78.6% of v1's size —
no breadth measure, no second stance, no 1,002 lambda changes. The same shape
as the regime rejection: switching mechanisms keep rediscovering that their
mean is doing the work. Under the frozen v1 contract that constant is not
reachable anyway (it is simply a lower volatility target, which is a different
strategy version), so the operative conclusion is unchanged: **v1 stands**.

Following the v2 precedent, the rejection is recorded here, not in the config
file (annotating the TOML would change the digest cited above). The config,
`study-v4` command, `StanceBlend` runner hook, breadth selector, and
`falsify_blend` harness all remain: the mechanism is sound and pre-wired for a
future stance pair that might pass, and the controls that rejected this one run
unchanged against the next. Report: `outputs/reports/amat_v4_study.pdf`.

**Re-measured 2026-08-11 on `alpaca-sip-2026-08-11` at $100,000** (2,414
sessions): the verdict reproduces on the fresh snapshot and funding — v1
1.01, v4 0.98, shuffled 0.83, random-matched 0.51, **static mean 1.03**,
defensive-only 0.54. Every row except the shuffled control moved by ≤ 0.01
Sharpe; the shuffled control moved 0.70 → 0.83 (+47.64% → +59.04% total),
because the seeded permutation is a function of the series *length* — one
extra session reshuffles the entire control series, which also means the
candidate-vs-shuffled margin is seed- and sample-sensitive (+0.28 on the
2026-08-08 snapshot, +0.15 here). The ordering, and therefore the verdict,
did not change. One additional finding at this funding: the blended
candidate **fails the symbol concentration gate** (GLD 25.6% of gross
positive P&L against the 25% limit) — an independent rejection ground the
$1,200,000 run did not trip. The regenerated report at this funding replaces
the artifact path above.

**Persisted 2026-08-13**: the same battery re-ran under the run-record
machinery and reproduced every row exactly (v1 1.0091, v4 0.9842, shuffled
0.8309, random-matched 0.5090, static mean 1.0253, defensive-only 0.5399;
verdict string "REJECTED: does not improve on unconditioned v1"). The
rejection now has citable evidence on disk: run record
`outputs/runs/study-v4-amat-etf-switching-20260813T175636.203139Z`, report
`outputs/reports/amat_v4_study.pdf` (19 pages, real tables).

---

### v3 regime conditioning — no config file, rejected before one was written

**Status: REJECTED 2026-08-10, on the first measurement.**

**Naming note (2026-08-11):** the version label "v3" was subsequently assigned
to a different, unrelated conditioning source — point-in-time news
(`configs/strategy/amat_v3.toml`, `amat-etf-news 3.0.0`, entry under
Candidates). The regime-conditioning experiment recorded here remains rejected
and has no config file; only the label was reused, for the same `[0, 1]`
weight-scalar hook.

The v3 thesis was "v1's allocator, conditioned by a multi-timeframe regime
estimate". It is implemented as one number per session — a multiplier in `[0, 1]`
applied to the target weights `decide()` already produced — computed by
`amat.validation.conditioning.regime_scalars` and passed to
`run_backtest(..., weight_scalars=…)`. **v1 is exactly this with a series of
ones**, which is what makes the comparison attributable to the conditioning and
nothing else.

No `amat_v3.toml` was written. A configuration is a claim that a strategy is a
candidate, and this one is not.

Measured on `alpaca-sip-2026-08-08`, 2,413 sessions, 2 bps one-way, corporate
actions applied, $1,200,000, identical data and funding for every row:

| Run | Total | Ann. | Vol | Sharpe | Max DD | Fills | Mean scalar | Flips |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **v1 unconditioned** | +84.82% | +6.63% | 6.59% | **1.01** | −8.69% | 3,641 | 1.00 | 0 |
| v3 regime-conditioned | +47.26% | +4.13% | 4.45% | 0.93 | −7.08% | 4,174 | 0.68 | 303 |
| control: shuffled | +44.87% | +3.95% | 4.80% | 0.82 | −7.31% | 8,544 | 0.68 | 1,403 |
| control: random, matched turnover | +51.60% | +4.44% | 5.38% | 0.83 | −8.60% | 4,318 | 0.75 | 303 |
| **control: static mean** | +54.77% | +4.67% | 4.48% | **1.04** | **−6.00%** | 2,905 | 0.68 | 0 |

**Two findings, and the second is the one that matters.**

*The regime timing is not nothing.* At 0.93 the candidate beats both its shuffled
control (0.82) and a random series of identical turnover and levels (0.83). The
model is not picking moments at random — the ordering of its cuts carries some
information.

*It is worth less than it costs.* It loses to unconditioned v1 (0.93 against
1.01), and it loses to **holding a constant 68% of target size, always** (1.04) —
a control with no model, no features, no refitting, and 1,269 fewer fills. Every
apparent benefit of the regime overlay is reproducible by simply holding less,
and the timing content does not pay for the 303 regime flips it costs.

That is the same shape as the v2 rejection at one tenth the severity, and it was
predictable from the sources the design was asked to follow: Shumway et al.'s
recommended jump model posts Sharpe 0.50 against SPY's 0.51 and loses to
buy-and-hold, while the models that beat SPY there are rejected by the authors as
unusable at 12–15 regime flips a year and carry no transaction costs anywhere in
the paper. This implementation flips **303 times in 9.6 years, about 29 a year** —
worse churn than the configuration those authors already called unusable.

**What survives.** The machinery, deliberately: `conditioning.py`,
`falsification.py`, the `weight_scalars` hook and 70 tests stay in the tree. The
rejection is only meaningful because the controls exist, and the next
conditioning idea should have to pass the same three. Note that the static-mean
control's 1.04 against v1's 1.01 is **not** a finding either — that gap is far
inside the ±0.38 standard error on this sample, and it describes moving along the
same risk/return line rather than beating it.

**A defect found while measuring, worth recording.** The first run produced a
scalar of 0.5 on 2,161 of 2,664 sessions with exactly **one** change — a constant
de-risking wearing a regime model's clothes. Cause: `CausalRegimeModel.assess()`
is a pure query by design and the label history only advances on `observe()`,
which `regime_scalars` never called, so `stability` stayed at 0.0 forever and
pinned the scalar to its floor. Had the scalar distribution not been printed
before the backtest ran, that constant would have been reported as "regime
conditioning". Fixed; the distribution is now asserted in the test suite.

### `amat_managed_v2.toml` — `72bff815afc9f513` — amat-managed v2.0.0

**Status: REJECTED 2026-08-10. Retained, still loads, still selectable via
`--engine v2`.**

29 symbols, the v1 core plus a risk overlay: setup grades, an expectancy gate,
a correlation cap, a regime scalar, fractional Kelly, and portfolio heat limits.
The overlay can only shrink or veto a v1 target, so v2 minus v1 is attributable
to risk management alone.

| Total | Ann. | Vol | Sharpe | Max DD | Symbol gate | Year gate |
|---:|---:|---:|---:|---:|---|---|
| +2.24% | +0.23% | 1.29% | 0.18 | −3.06% | PASS (DBC 24.8% of 25%) | **FAIL (2021 71.9% of 40%)** |

**Grounds for rejection, in order:**

1. **Year concentration 71.9% against a 40% cap.** `promotion-gates.md` lists
   "depends primarily on one instrument, year, or handful of trades" as an
   *automatic* rejection condition. This alone disqualifies it.
2. **Symbol concentration clears by 0.2pp** (24.8% of a 25% cap), which is not a
   margin to rely on.
3. **6.40% average gross exposure against an 8% volatility target.** It realizes
   1.29% volatility — 16% of target. It is not implementing the strategy it
   claims to implement.
4. **It keeps a tenth of v1's return at a third of its Sharpe** from identical
   data, costs, and funding.

**What was tried before rejecting it.** Three defects were found by code
inspection, each fixed test-first, before any of the above was treated as a
verdict on the design:

- size caps composed multiplicatively, so N independently-motivated ceilings in
  [0,1] produced a size no layer asked for;
- the expectancy gate tested a point estimate rather than an upper confidence
  bound, halting on noise;
- an unfitted regime model applied `risk_floor` through the entire 504-session
  warm-up, encoding "no estimate" as "worst estimate".

The fixes roughly doubled the return (+1.20% → +2.24%), improved Sharpe
0.10 → 0.18, lifted gross exposure 1.31% → 6.40%, and cut expectancy-gate vetoes
by 67%. Symbol concentration moved 33.3% → 24.8% with no change to concentration
logic, which is what identified concentration as a symptom of under-deployment
rather than an independent fault. The year gate still fails. Diagnosis in
`docs/results-2026-08-08.md`.

**What still binds**, measured on the full sample: `min_grade` 15,413 vetoes
(62% of all), `negative_expectancy` 5,873 (24%), `max_positions` 2,056,
`min_effective_bets` 1,242, `max_portfolio_heat` 122. No remaining defect is
claimed — the residual is configured restrictions behaving as configured.

---

## Registered experiments (`configs/experiments/`)

Machine-readable registrations begin 2026-08-13; each was written and
committed to disk **before** its run, and each run persisted a record under
`outputs/runs/`. Summaries here; full precision in the records.

### 0001 — target-volatility sweep — the frozen 8% stands; 6% earns eligibility

`amat sweep-target-vol` over {5, 6, 8, 10, 12}% annualized targets, split
fixed at 2022-12-30 before any result was seen; ranking by training-window
fold consistency only; snapshot `alpaca-sip-2026-08-11`, $100,000, 2 bps.
Run record `outputs/runs/sweep-target-vol-amat-etf-20260813T003400.396140Z`.

| Target | Train fold score | Train Sharpe | Valid. Ann. | Valid. Sharpe | Valid. Max DD |
|---:|---:|---:|---:|---:|---:|
| 5% | 0.789 | 1.03 | +5.95% | 1.23 | −4.28% |
| **6%** | **0.813** | 1.05 | +7.23% | **1.27** | −4.95% |
| 8% (frozen) | 0.652 | 0.89 | +8.41% | 1.19 | −6.24% |
| 10% | 0.558 | 0.84 | +9.60% | 1.22 | −6.80% |
| 12% | 0.506 | 0.82 | +10.37% | 1.25 | −7.57% |

The 6% target ranks first on training consistency and holds on the
ranking-blind validation slice, and the neighbourhood is a smooth monotone
plateau rather than a spike — the pattern of a real property, not a lucky
grid point. But the validation Sharpes span only 1.19–1.27, well inside one
standard error, so the risk-adjusted advantage is small. What the sweep
establishes unambiguously is the **frontier**: within the long-or-cash,
never-re-lever design, annual return rises nearly linearly with the target
(validation +5.95% → +10.37%) while drawdown worsens (−4.28% → −7.57%) and
consistency falls. "The optimal risk:reward ratio" is therefore a choice of
objective, not a discovery: per unit of risk the evidence mildly favours 6%;
per absolute annual profit it favours the highest target the drawdown
tolerance allows. Per the registered rule, the frozen 8% is unchanged; 6% is
eligible to be registered as a future version under the full protocol.
Caveat recorded at registration: all of this history is burned, so even the
validation window is ranking-blind, not analyst-blind.

### 0002 — volatility-state conditioning — survives its controls by 0.001 Sharpe; not pursued

`amat regime-experiment`: halve size while SPY's 21-session realized vol
exceeds its expanding 80th percentile (causal, zero fitted parameters —
deliberately the simplest thing still describable as a regime signal, after
v3's fitted KMeans and v4's breadth blend both died on these controls). Run
record `outputs/runs/regime-experiment-amat-etf-20260813T003358.615261Z`.

| Run | Total | Sharpe | Max DD | Fills |
|---|---:|---:|---:|---:|
| v1 unconditioned | +85.24% | 1.0091 | −8.69% | 3,643 |
| **vol-state conditioned** | +79.37% | **1.0494** | **−6.31%** | 3,581 |
| shuffled | +71.22% | 0.9446 | −8.57% | 7,058 |
| turnover-matched random | +69.51% | 1.0052 | −6.37% | 3,272 |
| static mean (0.903) | +78.49% | 1.0482 | −7.87% | 3,476 |

Nominal verdict: **SURVIVES** — the first conditioning candidate to beat
every matched control. Honest verdict: the margin over its own static-mean
control is **0.0012 Sharpe**, one part in a thousand, far inside the
seed/sample sensitivity already measured on the v4 shuffled control. The
*timing* of the cuts is therefore not established; virtually all of the
improvement is reproduced by always running at the series' mean 0.903 size.
What is real is the risk transformation — max drawdown −8.69% → −6.31% for
5.9pp of total return — and that is exactly the static trade the
target-volatility sweep (0001) expresses more directly and more honestly.
Decision: not promoted, no v6 conditioning candidate on this evidence, and
recorded so the third regime idea is not re-derived from scratch a fourth
time.

### 0003 — exit-rules sweep — falsified as pre-registered; the no-exit core stands

Registered 2026-08-13T18:55Z *before* running, with the failure prior in the
registration: stops realize losses volatility targeting already sized for;
take-profits truncate the right tail the strategy's positive skew depends on
(the trade-quality table above — median −$38.05 under mean +$78.81 — is that
skew, measured). Grid: stop-loss {none, 5%, 10%, 15%} × take-profit
{none, 10%, 20%}, re-entry embargo frozen at 21 sessions at registration.
Ran 2026-08-14 (`amat sweep-exit-rules`, training ranked through 2022-12-30,
validation 2023-01-02…2026-08-10 ranking-blind); run record
`outputs/runs/sweep-exit-rules-amat-etf-20260814T014415.823351Z`.

| Rules | Train ann | Train Sharpe | Train fold score | Valid ann | Valid Sharpe |
|---|---:|---:|---:|---:|---:|
| sl15%+tp20% | +6.41% | 1.18 | 0.812 | +6.81% | 1.11 |
| tp20% | +6.40% | 1.18 | 0.812 | +6.94% | 1.13 |
| **no exits (frozen core)** | +5.61% | 0.89 | 0.652 | **+8.41%** | **1.19** |

Verdict, verbatim: "12 candidates evaluated (multiple-testing risk rises
with each). The top training fold scores are indistinguishable (within
1e-09); no winner is declared and the frozen no-exit core stands." Both
halves of the pre-registered falsification clause fired: the top ranking is
a tie the guard refuses to break, **and** every overlay that beat the core
in training lost to it on the validation window — the take-profit's
training "improvement" (Sharpe 1.18 vs 0.89, shallower drawdown) is bought
by selling the right tail, and the bill only arrives out of window.
Stop-loss-only rows are near no-ops: the volatility target de-risks
positions before a 5–15% stop is ever touched. **Falsified.** No overlay is
promoted; the exit machinery (`amat.backtest.exits`) remains available for
future registered experiments only.

**Randomization control, 2026-08-15** (`--random-control 20`, seed
20260815; record `sweep-exit-rules-amat-etf-20260815T064113.906038Z`): 20
uniform draws (stop 3–30%, take-profit 5–60%, same embargo and split)
measured against the no-exit core, testing whether the grid conclusion was
an artifact of its 12 fixed points. Result: **zero of 20 draws beat the
core's validation annualized return** (+8.41%; best draw +8.31%); 7 of 20
nudge past its validation Sharpe (best 1.30 vs 1.19, inside noise) by
cutting volatility — the same risk-for-return trade the volatility-target
dial (experiment 0001) prices directly and more cheaply. The two rules with
the best *training* Sharpes (1.36, 1.38) rank mid-pack on validation —
training selection fails to transfer here too. The 0003 conclusion is not
grid-shaped.

### 0004 — TLT risk:reward variants — winner earns eligibility only; frozen universe unchanged

Registered 2026-08-15T06:45Z *before* running, motivated by the TLT audit
(the only losing sleeve) and pre-loaded with its own trap: the 2022 rates
shock sits inside the training window, mechanically favouring TLT-removal
on training, and removal after observing the loss is hindsight universe
selection. Grid: without-TLT, without-rates, rates cap 0.20/0.10, and the
critical control *without-IEF* (deleting the profitable rates sleeve).
Artifact `outputs/experiments/0004-tlt-variants-20260815T063354Z.json`.

| Variant | Train fold | Valid Sharpe | Valid ann |
|---|---:|---:|---:|
| **A: without TLT** | **0.706** | 1.25 | +8.88% |
| C: rates cap 0.20 | 0.653 | 1.24 | +8.78% |
| frozen v1 | 0.652 | 1.19 | +8.41% |
| B: without TLT+IEF | 0.648 | 1.24 | +9.20% |
| D: rates cap 0.10 | 0.639 | 1.29 | +9.24% |
| control: without IEF | 0.628 | 1.13 | +8.32% |

Verdict, verbatim: "6 variants evaluated. A: without TLT outranks frozen v1
on training AND holds on validation. Per registration this earns only
eligibility for a registered new version, with the hindsight-selection
caveat permanently attached… It changes nothing in place." Three readings,
all pre-registered: the control ranked **last**, so the effect is
TLT-specific (long duration), not "delete rates"; the winning margin (1.25
vs 1.19 validation Sharpe over 3.6 years) is inside one standard error;
and D's best-in-table validation Sharpe cannot be selected because it
ranked fifth on training — that discipline is the point. The frozen
12-symbol universe stands.

### 0005 — rebalance-band sensitivity — falsified; the neighbourhood is a plateau

Registered 2026-08-15T06:50Z *before* running (the v6 audit's cost
observation, given its registered test). Band grid {1,2,3,4,5}%; artifact
`outputs/experiments/neighborhood-20260815T064052Z.json`. Verdict,
verbatim: "The frozen 2% band ranks first on training; falsified per
registration." Costs fall monotonically with the band ($5,332 → $3,724
full-sample) and it does not matter at 2 bps; the validation-Sharpe spread
across the grid is 0.168, inside one standard error — "insensitive", per
the registration's own clause.

The same artifact closes the long-open **nearby-parameter-stability** item
with unranked one-at-a-time sensitivities: every horizon, deadband, and
covariance-lookback perturbation lands at validation Sharpe 1.11–1.36
around the frozen 1.19, with training and validation orderings
*disagreeing* (horizons 21/50/252: train Sharpe 0.75, validation 1.36).
That is a plateau plus noise, not a better configuration nearby. Nothing in
this section ranked, won, or became eligible — it was never registered as a
candidate search.

### 0006 — universe width and cross-sectional selection — falsified as pre-registered

Registered 2026-08-20 in `configs/experiments/0006-cross-sectional-universe-width.toml`,
**before any arm ran**, with the registered prior "primary_expectation = fails".
Measured the same day on `alpaca-sip-2026-08-11`, 30 symbols, $100,000, 2 bps.
All of it is burned history: nothing here is out-of-sample evidence and nothing
is promoted.

Two separable questions, deliberately not confounded.

**Secondary — does a wider universe help?** No, on this sample. Arm B30 holds
the v1 core unchanged over all 30 snapshot symbols (10 clusters, caps from
cluster size alone) and loses to the 12-symbol frozen core:

| Arm | Symbols | Train Sharpe | Fold score | **Valid Sharpe** | Valid ann | Valid maxDD | Fills |
|---|---:|---:|---:|---:|---:|---:|---:|
| A1 frozen v1 | 12 | 0.892 | 0.652 | **1.194** | +8.44% | −6.24% | 3,643 |
| A2 v6 (live) | 11 | 1.020 | 0.839 | **1.323** | +7.67% | −4.94% | 3,110 |
| B30 wide, no selection | 30 | 0.702 | 0.496 | **1.110** | +6.92% | −7.66% | 5,742 |

Widening costs 58% more fills and buys a worse result on every axis. The added
symbols are mostly additional equity beta (four US sectors, VTI, style tilts),
so they add correlated positions rather than independent bets, and the
volatility target then spreads the same risk budget more thinly across them.

**Primary — does ranking within the wide universe help?** No. Ten candidates,
`max_positions` ∈ {5, 8, 10, 15, 20} × `min_horizon_agreement` ∈ {0, 3}, ranked
on training folds only. Training-ranked winner: **K=15, no agreement filter**,
fold score 0.527. Measured ranking-blind on validation it scores **0.982** —
below the A1 baseline (1.194) and below its own hold-everything arm (1.110), so
it fails two of the three registered win conditions before the control is even
consulted.

Selection is monotone in the wrong direction: validation Sharpe falls from 1.110
(all 30) through 1.037 (K=20), 0.982 (K=15), 0.921 (K=10), 0.902 (K=8) to 0.745
(K=5). Concentration hurts steadily. The `min_horizon_agreement = 3` rule — the
v6 audit's hypothesis #2 — makes every single K worse and is falsified outright.

**The decisive control: random-K at matched position count.** 20 seeds, holding
selected names while they remain eligible and filling vacancies at random.

| | Validation Sharpe | Fills |
|---|---:|---:|
| Rank-based K=15 | 0.982 | 6,023 |
| Random-K, median seed | **1.007** | 3,630 |
| Random-K, min / max | 0.884 / 1.180 | |

The candidate beats **5 of 20** random seeds. Ranking by trend strength does not
select better than a coin flip does, and it pays 66% more turnover to fail.

**Where the information actually is.** At 0 bps (diagnostic only, guardrail 7)
rank-based K=15 reaches 1.098 against the random-K median of 1.062, beating
14 of 20 seeds. So the ranking *does* carry a little information — and it is
smaller than the turnover required to act on it. This is the falsification
module's own standard: "a conditioning signal that only wins gross is not a
finding, because gross is not investable."

**Verdict: falsified, as the registered prior predicted.** No arm earns
eligibility. The frozen 12-symbol universe and the absence of any selection rule
both stand. Deflated for 10 trials per universe, the best candidate's margin is
negative before deflation and is not restated here as though it were positive.

This is now the **fourth** distinct mechanism to die in this repository's
controls, and it died in a new way worth recording: the previous three (v3 news
scalars, v4 breadth blend, 0002 volatility-state conditioning) each reduced to
"hold less on average" against the static-mean control. This one is not a sizing
rule at all — it changes *membership* at constant size — and it still lost,
because among 30 highly correlated ETFs the strongest trends are repeatedly the
same factor wearing different tickers.

**The 92-symbol extension (arms B92/C92) was registered at the same time** and
its snapshot was still ingesting when these numbers were produced. Registering
both universes in advance is what keeps running the second from being a second
look at the data. It was ingested and run the same day; see below.


#### The wide-universe extension (arms B92/C92, run as B81/C81) — 2026-08-20

Registered at the same time as the 30-symbol arms and before either was
measured. Snapshot `alpaca-sip-wide92-2026-08-20`: 92 symbols, 2,665 sessions,
window coverage 0.9866, ingested the same day.

**Deviation, recorded rather than hidden: run on 81 symbols, not 92.** Two
mechanical exclusion rules, both reading data quality and never returns:

- *Ambiguous distributions* — 8 symbols (BND, BNDX, EWT, IWF, MTUM, TLH, VCIT,
  VCSH) carry 18 corporate actions with conflicting values on one ex-date.
  Twelve fall in December, the signature of an income distribution and a
  year-end capital-gains distribution booked to one ex-date — for which summing
  would be correct. That is a hypothesis about vendor bookkeeping, not evidence
  about any record, and this registry forbids relaxing the loader to sum.
  Eighteen cannot be resolved to the standard the SHY entry met, so they are not
  resolved. SHY is retained; its one conflict has an evidence-backed entry,
  re-recorded against the new snapshot id.
- *Incomplete coverage* — 3 symbols (JNK, SLX, GDXJ). The probe that built the
  92-symbol list tested only the raw daily pull; `_build_daily_frame`
  inner-joins raw and adjusted and drops cells present in one. JNK survives that
  join only from 2019-05-06, and because `load_panel` intersects, leaving it in
  truncated the panel from 2,665 sessions to 1,826 and made the arm
  incomparable. Caught before any 84-symbol number was reported.

**The bias this carries, stated plainly.** Both rules are mechanical but applied
after seeing which symbols fail, and the excluded set is mostly bond funds,
which underperformed 2016–2026. Removing weak performers *flatters* these arms.
The registered prior is failure, so a flattering bias makes a failure result
more convincing, not less — but a success here would not be safe to read as
evidence.

| Arm | Symbols | Fold score | **Valid Sharpe** | Valid ann | Fills |
|---|---:|---:|---:|---:|---:|
| A1 frozen v1 | 12 | 0.652 | **1.194** | +8.44% | 3,643 |
| A2 v6 (live) | 11 | 0.839 | **1.323** | +7.67% | 3,110 |
| B30 wide, no selection | 30 | 0.496 | 1.110 | +6.92% | 5,742 |
| B81 wide, no selection | 81 | 0.133 | **0.788** | +4.72% | 13,066 |
| C81 K=5 +3h *(training winner)* | 81 | 0.238 | 0.964 | +7.44% | 6,125 |

**Width degrades monotonically: 1.194 (12) → 1.110 (30) → 0.788 (81)**, at 3.6×
the turnover. The wider the universe, the worse the frozen core performs on it.

**But the selection result inverts, and the inversion is informative.** At 30
symbols the training winner was K=15 — selecting 15 of ~24 eligible, which is
barely a selection — and it lost to random-K (5/20 seeds). At 81 symbols the
winner is K=5 of ~50 eligible, a genuinely selective rule, and against 20
random-K seeds sharing the same eligibility filter it wins decisively:

| | Valid Sharpe | Fills |
|---|---:|---:|
| Rank-based K=5 +3h | **0.964** | 6,125 |
| Random-K median seed | 0.562 | 1,952 |
| Random-K min / max | −0.032 / 0.967 | |

18 of 20 seeds beaten. **Cross-sectional ranking does carry real information**
— the 30-symbol arm simply was not selective enough to show it, and its
zero-cost diagnostic (ranking beat random gross but not net) pointed the same
way. Note also that the `min_horizon_agreement = 3` rule, falsified outright at
30 symbols, is part of the winner at 81.

**Verdict against the registered rules: still not a win.** A win required
beating (a) the A1 baseline, (b) the hold-everything arm, and (c) the median
random-K seed. The candidate passes (b) — 0.964 vs 0.788, so selection *does*
rescue an over-wide universe from its own turnover — and passes (c) decisively.
It fails **(a)**: 0.964 against the frozen 12-symbol core's 1.194.

The honest summary is narrower and more useful than "everything fails":
**ranking carries information, and it is less information than widening
destroys.** Selection recovers part of what a large universe costs and never
recovers all of it. Combined with the 30-symbol arm's zero-cost finding, both
universes say the same thing from opposite directions — the edge exists and is
too small to pay for the turnover and diversification it requires.

No arm earns eligibility. The frozen universe and the absence of any selection
rule both stand. Reported with the trial count (10 per universe, 20 total) and
without deflation-adjusted restatement, because the best margin is negative
before deflation.

#### Two defects found and fixed while running this

1. **The 30-symbol study was blocked** by the documented SHY 2021-12-01 conflict
   (`cash_dividend` = [0.018007, 0.031906]). Resolved by the second remedy the
   registry named — a per-record exception ledger
   (`configs/data/action-exceptions.toml`, `amat.data.action_exceptions`) that
   is scoped to one snapshot id, must match the values actually present, and
   leaves every unlisted conflict failing closed. The reading chosen is
   0.018007, on the evidence that SHY pays monthly plus a second mid-December
   distribution in all eleven years of the sample, and 0.018007 pairs with
   2021-12-16's 0.018063 while 0.031906 pairs with nothing.

2. **The trade-attribution replay was split-blind.** `TradeLedger` rebuilds
   round trips from fills, and a fill stream does not carry a split, so after
   XLE's and XLK's 2:1 on 2025-12-05 the replay met a legitimate sell of twice
   what it thought was held. `insights.py` already applied splits correctly;
   `reporting.build_report` and `reporting.sleeves` did not, and now do.
   **No published v1 result is affected** — v1's only in-universe split is USO's
   2020-04-29 reverse split, and v1 held no USO position within four months of
   it (verified: zero USO fills 2020-03-01..2020-07-01; the run still reports
   3,643 fills, matching every earlier record). The hazard is nonetheless real
   and directional: a *forward* split raises, a *reverse* split silently leaves
   a phantom position with mis-stated P&L. Pinned by regression tests in
   `tests/unit/test_cross_section_and_scan.py`.

### 0007 — selection inside the frozen universe — nominally survives, falsified on the margin

Registered 2026-08-20 in `configs/experiments/0007-selection-within-the-frozen-universe.toml`,
**before any arm ran**, with the prior `expectation = "fails"` and a stated
mechanism. Measured the same day on `alpaca-sip-2026-08-11`. Burned history;
nothing promoted.

0006 left one question unasked: does a selective rule help *inside* the frozen
universe, where no widening cost is paid at all?

**The registered prior was wrong in direction and right in conclusion.** It
predicted selection would hurt, because the frozen 12 carries roughly half the
pairwise correlation of the wide universes (+0.192 against +0.377) and so has
little redundancy for a ranking to prune. It further predicted the damage would
appear as *higher* realised volatility, from concentrating into fewer correlated
names. Both specifics are falsified: selection nominally *helped*, and realised
volatility went *down*, not up.

| Arm | Fold score | **Valid Sharpe** | Valid ann | Valid vol | Fills |
|---|---:|---:|---:|---:|---:|
| v1 baseline | 0.652 | 1.194 | +8.44% | 7.07% | 3,643 |
| **v1 K=6** *(training winner)* | 0.742 | **1.246** | +8.73% | 7.01% | 2,992 |
| v6 baseline | 0.839 | 1.323 | +7.67% | 5.80% | 3,110 |
| **v6 K=6** *(training winner)* | 0.889 | **1.345** | +7.70% | 5.73% | 2,712 |

Both training winners beat their baselines on the ranking-blind validation
window, at ~18% less turnover. Under the registered win conditions alone, both
pass.

**Why they are still recorded as falsified.** Selection cuts gross exposure —
v1 68.2% → 62.6% at K=6, and → 45.3% at K=3 — so it is partly a de-risking
mechanism, which is what has killed three earlier mechanisms here. A
**static-mean control** was therefore added (a constant weight scalar
reproducing the candidate's own mean gross, costing no turnover). Adding a
control can only make a test harder, so this is not a moved goalpost.

| | v1 K=6 | v6 K=6 |
|---|---:|---:|
| Candidate | 1.246 | 1.345 |
| Static-mean control | 1.206 | 1.325 |
| Random-K median (20 seeds) | 1.140 | 1.307 |
| Seeds beaten | 15/20 | 12/20 |

The candidates clear both controls — and every margin is inside noise. Paired
bootstrap of the Sharpe difference against the static-mean control over the
902-session validation window, 4,000 resamples:

| | Difference | 95% CI | P(diff ≤ 0) |
|---|---:|---:|---:|
| v1 K=6 | +0.037 | **[−0.206, +0.286]** | 0.378 |
| v6 K=6 | +0.018 | **[−0.204, +0.255]** | 0.424 |

Neither interval excludes zero, and both p-values are close to a coin flip. This
is the 0002 precedent exactly: *"survived nominally by 0.0012 Sharpe over its
static-mean control and was still not pursued; that margin is the system
working."*

**On the deflated Sharpe, and why it is not the number to quote.** DSR against
the registered 10-trial count returns 0.937 (v1) and 0.964 (v6), which reads as
significant. It is answering the wrong question. DSR asks whether an *absolute*
Sharpe of 1.33 could be produced by ten trials of a zero-skill process; the
un-selected v6 baseline already scores 1.323 with **zero** trials. The quantity
in dispute is selection's *contribution*, and the paired test above is what
measures it. Quoting the DSR here would be a true statement used to support a
false claim.

**Verdict: no promotion, no eligibility.** The frozen universes stand and no
selection rule is adopted. Trial count 10 per universe, 20 total.

#### The diagnostic that explains 0006 and 0007 together

Pre-declared in the 0007 registration as a diagnostic, not a verdict input.
Holding selectivity fixed at K ≈ 30% of each universe's median eligible count,
so only redundancy varies:

| Universe | N | Mean pairwise corr | Median eligible | K | Rank Sharpe | Random median | Beats |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1 | 12 | +0.192 | 7 | 2 | 0.894 | 0.579 | **19/20** |
| v6 | 11 | +0.228 | 7 | 2 | 1.086 | 0.660 | 17/20 |
| wide30 | 30 | +0.344 | 21 | 6 | 0.892 | 0.768 | 17/20 |
| wide81 | 81 | +0.377 | 54 | 16 | 0.688 | 0.544 | 13/20 |

**The redundancy hypothesis is falsified.** The rank correlation between
redundancy and beat-rate is **−0.821** — the opposite sign to the prediction.
Ranking beats chance *more* in the diverse universe, not less, which in
hindsight is unsurprising: a trend score differentiates genuinely between assets
with different drivers, and among near-duplicates it is closer to a coin toss.

What the row also shows is the finding that unifies both experiments. At matched
selectivity the ranking beats random in **every** universe (19, 17, 17, 13 of
20, against 10 by chance) — and **every one of those absolute Sharpes is below
its own baseline** (0.894 vs 1.194; 1.086 vs 1.323; 0.892 vs 1.110; 0.688 vs
0.788).

> The trend ranking carries real information. Concentration destroys more, via
> lost diversification, than the ranking's information recovers. That holds
> across a 12-symbol universe and an 81-symbol one, at every selectivity tested.

Which is why the K=6 winners above are the shape they are: the least selective
rules on the grid, barely concentrating at all, capturing a small turnover
saving — and no effect distinguishable from holding slightly less.

## 2026-09-07 v9 structure-wide30 research run — higher frequency, not promoted

This operator-requested research run was **not pre-registered** and used burned
history. It is therefore diagnostic evidence only, regardless of whether a
headline improves. The final citable record is
`outputs/runs/study-amat-etf-structure-wide30-20260907T153208.806426Z`; its
configuration digest is
`6a8b3cfe12241066d64fc44abb8adab31b8878625c73fa03fc4576352b8ff4c7`.
The matched current-v6 record is
`outputs/runs/backtest-amat-etf-regime-quality-20260907T150504.332389Z`.
Both end on the untouched holdout boundary, 2026-08-10; neither accessed data
past it.

V9 expands v6's 11 continuously covered ETFs to 30 liquid cross-asset ETFs and
adds a causal, soft structure multiplier. The multiplier combines four
interpretable price features — five-session variance ratio, Wilder RSI with a
healthy plateau, 63-session Donchian position, and downside-variance share —
and can reduce an asset's raw trend score by at most 25%. It cannot hard-delete
an asset. The signal remains long-or-cash because the executor contract has no
borrow, locate, short-margin, or signed-plan semantics; presenting this run as
long/short would be fictitious.

| Full-sample primary result | Current v6 | V9 wide30 | V9 − v6 |
|---|---:|---:|---:|
| Symbols | 11 | 30 | +19 |
| Total return | **+77.08%** | +65.88% | −11.20 pp |
| Annualized return | **+6.15%** | +5.43% | −0.72 pp |
| Annualized volatility | **5.41%** | 6.26% | +0.85 pp |
| Sharpe | **1.137** | 0.866 | −0.270 |
| Max drawdown | **−7.89%** | −9.45% | −1.55 pp worse |
| Average gross exposure | 56.38% | **76.38%** | +20.00 pp |
| Fills | 3,110 | **5,727** | +84.1% |
| Costs | **$3,496.83** | $4,503.47 | +$1,006.64 |
| Annualized cost drag | **0.365%** | 0.470% | +0.105 pp |
| Best-symbol P&L share | 22.98% | **14.55%** | −8.43 pp |

The candidate achieves the requested breadth, deployment, diversification, and
trade-frequency goals, but not the primary return/risk objective. More fills
mostly redistribute the same trend exposure across correlated listings and add
cost. Rates and credit are the clearest weak cluster in combined attribution:
TLT, HYG, TIP, AGG, LQD, EMB, and IEF are all net negative, while GLD, XLK,
QQQ, SPY, EEM, XLE, and VTI dominate profits. Removing those losers now would
be hindsight selection; the next test must specify a causal structure or
relative-value rule in advance and compare it with a static exposure-matched
control.

Two unrecorded exploratory iterations were used to debug and narrow the
operator-requested configuration. They have no evidentiary status and are not
quoted as results. This search count is disclosed because choosing the last
configuration after looking at earlier output raises multiple-testing risk.

**Decision: do not promote v9.** Keep it as a research strategy and shadow
candidate. The next honest test is a pre-registered walk-forward ablation that
holds universe, target volatility, covariance, and rebalance rules fixed and
switches only the structure multiplier. Follow that with cost stresses and a
new post-2026-08-10 paper period. True long/short, intraday, event/news, ML, and
statistical-arbitrage sleeves remain separate contracts described in
`docs/regime-adaptive-strategy-workflow-2026-09-07.md`; they were not smuggled
into this daily long-only backtest.

**Report-generation defect found and fixed.** The full PDF initially failed on
XLE's 2025-12-16 post-split sale. Holdings reconstruction received corporate
actions, but episode trade reconstruction did not, so its replay thought the
sale exceeded held shares. `build_insights` now passes the identical action map
to both replays, with a regression test. The rerun completed a 30-sleeve,
episode-diagnostic PDF at
`outputs/reports/v9-structure-wide30-20260907.pdf`.

## 2026-09-07 v10/v11/v12 breadth and adaptive attribution — not promoted

These operator-requested experiments used already-inspected history and were
not pre-registered. They are diagnostic attribution, never holdout evidence.
The expanded immutable snapshot contains 128 instruments; 124 are tradable in
v10/v11. RTX is quarantined because the ledger cannot represent the securities
distributed in the 2020 UTX transformation. COP, DVN, and FANG are quarantined
because their fixed and variable dividends collide in the provider's normalized
cash-dividend field. Excluding those records is safer than guessing or silently
inventing cash.

| Arm | Config digest | Purpose | Result |
|---|---|---|---|
| v10 breadth control | `04e6f23e888c6a04` | Frozen daily long/cash signal over 124 assets | Rejected |
| v11 broad adaptive | `c310985db3941d84` | v10 plus top-24 selection, structure/payoff scaling, causal KMeans risk conditioning | Rejected |
| v12 compact adaptive | `6cbfd14a1e06d63b` | v11 stack on v6's original 11 assets | Rejected |

The citable v10 full study is
`outputs/runs/study-amat-discovery-defense-energy-124-20260907T163138.117681Z`;
it includes all 124 independent sleeves and the PDF
`outputs/reports/v10-discovery124-20260907.pdf`. On the same 2,414 sessions as
current v6 it returned 35.64%, Sharpe 0.710, and maximum drawdown -7.00%, with
18,268 fills and $2,566.53 cost. Current v6 returned 77.08%, Sharpe 1.137, and
-7.89% drawdown with 3,110 fills. Thus breadth created 5.87 times the fills but
lost 41.43 percentage points of return and 0.427 Sharpe.

The exact 2017-07-03 matched-start v10 record is
`outputs/runs/study-amat-discovery-defense-energy-124-20260907T165731.127263Z`.
The v11 record is
`outputs/runs/study-amat-adaptive-defense-energy-124-20260907T164125.129005Z`,
with PDF `outputs/reports/v11-adaptive124-20260907.pdf`. V11 improved total
return from 34.16% to 39.16%, but Sharpe fell from 0.696 to 0.665, maximum
drawdown worsened from -8.11% to -9.92%, and costs rose from $2,513.53 to
$6,257.13 even though fills fell 27.0%. Selection reduced order count but
increased traded notional and did not improve the risk-adjusted objective.

The final softened v12 record is
`outputs/runs/study-amat-adaptive-compact-11-20260907T161113.539781Z`, with PDF
`outputs/reports/v12-adaptive11-soft-20260907.pdf`. On the exact same dates as
the v6 compact control, v12 returned 60.06% versus 70.64%, Sharpe 1.012 versus
1.114, and drawdown -7.05% versus -7.89%; it added 303 fills. The softer stack
was materially better than the first over-filtered v12 iteration (+31.26 pp
return and +0.275 Sharpe), but still failed to exceed the incumbent.

**Decision: promote none.** Preserve v6. Use v10 as a discovery dataset, not a
production universe; run causal family-shrunk eligibility and single-component,
turnover-matched ablations. Defense was the strongest new family (17/18
standalone sleeves positive, median Sharpe 0.467). Currency ETFs were the
weakest transfer (3/7 positive, median Sharpe -0.068), which supports a separate
carry/rate-differential FX sleeve rather than reuse of the equity model. Full
interpretation and promotion gates are in
`docs/strategy-adjustments-2026-09-07.md`.

## V6 research candidate — static 6% risk with auditable regime context

> **Universe changed 2026-08-15: TLT removed by operator decision.** v6 is now
> an eleven-symbol strategy at config digest `2712f694227321e4`. Everything in
> this section below the "TLT removed" heading describes the twelve-symbol
> configuration at digest `a27689fbe4ca07ab` and remains the record for it. The
> before/after measurement, and why the change is worth less than it looks,
> are in **[TLT removed from v6](#tlt-removed-from-v6--measured-2026-08-15)**.

### `amat_v6.toml` — `a27689f759bcc782` — research only

Run record `outputs/v6/v6-audit-20260813T221018Z.json`. V6 keeps the original
12-asset universe and signal, but uses the 6% volatility target selected by
registered experiment 0001. It also records a causal per-asset regime and
liquidity context and evaluates per-asset stop/target policies without letting
either experiment silently control the canonical result.

| Same burned sample | Total | Sharpe | Max DD | Fills | Costs |
|---|---:|---:|---:|---:|---:|
| v1, static 8% | +85.24% | 1.009 | -8.69% | 3,643 | $4,712 |
| **v6, static 6%** | **+77.97%** | **1.137** | **-8.04%** | **3,488** | **$3,881** |
| v6 regime/liquidity timing | +61.29% | 1.042 | -7.08% | 3,144 | $3,207 |

The timing overlay is rejected because it trails its constant-risk control;
`asset_regime.capital_control_enabled` is therefore false. The offline
per-asset contextual bandit evaluated no-exit and nine stop-loss/take-profit
actions. Zero rules cleared the paired, multiplicity-adjusted validation lower
confidence bound, so `risk_learning.enabled` is also false. The 6% core is a
research candidate, not a promotion: all history through 2026-08-10 is burned
and the apparent Sharpe improvement is not independent evidence.

#### The planned v6 evidence — completed 2026-08-15

The v6 audit's "required next evidence" items are now measured, on the same
protocol as v1's (the standard commands accept the v6 configuration
directly; canonical v6 *is* the static 6% core they run):

- **Cost ladder / full stress battery** (record
  `stress-amat-etf-regime-quality-20260815T063653.149460Z`): survives 5×
  costs; lag 2/3 and missed-fill injections immaterial; session-close fills
  −0% effect; leave-one-out Sharpe 1.00–1.15 across all twelve deletions;
  top-10 trades carry 33.9% of profit; correlation +0.33 vs SPY. The
  signal-free control repeats v1's lesson at 6%: +78.52% total at Sharpe
  0.97 and −14.51% DD — the signal buys drawdown, not return.
- **Monte Carlo** (record
  `montecarlo-amat-etf-regime-quality-20260815T064106.293766Z`; PDF
  `outputs/reports/amat_v6_montecarlo.pdf`): observed run at the 74th/78th/
  72nd percentile of resampled return/Sharpe/drawdown; 0.5% of paths end
  below funding; 1.0% breach −20% DD.
- **Forward projection** (PDF `outputs/reports/amat_v6_projection.pdf`):
  10-year median +83.24% total from $100k, p05 +43.80%; within-resampling
  frequencies only.
- **Trailing windows** ending 2026-08-10 (artifact
  `trailing-horizons-20260815.json`): 1y +16.43% (Sharpe 2.41, DD −3.06%),
  2y +19.74% (1.58), 5y +32.88% (1.03), 10y +77.97% (1.14). Dated PDFs:
  `outputs/reports/v6-horizon{1y,2y,5y,10y}-20260815.pdf`.
- **Paper capability**: `amat simulate` now accepts the v6 configuration
  (static core; the disabled overlay is never applied), so the
  post-holdout paper period can be served — the one evidence item only
  calendar time can produce.
- **The one registered "improve returns" lever** the audit identified — the
  wider rebalance band — was registered as experiment 0005 and **falsified**
  (the frozen 2% ranks first on training; the neighbourhood is a plateau).
  No other return-improving change is admissible from burned data, and per
  the audit's own instruction none was attempted by re-tuning.

### V6 randomized 1/3/6-month robustness and liquidity refinement — research only

Run record `outputs/v6/random-windows/v6-random-windows-20260813T231103Z.json`,
seed `20260813`: 36 windows sampled from the final three years, equally
stratified across quiet/normal/volatile SPY volatility, with 2/4/6/10 bps
all-in cost runs. Canonical v6 was positive in 83%, 83%, and 92% of the 1/3/6-
month windows at 2 bps. At 10 bps the three-month rate fell to 75%, directly
rejecting a higher-transaction-count objective.

A 36-cell v6 grid crossed target volatility, trend deadband, and rebalance
threshold at 6 bps. The existing 6% / 0.20 / 2% settings ranked first on the
predeclared development half, so no core parameter changed. An expanded
31-action per-asset stop/target bandit activated zero of 12 rules.

The run exposed entry participation up to 8.84% of a five-minute window. An
entry-only participation cap was implemented and measured in
`outputs/v6/random-windows/v6-participation-sensitivity-20260813T231503Z.json`.
The 0.5% cap improved several volatile and 10-bps slices while leaving sells
unrestricted. It is a forward-test hypothesis, not enabled in canonical v6,
because its threshold was selected after viewing burned history.

### Heavy-tailed regime stress (`amat stress-heavytail`) — measured 2026-08-15

**SYNTHETIC DATA. Not a backtest result, not a forecast.** Panels carry
`source="synthetic-heavytail"`. The tail index, regime matrix and downside bias
were chosen, not estimated; passing is not evidence of an edge, failing is
evidence of fragility.

Every prior robustness harness resamples observed history — the block bootstrap
reorders the panel's own joint moves, the stress battery perturbs costs, timing
and membership around the same path — so **none of them can produce a move the
decade 2016-2026 did not contain.** `src/amat/validation/heavytail.py` closes
that gap: log returns are diffusion under a persistent four-state Markov regime
(calm/normal/stressed/crisis, with per-regime volatility multiplier, jump
intensity and drift) plus **Pareto**-distributed jumps — a market-wide jump
propagated by each symbol's measured beta, and smaller idiosyncratic gaps.

Prices are calibrated per symbol from the snapshot, which is what makes them
relevant rather than generic: starting level = that symbol's actual last close;
sigma = its realized annualized volatility; beta = its beta to the equal-weight
market; and the full empirical correlation matrix, PSD-repaired by eigenvalue
clipping. Drift is **zero** by default so a long-only book cannot inherit the
sample's bull market — which is why the passive control loses money too, and
why the strategy column must be read against it rather than against zero.

24 scenarios × 756 sessions, $100,000, 2 bps one-way, seed 20260815:

| | v1 (12 sym, 8% target) | v6 (11 sym, 6% target) |
|---|---:|---:|
| Total return p05 / p50 / p95 | −15.9% / **−5.2%** / +7.6% | −7.9% / **+0.3%** / +9.4% |
| Max drawdown p05 / p50 | −18.3% / **−10.1%** | −13.0% / **−7.5%** |
| Worst drawdown, any scenario | **−20.2%** | **−13.1%** |
| Losing scenarios | 19/24 | 11/24 |
| Beat buy-and-hold | 14/24 | 14/24 |
| Smaller drawdown than buy-and-hold | 23/24 | **24/24** |
| Buy-and-hold p50 return / worst DD | −13.6% / −43.8% | −6.0% / −44.8% |

**The finding that matters: the historical −8.7% maximum drawdown is not a
bound.** The same v1 configuration drew down **−20.2%** in one of these
markets, 2.3× its worst observed session-history drawdown, and lost money in
19 of 24. A reader who took the backtest's drawdown as a planning figure would
be wrong by more than a factor of two the first time the market changes regime.

What survives the test is the *shape* of the claim the signal-free control
already established: the strategy is a drawdown-control mechanism. It cut the
passive book's drawdown in 23/24 (v1) and 24/24 (v6) scenarios, roughly halving
it at the median. It did not make money at zero drift, and at zero drift a
long-only trend follower should not be expected to.

v6's advantage here conflates two changes — the 6% volatility target and the
eleven-symbol universe — and the target is almost certainly the dominant term.
This measurement does not separate them and is not offered as evidence for the
TLT removal below.

Raising the tail to `--tail-index 2.0`, where the theoretical variance is
**infinite**, barely moved either strategy (v1 worst drawdown −19.1%, v6
−13.0%) even as the worst single market session reached −15.8%. The deadband,
volatility target and gross-exposure cap absorb isolated extreme sessions; what
costs money is a *sustained* regime shift the trailing volatility estimate has
not caught up to. That is the failure mode to watch, not the single-day crash.

Run records `stress-heavytail-amat-etf-20260815T181135.797974Z` and
`stress-heavytail-amat-etf-regime-quality-20260815T181215.539220Z`.

### TLT removed from v6 — measured 2026-08-15

**Status: applied at operator instruction. `a27689fbe4ca07ab` (12 symbols) →
`2712f694227321e4` (11 symbols).** The rates cluster now holds IEF alone; its
`gross_cap` of 0.35 no longer binds, so `max_symbol_weight` (0.20) is the
effective rates cap. No other cluster, cap, or parameter changed, and
`tests/unit/test_v6.py::test_v6_universe_is_the_v1_universe_minus_tlt` pins
that difference exactly so a second unreviewed universe edit fails the suite.
v1 through v5 are untouched and still carry TLT.

**This is hindsight selection and the file should say so.** TLT was deleted
*because* it was observed losing on the very sample every number below is
measured on. Registered experiment 0004 tested this exact removal under the
split protocol in advance and found the winning margin inside noise
(validation Sharpe 1.25 vs 1.19), which is why it earned eligibility only and
the frozen v1 universe was left alone. Nothing measured here overturns that.

Campaign `outputs/campaigns/v6-no-tlt-20260815/`, same snapshot
(`alpaca-sip-2026-08-11`), funding ($100,000), costs (2 bps one-way) and end
session (2026-08-10) as campaign `20260815T161255Z`, so the columns are
like-for-like:

| Measure | v6 with TLT | v6 without TLT | Change |
|---|---:|---:|---|
| 10y total / Sharpe / max DD | +77.97% / 1.137 / −8.04% | **+77.08% / 1.137 / −7.89%** | **−0.89pp / ±0 / +0.15pp** |
| 5y total / Sharpe | +32.88% / 1.03 | +35.52% / 1.09 | +2.64pp / +0.06 |
| 2y total / Sharpe | +19.74% / 1.58 | +22.25% / 1.75 | +2.51pp / +0.17 |
| 1y total / Sharpe | +16.43% / 2.41 | +17.41% / 2.50 | +0.98pp / +0.09 |
| 6m total / Sharpe | +5.07% / 1.76 | +5.30% / 1.85 | +0.23pp / +0.09 |
| Fills / costs | 3,488 / $3,881 | 3,110 / $3,497 | −378 / −$384 |
| Monte Carlo observed %ile (return / Sharpe / DD) | 74% / 78% / 71% | 68% / 76% / 67% | **lower on all three** |
| Leave-one-out Sharpe band | 1.00–1.15 | 1.02–1.16 | unchanged in width |
| Cost ladder Sharpe @ 6 bps | 1.02 | 1.03 | +0.01 |
| Walk-forward positive folds | 10/10 | 10/10 | unchanged |
| Projection 10y median | +83.24% | +81.78% | −1.46pp |

**Read the first and last rows together.** Over the full 9.6-year sample the
deletion *reduced* total return (−0.89pp) and left Sharpe unchanged to three
decimals; the projection median fell with it. Every apparent gain sits in the
2-year and 5-year windows — exactly the 2024–2026 stretch where TLT's whipsaw
losses were concentrated. That is the signature of removing a known past loss,
not of a structural improvement, and the Monte Carlo percentiles moved *down*
on all three dimensions: the observed result is now less exceptional relative
to what its own return process produces by chance. Over the decade, the
diversification TLT supplied roughly paid for its own trading losses.

The change reproduces the prior campaign's `without TLT` leave-one-out row to
every digit (+77.08% / +6.15% / 5.41% / 1.14 / −7.89%, 3,110 fills), which
independently verifies that the edit did one thing and nothing else — and is
also a reminder that the stress battery had already priced this decision
before it was taken.

`without IEF` now costs more than it did with TLT present (+72.35% / 1.06
versus +73.23% / 1.05), so the remaining rates sleeve is still earning its
place and no further deletion follows from this one.

Unchanged by this edit: v6 is research only, not promoted; the holdout stays
locked; and none of the above is out-of-sample evidence.

## Sensitivities

### `amat_managed_v2_no_kelly.toml` — `71fcbdbb8533b266` — amat-managed-no-kelly

**Status: sensitivity only. Never a candidate.**

`amat_managed_v2.toml` with `use_kelly = false`, generated to test the
hypothesis that the Kelly cap was the dominant remaining constraint after the
three fixes.

**The hypothesis was falsified.** Removing Kelly gave −3.84% total, 1.66%
volatility, Sharpe −0.25, and a year gate failing at 55.0%. Kelly's median
applied fraction is exactly 0.00000 — it zeroes the book in over half of all
sessions — so removing it made things worse, not better.

Recorded because the falsification is the result. An earlier attribution run
over a 1,006-session sub-window (half of which was regime warm-up) had made
Kelly look dominant; it did not transfer to the full sample. Attribution
measured on a sub-window is not attribution.

---

## Known defects affecting every result in this file

### Corporate actions were ingested and never applied — FIXED 2026-08-10

**Found.** `actions_by_session` appeared in `src/` in exactly one file: declared
as a parameter at `src/amat/backtest/runner.py:195`, defaulted away at `:239`
(`actions_by_session = actions_by_session or {}`), and consumed at `:270`. **No
caller anywhere passed it**, and no loader for `actions.parquet` existed.

So every number recorded above this line was produced by marking positions at raw
closes with no cash credit for any dividend — 436 ex-dates in the v1 universe
over the sample, 1,500 across the whole snapshot.
`src/amat/domain/actions.py` existed, was tested, and carried the correct
`cash_credit` semantics; it was simply never reached from a backtest.

**Direction of the error, now settled.** Features use *adjusted* closes; valuation
uses *raw* closes. On an ex-date the raw close falls by the distribution, and with
no offsetting credit the ledger booked that fall as a market loss. The strategy
therefore *paid* for every dividend it received, and every figure understates the
result. This was compounded by the benchmark: the SPY comparison was measured on
*adjusted* closes, so a **total-return benchmark was set against a price-return
strategy** and the gap read as a shortfall in skill. Both sides are total return
from this date.

**Fixed.** `amat.data.snapshot.load_corporate_actions` reads the partition and
`amat study` passes it to every combined and sleeve run. Three vendor hazards are
resolved in the loader rather than left to chance:

| Hazard | Instance in the shipped snapshot | Treatment |
|---|---|---|
| Exact duplicate row | QQQ 2022-09-19, `0.51856` twice | Collapsed to one — the neighbouring quarters are single rows, so this is one payment reported twice |
| Conflicting rates on one ex-date | SHY 2021-12-01, `0.018007` and `0.031906` | **Fails closed.** Summing invents income, picking one discards it, and the partition does not say which is right |
| Ex-date off the session axis | none present | **Fails closed** — an action the loop never reaches could not be applied, and dropping it would lose the entitlement silently |

Rates convert through `Decimal(str(value))`, not `Decimal(float)`, so the stored
figure is the rate the vendor quoted rather than a 50-digit binary expansion.

**The split that did not bite.** USO reverse-split 1-for-8 on 2020-04-29 — inside
the frozen universe and inside the sample. An unapplied split would have booked a
phantom 8× gain on any held position as market P&L. The strategy was flat USO
that session, so no published result was corrupted. That is luck, not design: USO
is held on 1,112 of 2,664 sessions. The other three splits (EWJ, XLE, XLK) are
outside the v1 universe.

Tests: `tests/unit/test_corporate_actions_reach_the_ledger.py`, 15 cases covering
the loader's ambiguity handling, end-to-end arrival at the accountant, and the
split-rescale defect below.

#### A defect the fix created: in-flight instructions across a split

Applying corporate actions introduced a bug that could not exist before, because
holdings and instructions were previously both denominated in unadjusted shares
and agreed with each other by being equally wrong.

An instruction is an **absolute share count** fixed at the decision session and
filled one or more sessions later. The accountant applies actions to opening
positions *before* fills. So a split landing between decision and execution
leaves the instruction denominated in shares that no longer exist. Measured on
the live accountant, 1,000 shares with a pending full exit:

| Direction | Ratio | Result |
|---|---|---|
| Reverse split | 0.125 | `AccountingError: sale of 1000 exceeds held 125` — a crash |
| Forward split | 2 | **No error.** Sells half; 1,000 shares still held after an intended exit |

The forward case is the dangerous one: silent, and it propagates into every
downstream statistic. `run_backtest` now restates every pending order and missed
fill by the split ratio before settling, emits `TradeReason.CORPORATE_ACTION`
on the restated instruction — its first legitimate producer — and records a run
warning naming the symbol and ratio. Notional is preserved exactly, since
quantity scales by the ratio while the window price scales by its inverse.

#### A control that is weaker than it was documented to be

Three separate docstrings asserted that a missing corporate action would raise
`AccountingError`. It does not, and the false claim is part of why the input went
unsupplied for two years. `market_pnl` is *computed* as
`closing value − opening value − traded notional`, so it absorbs any mark
movement by construction: the reconciliation identity proves that cash and shares
are accounted for, never that a price step had a cause.

Nor does the snapshot gate cover it. `_check_daily_quality` measures
discontinuity on the **adjusted** series, and a split leaves the adjusted series
smooth — the jump is in the **raw** series that valuation uses. **A vendor that
omits a split passes every gate in this repository.** The system detects a
*mislabelled* corporate action and does not detect a *missing* one; vendor
completeness is the control. Corrected in `backtest/accounting.py`,
`boundaries.py`, and as a dated correction block in `docs/audit-2026-08-08.md`.

#### Live consequence: the 29-symbol study cannot run — RESOLVED 2026-08-20

The fail-closed policy on ambiguous distributions is correct and it currently
blocks one workflow. Verified:

```
load_corporate_actions(snapshot, <29 v2 symbols>, sessions=panel.sessions)
→ DataIntegrityError: ... SHY 2021-12-01 cash_dividend=[0.018007, 0.031906]
```

The v1 12-symbol universe excludes SHY and is unaffected. Any study over the
rejected v2 configuration now raises. **Do not resolve this by relaxing the
loader to sum.** The resolutions are a re-ingested snapshot with the conflict
corrected at source, or a documented per-record exception ledger that states
which reading was chosen and why.

**Resolved 2026-08-20 by the second remedy.** `configs/data/action-exceptions.toml`
and `amat.data.action_exceptions` record per-record decisions with their
evidence. The ledger does not relax the check: entries are scoped to one
`snapshot_id` (a re-ingest inherits nothing), are matched against the values the
partition actually holds (a stale entry fails loudly rather than applying to
different data), and there is no wildcard or per-symbol policy — every conflict
needs its own entry, or it still fails closed. The reading chosen for SHY is
0.018007; the evidence is in the ledger and summarised in experiment 0006.

The same file now also carries `[[price_discontinuity]]` entries, which confirm
a specific adjusted-price bar against the ingest's 60% jump gate. That gate was
calibrated on 30 broad, liquid ETFs and produces false positives on a wider
universe: GDXJ's +62.7% on 2020-03-17 is a real sector-wide move (GDX +50.5%,
SIL +44.2% the same session; raw and adjusted agree; no split recorded), not a
missing corporate action. A survey of all 92 wide-universe symbols over
2016-01-04..2026-08-10 found it to be the *only* unexplained discontinuity in
the sample. Confirmations are per-bar and snapshot-scoped; the threshold itself
is unchanged.

### Turnover is promised and never reported

`src/amat/metrics/__init__.py` names turnover in its own module docstring, and no
artifact reports it. An external estimate during the 2026-08-10 execution-window
investigation put it near 18× equity per year one-way, which if right means every
1 bps of cost-model error is roughly 18 bps/yr of return — a far larger lever
than where in the session the fills happen. Unverified; it needs measuring, not
citing.

## Known weakness: the config digest hashes comments

`content_sha256` is computed over the configuration file's raw text, so a
comment edit changes it. That means:

- the digest cannot distinguish "the strategy changed" from "someone fixed a
  typo"; and
- a config cannot be annotated without orphaning every result that cited it.

Demonstrated 2026-08-10 and reverted. The fix is to hash the *parsed and
normalized* configuration rather than the file bytes — but that would change
every digest in the repository at once, invalidating all recorded provenance,
so it is a deliberate migration and not a casual edit. Until then, treat
strategy config files as immutable evidence and record decisions here.

---

## Not yet run

Listed so their absence is not mistaken for a pass. From
`promotion-gates.md` and `validation-protocol.md`.

Completed on 2026-08-13/14 (see the v1 entry and "Registered experiments"
above; none of it is out-of-sample evidence, because the sample is burned):

- ~~expanding walk-forward evaluation~~ — run; 9/10 positive calendar folds;
- ~~≥60% positive annual folds~~ — measured at 90%, consistency only;
- ~~results by major historical market regime~~ — causal SPY trend/vol slices;
- ~~Monte Carlo / bootstrap robustness~~ — 200 stationary-bootstrap paths
  2026-08-14; observed run at the 60th/70th/84th percentile of its own
  resampled return/Sharpe/drawdown distributions;
- ~~explicit exit rules (stop-loss/take-profit)~~ — experiment 0003, run
  2026-08-14, falsified exactly as pre-registered; the no-exit core stands;
- the locked holdout — **declared 2026-08-13, never opened** (that is the
  correct state until post-declaration data accrues);
- nearby-parameter stability — **partial**: the volatility-target
  neighbourhood is measured (a smooth plateau; experiment 0001); horizons,
  deadband, covariance lookback, and rebalance thresholds remain unmeasured.

Completed 2026-08-15 — the entire stress list, in one recorded battery per
strategy (`amat stress`; v1 record `stress-amat-etf-20260815T063809.490261Z`,
v6 record `stress-amat-etf-regime-quality-20260815T063653.149460Z`; details
in the v1 entry's "Stress battery" subsection):

- ~~2×, 3×, 5× cost stresses on the real snapshot~~ — survives 5×;
- ~~one- and two-session execution delays~~ — lag 2/3, immaterial (required
  the in-flight netting engine fix, disclosed in README);
- ~~deterministic 5% and 10% missed fills~~ — immaterial;
- ~~next-open and next-close execution alternatives~~ — session-close fills
  run; a true next-open needs auction prints the snapshot does not carry
  (the window VWAP is the open proxy the baseline already uses), recorded
  as such rather than fabricated;
- ~~top-five and top-ten trade removal~~ — 22.6% / 33.8% of net profit;
- ~~best-symbol and best-year removal~~ — full leave-one-out (12 re-runs)
  and best-year recompounding, both survivable;
- ~~signal-free allocator benchmark~~ — run; the signal's contribution is
  drawdown control, not raw return;
- ~~correlation of each engine's returns to SPY and to 60/40~~ — +0.36 /
  +0.39 (v1), +0.33 / +0.37 (v6);
- ~~nearby-parameter stability (horizons, deadband, covariance lookback,
  rebalance threshold)~~ — measured 2026-08-15 (experiment 0005 + unranked
  sensitivities): a plateau.

Still not run — the items only new machinery or calendar time can produce:

- engine parity (the NautilusTrader event-driven adapter does not exist);
- the locked holdout — declared, never opened; genuinely out-of-sample
  evidence accrues only as post-2026-08-10 data arrives;
- the v1/v6 paper periods (`amat simulate` journals exist; observations
  must accumulate).
