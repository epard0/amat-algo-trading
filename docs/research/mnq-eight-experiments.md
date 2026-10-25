# Eight controlled RSI/Kalman experiments

Run:

```sh
.venv/bin/python -m autoresearch.funded_experiments \
  --root outputs/NEW-EIGHT-EXPERIMENT-STUDY \
  --source outputs/mnq-apex-research-2026-10-14 \
  --external outputs/mnq-public-june2025-2026-10-14
```

The source has 62 complete matched Last/Bid/Ask sessions for the public MNQ June2025 contract label, March17–June13. Raw CSV files are pinned to the Git commit, SHA256 checked, and downloaded without executing upstream code. Repository duplicate directory copies are deduplicated by canonical shortest path. Timestamp duplicates must match exactly. Invalid OHLCV, tick increments, missing sessions and changed bytes are rejected or abstained as appropriate.

This is newly used public research data, not independently verified exchange data. New York timezone and minute-end timestamps are inferred from the maintenance gap and volume pattern; minute-start interpretation is also replayed as a sensitivity check. Contract identity is a directory label, not an independently authenticated instrument ID. No paid market-data key was available in checked environment configuration. Bid/Ask series are separate minute OHLC observations, not synchronized quote events; source audit records crossed minute-open pairs. Actual event-level spread, fill latency and stop ordering cannot be established from these files.

The protocol freezes thirteen configurations before training and external evaluation: plain and countertrend RSI controls; one-contract caps; model-standardized Kalman slope thresholds2/4; a two-bar slope-deceleration confirmation with a ten-minute expiry; and30/60-minute or RSI50 exits. Risk remains$100, ATR stop1.5, maximum4 contracts, one entry/day. Fixed one-contract variants abstain when one contract exceeds the risk ceiling. The uncertainty threshold is a model diagnostic, not a calibrated probability.

All configurations replay base/adverse/stress costs with one/two-minute entry delays. Quote approximations execute entries using ask for longs and bid for shorts, mark/exit on the opposite side, and retain the same adverse residual slippage and commissions. Fixed-R target/stop trades can have identical P&L despite changed quote entry prices when the same barriers are hit; quote differences must be evaluated through actual replay rather than subtracting an extra spread twice. RSI50 exits act at the next open after a completed-close trigger. Holding limits exit at the scheduled open, with prior stops/gaps processed conservatively.

Subgroups attribute actual control trades by direction and signal before/after11ET; they do not select or add subgroup filters. Fewer than20 trades marks a subgroup as insufficient.

Multiplicity accounting replays archived and current recorded policies on the same original training sessions. A seeded, centered five-session moving-block bootstrap computes a maximum standardized mean over the policy matrix and adjusted p-values. This approximate test is not the Deflated Sharpe Ratio and does not correct unknown unrecorded trials or prove stationarity. The promotion gate requires ten training entries per scenario, positive worst-block/bootstrap training score, and adjusted p≤.05. External and old-validation observations are never used to select a configuration.

Protocols, selection, full daily paths, subgroup diagnostics, historical trial hashes and multiplicity results persist in a fresh directory. Completed results append to the shared research-state database without replacing previous champions. Experiments1/2 are explicitly partial until independently verified history and synchronized event-level quotes are available. Deployment remains disabled.

Sources: [public source](https://github.com/mbytes21/MNQ_DATA/tree/fc5508e2c152938d6d9eb70a36b888ae26107176), [selection-bias research](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).

The external2025 period precedes the2026 training sample. Its purpose here is a previously unused regime-stability test; it is not a forward walk-forward test. One source day was inspected for format/timezone auditing before metric evaluation. These qualifications remain part of every profitability interpretation.
