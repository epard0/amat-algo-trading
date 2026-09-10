# Strategy adjustment study — 2026-09-07

## Decision

Do not promote breadth or a clock-time oil/gas rule from the evidence currently
available. Use the 128-asset data universe as a discovery panel, preserve v6 as
the production reference, and use the completed attribution matrix below to
design single-component ablations. Learned regimes may condition risk in
v11/v12, but must not manufacture direction. Long/short and
hourly/intraday sleeves require new market-data and accounting contracts before
their performance can be measured honestly.

All results below use already-inspected history and are research evidence, not
an out-of-sample profitability claim.

## What the existing results prove

| Metric | v6 current, 11 assets | v9 wide/structure, 30 assets | Change |
|---|---:|---:|---:|
| Total return | 77.08% | 65.88% | -11.20 pp |
| Annualized return | 6.15% | 5.43% | -0.72 pp |
| Annualized volatility | 5.41% | 6.26% | +0.85 pp |
| Sharpe | 1.137 | 0.866 | -0.270 (-23.8%) |
| Maximum drawdown | -7.89% | -9.45% | 1.55 pp worse |
| Average gross exposure | 56.38% | 76.38% | +20.0 pp |
| Fills | 3,110 | 5,727 | +2,617 (+84.1%) |
| Costs | $3,496.83 | $4,503.47 | +$1,006.64 (+28.8%) |
| Missed fills | 28 | 638 | +610 |
| Weighted completed-trade win rate | 49.50% | 43.76% | -5.74 pp |
| Assets with negative attributed P&L | 1/11 | 10/30 | +24.2 pp |

The comparison quantifies the central problem: v9 successfully increased
breadth, exposure, and activity, but the marginal trades had lower quality.
The small increase in session hit rate (57.07% to 57.69%) did not compensate for
more volatility, drawdown, misses, and costs. Frequency is therefore a capacity
problem only after eligibility is solved; it is not itself an objective to
maximize.

Win rate must be interpreted jointly with payoff. For a sequence with win
probability `p` and average-win/average-loss ratio `b`, expectancy is positive
when `p > 1 / (1 + b)`. From the v6 trade report:

- IEF: win rate 37.69%, profit factor 0.841, implied payoff ratio 1.39 and
  break-even win rate 41.84%. The observed rate misses break-even by 4.15 pp.
- SLV: win rate only 32.48%, but profit factor 1.574 implies payoff ratio 3.27
  and a 23.40% break-even win rate. Rejecting it for a low win rate would remove
  a positive-payoff sleeve.
- USO: win rate 43.24%, profit factor 1.777, implied payoff ratio 2.33 and
  30.00% break-even win rate.

This is why the adaptive selector must rank lower-bound net expectancy or
shrunk profit factor, not raw accuracy.

## Clock-time pattern falsification

The first scan tested 184 hypotheses (92 symbols ×
overnight/intraday) with a fixed training boundary of 2022-12-30, 4 bps
round-trip cost, and Benjamini–Hochberg correction at 10% in both slices. Zero
hypotheses survived. The expanded scan then tested 254 hypotheses over 127
tradable candidates and again found zero survivors in both train and validation
after correction. This is a direct multiple-comparisons check on the proposed
"law of large numbers" search: adding assets produces more attractive-looking
sample means, but it also produces more chances to discover noise.

The proposed energy overnight proxy is weak after costs:

| Symbol | Train net mean | Validation net mean | Validation win rate | Validation PF | Train/validation BH q |
|---|---:|---:|---:|---:|---:|
| XLE | 1.88 bps | 1.45 bps | 53.82% | 1.045 | 1.00 / 1.00 |
| XOP | 6.07 bps | 0.79 bps | 52.49% | 1.019 | 1.00 / 1.00 |

