# Trade reduction and group-specific risk: results

All 27 preregistered autoresearch trials completed with zero engine warnings. None of the eight challengers passed the retention rule. Daily core groups remains the current best and default.

## Controlled design

- Native eleven-ETF daily-group strategy, unchanged universe, group ranking, allocation, caps and signal horizons.
- Source: `alpaca-sip-core-2026-10-13-verified`; daily and observed opening windows from January 4, 2016 through October 12, 2026. Each account starts flat with $100,000 on January 3, 2017; prior prices provide signal warmup.
- Real observed 09:30–09:35 VWAP, next-session execution, split/dividend accounting and 1% opening-window participation limit. Transaction costs of 2/6/10 bps per side combine assumed commission/spread/impact; these are sensitivity assumptions, not historical bid/ask calibration. Cash yield is zero.
- Fixed rules evaluated over the full history, the continuing 2020-onward account, every calendar year, named stress windows, and months. No fitting, threshold search or annual strategy switching occurred. The history has prior research exposure; yearly forward evaluation is not an untouched holdout.
- Trade bands of 3%, 4% and 5% replace the 2% minimum target-weight change. Two combinations pair 3%/4% bands with group weakness. Trade bands can delay exits and re-entry, so fewer trades do not automatically mean better execution.
- Group weakness halves target weights only in a group whose mean 20-session log return is negative and fewer than half of whose members exceed their trailing 63-close mean.
- Group shock halves target weights only in a group whose mean five-session log return is below log(0.96).
- Group volatility halves target weights only in a group with negative mean 20-session return and 20-session volatility of equal-weight group daily log returns above 1.5 times trailing 60-session volatility.
- Group features use completed close-time information only. Risk cuts apply after native sizing, leave foregone exposure in cash and do not redistribute it into other groups. Actual changes still depend on native trading thresholds and fills.
- Retention requires net return at least equal to daily core groups in both full-history and 2020-onward comparisons at every cost, plus no worse full-history drawdown at 6 bps. The rule was frozen before execution.
- All three daily-core control paths reproduce every previously retained session value exactly (2,458 rows each), including equity, fees, exposure, fill count and missed count. Actual snapshot partition hashes, frozen source hashes and canonical result seals were checked.
- Protocol SHA-256: `80ac9713f736f281adc64263f4e5de7f642dc6ab09adc9065ec766e2561733c5`.

## Full-history returns and trading at realistic cost assumptions

| Approach | 2 bps net | 6 bps net | 10 bps net | 6 bps drawdown | Filled trades at 6 bps | Annual gross traded volume / NAV | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Trade beyond 3% drift | 261.62% | 221.41% | 182.48% | 16.56% | 1772 | 29.49× | rejected |
| 3% drift + group weakness | 182.22% | 148.65% | 125.46% | 16.94% | 1981 | 31.26× | rejected |
| Trade beyond 4% drift | 256.92% | 218.06% | 177.98% | 15.83% | 1602 | 28.63× | rejected |
| 4% drift + group weakness | 186.48% | 149.52% | 125.50% | 16.41% | 1819 | 30.60× | rejected |
| Trade beyond 5% drift | 258.67% | 214.78% | 175.86% | 16.98% | 1515 | 28.74× | rejected |
| Daily core groups · reference | 262.75% | 220.32% | 187.59% | 16.57% | 2024 | 30.21× | incumbent |
| Group shock filter | 231.13% | 193.08% | 163.05% | 17.74% | 2164 | 31.81× | rejected |
| Group volatility filter | 245.36% | 209.38% | 177.16% | 16.86% | 2044 | 30.26× | rejected |
| Group weakness filter | 180.40% | 142.33% | 120.69% | 17.78% | 2224 | 31.95× | rejected |

The 3% band cut filled trades by 12.45% at 6 bps, but annual traded volume only fell by about 2.38%. It mostly removed small trades. Return improved by 1.09 percentage points at the middle cost but declined by 1.13 points at the low cost and 5.11 points at the high cost. It is not a stable improvement and was removed.

The wider bands saved additional trade count but lost return. Group filters often increased turnover by adding partial exits and re-entry. The group-weakness filter traded more frequently and substantially reduced growth; lower dollar fees on a smaller, slower-growing account should not be confused with improved execution efficiency. Normalized traded volume is reported alongside dollar costs.

