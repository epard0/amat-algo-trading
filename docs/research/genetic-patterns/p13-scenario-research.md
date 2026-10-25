# P13 full-history scenario and reporting research

Research proposal only, based on inspection of the retained data and current implementation on 2026-09-29. No scenario study was executed for this document. It complements `p13-protocol-audit.md`; it does not change source, historical evidence, selections, or paper/live authority.

## Existing capabilities and the integration gap

`src/autoresearch/market_scenarios.py` already defines 17 daily scenario families: bull, bear, range, whipsaw, crash/recovery, correlation break, Gaussian, Student-t with 5 and 3 degrees of freedom, Laplace, negatively skewed innovations, volatility mixture, volatility clustering, gap down, gap up, liquidity crisis, and flat prices. `scenario_panel` preserves the observed prefix and calendar, calibrates volatility and volume from at most 252 strictly pre-anchor sessions, and requires at least 63 preceding sessions. Distribution parameters, event offsets, and coverage requirements are declared. Its hypothetical suffix uses total-return prices, so real corporate actions must not also be applied there.

This is a daily generator. Its opening prices, ranges, execution VWAPs, and volumes are modeled separately; it does not produce a common intraday path from which the higher timeframes can be derived. Daily scenarios plus unchanged real intraday features would combine incompatible worlds and invalidate MTF attribution.

`src/amat/validation/montecarlo.py` jointly resamples daily cross-asset returns, opening execution displacement, volumes, and candle ratios using stationary blocks. It preserves cross-asset alignment and some short-range dependence. However, `bootstrap_panel` samples the entire source panel supplied to it, does not retain a calibration prefix, and has no separate target calendar. Passing the full observed panel would use future information when claiming pre-anchor calibration. `run_monte_carlo` invokes the ordinary static strategy runner; it does not evaluate the P11 candidate overlay or the proposed continuous recipe schedule. Its deterministic seed ordering is useful, but its in-memory result collection is not a resumable large-study ledger.

The existing three-page Monte Carlo PDF has equity bands, metric histograms, percentile comparisons, and within-generator exceedance frequencies. Those presentation primitives are useful; their current study object lacks paired baseline differences, MTF availability, scenario event traces, annual recipe state, and a complete replay manifest.

## Full history means a complete calendar, not invented observations

The current common daily snapshot covers 2016-01-04 through 2026-09-25. Its 11 assets are SPY, QQQ, IWM, EFA, EEM, IEF, GLD, SLV, DBC, USO, and UUP. The retained native 15-minute receipt declares 2019-01-02 through 2026-09-25. Earlier 15-minute data are unavailable in this evidence set. Existing minute receipts cover only a recent 2026 interval; they are not a historical calibration source for 2020.

Every run should include the entire daily calendar from 2016-01-04, with explicit row states: observed calibration prefix, generated suffix, unavailable input, indicator warmup, and training-ineligible feature. Before 2019, intraday inputs remain unavailable rather than being filled with invented historical observations. Weekly and monthly rows come from completed exchange periods; partial periods and indicator warmup remain unavailable. Report the full-span ledger and full-span descriptive statistics as requested, alongside separately labeled calibration-prefix, generated-suffix, and post-selection results. Including a prefix in a chart does not make it an independent performance test.

A practical primary synthetic anchor is 2020-01-02: calibrate using only observations through the preceding exchange session, including the available 2019 intraday history; preserve the earlier observed prefix. This permits a long synthetic suffix without using 2026 minute data to model a 2020 decision. If synthetic intraday is required from 2016 itself, obtain an earlier calibration dataset or explicitly label an assumption-only scenario branch. It cannot be called an empirically calibrated 2016 backtest using the present receipts.

Retained P11 schedules are frozen interventions on these worlds. Applying their historically selected recipes to synthetic data tests those recipes' conditional robustness; it does not simulate a selector that would have discovered them on that synthetic history. A future adaptive-selection experiment must rerun selection separately inside each world's legal training/validation boundaries and has a different computation budget and interpretation. Do not allow synthetic returns to select among the retained balanced, return, and defensive policies.

## A coherent joint market generator

The runner executes against the 09:30–09:35 window. A 15-minute OHLCV bar cannot determine that window's VWAP or volume. Use one canonical five-minute OHLCV trace plus an explicit five-minute VWAP constrained to its low/high range, with joint cross-asset innovations and volume. Aggregate three consecutive components into 15-minute bars; aggregate twelve into session-open-anchored hours; aggregate all available regular-session components into daily bars. Then derive weekly and monthly bars from complete daily periods. A normal full session has 78 five-minute bars; half days and exceptional closures use the actual XNYS schedule, not that constant. Omit incomplete final hours to match the current contract.

