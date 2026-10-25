# Genetic pattern research for AMAT

Prepared 2026-09-27. This document preserves the researched design and full
roadmap. The first implementation milestone is now available; see
[implementation-status.md](implementation-status.md) for its exact scope and
commands. P2 portfolio integration is now implemented; see
[portfolio-replay.md](portfolio-replay.md). Profitability validation and later
research phases remain separate from implementation acceptance.

The useful product is a library of small, causal, reproducible patterns with
evidence of incremental value. Start by improving the existing ETF strategy's
features and entry decisions. A beautiful pattern or a profitable standalone
backtest is insufficient: the candidate must improve AMAT after costs and survive
its existing controls.

## Source coverage and implications

Both complete English automatic-caption transcripts were retrieved and read,
not just the linked timestamps. They and video metadata are stored locally under
`amat/data/research/genetic-patterns-2026-09-27/sources/`. Captions can mistranscribe
numbers and do not expose every chart or formula displayed on screen. This is a
transcript-and-code review, not a frame-by-frame visual review. Do not claim exact
replication of chart results or undocumented parameters.

### Video 1: candlestick evolution

[Original video](https://www.youtube.com/watch?v=2XQ3PsZActM).

| Segment | Relevant idea | AMAT decision |
|---|---|---|
| 00:29–01:27 | Conjunctions of lagged OHLC comparisons; remove duplicate/trivial rules | Explicit candle genome with canonicalization and satisfiability checks |
| 01:44–05:07 | Return, profit factor, Martin fitness; occurrence floor | Replication diagnostics; use AMAT's incremental objective for integration |
| 05:07–08:35 | Elitism, fitness-weighted parents, crossover, two mutation modes, stopping | Bounded, seeded, resumable GA; record all trials |
| 08:35–10:39 | Hourly BTC; three comparisons across recent candles; sequentially diversify patterns by return correlation; OR ensemble | Reproduce separately; measure incremental portfolio benefit and match overlap |
| 10:39–11:53 | Re-run discovery on 100 permuted datasets | Null must include the whole search and ensemble selection |
| 11:53–13:12 | Annual walk-forward, long/short conflict cancellation, ETH comparison | Temporal evaluation and transfer checks; no new AMAT short authority |
| 13:12–end | Magnitude may add information; weak edge; omitted fees/slippage | Add normalized candle differences later; costs are mandatory |

The video describes weak theoretical performance and declines live deployment.
Its fee discussion is historical, not a current fee assumption. No linked source
repository appears in the retrieved description.

### Video 2: symbolic indicator evolution

[Original video](https://www.youtube.com/watch?v=7tbKgl1yep8),
[linked repository](https://github.com/ZiadFrancis/Genetics_Trading_Part_1), inspected
commit `28ea98fca96325363e696f37eb820c78b7854d24`.

| Segment | Relevant idea | AMAT decision |
|---|---|---|
| 00:00–05:06 | Evolve expression trees; expensive training, cheap inference; multiple assets or technical indicators | Typed numerical trees over causal normalized features |
| 05:06–06:35 | Four FX pairs, five-minute OHLC; train/validation/test separation | Reuse ETF panels first; make external inputs and availability explicit |
| 06:35–08:32 | Arithmetic primitives; desired exposure; turnover deadband | Bounded feature output; portfolio layer retains sizing authority |
| 08:32–10:04 | Vectorized versus event-driven testing; exponential return fitness | Fast screening followed by authoritative AMAT replay |
| 10:04–12:56 | Tournament selection, crossover/mutation, hall of fame, validation winner | Deterministic search state and validation-access budget |
| 12:56–end | Persisted model inference; failed training score despite attractive test result; possible stop/target variants | Persist inert JSON; failed candidates stay failed; exits are a separate experiment |

Narration, demonstration settings, and repository defaults differ. The two
mutation/crossover probabilities are independent opportunities, not probabilities
that need normalization. A hall of fame archives winners; it does not itself
guarantee elitist survival in the next population.

### Code audit beyond the demonstrations

The inspected script uses a return-only fitness, forward-fills resampled OHLC,
and enables `trade_on_close=True`. It accepts raw multi-currency prices as
arithmetic inputs and initializes opposing orders. These choices require independent
accounting and timing review; none should become an AMAT default. The vectorized
and event-driven variants need explicit fill/position/equity parity tests before
their results can be considered interchangeable. The repo has no CSV data or
environment lockfile in its inspected tree. Do not deserialize downloaded `dill`
models or use their reported performance as evidence for AMAT.

## Where this fits in the actual repository

| Current component | Existing capability / limitation | Proposed change |
|---|---|---|
| `src/autoresearch/specs.py` | `PatternSpec`: 1–4 threshold/temporal conditions, at most 3 patterns; terms capped at 4 | Preserve old contracts; add a versioned discriminated genome model and an explicit new program version |
| `src/autoresearch/patterns.py` | Causal Boolean patterns with unavailable propagation | Keep compatibility adapter; implement new evaluator alongside it |
| `src/autoresearch/registry.py` | Versioned features, transforms, availability metadata | Candle primitives, declared dependencies, lookback accounting |
| `src/autoresearch/signals.py` | Weighted/ridge signals and pattern design columns | Add pattern columns; fit all transforms on the correct fold only |
| `src/autoresearch/search.py` | Proposal/refine/combine/ablate/policy search | Bounded evolutionary proposal source, with a separate full genome ledger |
| `src/autoresearch/objective.py` | Exposure-matched incremental Sharpe, complexity penalty and quality gates | Count genome complexity; preserve objective/reference/cost semantics |
| `src/autoresearch/validation_contract.py` | Calendar checks and finite statistical protocol | Register whole searches; track every adaptive validation exposure |
| `src/autoresearch/ledger.py`, `worker.py`, `loop.py` | Experiment execution and provenance | Cache keys, resumable RNG state, genome ancestry, explicit failure categories |
| `src/autoresearch/promotion.py` | Promotion artifacts and gates | Add evaluator/schema/data hashes and reproduction evidence |
| `src/amat/backtest/panel.py`, `data/snapshot.py` | Adjusted HLC present; raw open in snapshot but no adjusted open in panel | Add optional adjusted open from same row adjustment factor; fail closed when unavailable |
| `src/amat/patterns.py` | Separate session-leg pattern scanner | Use as a descriptive control, not as proof for adaptively mined patterns |
| `src/autoresearch/intraday/` | Separate intraday contracts/data/evaluation | Later adapter; bar-close availability and market-session conventions required |
| `src/amat/planning/`, executor | Planning versus paper execution boundary | Only frozen approved evaluation artifacts may affect plans; no GP or training in executor |

Important compatibility constraint: `SignalSpec` currently rejects router signals
combined with patterns/interactions. Do not remove that check casually. Initial
GP candidates use supported weighted/ridge compositions. Router integration is a
separate contract change with ablation tests. Existing frozen research workspaces
must retain their original evaluator, hash and schema semantics.

## Concrete patternSchema

The sibling [patternSchema.json](../../../amat/docs/genetic-patterns/patternSchema.json) is JSON Schema Draft 2020-12.
It defines `patternSchema/0.1.0`, with two strict genome variants:

1. `candle_conjunction`: `maxLag` and 1–8 comparisons of `{field, lag}` operands.
   A lag of zero is the latest **completed** candle. `maxLag=2` allows exactly
   three candle positions, avoiding an ambiguous candle-count convention.
2. `symbolic_indicator`: a recursive numerical expression tree. Terminals are
   constants or named causal features; operations are add/subtract/multiply,
   safe division, min/max, negation, absolute value and tanh. Inputs are
   dimensionless after a declared rolling transform; output is tanh-bounded.

Both include timeframe, price basis, information availability, missing policy,
and a downstream binding. They contain neither Python source nor executable
serialization. [Candle example](../../../amat/docs/genetic-patterns/candle.example.json) represents
`open[t] > low[t-1] AND close[t] < high[t-1]`; it is an illustrative grammar
example, not a validated entry rule. [Symbolic example](../../../amat/docs/genetic-patterns/symbolic.example.json)
combines trend efficiency, participation, and volatility shock, also unvalidated.

The actual runtime must implement additional semantics that JSON Schema cannot
fully enforce:

- Reject NaN/infinity at decoding. Require every lag to be within the declared
  bound. Reject direct self-comparisons, duplicates, impossible inequalities,
  and tautologies implied by OHLC ordering; detect contradiction cycles.
- Canonicalize commutative operations and conjunction order, preserving floating
  semantics. Keep genotype hashing separate from evidence and human labels.
  Deduplicate training match/output vectors as well as syntax. Do not use
  validation outcomes for behavioral deduplication.
- Candle comparisons use the same instrument and adjustment basis. Derive open,
  high, and low using `adjusted_close/raw_close` consistently with the existing
  loader. Test splits, distributions and point-in-time adjustment invariance.
  Raw prices remain valuation/execution inputs, not mixed feature operands.
- Initially cap expression depth at 4 and total nodes at 31. Recheck both after
  crossover and mutation. Every operator has fixed arity, units and causality.
- Feature leaves resolve through a frozen registry version. `lookback` maps to
  the named feature's primary lookback. For v0.1, freeze secondary lookback at 5,
  smoothing at 1, normalization window at 63, clipping at 3, and benchmark SPY.
  Reject combinations violating existing feature validators (e.g. shock with
  primary lookback 5). Registry must define the four proposed candle features
  before accepting them; ATR uses the same lookback and adjusted OHLC. These
  defaults are part of the new registry hash, not inherited implicitly.
- `source=self` means the evaluated asset; any named symbol must be an authorized
  frozen panel member, aligned by information availability. Missing external
  inputs are unavailable. No backfill or latest-value lookup across a decision.
- `safe_div(a,b)` is unavailable when `abs(b)<1e-8`; no artificial profitable
  fallback. All other nonfinite results are unavailable. Flat rank inputs yield
  midpoint rank; zero-variance z-scores are unavailable. Cap intermediate finite
  magnitudes at a documented registry bound, initially 1e6, and record clipping.
- Missing propagates strictly, including through future Boolean OR ensembles.
  Unavailable does not mean a confirmed non-match, nor a zero training return.
  Abstention behavior is explicit and consistently replayed.
- A Boolean feature produces 0/1; a numerical feature produces [-1,1]. For a
  `feature` binding, transform/center the column explicitly and cap its score
  contribution by `maxEffect`; the example uses 0.25. Threshold is used only by
  filter/attenuator bindings. Filters require Boolean true or numeric value
  above threshold; a risk warning attenuates baseline target exposure by at most
  `maxEffect`. Missing filter/attenuator evidence uses the frozen baseline and
  records abstention. Phase 1 only enables `feature`; the other roles are staged.
- No pattern chooses order quantity, leverage, universe membership, or broker
  action. Thresholds, sign, ensemble logic, holding periods and role selection
  are search degrees of freedom and belong in the trial ledger.

Separate the immutable genome from a `PatternArtifact` evidence envelope:
`genome_sha256`, evaluator/registry/code versions, dataset manifest hashes,
research protocol hash, trial ID, seed/RNG state, generation, parent hashes,
mutation operator, fold fit states, costs, per-fold metrics, errors, and status.
Statuses are proposed/evaluated/rejected/validated/shadow/retired. Promotion must
resolve an artifact by hash, never by a mutable filename such as “best”.

## What would be useful first

The first question is whether candle information adds value to AMAT's trend and
participation features. Train a small candle family and compare **baseline +
pattern** to the exact baseline. Follow with symbolic features only after the
candle evaluator is trustworthy. Then test an entry filter or a bounded risk
attenuator as an independently registered experiment. Holding policy, universe,
costs and risk caps remain frozen within each comparison.

The report should show the human-readable formula, a few training-only matched
candles, matched and unmatched event counts, forward-return distributions,
incremental net performance, turnover, drawdown duration, asset/year breakdown,
missing-data abstentions, and ablations. Persist failed patterns so the agent
can explain why similar ideas failed without selecting on fresh holdout outcomes.

## Data acquired and available

New, provider-checksummed downloads: 144 monthly Binance spot archives, BTCUSDT
and ETHUSDT hourly, January 2018 through December 2023 inclusive. They total
5,972,954 compressed bytes. Each merged file has 52,464 rows; archive files and
checksums are retained. These are method-replication inputs, not an expansion of
AMAT's ETF universe. Data are from [Binance's public archive](https://github.com/binance/binance-public-data).

Local paths under `amat/data/research/genetic-patterns-2026-09-27/`:

- `manifest.json`: retrieval time, URLs, individual archive hashes, normalized
  CSV hashes, row counts and exact anomaly timestamps.
- `BTCUSDT-1h-2018-2023.csv`, `ETHUSDT-1h-2018-2023.csv`.
- `raw/`: original ZIP and CHECKSUM files.
- `sources/`: both captions/metadata and three inspected reference Python files.

Each asset has 163 absent expected UTC hourly grid positions, 43 off-grid opens,
13 irregular close timestamps, four zero-volume rows, and no duplicate opening
timestamps or invalid OHLCV relationships. These counts may overlap. In fact,
52,584 expected hours - 163 missing + 43 off-grid = 52,464 rows. A provider
checksum establishes faithful download, not research readiness. Retain anomalies
in raw data; build a regular grid with explicit unavailable masks. Exclude every
feature or label window touching invalid bars, and preserve the accounting
calendar. Do not compress gaps into adjacent observations or forward-fill prices.
Short-side spot P&L is theoretical without a borrow/funding model.

Existing primary AMAT data: `data/snapshots/alpaca-sip-core-2026-09-14-verified/`
contains 80,640 daily rows over 2,688 sessions and 30 ETFs, 2016-01-04 through
2026-09-11. Raw OHLCV and adjusted close are present with no null cells in the
daily table. Partition hashes were checked against `research-datasets.json`.
The existing registry also reports 3,560,781 minute rows for SPY/QQQ/IWM/GLD;
that entire minute archive was not re-audited for this plan. Read its base and
extension manifests and calendar coverage before intraday experiments.

The second video's exact four FX CSVs are absent from its repository. They are
not necessary for the proposed ETF integration and were not downloaded. If exact
FX replication becomes a separate objective, acquire EURUSD/GBPUSD/AUDUSD/USDJPY
five-minute BID bars for 2022-07-05 through 2025-07-05, verify provenance/timezone,
and add ask/spread data and USD account conversion. This plan transfers its
multi-input GP method; it does not claim FX result reproduction.

Re-download/verify with `python amat/scripts/download_genetic_pattern_data.py`
from the workspace root. It reuses only locally checksum-matching archives.
Data remain ignored by Git; commit schemas, plans and audit summary, not datasets.

## Search and evaluation protocol

Freeze the protocol before evolution, including registry, dataset, costs, baseline,
folds, label availability, universes, genome bounds and trial/compute budgets.

1. **Start small.** Proposed pilot: population 256, 20 generations after the
   initial population, three fixed seeds (11, 29, 47), at most 16,128 candidate
   slots per family. Start with a 32 × 3-generation dry run to measure compute.
   One elite, tournament size three, crossover 0.8, local mutation 0.1 and fresh
   replacement 0.02, with independent events. Freeze these choices; they are
   engineering starting values, not performance-tuned recommendations.
2. **Grammar first.** Generate typed valid trees/rules. Canonicalize, deduplicate,
   reject constant/rare/nonfinite candidates, and cache training values. Cache
   key includes genome, data, fold, transform, evaluator and cost/protocol hashes.
   Resume with the complete RNG state, not just the initial seed.
3. **Cheap screen, honest final replay.** Vectorized training diagnostics can
   shortlist candidates; AMAT's accounting determines accepted fitness and
   promotion. Initially use its current net incremental Sharpe minus 0.01 per
   added complexity unit, with genome complexity explicitly versioned. Record
   every screened trial, not only expensive replays. Keep the original baseline
   as the fixed score reference and the incumbent as the retention comparator.
4. **Eligibility.** Existing drawdown, exposure, fold, cost-stress, liquidity,
   data-integrity and uncertainty gates remain mandatory. Proposed extra support
   floor: at least 100 distinct matched sessions overall and 30 per scored fold;
   expose symbol concentration and non-overlapping event counts. These are
   pre-registration choices, not proof of sufficient statistical power.
5. **Separate development from selection.** Re-run full evolution within each
   outer historical fold. A feasible replay begins with 2016–2019 fitting,
   2020 inner selection, 2021 outer evaluation; roll forward annually through
   2025. Supply earlier warmup history where possible; never score incomplete
   windows. Features and labels must be point-in-time available at every refit.
   Purge any training label whose interval crosses the next split; the purge is
   derived from actual execution/exit timestamps, up to the supported 60-session
   horizon, not a guessed fixed five-day gap. Permit past-only warmup into an
   evaluation fold, but never future fitting or labels.
6. **Respect prior research exposure.** Historical 2021–2026 data already exist
   in AMAT research. They are historical walk-forward development evidence, not
   automatically a virgin final holdout. Audit prior access and freeze one final
   unused period or collect a future paper window after the candidate is locked.
   Existing Program split guards must remain intact; don't relabel used data.
7. **Equal-budget controls.** Compare GA/GP with random search over the identical
   grammar and budget, existing hand-authored patterns, no-pattern baseline,
   incumbent, simple normalized-candle features, and exposure-matched controls.
   Run remove-pattern/remove-leaf, threshold perturbation, one-extra-bar delay,
   cost stress and held-out asset-group diagnostics. Equal compute and valid
   unique candidate counts both belong in the report.
8. **Diversity and ensembles.** Begin with one pattern and the existing cap of
   three. A five-pattern video replication belongs in its own research harness.
   Diversity uses training-only return correlation and match overlap. Zero-
   variance returns are ineligible, not NaN-based “perfect diversification”.
   If adding a pattern does not improve incremental replay, don't add it.
   Report correlated ETF/date observations as dependent, not independent trades.
9. **Search-aware nulls.** For a promising frozen search protocol, repeat the
   complete discovery/selection pipeline on structure-preserving null data.
   Preserve OHLC constraints, calendar, cross-asset dependence and the volatility
   structure appropriate to the null hypothesis; document what relationship is
   destroyed. Never independently shuffle open/high/low/close columns. Use
   `(1 + exceedances)/(B + 1)` and report Monte Carlo uncertainty. At B=100,
   minimum p is 1/101; this cannot support arbitrary multiple-testing claims.
   Paired block bootstraps over held-out continuous returns are complementary
   diagnostics, not a correction for searching millions of genomes on that data.
10. **Freeze and shadow.** Store formula, fitted state and evidence. Run causal
    inference without evolution, initially report-only, then paper shadow.
    Monitor prevalence, feature range, turnover, ablation benefit and evaluator
    parity. Refits require a new artifact and evaluation cycle. Retire or revert
    according to a predeclared rule; don't retune after seeing a failed holdout.

Return, profit factor, Ulcer Index and Martin/UPI remain useful diagnostics. Compute
Ulcer Index from the root mean square of percentage drawdowns; specify the return
period and risk-free numerator for UPI. Zero drawdown is undefined, not infinite
fitness. The [original Ulcer Index explanation](https://www.tangotools.com/ui/ui.htm)
helps avoid conflating the index with its return-to-risk ratio.

## Implementation sequence and acceptance criteria

| Phase | Work | Deliverable / acceptance |
|---|---|---|
| P0, 1–2 days | Data masks, source/protocol manifests, schema models, adjusted-open plumbing | Valid/invalid fixture suite; adjustment tests; complete calendar; artifact hashing |
| P1, 2–3 days | Candle evaluator and small genetic search | Hand-calculated matches; contradiction rejection; deterministic resume; prefix/future-mutation invariance |
| P2, 2–4 days | Signal adapter, ledgers, objective and report | Replay parity; no-pattern equivalence; existing research tests pass; legacy artifacts retain semantics |
| P3, 3–5 days | Typed symbolic evaluator and registry; bounded crossover/mutation | Arity/type/units/depth checks; numerical edge tests; vectorized/reference parity; no arbitrary execution |
| P4, 3–5 days plus compute | Nested replay, random controls, null tests, ablations | Reproducible evidence bundle; full failed-trial accounting; explicit pass/fail report |
| P5, 1–2 days plus observation | Frozen inference and shadow reporting | Training/inference parity; missing artifact refusal; measured latency; paper-only boundary tests |
| P6 | Completed-bar campaigns and 15m/1h/1d/1w/1mo spotting | Strict history parity, separate timeframe calibration, complete-period aggregation, immutable receipts, prospective/backfill distinction |
| P7 | Reconcile adjusted history and refresh shadow calibration | Unchanged raw history; documented cash-action factors; retained source evidence; new immutable snapshot/artifact; successful backfill without prospective misclassification |
| P8 | Explain drift and audit intraday coverage | Frozen-policy attribution; immutable per-asset gap audits; fixed-cutoff history extension; no suspension reset |
| P9 | Investigate native aggregates and source gaps | Separate native receipts; exact-interval parity; explicit hourly alignment; source-gap samples; no inference substitution |
| P10 | Native multi-timeframe portfolio integration and broad parameter research | Causal 15m/1h/1d/1w/1mo alignment; baseline parity; 2,305 training recipes; separate validation/test; exit and MTF ablations; cost and paired-return stress; immutable resume |
| P11 | Diversified rolling walk-forward parameter evaluation | Fresh fold eligibility; family/Pareto training shortlist; three frozen validation policies; 2,307 training proposals; separate outer counterfactual comparisons and active-parameter stability |
| P12 | Matched component removal and cost stress | Frozen P11 selections; MTF/exit/timeframe controls; paired baseline at 2/5/10/20 bps; exact P11 parity and immutable resume |

P3 is implemented for adjusted daily research. See [symbolic-programming.md](symbolic-programming.md)
and [p3-verification.md](p3-verification.md) for contracts, commands and measured results.

P4 symbolic validation is implemented: [nested-validation.md](nested-validation.md),
with measured evidence in [p4-verification.md](p4-verification.md).

P5 report-only infrastructure is implemented: [shadow-inference.md](shadow-inference.md),
with evidence in [p5-verification.md](p5-verification.md). Prospective observation
is not replaced by historical replay.

Estimates are engineering effort, not guaranteed discovery time. Budget expensive
null runs only after profiling; 100 reruns of a 16,128-slot pilot already imply
over 1.6 million candidate slots per family before folds. Vectorize across dates
and symbols and batch candidates; avoid storing population × dates × assets ×
nodes tensors. Optional DEAP can provide typed variation operators, but keep the
JSON evaluator and AMAT replay authoritative. Inspect a compatible pinned version
before adding a research-only dependency. See [DEAP's GP documentation](https://deap.readthedocs.io/en/master/tutorials/advanced/gp.html).

Definition of done: a reader can reproduce a pattern's training and validation
results from its frozen artifact, see whether it adds net value beyond the
incumbent, and run the same causal evaluator in shadow planning. Passing a search
fitness threshold alone never grants execution authority. A well-supported
finding that genetic search adds no value is a valid outcome.

P6 implementation and commands: [multi-timeframe-shadow.md](multi-timeframe-shadow.md).
Measured verification: [p6-verification.md](p6-verification.md).

P7 implementation: [adjustment-reconciliation.md](adjustment-reconciliation.md).
Measured evidence: [p7-verification.md](p7-verification.md).

P8 implementation and results: [p8-verification.md](p8-verification.md).

P9 contract and results: [native-intraday-evidence.md](native-intraday-evidence.md), [p9-verification.md](p9-verification.md).

P10 contract and results: [parameter-search.md](parameter-search.md), [p10-verification.md](p10-verification.md).

A later research extension is a registered family-adapter comparison. Fresh GP
evolution and fitted ridge/router/entry models must fit only inside each training
window, retain fitted state, and replay the same portfolio dates/costs. Matured
exit labels must end before each fit cutoff. Repeated outer windows and asset
groups should measure parameter stability before widening the search budget.
A separate intraday execution contract is needed for partial profit ladders,
trailing stops, and bracket-touch fills. Joint price-path Monte Carlo must
preserve cross-asset and cross-timeframe OHLCV/action consistency and recompute
features on every path; resampling fixed realized returns is a different test.

P11 methodology: [walkforward-parameters.md](walkforward-parameters.md).
Registered folds: [p11-folds.json](../../../amat/docs/genetic-patterns/p11-folds.json).

P11 measured results: [p11-verification.md](p11-verification.md).
P12 implements matched component removal and execution-cost stress:
[methodology](component-comparison.md), [verification](p12-verification.md).
Actual portfolio transitions when annual selections change remain a distinct
next step: carry holdings, capital, entry bases and exit/embargo state across
changes and charge transition costs. Separate warm-state fold returns must not
be presented as an executed switching portfolio.

P7 campaign clarification: [current status](p7-current-status.md).


P13 research is documented; the historical implementation and results are recorded below.
See the [continuous-state design](p13-research-plan.md),
[full-history/scenario/report contract](p13-full-history-validation.md), and
[Docker paper-demo integration audit](p13-paper-integration-audit.md).
Every planned run covers its complete registered calendar with explicit warm-up
and missing-data coverage. The proposed extended scenario matrix contains
17,408 frozen-mechanism runs; that synthetic campaign is still pending. Historical results below do not authorize promotion.


P13 foundation implementation is complete in the isolated
`/home/supasuge/Documents/amat-p13-workspace/amat` checkout on
`codex/p13-continuous-history`. It adds full-calendar scheduled portfolio replay,
frozen data coverage and 16 measured historical primary paths. See
[implementation](p13-foundation.md) and [results/verification](p13-foundation-verification.md).
Continue P13 work in that isolated checkout; the Docker-mounted host runtime
remains unchanged. The 76-run historical sensitivity matrix is now complete; coherent synthetic worlds,
adaptive refitting and executor parity remain pending.


P13 historical sensitivity controls completed October 1, 2026: all 76 paths retain
the entire verified 2,698-session calendar; the baseline leads returns at every
registered cost. See [implementation](p13-sensitivity.md) and
[results and verification](p13-sensitivity-verification.md). The associated report,
annual/regime data, full account traces and transition diagnostics are retained
in the isolated `outputs/genetic-patterns/2026-09-30-p13-sensitivity` bundle.
No synthetic campaign, parameter promotion or Docker runtime change occurred.


P13 coherent market generation completed October 1, 2026: 17 full-calendar worlds,
all five timeframes, canonical five-minute execution data, and observed-prefix
account parity. See [generator](p13-synthetic-generator.md) and
[verification](p13-synthetic-generator-verification.md). Synthetic MTF feature
adaptation and the four-policy/four-cost replay matrix are next; 544/17,408-run
campaigns and adaptive refitting remain pending. Docker runtime is unchanged.