## Yearly forward evaluation at 6 bps

| Year | Daily core | 3% band | Group weakness | Group shock | Group volatility |
|---|---:|---:|---:|---:|---:|
| 2020 | 20.44% | 19.40% | 13.78% | 17.61% | 23.53% |
| 2021 | 18.34% | 17.94% | 11.32% | 15.38% | 18.31% |
| 2022 | 8.86% | 8.76% | 9.43% | 6.48% | 8.28% |
| 2023 | -6.77% | -6.84% | -8.96% | -6.90% | -7.09% |
| 2024 | 13.93% | 15.18% | 10.90% | 13.14% | 12.14% |
| 2025 | 32.74% | 33.48% | 31.26% | 31.95% | 30.39% |
| 2026 (partial) | 24.86% | 24.32% | 20.18% | 24.82% | 24.20% |

## Stress-window returns at 6 bps

| Window | Daily core | 3% band | Group weakness | Group shock | Group volatility |
|---|---:|---:|---:|---:|---:|
| 2018 Q4 | -5.01% | -5.00% | -4.64% | -5.01% | -5.01% |
| Feb 19–Apr 30, 2020 | -3.62% | -4.44% | -2.77% | -1.47% | -1.13% |
| 2022 | 8.86% | 8.76% | 9.43% | 6.48% | 8.28% |

## Uncertainty

The saved decisions include a paired three-month block bootstrap of monthly log-return differences, 5,000 resamples with a fixed seed. Intervals describe annualized log-return differences at 6 bps over full history; overlapping market regimes and repeated use of these dates limit inferential strength. The 3% band interval is [-0.57%, 0.55%], spanning zero. A slightly better middle-cost historical outcome is not evidence of a reliable improvement.

## Removal and overall default

All eight challengers are rejected and absent from the active dashboard strategy list. No challenger production profiles were installed. Fixed definitions, sealed measurements and rejection decisions remain only as reproducible audit evidence.

Daily core groups is now the common default for guided actions, scans, historical backtests, ordinary autoresearch initialization and the executor fallback. The historical shortcut supports `daily-core` and defaults to it, uses the complete verified core dataset when available, preserves pre-start signal history, and opens its account at the requested executable session. Independent single-symbol sleeves are disabled for this shortcut because group selection needs its whole universe.

The local executor environment selects the daily-core profile and its matching authorized digest. Its existing DRY_RUN=true and EXECUTOR_DISABLED=true settings were preserved; no daemon or submission action was started. The Find Assets default uses the configured group context rather than the incompatible independent-asset duration sweep. Explicit historical configurations remain selectable as references.

## Remote instance

The configured remote host was reachable, but SSH authentication was denied for both available local keys. No remote files, services or trading state were changed. The remote update/rebuild was skipped under the user’s instruction to ignore it if usable saved credentials were unavailable.

## Dashboard and reproduction

The default analysis now opens “Daily core groups · fewer trades & group risk.” It shows net growth, drawdown, monthly returns, yearly forward evaluation, stress windows, costs and normalized turnover. Rejected experiments, all cost comparisons and confidence intervals are collapsed under an audit disclosure.

```sh
PYTHONPATH=amat/src amat/.venv/bin/python amat/scripts/run_efficiency_study.py \
  --output amat/outputs/analysis/NEW-IMMUTABLE-STUDY
```

No settings were tuned after examining these results. A next experiment should target actual high-notional, low-benefit switching or rank stability, with measured spreads and execution-event evidence, rather than assuming that reducing the count of small trades will improve returns. No additional candidate has been installed on that hypothesis.

## Verification

- 27 sealed trials and candidate-decision evidence verified; zero engine warnings.
- Exact baseline session parity with the previous long-history study at all three costs.
- 74 targeted strategy/CLI/sleeve tests, 54 API tests and all 353 executor tests passed.
- Ruff and targeted strict type checks passed; the production frontend rebuilt successfully.
- A real offline default `amat backtest` produced a 2019 report using full 2016 signal context and an account opened in January 2019. The current-profile read-only scan also completed.
- The local executor profile and authorized digest match, with dry-run and submission-disable preserved. No broker action was dispatched.