These returns use prior official close to the next 09:30–09:35 VWAP. The
snapshot has no 15:55–16:00 observations, so it cannot test a 4:55 PM order.
Implementing that schedule now would substitute a different fill for the one
named by the hypothesis. The next data snapshot should add 15:55–16:00 raw
VWAP, 30/60-minute bars, spread proxies, and volume; then repeat the test by
weekday, inventory-announcement day, volatility regime, and curve state under
nested multiple-testing correction.

## Three strategy arms and the attribution matrix

1. `amat_v10_discovery128.toml` — breadth-only control. Same daily long/cash
   signal family, 124 tradable ETFs and companies from a 128-symbol data
   snapshot, with a 5% symbol cap. It adds 18 tradable defense instruments, 22
   energy-company records, and 6 oil/gas product ETFs to the usable 81-asset
   base. RTX remains data-only because its 2020 spin-offs/merger cannot be
   reconciled by the current single-security ledger. COP, DVN, and FANG are
   also data-only because their fixed-plus-variable dividends collide in the
   normalized action field; guessing one or summing them would corrupt cash.
2. `amat_v11_adaptive128.toml` — same 124 assets plus top-24 causal selection,
   two-of-three horizon agreement, soft structure quality, trailing information-ratio
   risk budgeting, and causal KMeans regime risk conditioning.
3. `amat_v12_adaptive11.toml` — v11's adaptive stack on v6's original 11 assets.
   This is the compact control that prevents a universe change from taking
   credit for a model change.

The comparisons have one interpretation each:

| Comparison | Effect identified |
|---|---|
| v10 − v6 | Breadth, before adaptive selection |
| v12 − v6 | Adaptive stack, holding the universe fixed |
| v11 − v12 | Breadth under the same adaptive stack |
| v11 − v10 | Adaptive stack inside the same broad universe |

### Broad-universe results

The full v10 study ran 124 per-asset sleeves through the same corporate-action,
fill, cost, and report path. On 2,414 sessions (2016-12-30 through 2026-08-10),
v10 returned 35.64% (3.24% annualized), with 4.56% volatility, Sharpe 0.710,
-7.00% maximum drawdown, 56.00% average gross, 18,268 fills, 759 missed fills,
and $2,566.53 costs. Against v6 on the identical dates, it lost 41.43 percentage
points of total return and 0.427 Sharpe while generating 5.87 times as many
fills. Breadth supplied observations and activity, but the marginal assets
diluted expectancy.

The exact matched-start comparison for the adaptive broad model is:

| Metric, 2017-07-03 to 2026-08-10 | v10 broad control | v11 adaptive broad | Change |
|---|---:|---:|---:|
| Total return | 34.16% | 39.16% | +5.00 pp |
| Annualized return | 3.29% | 3.71% | +0.42 pp |
| Annualized volatility | 4.73% | 5.57% | +0.85 pp |
| Sharpe | 0.696 | 0.665 | -0.031 |
| Maximum drawdown | -8.11% | -9.92% | 1.80 pp worse |
| Average gross | 57.76% | 52.12% | -5.64 pp |
| Fills | 17,603 | 12,851 | -4,752 (-27.0%) |
| Costs | $2,513.53 | $6,257.13 | +$3,743.60 (+148.9%) |
| Missed fills | 715 | 94 | -621 |

Selection improved absolute return and reduced fill count, but did not improve
risk-adjusted return or drawdown and greatly increased traded notional/cost.
Therefore v11 is also rejected for promotion. The next selector must optimize
net expected value per unit turnover and compare against a turnover-matched
random top-24 control; merely selecting fewer names is not cost control.

The v10 sleeves show where narrower, family-specific models are justified:

| Family | Positive sleeves | Median return | Median Sharpe | Weighted trade win rate |
|---|---:|---:|---:|---:|
| US defense | 17/18 | 23.21% | 0.467 | 48.76% |
| Energy companies | 14/19 | 5.40% | 0.125 | 42.29% |
| Energy products | 4/6 | 6.32% | 0.175 | 44.67% |
| Currencies | 3/7 | -2.24% | -0.068 | 41.60% |
| Real estate | 3/5 | 0.64% | 0.017 | 36.90% |