An alternative with smaller storage is a native 15-minute trace plus a jointly modeled first-five-minute execution window nested within the first bar. It must satisfy price-range and volume constraints and disclose that the rest of the sub-bar path is unidentified. This is adequate only if no other five-minute execution or signal feature is claimed. Neither alternative may borrow actual execution prices or volumes after the synthetic anchor.

There are no retained five-minute observations covering the proposed 2019 calibration interval. Consequently, within-15-minute structure is an explicit bridge-model assumption unless additional pre-anchor data are acquired. Calibrate session-level volatility, cross-asset dependence, intraday seasonal shapes, opening-window/daily relationships, and missingness only from the available pre-anchor observations. Freeze the calibration digest, dates, usable counts, shrinkage choices, and bridge assumptions. Do not fit a regime model on the generated suffix or the full real history.

Recommended generator requirements:

- One joint innovation stream drives prices, gaps, volume, and liquidity. Preserve cross-asset relationships within a world. Do not generate each timeframe independently.
- OHLC aggregation is first/max/min/last, volume sums, and VWAP is volume-weighted. Zero-volume execution windows produce no fill opportunity; distinguish valid no-trade intervals from missing data.
- Positive finite prices, valid OHLC envelopes, nonnegative volumes, and a consistent adjusted/raw basis are mandatory. Do not silently clip or redraw numerically extreme paths; retain a failed-world receipt.
- Use a total-return synthetic suffix with no real suffix corporate actions. Prefix-to-suffix units, raw/adjusted entry bases, splits, and inherited positions require an explicit conversion contract and reconciliation test.
- Separate complete synthetic latent truth from an optional observation mask. Missing-bar stresses must propagate through strict aggregation and latest-closed-bar alignment without forward filling or weight renormalization.
- Recompute features and causal regimes from each world's own history. Keep prespecified training eligibility fixed for frozen-schedule robustness, or identify a distinct world-specific causal-eligibility experiment; never use suffix availability to qualify a feature retrospectively.
- Disclose monthly scarcity and the actual number of source-period observations. Repeated daily carries of one monthly value are not independent monthly observations.

The 17 existing families supply distribution and shock ideas, not a ready-made calibrated five-minute generator. Intraday placement of shocks, volume coupling, half-day handling, halts, and bridge sensitivities need new contracts and tests before a large run.

## Paired, resumable experiment matrix

The proposed main matrix is 17 families × 64 deterministic seeds × 4 policies × 4 fixed costs (2, 5, 10, 20 bps): **1,088 market worlds and 17,408 full-history replays**. The four policies include baseline. All policies and costs share the identical market-world bytes for each family/seed; cost changes execution accounting, never the underlying market. Preserve each policy's own continuous holdings, orders, bases, embargoes, and cash. Shared current recipes do not justify deduplicating different prior histories.

Report paired policy-minus-baseline outcomes at the same family, seed, cost, calendar, and metric window. Count failures and incomplete runs in denominators. Scenario families are deliberate stresses, not an empirical mixture of equally likely future regimes; do not pool their counts into a market forecast. Sixty-four seeds provide Monte Carlo variation within a declared model, not 64 independent historical market samples. A stationary-bootstrap branch, alternative bridge assumptions, delay controls, and component ablations should be separately budgeted additions rather than silently multiplied into this matrix.

Bind every world to generator version/source hash, calibration/data hashes, calendar, ordered assets, scenario specification, seed, and bridge contract. Bind every replay to its world digest, entire policy schedule and eligibility, cost/accounting configuration, evaluator/dependency hashes, initial state, and measurement windows. Atomic per-world/per-replay receipts and a writer lock permit deterministic resume; verify checksum and complete identity before reusing a result. Never bless partially published or mismatched files. Unexpected integrity failures stop the affected campaign; expected numerical world failures remain explicit records shared across its paired policies.

Profile a few complete world/replay pairs before allocating storage or workers. Five-minute OHLCV/VWAP doubles require roughly 2.7k sessions × 78 slots × 11 assets × 6 values × 8 bytes, about 111 MB per full-calendar world before timestamps, masks, or compression. The synthetic suffix is smaller, but 1,088 expanded worlds can still exceed 100 GB. Store the shared observed prefix once, use compressed immutable world partitions or deterministic regeneration with digests, and bound worker caches. Float32 conversion changes numerical behavior and must not be introduced merely as an undocumented storage optimization.

