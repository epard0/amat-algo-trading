# MNQ indicator research

The existing funded replay now accepts indicator strategies as well as ORB. Strategy definitions are independent implementations informed by [SharpeEdge](https://github.com/pietrobelli26/sharpe_edge_code/tree/afda167339d296c1c9fc89ea157fe5f70862c339), inspected at the pinned commit. Upstream notebooks were read as text, never executed. Kalman and Bollinger concepts were adapted; upstream FX/index observations and reported performance were not treated as MNQ evidence. Logistic regression, SVM, and synthetic prop-firm Monte Carlo remain future research rather than implemented families.

| Family | Entry at completed minute close |
|---|---|
| EMA/ADX trend | EMA12/26 crossover with ADX14 ≥20 |
| MACD momentum | MACD12/26/9 histogram crosses zero in agreement with EMA26 |
| RSI reversal | RSI14 crosses back above30 or below70 |
| Bollinger reversion | Price reenters SMA20 ±2 sample standard deviations |
| VWAP reversion | Price reenters session VWAP ±ATR14 |
| Donchian/volume breakout | Break prior20-bar high/low with volume ≥1.2× prior20-bar average |
| Kalman trend | Forward local-linear slope crosses ±0.05×ATR14, agreeing with session VWAP |

Each family tests 1.5/2.5 ATR stops and 1R/2R targets: 28 configurations. RSI and ATR use recursive alpha1/14 smoothing initialized from the session; a 40-minute warmup precedes entries. Kalman covariance parameters are fixed in price units (observation variance4, slope process variance0.01), not fit to the full sample. Indicators reset daily. No backward filling or smoothing with future observations is permitted. Signal-time ATR remains frozen through execution.

Shared execution: next-open fills, adverse slippage each side, stop rounded away from entry to MNQ ticks, integer contracts, $100 intended risk, maximum4 contracts, one entry/day, entry before12:30ET, flat15:59ET. Gaps may exceed intended risk. Take-profit orders use conservative adverse exit slippage; stops are processed first when minute OHLC ordering is ambiguous. No signal-triggered exit or overnight position.

Selection uses training dates before2026-03-02 only, three cost assumptions and 1/2-minute delays. At least10 training entries per scenario are required. Each family's selected configuration maximizes the minimum chronological-block mean and descriptive bootstrap lower-tail mean. The five-session moving-block bootstrap is not a multiplicity-corrected significance test. Family selections are saved before validation starts. Validation has already been researched; it is not a fresh holdout.

```sh
.venv/bin/python -m autoresearch.funded_indicator_study \
  --root outputs/NEW-INDICATOR-STUDY \
  --source outputs/mnq-apex-research-2026-10-14
```

Every study requires a fresh directory. Protocol, source/engine hashes, training trials, family selection and validation results persist as JSON. Completed studies append immutable evidence and a snapshot to funded-research-state.sqlite3 without replacing existing champion history. More favorable validation performance alone does not promote a strategy. Gemini remains unused while its previously diagnosed generation quota is zero.

Initial study: outputs/mnq-indicator-families-2026-10-14/REPORT.md. No configuration passed positive training robustness. Funded target passes: zero. Research only; deployment disabled.

## RSI with Kalman gates

`python -m autoresearch.funded_rsi_kalman --root outputs/NEW-RSI-STUDY --source outputs/mnq-apex-research-2026-10-14`

Tests 34 predefined configurations: plain RSI with1R/2R targets, plus four Kalman gates × two normalized slope thresholds(.05/.15) × two fixed slope process variances(.001/.01) × two targets. All retain RSI14 30/70 recross signals, ATR1.5 stops and risk$100. Gates are aligned, countertrend, calm, and no strong opposition. Slope is divided by causal ATR14. Filter observation variance stays4. No fitted model, backward smoothing, future data, or validation-based selection is used. Family choices require ten training entries in each cost/delay scenario.

`python -m autoresearch.funded_rsi_diagnostics --root outputs/NEW-RSI-STUDY --source outputs/mnq-apex-research-2026-10-14`

After selection freezes, diagnostic comparisons pair each selected filter with an otherwise identical plain-RSI control. They replay both1/2-minute delays and three cost scenarios, report full-period P&L, paired descriptive bootstrap differences and largest-winner removal. This step never changes selection. Protocols, results and evidence append to the shared state database without changing prior best histories.

Completed findings: `outputs/mnq-rsi-kalman-2026-10-14/FINDINGS.md`. Countertrend improves relative results but remains stress-cost negative at the default delay; calm is sparse. No positive training robustness gate or funded target passes.
