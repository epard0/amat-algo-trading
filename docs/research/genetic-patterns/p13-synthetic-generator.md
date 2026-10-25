# P13 coherent synthetic-market generator

The generator runs only in `/home/supasuge/Documents/amat-p13-workspace/amat`.
It adds separate research modules; the Docker-mounted runtime and prior P11/P12/
P13 numerical code remain unchanged. Historical sensitivity evidence is retained
under its own immutable registration.

## World contract

Each world retains all 2,698 verified XNYS sessions from January 4, 2016 through
September 25, 2026. The first 1,006 daily observations and associated corporate
actions remain observed and unchanged. Generation begins January 2, 2020 and
covers the remaining 1,692 sessions. The cutoff is December 31, 2019.

A canonical raw five-minute OHLCV/VWAP path drives the entire generated suffix:
three bars form a 15-minute bar, twelve form a full session-open hour, and all
regular-session bars form a daily candle. Calendar weeks and months use the
existing strict completed-period aggregator. Exchange holidays, DST and half days
come from XNYS. Incomplete final hours are omitted, with daily candles retaining
the full session. Every world has 1,446,984 generated five-minute asset bars for
the same 11-ETF universe. Observed prefix five-minute bars are not fabricated.

Prices must be positive and finite, OHLC envelopes valid, volume nonnegative,
and modeled VWAP within its bar's bounds. The first five-minute bar is the
09:30–09:35 Eastern execution window. Zero execution volume represents a no-fill
opportunity, distinct from missing optional feature observations. Extreme numerical
paths fail with retained failure evidence rather than clipping/redrawing prices.

The independent validator recomputes daily marks, execution prices/volumes, 15m,
hourly, weekly and monthly data from the canonical trace. It verifies interval
ordering, observed daily-prefix equality and universe identity. Tests independently
compare preserved native-prefix values and four-component hours.

## Calibration and assumptions

Daily return volatility and cross-asset correlation use 252 strictly pre-anchor
returns. Correlation receives 10% shrinkage toward identity; estimated daily sigma
is bounded to 0.004–0.04 as a declared calibration assumption. These bounds do not
clip generated return tails. Native volume seasonality and missingness use up to
252 preceding sessions, retaining usable counts for every asset and intraday slot.
Opening-window/session volume fractions use the observed pre-anchor data.

There are no retained pre-2020 five-minute observations. Intrabar structure is an
explicit bridge assumption: correlated log Brownian bridges conditional on daily
endpoints and opening gaps, modeled wicks, and five-minute VWAP `(O+H+L+C)/4`.
`bridge_scale` changes the modeled intraday amplitude. Modeled VWAP is not an
observed broker execution. Calibration hashes and assumptions accompany every world.

The 17 families are bull, bear, range, whipsaw, crash/recovery, correlation break,
Gaussian, Student-t(5), Student-t(3), Laplace, skewed, volatility mixture,
volatility clustering, gap down, gap up, liquidity crisis and flat prices.
Scenario frequencies are experimental allocations, not forecasts. The correlation
break overrides daily dependence with 0.35/0.95; gap residual and intraday-bridge
covariance retain the calibrated structure. Volatility clustering changes daily
returns while bridge amplitude retains calibrated sigma. Event shocks are added
exactly once; family-specific event offsets and coverage status are retained.

## Price, volume and account continuity

Raw prices initialize from the last observed raw close. The adjusted/raw price
factor at that boundary stays fixed throughout the synthetic suffix. Thus existing
holdings, cash, pending orders and adjusted exit bases retain their units. Prefix
actions are retained strictly before the anchor; no observed dividends or splits
are credited in the synthetic suffix. The suffix models total-return price
relatives on the inherited raw-price scale.

Observed native volumes use the vendor's adjusted basis, which differs materially
from raw daily/execution shares for assets such as USO. A fixed per-asset research
volume factor is calibrated as the median complete-native-session volume divided
by raw daily volume before the anchor. Generated native-view volume uses that
factor; canonical bars, daily volume and execution capacity stay in modeled raw
shares. This empirical factor incorporates split adjustment and feed/session
coverage. It is not an asserted exact corporate-action conversion. Aggregation
checks account for this declared volume scale.

The latent generated market is complete. A separate pre-anchor-calibrated
observation mask applies to optional native feature views. A missing component
makes its entire aggregate unavailable, without forward filling. The mask is
independent by asset and 15-minute slot, an explicit simplification. The 2016–2018
native-feature gap remains visible; it does not truncate the daily account.

## Evidence and integration boundary

Worlds are registered by full source/dependency/input hashes, calendar, anchor,
family, seed and bridge scale. Compressed canonical bars are stored once per
world, independent of policies and costs. Reconstructable full daily/view arrays,
metadata, prefix actions, checksums and readable reports accompany them. Immutable
resume verifies the complete inventory and never regenerates a completed world.

This stage implements market generation, not the 544-run policy/cost smoke campaign
or the 17,408-run extended campaign. Bounded baseline account checks can verify
unchanged historical state and the flat-world bridge separately. They do not test
all selected P11 policies. The existing non-daily symbolic evaluator deliberately
accepts only explicit observed/native source contracts: a synthetic research-view
adapter with world/frame provenance is the next step. Do not relabel synthetic
views as Alpaca data or reuse observed suffix feature arrays to pass that gate.

Next: implement that research adapter, recompute all five-timeframe features within
each verified world, preserve frozen historical selection/eligibility provenance,
and register the four-policy/four-cost paired replay matrix. Then run the 544-run
smoke campaign before expanding to 64 seeds. Full adaptive refitting, broader
method-family adapters and paper-executor parity remain separate requirements.