Defense is the strongest new family in this inspected sample; currencies are
the clearest evidence that the equity trend rules do not transfer unchanged.
Within energy, MPC (37.57% standalone return, Sharpe 0.721) and WMB (30.93%,
0.639) justify continued study, while EQT, SLB, OVV, and CVX all had negative
standalone return. This dispersion supports family shrinkage plus causal
symbol eligibility, not a permanent energy-company whitelist selected from the
same sample.

Trade economics reinforce that conclusion. In combined v10 attribution, MPC
won only 36.2% of 69 completed trades but had profit factor 6.27 and an implied
11.04:1 average-win/average-loss ratio; its break-even win rate was only 8.3%.
USO won more often (39.1% of 92) but its profit factor was 0.47, implied payoff
0.72, and break-even win rate 58.0%. Raising a generic accuracy threshold would
prefer the losing sleeve. Selection must estimate `p * average_win -
(1-p) * average_loss - costs`, with uncertainty, per asset and regime.

The first completed control is already a rejection. On the identical
2017-07-03 through 2026-08-10 interval, a v6 control with the same 378-session
warm-up returned 70.64% (6.06% annualized), Sharpe 1.114, maximum drawdown
-7.89%, average gross 55.35%, 2,962 fills, and $3,259.35 costs. v12 returned
28.81% (2.83% annualized), Sharpe 0.737, maximum drawdown -6.70%, average gross
32.99%, 3,638 fills, and $4,319.59 costs. Thus the complete adaptive stack:

- lost 41.83 percentage points of total return and 0.378 Sharpe;
- reduced drawdown by only 1.20 percentage points and volatility by 1.60 points;
- cut deployed gross exposure by 22.36 percentage points; and
- added 676 fills and $1,060.24 of costs despite taking much less risk.

This identifies over-filtering plus excess turnover, not insufficient signal
variety. v12 is retained as falsification evidence but is not a promotion
candidate. Its next ablation must restore the 2% rebalance band and test the
regime, agreement, structure, and risk-budget components one at a time against
matched-turnover controls; the combined stack should not be tuned as a unit.

The immediate v12.0.1 softening test restored the 2% band, changed confirmation
from three-of-three to two-of-three horizons, raised the structure floor from
0.75 to 0.90, narrowed the risk-budget range from 0.50–1.50 to 0.75–1.25, and
raised the learned-regime floor from 0.50 to 0.75. On the same interval it
returned 60.06%, Sharpe 1.012, drawdown -7.05%, gross 51.31%, 3,265 fills, and
$3,379.37 costs. Relative to v12.0.0, that is +31.26 pp return, +0.275 Sharpe,
+18.32 pp gross, 373 fewer fills, and $940.22 less cost, for only 0.35 pp more
drawdown. This is quantified support for soft, non-compounding conditioning.
It still trails matched-start v6 by 10.57 pp return and 0.102 Sharpe while
adding 303 fills, so it also proves that the complete adaptive stack is not yet
worth its complexity. Keep v12.0.1 as the next ablation base, not a promotion.

The learned-regime scalar explains part of the remaining issue: in v12.0.1 it
averages 0.842, is below one on 63.38% of sessions, sits exactly at its 0.75
floor on 62.74%, and changes value 180 times. It is functioning mainly as a
frequently switching constant de-risker rather than a nuanced classifier.
Before another parameter search, replace the linear label-to-size rule with
out-of-fold regime-specific payoff estimates, require transition hysteresis,
and update the allocation scalar only on the scheduled refit date. Compare
that model to a constant 0.842 scalar and a shuffled series with matched flips;
if it cannot beat both, the learned timing carries no information.

The currently supported files are daily and long/cash. They do not pretend to
enable shorts or hourly/intraday execution. A true multi-horizon version needs:

- immutable 30/60-minute bars plus open and close execution windows;
- a signed-position ledger with short proceeds, borrow availability/fees,
  margin, short dividends, corporate actions, forced buy-ins, and signed P&L;