## Regimes and pattern evidence

`src/amat/validation/regimes.py` already supplies a causal SPY classifier: trailing 63-session direction and 21-session realized volatility relative to its expanding median, with warmup labels. `regime_performance` attributes session returns using the prior session's label, avoiding a large return labeling itself volatile. Preserve that lag. Its noncontiguous regime slices deliberately omit drawdown because joining separated observations would manufacture a portfolio path; label their annualized returns as conditional slice statistics, not continuous regime investments.

Report three distinct label types: causal observed regime, generator-declared latent state/event, and retrospective named historical episode. Only the first is available to a causal decision rule. Known future generator events and retrospective episode names are explanatory annotations, never strategy inputs. Pattern diagnostics should include eligible assets, finite source bars, detections, signal magnitude, realized coefficient contribution, and subsequent outcomes using a declared horizon. Detection/outcome associations are descriptive and cannot authorize new thresholds after examining these runs.

## Reproducible readable report and data artifacts

Proposed output layout:

| Artifact | Required contents |
|---|---|
| `manifest.json` | Immutable experiment contract, hashes, source/dependency identities, full calendar, anchors, matrix, failure policy |
| `coverage.csv` | Asset/timeframe start/end, expected/present/missing bars, calibration counts, warmup and eligibility reasons |
| `worlds/<world_id>/receipt.json` | Generator/calibration/seed identity, world checksum, bridge assumptions, events, completion/failure |
| `worlds/<world_id>/bars/*.parquet` | Canonical generated OHLCV/VWAP with UTC interval bounds, session, symbol, observed/generated/missing status; observed prefix referenced once |
| `replays/<replay_id>/receipt.json` | Full replay identity, accounting parity checks, status and exact metric windows |
| `daily.parquet` | World, policy, cost, session, equity, return, cash, exposure, turnover, fees, drawdown, active recipe, causal regime |
| `fills.parquet`, `transitions.parquet` | Decision/execution provenance, fills/misses, entry basis, embargo, boundary state reconciliation |
| `features.parquet` | Sparse detection/contribution records, timeframe source close/availability, eligibility and missing reasons |
| `metrics.csv`, `paired_deltas.csv` | Full-span and separated-window results, baseline differences, counts and failure denominators |
| `regimes.csv`, `events.csv` | Lagged causal slices and separately identified generator/retrospective event attribution |
| `report.html` and `report.pdf` | Linked human-readable analysis with coverage, observed continuous paths, synthetic fans, paired distributions, regime/event tables, and limitations |

Partition large tables by world or family and seed; never require one giant combined dataframe to resume or produce a summary. Use ISO dates/UTC timestamps, schema versions, stable identifiers, explicit null reasons, and documented metric conventions. Publish CSV summaries for easy inspection and typed Parquet detail for reproducibility. Separate gross exposure from realized volatility; risk scalar is not a realized-risk matching guarantee.

## Acceptance gates before running the large matrix

1. Prefix-poison tests: changing any real observation after calibration cutoff cannot change calibrated parameters or generated bytes for a fixed seed; extending the requested horizon cannot change the earlier generated prefix.
2. Cross-timeframe reconciliation: every valid aggregate and opening execution window agrees with the canonical trace, including DST, half days, holidays, missing components, zero volume, and completed monthly boundaries.
3. Replay parity: observed constant-recipe paths reproduce existing authoritative results; full scheduled paths satisfy the state and activation tests in the companion protocol audit.
4. Paired-world identity: all policy/cost cells share identical world hashes; a zero-effect overlay matches its matched baseline where remaining recipe settings match.
5. Accounting: synthetic suffix has no real corporate actions, transition units reconcile, fees/participation are recomputed, and cash plus marked positions equals equity.
6. Determinism: one versus multiple workers and interrupted/resumed runs produce identical receipts, features, and outcomes; hash mismatches fail closed.
7. Report reconciliation: counts match the registered matrix, all failures are visible, metric dates match return arrays, regime labels are lagged, and no noncontiguous slices are presented as drawdown paths.

Passing these gates would authorize a research run only. A narrowly scoped Docker paper mechanism must separately consume approved observed-data decisions and preserve its existing execution authority; synthetic data and scenario-selected conclusions must not enter that mechanism by accident.
