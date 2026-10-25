# Trade performance monitoring

The monitor answers three separate questions for daily research backtests and paper trading:

1. **Instruction:** what did this model ask for, using information available at the decision?
2. **Execution:** what filled, and how did it differ from the instruction?
3. **Outcome:** what happened after that entry or exit, and did an explicit forecast match its target?

A losing position can follow the model correctly. A profitable fill can still violate its instruction.
The monitor therefore keeps execution flags, price outcomes and forecast errors as separate fields.

## What is recorded

| Evidence | Research backtests | Paper trading |
|---|---|---|
| Identity | Candidate and program digests, cost case, market-data source | Configuration digest, strategy/version, revision, snapshot, deterministic client order ID |
| Intent | Signal and execution sessions, side, requested shares, reason; retained through partial/missed settlement | Original plan, target/current weights, requested shares, recorded quantity adjustments |
| Decision context | Causal effective signal score, range/trend/stress label, entry-policy reason and multiplier when present | Core trend score, portfolio risk forecast and binding constraints from new plans |
| Execution | Filled shares, weighted fill price, costs, missed-fill reasons | Broker quantity, cumulative filled quantity, average price, status, submission/fill timestamps |
| Price path | Adjusted, side-signed 1/5/20/60-session close markouts and excursions | Same definitions using one completed-price snapshot from a later plan |
| Explicit forecasts | Entry model's return, adverse excursion, deferral advantage and lower-quartile deferral estimate | Unavailable for the current target-weight plan; never inferred from trend scores |
| Summary | Source/model/cost case/side/opportunity cohorts; asset, regime and reason breakdowns | Durable model/side summaries in the daily report; full filters in the shared viewer |

Paper plans are still inert JSON. The added `monitoring` object supplies observations and decision
diagnostics; it does not affect order sizing or authorize a new trade. The executor remains a standard
library process and imports no AMAT modules.

## Using the reports

### Research runs

New daily research workers automatically write these beside each `primary` and `stress` result:

```text
trade-monitor.json     Complete event records, forecast errors, cohorts and definitions
trades.csv             Flat event export, including maturity status for every horizon
trade-monitor.html     Self-contained interactive viewer
```

The main research report links **Trade performance monitor** under its analysis downloads. The
analysis exporter combines verified primary/stress artifacts under `analysis/trades/`, checks their
recorded hashes and counts repeated cached event identities once. Its manifest includes the input,
renderer and output hashes. Old frozen research sessions retain their original controller and do
not retroactively acquire missing instruction/forecast records.

The viewer filters source, run, model, asset, side, regime and event type. Search finds reasons,
order IDs and execution flags. Choose the outcome horizon, then use **Inspect** for the full
decision, execution and forecast evidence. The screen shows the latest 200 matching events; CSV
and JSON retain all events.

To combine existing research and paper evidence into a new local report:

```bash
cd /home/supasuge/Documents/amat-workspace/amat
./research trades \
  /absolute/research/session/outputs/runs/CANDIDATE/primary/trade-monitor.json \
  /absolute/paper/state/trade-monitor.json \
  --output outputs/trade-monitor
```

This command reads JSON only. It uses the current viewer without replacing an older experiment's
frozen runtime. It validates schema and duplicate event identity, and records input hashes; session
analysis additionally checks those hashes against the experiment's original evidence manifest.

### Paper trading

After the updated executor and AMAT code are running, the existing end-of-day report phase records:

```text
STATE_ROOT/trade-observations/YYYY-MM-DD.json   Durable inputs for offline replay
STATE_ROOT/trade-monitor.json                 Full event history and summary
STATE_ROOT/trade-monitor.md                   Readable model/side summary
STATE_ROOT/trade-monitor.lock                 Single-writer lock
```

The daily report includes this session's instruction/attention counts, historical coverage and
five-session outcome averages by model and side. Existing report delivery uses the same report
object. The CLI below does not submit orders, contact a broker or send mail:

```bash
cd /home/supasuge/Documents/amat-workspace/executor
.venv/bin/executor trades --state-root /absolute/paper/state
```

Use the generated JSON with `./research trades` for the interactive viewer and CSV. Back up
`trade-observations/` with the other durable state. It is outside the date-file report pruning paths.
One state directory represents one paper account; keep different accounts in separate reports.

Repeated reports replace that session's cumulative broker snapshot instead of adding the filled
quantity again. Previous plans are retained when a same-session plan changes, and later order
snapshots resolve against the original recorded plan. A failed broker refresh keeps the last
observed fills and marks the current order data unavailable. Invalid new price context is rejected
before replacing an existing observation. A monitor failure appears in the regular report's warnings.

New plans carry the last 65 completed sessions for every covered asset, including unheld assets.
This allows post-exit measurement and uses actual panel session dates across weekends and holidays.
Both endpoints and the entry adjustment factor come from a single snapshot, so a split or dividend
adjustment is not combined with a different day's historical adjustment basis.

Price outcomes generally appear with the next session's plan. There must be sufficient history,
valid prices and a verified fill timestamp. A long outage beyond the available history can leave
some horizons unavailable. Legacy plans without monitoring context cannot reconstruct those prices
or forecasts. Missing, pending and execution-mismatch states are visible rather than replaced by zero.

## Exact metric definitions

One basis point (bp) is 0.01%. Events are instruction-level observations; additions and partial
exits are separate events. These metrics do not assume that every fill is a completed position.

For a fill at raw price `P` in session `t`, define its adjusted entry basis from the same price
snapshot as `A = P × adjusted_close[t] / raw_close[t]`. Let `s = +1` for a buy and `−1` for a sell.