- separate sleeve clocks whose targets net through one gross/net/sector/factor
  risk layer; and
- executor reconciliation that can distinguish `sell_to_close` from
  `sell_short` and `buy_to_cover`.

Until those contracts exist, negative weights would be clipped to zero by the
portfolio code and refused by the ledger and executor. A configuration flag
cannot change that fact.

## Adaptive eligibility and narrowing rules

Keep all 128 symbols in the discovery dataset, but make tradability a causal
decision refreshed monthly from trailing observations. Do not permanently
delete an asset because it lost in this one sample.

For each symbol and strategy sleeve, estimate wins, losses, payoff ratio,
turnover, cost, tail loss, regime-conditioned expectancy, and effective sample
size. Shrink symbol estimates toward their family (defense, E&P, refiners,
rates, currencies, and so on) so a lucky 15-trade asset cannot outrank a stable
150-trade asset. Eligible symbols should satisfy all of:

- at least 30 completed training trades and 10 later validation trades;
- net profit factor above 1.10 in training and above 1.00 in validation at
  baseline cost, with positive net expectancy in both;
- positive lower confidence bound for net expectancy after family-wise false
  discovery control;
- no single year contributes more than 50% of cumulative P&L;
- positive performance at 2× baseline costs and no more than a 25% deterioration
  in maximum drawdown versus its family control; and
- execution-window coverage at least 99.5%, or an explicit missingness model
  that is itself validated.

The 1.10 profit-factor floor is a margin, not magic: PF 1.02 (XOP's validation
overnight proxy) leaves only 2 cents of gross profit per dollar of gross loss
before model error, impact, or worse fills. PF 1.10 leaves 10 cents, still thin
but meaningfully harder for small execution error to erase.

Portfolio promotion should require a later locked slice to exceed v6 by at
least 0.15 Sharpe, with the stationary-bootstrap lower bound on the Sharpe
difference above zero, while maximum drawdown is no worse than v6 by more than
1 percentage point and 5× costs do not make total return negative. A threshold
without an uncertainty bound invites selection on noise.

## Market structure and indicator additions

Indicators should answer different questions rather than provide correlated
votes:

- **Direction/persistence:** multi-horizon normalized momentum, ADX, Kaufman
  efficiency ratio, variance ratio, and rolling Hurst estimates. Agreement
  separates a persistent trend from a volatile endpoint-to-endpoint move.
- **Location/extension:** Donchian position, distance to rolling support and
  resistance, RSI, and Bollinger `%B`. This avoids initiating a trend sleeve
  after an extreme extension and can route mean-reversion candidates instead.
- **Volatility/shape:** ATR percentile, Bollinger bandwidth, realized downside
  variance share, skew, jump/gap frequency, and volatility-of-volatility. These
  determine stop distance, sizing, and whether a breakout/mean-reversion model
  is structurally appropriate.
- **Participation/liquidity:** dollar-volume percentile, opening-window share,
  volume z-score, OBV/accumulation, and Amihud illiquidity. These reduce false
  breakouts and prevent the frequency target from concentrating in costly tape.
- **Cross-asset state:** dollar, rates, equity breadth, oil/gas curve carry,
  EIA inventory/storage surprise, and sector-relative momentum. Commodity and
  FX sleeves need macro/carry context that an equity RSI cannot represent.

No feature earns a vote merely because it has a familiar name. Add one family
at a time, compare it to permuted/matched-turnover controls, and keep it only if
the later slice improves net expectancy or downside risk.

## Statistical-arbitrage sleeve

The repository now calculates pair OLS hedge ratios, spread, ADF statistic,
half-life, and mean crossings as diagnostics. The next executable sleeve should
use formation/trading windows and require stable economic membership plus:

`spread_t = log(P_A,t) - beta_t log(P_B,t)`

`z_t = (spread_t - trailing_mean_t) / trailing_std_t`

