# Signal research roadmap: what to build, what not to, and why

Written 2026-08-20, after exploring this repository, reading its experiment
registry, and researching the specific techniques proposed. It evaluates five
candidate directions — vendor signal services, a wide-universe scanner with our
own indicators, a news/sentiment channel, genetic-programming alpha search, and
AlphaZero-style deep reinforcement learning — and states plainly which of them
this system's data can support.

Nothing here is a profitability claim. The one direction that was built and
measured (§2) was **falsified** across both of its registered universes — as its
pre-registration predicted, though not for the reason it predicted.

---

## 0. The constraint that decides everything

Every proposal below is really a bet about *sample size*, and this repository's
sample is fixed and small:

| Resource | Amount |
|---|---|
| Sessions | 2,665 (2016-01-04 → 2026-08-10) |
| Symbols traded | 11 (v6) or 12 (v1); 30 available, 92 ingested 2026-08-20 |
| Round trips, whole history | ~860 |
| Genuinely out-of-sample sessions | **~7** (holdout declared 2026-08-13) |
| Point-in-time news history | **9 days** (collection began 2026-08-11) |

All history at or before 2026-08-10 is **burned** — inspected by the v1–v6
full-sample studies before the holdout was declared. It can produce diagnostics
and falsifications. It cannot produce evidence.

Against that, the repository's track record is the single most useful prior
available:

| Mechanism | Outcome |
|---|---|
| v3 news scalars | No independent result; store has no history |
| v4 breadth blend | **Rejected** |
| v5 universe change | **Rejected** |
| 0002 volatility-state conditioning | Survived by 0.0012 Sharpe; not pursued |
| 0003 stop-loss / take-profit overlays | **Falsified** |
| 0005 rebalance-band sensitivity | **Falsified** |
| v6 regime + liquidity overlay | **Lost to its static control** (1.04 vs 1.14) |
| v6 per-asset offline RL bandit | **0 of 12 rules activated** |
| 0006 cross-sectional selection (this doc, §2) | **Falsified** (both universes) |
| 0007 selection inside the frozen universe | **Falsified** — cleared every control, margin inside noise |

Ten mechanisms, zero promotions. That is not a broken research process; it is
a working one. The useful question is therefore never "what else could we add"
but **"what would add information rather than parameters."**

Two directions add information: more assets, and more time. Everything else on
the list adds model capacity to a fixed sample.

---

## 1. Vendor signal services — do not buy

The specific service raised was **Pro Ticker Signals**. What it actually is: a
consumer mobile app (Beyond Media Group, ~130k downloads) that pushes daily
trade ideas before 09:00 ET. It has no public API, and its own user reviews
report alerts arriving late or after the move.

It fails this system's entry requirements on three independent counts, any one
of which is disqualifying:

1. **No point-in-time archive.** We cannot ask "what did this service say on
   2019-04-12". Without that, it can never be backtested — only forward-tested,
   at months per experiment.
2. **No API.** A trading loop cannot consume push notifications.
3. **Unverifiable construction.** A signal whose derivation is unknown cannot be
   distinguished from one fitted to the period it is marketed on.

The generalisation matters more than the specific product: **a signal you cannot
reconstruct point-in-time is not a research input, whatever its accuracy.**

Better sources exist, and this repository already identified them in
`docs/research/news-pipeline.md`:

| Source | Why it qualifies |
|---|---|
| **FRED / ALFRED vintages** | ALFRED serves each series *as it stood on a past date*. This is genuine point-in-time macro — the one thing news archives cannot offer. Free key; the host is already on the allowlist. |
| **SEC EDGAR** | `acceptanceDateTime` is a vendor-side timestamp that is trustworthy *historically* by construction. No key. |
| **Alpaca News** | Uses credentials already held, on the host `amat.data.alpaca` already pins. ~50 lines on the existing client base. |

Recommended order: Alpaca News (zero new credentials), then ALFRED (real
point-in-time macro), then EDGAR if filing events become a feature family.

---

## 2. Own indicators across a wide universe — BUILT, and falsified

This was the direction chosen, built, and measured. It is documented in full in
`docs/research/experiment-registry.md` under "0006"; the summary:

**What was built**

- `amat scan` — a point-in-time, multi-timeframe asset scanner. It ranks every
  symbol by the strategy's *own* 21/63/252-session volatility-normalized trend
  and shows the per-horizon breakdown behind each rank. It computes from the
  panel prefix only, so a scan dated years ago is what the strategy actually
  saw then. It shares the allocator's feature code, so it cannot drift from the
  strategy it describes.