| Metric | Definition and interpretation |
|---|---|
| H-session markout | `10,000 × s × (adjusted_close[t + H − 1] / A − 1)`. One session ends at the execution-session close. |
| Favourable close excursion | Maximum of zero and all signed close markouts from entry through that horizon. |
| Adverse close excursion | Maximum of zero and the negative of the minimum signed close markout over that horizon; reported as a positive loss magnitude. |
| Decision-to-fill gap | `10,000 × s × (fill / reference − 1)`. Positive is an adverse price change. Backtests restate the decision close to the execution adjustment basis. Paper's initial reference is the raw decision close; corporate-action gaps can be present. Each event states its basis. |
| Date-weighted markout mean | Average each execution date's events first, then average dates equally. A many-asset rebalance does not receive extra date weight. |
| Positive markout rate | Fraction of matured events whose signed markout exceeds zero. This is an entry/exit timing metric, not a closed-trade win rate. |
| Forecast error | Actual target in bp minus the prediction recorded at the decision. Negative mean error indicates overprediction for that target. |
| Forecast MAE / RMSE | Mean absolute error / root mean squared error over matured predictions. RMSE gives large misses more weight. |

A positive sell markout means the asset declined after the sale. It does not describe a short
position's return. Markouts and close excursions exclude fees, bid/ask spreads and realized
position accounting. They may continue after the actual position was closed. Intraday highs/lows
before an entry are not used as evidence of an attainable excursion.

The entry model uses its original labels, separately from these close-based markouts:

- **Net return:** next-session execution window to the window after the configured 20 or 60 sessions,
  using adjusted prices and two bp each way: `(exit / entry) × (1 − 0.0002) / (1 + 0.0002) − 1`.
  This label definition remains two bp even when inspecting a six-bp portfolio cost case.
- **Adverse excursion:** worst loss using the entry-session close followed by the next four
  sessions' lows. The entry-session low could have occurred before execution and is excluded.
- **Deferral advantage:** next-window price divided by the following-window price minus one.
  This measures the price benefit of delaying one session under equal capital and common eventual exit.
- **Deferral lower quartile:** a conditional quantile estimate for that same target. The fraction
  of actual outcomes below the prediction is recorded; this is not a calibrated confidence interval.

Each head records its own maturity. A return prediction cannot be judged using an earlier close
merely because the position was sold before the prediction horizon. Baseline opportunities the
candidate did not act on are shown separately when the entry model is active. These price
counterfactuals help inspect missed opportunities but do not establish the causal value of a policy.

## Turning the measurements into improvements

| Repeated finding | Hypothesis to investigate | Test to run |
|---|---|---|
| Wrong quantity/side, unmatched order ID, late submission, partial or rejected fills | Execution or reconciliation issue | Replay original plan and broker evidence; change execution handling before changing the signal |
| Model follows instructions but negative forecast bias persists | Expected returns or timing benefits are overstated | Compare forecast targets and residuals by regime and asset on a later chronological block |
| Large early adverse move, later positive markout | Entry timing may be poor | Compare allowed one-session deferral against the same baseline opportunities and costs |
| Price continues up after sells | Exit timing may be early | Test a bounded exit-policy alternative while holding entries, universe and risk controls fixed |
| Positive short-horizon markout becomes negative later | Holding horizon or exit policy may be mismatched | Examine 1/5/20/60-session paths and actual position episodes, then test a new exit rule |
| Good aggregate results but weak stress/range cohorts | Regime dependence | Re-run the same candidate through the existing scenario and cost suite, including fat tails, gaps and liquidity stress |
| Recent outcomes deteriorate while older outcomes are positive | Model or execution behavior may have changed | Inspect source/model/asset mix and data coverage before attributing the change to model drift |

The viewer compares the latest 30 event dates with the previous 30. Cohorts with fewer than
30 matured events or 10 distinct dates are marked `insufficient_sample`. These are descriptive
minimums, not proof of statistical significance. Overlapping 60-session outcomes and simultaneous
asset trades share information. Use date/block resampling and chronological evaluation for
inference; keep an untouched final block when selecting improvements.

There is no automatic retuning, strategy promotion, trading halt or new notification schedule in
this monitor. Execution flags identify observed discrepancies; outcome comparisons generate research
hypotheses. In particular, standard EWMA control limits assume conditions that cannot simply be
applied to dependent trade observations. [NIST's EWMA guidance](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc324.htm)
describes those assumptions and the need for a representative historical reference.

## Further evidence needed for richer model judgments

The current paper model emits target weights and scores. To evaluate probability calibration,
a model must record a probability, the exact event it predicts and its horizon before execution.
Then compare forecast buckets with realized frequencies and use proper scoring rules such as
Brier or log loss alongside reliability diagrams. Brier score alone does not isolate calibration.
[Scikit-learn's calibration documentation](https://scikit-learn.org/stable/modules/calibration.html)
explains that distinction and the need for independent calibration data.

Other additions require real inputs rather than inferred substitutes:

- Broker activities, fees and corporate actions for economic P&L allocated across complete
  position episodes and partial exits.
- Quotes and actual fill timestamps for spread, arrival-price slippage and market impact.
- An explicit initial risk budget or stop definition for meaningful R multiples.
- An ex-ante predicted distribution or interval for coverage and tail-surprise measurements.

The existing account return, drawdown, tail-risk and trade-episode reports remain useful alongside
this instruction monitor. They measure different aspects of model behavior.