Enter only when the formation-window spread is stationary after FDR correction,
the estimated half-life is shorter than the trading window, crossings are
sufficient for the assumed costs, and hedge-ratio drift stays bounded. Size the
pair beta-neutral, exit at mean reversion/time stop/structural break, and charge
both legs plus borrow. Cluster exposure must include both legs even when net
market value is near zero.

On the latest 504-session window of the 92-symbol snapshot, 231 of 4,186 pairs
passed the descriptive correlation/ADF/half-life/crossing gates. That large
count is a warning, not 231 opportunities: the approximate ADF cutoff is not a
multiple-testing-corrected p-value. Examples worth a formation/trading-window
study include FXB/FXF (correlation 0.6548, ADF t -3.20, half-life 15.8 sessions,
57 crossings) and IWD/VLUE (0.8545, -3.57, 13.7 sessions, 49 crossings). The
largest current divergence, USMV/XLU at 4.42 spread standard deviations, is not
an entry recommendation; extreme divergence may signal a broken relationship.
The expanded diagnostic tested 8,001 pairs and found 266 descriptive passes.
The lower pass fraction despite a larger search is useful triage, but these are
still not deployable signals until the ADF/entry search is corrected for
multiple testing and replayed in rolling formation/trading windows with both
legs' costs and borrow.

## Asset-specific sleeves

- **Oil/natural gas:** use futures-curve carry/roll yield, EIA inventory and gas
  storage surprise, weather/seasonality, dollar, and realized volatility. Keep
  BOIL/KOLD/UCO/SCO discovery-only with low caps because daily reset makes them
  unsuitable as generic long-term proxies.
- **FX:** combine time-series momentum with rate differential/carry, dollar
  regime, realized volatility, and risk-off state; neutralize overlapping USD
  legs across currency ETFs.
- **Defense:** combine relative trend and liquidity with point-in-time DoD
  contract awards, SEC filings, backlog/guidance surprise, and geopolitical
  event novelty. Avoid raw headline count, which rewards syndication.
- **News/event:** score event type, surprise versus prior information, novelty,
  source reliability, entity confidence, and publication-to-market latency.
  A long-horizon thesis should be a separate low-turnover sleeve with its own
  expiry, not a reason to suspend an intraday exit.

## News-source state

Alpaca historical news ingestion is implemented as a GET-only, host-pinned,
cursor-paginated collector using the existing Alpaca credentials. It stores
vendor bytes and real ingestion time, rejects malformed pages, and cannot
backdate knowledge. SEC EDGAR, EIA, GDELT, and DoD contract awards are declared
in `configs/data/sources.toml` but deliberately not described as ingested or
used until their point-in-time feature pipelines and coverage checks exist.

## Multi-agent workflow

Use agents as independently accountable research roles, not a committee that
votes on trades:

1. Data agents produce immutable price, news, filing, energy, and contract
   snapshots with coverage manifests.
2. Structure agents classify trend/range/transition and volatility/liquidity
   state per horizon, returning features and confidence only.
3. Specialist sleeve agents propose signed targets for trend, stat-arb,
   commodity/FX, defense-event, and long-horizon news mandates. Each supplies
   expiry, expected payoff, uncertainty, data timestamp, and invalidation.
4. A skeptic agent runs leakage, placebo, shuffled-label, random-selection,
   cost, missing-fill, and multiple-testing controls; it can veto an experiment
   artifact, not place a trade.
5. One deterministic risk allocator nets the sleeve targets and enforces gross,
   net, cluster, factor, liquidity, drawdown, and borrow constraints.
6. The executor independently reconciles the signed plan with broker positions,
   creates idempotent intents, and records fills/misses.
7. An evaluator attributes P&L and risk by asset, sleeve, regime, horizon, and
   source; monthly evidence updates eligibility, while strategy promotion stays
   behind a locked holdout and human approval.

This architecture gains specialization and adversarial review without allowing
language-model agreement to bypass deterministic portfolio and execution rules.