- `amat.portfolio.cross_section` — top-K selection and a horizon-agreement
  filter, off by default, applied after the dead-band and before any sizing.
- `configs/strategy/amat_v7_{wide,xs}30.toml` and `amat_v7_{wide,xs}81.toml` —
  the v1 core over the 30- and 81-symbol universes, with and without selection.
- `alpaca-sip-wide92-2026-08-20` — a new 92-symbol snapshot, 2,665 sessions,
  window coverage 0.9866.

**What it found**

Width degrades monotonically. Validation Sharpe: **1.194** at the frozen 12
symbols, **1.110** at 30, **0.788** at 81 — the last at 3.6x the turnover
(13,066 fills against v1's 3,643). The wider the universe, the worse the frozen
core performs on it.

The selection result *inverts between the two universes*, and the inversion is
the finding. At 30 symbols the training winner was K=15 of ~24 eligible — barely
a selection — and it lost its random-K control, beating 5 of 20 seeds. At 81
symbols the winner is K=5 of ~50 eligible, a genuinely selective rule, and it
beats **18 of 20** random seeds (0.964 against a random median of 0.562, spread
-0.032 to 0.967).

So **cross-sectional ranking does carry real information**; the 30-symbol arm
was not selective enough to reveal it. Its zero-cost diagnostic said the same
thing more quietly — ranking beat the random median gross (1.098 vs 1.062,
14/20 seeds) and lost net.

**It is still not a win.** The registered rules required beating the baseline,
the hold-everything arm, *and* the random control. The 81-symbol candidate
passes the second (0.964 vs 0.788 — selection genuinely rescues an over-wide
universe from its own turnover) and the third decisively. It fails the first:
0.964 against 1.194.

**The generalisable lesson.** Ranking carries information, and it is *less
information than widening destroys*. Among these ETFs the strongest trends are
repeatedly the same factor wearing different tickers — the 30-symbol top four on
2026-08-10 were XLV, XLF, XLE, VTI, three US sectors and a total-market fund.
Ranking correlated assets reshuffles one beta; it does not diversify.

The 81-symbol arm was registered as 92 and run as 81 under two mechanical
data-quality exclusions (eight symbols with unresolvable ambiguous year-end
distributions, three that lost coverage to the raw/adjusted inner join). Both
rules read data quality and never returns, but they were applied after seeing
which symbols failed, and the excluded set is mostly bond funds — which
*flatters* the wide arm. That makes a failure more convincing and a success
unsafe to rely on.

**0007 closed the follow-up question.** Does selection help *inside* the frozen
universe, where no widening cost is paid? Registered separately with the prior
"fails". Both training winners (K=6) nominally beat their baselines at ~18% less
turnover — 1.246 vs 1.194 for v1, 1.345 vs 1.323 for v6 — and cleared both a
random-K control and a static-mean control added because selection cuts gross
exposure (v1 68.2% -> 62.6%). But a 4,000-resample paired bootstrap over the
902-session validation window puts the difference against the static-mean
control at +0.037 and +0.018 with 95% CIs of [-0.206, +0.286] and
[-0.204, +0.255]. Neither excludes zero. Falsified on the margin, as 0002 was.

**The mechanism, measured across all four universes.** Holding selectivity fixed
at K = 30% of median eligible, ranking beats random selection in *every*
universe (19, 17, 17, 13 of 20 against 10 by chance) and *every* resulting
Sharpe is below its own baseline (0.894 vs 1.194; 1.086 vs 1.323; 0.892 vs
1.110; 0.688 vs 0.788). The redundancy hypothesis is itself falsified -- the
correlation between universe redundancy and beat-rate is **-0.821**, the
opposite sign to the prediction.

> The trend ranking carries real information. Concentration destroys more,
> through lost diversification, than that information recovers.

**What survives.** The scanner itself is genuinely useful as a research and
operations instrument even though the selection rule it enables is falsified —
it answers "what does the strategy see, and why" for any date in history. It
just should not be wired to capital.

---

## 3. News and sentiment — build the intake, do not model yet

The pipeline already exists (`src/newsdesk/`), is audited, and is well
constructed: append-only SQLite, knowability filtered on `ingested_at` (our
clock) rather than `published_at` (revisable), a circuit breaker on vendor
quota, and hard import isolation from `amat`.

Its blocker is not the model. It is that **collection began 2026-08-11**, so
there are ~9 days of point-in-time history and the store cannot honestly be
backfilled. Any news-conditioned backtest over 2016–2026 reads zero knowable
articles at every decision point — which is the correct behaviour, not a gap.

On model choice, when the data eventually exists: **FinBERT is no longer the
right default.** It is a 2019 BERT fine-tune on ~4,000 labelled samples, with
documented insensitivity to numerical values and degradation on complex
sentences. Current work (FinDPO, FinLlama, instruction-tuned Llama/Qwen/DeepSeek
variants) outperforms it on both classification and downstream return studies.
Choose at build time, not now.

**Recommendation: start the clock, do not model.** Add the Alpaca news client
(free, credentials held, ~50 lines) so per-symbol coverage accrues from today.
Revisit sentiment features when there are ≥12 months of forward-collected
history. Anything sooner is a sensitivity dressed as a result.

---

## 4. Genetic programming / symbolic alpha search — highest risk on this data

GP evolves expression trees over OHLCV and keeps what correlates with forward
returns. Its failure mode is exactly this repository's central concern:
**selection under multiple testing.** Searching thousands of expressions against
~860 round trips of burned history will find beautiful formulas that mean
nothing; the maximum Sharpe over N pure-noise candidates is inflated by
construction.

This is not a reason it can never be run. It is a reason it can only be run
*bounded*:

1. Pre-register the trial budget. The count enters the verdict, not a footnote.
2. Apply the **Deflated Sharpe Ratio** (Bailey & López de Prado, 2014), which
   corrects a Sharpe for selection bias, sample length, non-normality, and the
   number of trials.
3. Constrain the grammar hard. Fewer operators and shallower trees mean a
   smaller hypothesis space and a smaller deflation penalty.
4. Expect falsification, and register that expectation.

**Recommendation: defer.** Not because it is unsound, but because it consumes
the scarcest resource here — untouched data — faster than any other technique,
and this sample has none to spend. Revisit when genuine out-of-sample history
has accrued.

---

## 5. AlphaZero-style deep RL — do not build

AlphaZero works because of four properties, and markets have none of them:

| AlphaZero needs | Markets provide |
|---|---|
| A perfect simulator of the environment | No simulator. A market model is the thing being learned. |
| Unlimited self-play data | 2,665 sessions, fixed. You cannot self-play a market. |
| Known, deterministic, stationary rules | Unknown, stochastic, and non-stationary — regimes shift, correlations move, edges decay. |
| A clean terminal reward (win/lose/draw) | A noisy, low-signal-to-noise continuous reward. |

The critical one is the second. AlphaZero's strength is generating its own
training data by playing itself; the resulting positions are *real* positions in
the target domain. The equivalent in trading is training against generated
markets — which teaches the agent to trade *your generator*, and the generator
is the very model you lack. This is why published deep-RL trading results
overwhelmingly report strong in-sample performance and weak generalisation.

This repository has already reasoned to the same conclusion independently. From
`docs/research/v6-design-and-latest-audit.md`, after its per-asset bandit returned zero
activated rules:

> "Deep RL is not justified by 20–60 independent training episodes per asset. It
> would add policy capacity and thousands of comparisons without adding data."

That is correct and the research supports it. The defensible RL formulation for
this problem — an offline contextual bandit over a small discrete action set,
with shrinkage and a multiplicity-adjusted confidence bound — **has already been
built and run**, and every one of its 12 lower confidence bounds landed at or
below zero. That is the fail-safe working, not a missing feature.

**Recommendation: do not build.** If the appetite is for more model capacity,
the honest sequencing is: accrue out-of-sample data first, then revisit.

---

## 6. Recommended sequence

Ordered by information added per unit of risk.

1. **Let the paper deployment run.** It is live now. Every session it trades is
   genuine out-of-sample evidence against a declared holdout — the only kind
   this system does not have. Nothing else on this list produces that.
2. **Start news collection.** Add the Alpaca news client so coverage accrues
   from today. Cost: a day. Payoff: makes §3 possible in a year.
3. **Add ALFRED macro vintages.** Real point-in-time macro, free, and a
   genuinely different feature family from price trend.
4. **Stop testing selection rules.** 0006 and 0007 tested four universes and
   twenty candidates between them. The ranking beats chance everywhere and beats
   the baseline nowhere. That question is answered; further variants would spend
   trials on a settled result.
5. **Keep the scanner, not the selector.** `amat scan` is a good instrument.
   Wiring it to capital was tested and falsified.
6. **Defer GP and deep RL** until out-of-sample history exists to spend.

The uncomfortable summary: this strategy's most valuable next input is not a
model, a signal vendor, or a search technique. It is **time**.
