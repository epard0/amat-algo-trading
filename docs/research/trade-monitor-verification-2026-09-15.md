# Trade monitor verification — 2026-09-15

## Delivered evidence

- [Interactive report: real backtests and explicitly labelled paper fixtures](../../amat/outputs/validation/trade-monitor-2026-09-15/results/joint-view/trade-monitor.html)
- [Complete event CSV](../../amat/outputs/validation/trade-monitor-2026-09-15/results/joint-view/trades.csv)
- [Complete event JSON and input hashes](../../amat/outputs/validation/trade-monitor-2026-09-15/results/joint-view/trade-monitor.json)
- [Replay metadata and forecast statistics](../../amat/outputs/validation/trade-monitor-2026-09-15/results/verification.json)
- [Frozen program manifest](../../amat/outputs/validation/trade-monitor-2026-09-15/program.json)
- [Replay script](../../amat/outputs/validation/trade-monitor-2026-09-15/verify.py)
- [Metric definitions and operation](trade-performance-monitoring.md)

The backtest replay used cached, inspected development data from
`alpaca-sip-core-2026-09-14-verified`: 11 assets, 1,762 source sessions, measured from
2018-01-02 through 2022-12-30. The immutable runtime was
`/tmp/amat-trade-monitor-verification-80tiaxoe/frozen`, program digest
`b059ea6b13b4` (full digest in the manifest). No later validation prices were loaded.
The replay took 351.66 seconds while the regression suite was also running.

| Candidate | One-way portfolio cost | Recorded instruction events | Events with an explicit forecast |
|---|---:|---:|---:|
| Frozen v6 baseline | 2 bp | 1,493 | 0 |
| Frozen v6 baseline | 6 bp | 1,496 | 0 |
| V6 plus default entry-quality forecast policy | 2 bp | 1,493 | 785 |
| V6 plus default entry-quality forecast policy | 6 bp | 1,496 | 786 |

These are 5,978 instruction events across four diagnostic runs, not 5,978 independent positions.
The default entry policy made **zero entry reductions** in this replay. The extra candidate exposes
forecast behavior; it does not demonstrate a portfolio improvement over the baseline.

The joint viewer also includes **two paper fixtures** under the distinct `paper_fixture` source.
They demonstrate a full buy, a partial sell, a split and a five-session outcome crossing a weekend.
They are not observations from an actual paper account. Production paper capture starts when the
updated AMAT and executor code run the existing daily report flow.

## Example findings to investigate

For the entry-quality candidate's two-bp portfolio case:

| Forecast target | Matured events / distinct execution dates | Mean prediction | Mean actual | Mean actual minus prediction | MAE |
|---|---:|---:|---:|---:|---:|
| 60-session asset return, two bp each way | 749 / 526 | 198.40 bp | 158.93 bp | −39.48 bp | 794.60 bp |
| Five-session adverse excursion | 781 / 548 | 121.90 bp | 142.50 bp | +20.59 bp | 128.74 bp |
| One-session deferral advantage | 784 / 550 | −3.96 bp | +0.25 bp | +4.21 bp | 82.92 bp |

The 60-session return forecasts were optimistic on average in this sample; adverse excursion
was underestimated on average. These observations justify inspecting residuals by regime and asset.
They do not establish that a replacement model would improve performance. The evaluation uses
previously inspected development history, event-weighted forecast errors and overlapping horizons.
Compare a proposed change with a simple forecasting benchmark and unchanged execution/risk controls
on a later chronological block before accepting it.

## Checks

- Broad AMAT regression run: **1,993 passed, one existing skip**. One additional test deliberately
  refused to run after source changed during the suite (`controller changed`). The same complete
  research-loop/resume/evidence-export test passed in isolation after code settled: **1 passed**.
- Final affected AMAT tests: **82 passed** (monitor, telemetry, report presentation, CLI shortcuts,
  execution plans and terminal decisions). Additional final monitor coverage: **10 passed**, including
  baseline opportunities that produce no candidate order.
- Final executor suite: **177 passed**, including the actual offline v6 plan-to-executor integration.
  The integration used `UV_CACHE_DIR=/tmp/amat-trade-monitor-uv UV_NO_SYNC=1` so uv could use writable
  scratch space without changing the installed dependency environment.
- Ruff lint passed for both projects. Strict mypy passed for **162 AMAT** and **15 executor** source
  files. `git diff --check` passed.
- Tests cover pending horizons, exact forecast-label maturity, split normalization, buy/sell signs,
  close excursions, lost/missed instructions, cumulative partial fills, order identity, recorded
  quantity adjustments across later reports, invalidated execution evidence, future/duplicate price
  frames, daily-report retention, date-weighted summaries, input-hash validation, duplicate events,
  HTML escaping, CSV formula protection and credential-free offline replay.
- The exported viewer's JavaScript passed `node --check`. **Visual browser validation is unverified:**
  a local socket could not be opened in the sandbox, and the browser URL policy rejected direct
  navigation to the generated local HTML file. The policy was not bypassed.

The replay manifest describes the calculation snapshot used for its evidence. Subsequent additions
to report links, paper decision context and reconciliation edge cases were checked by the final
focused and executor tests above. Older frozen research programs remain unchanged.

## Replay

Use a fresh output path; the script refuses to overwrite existing evidence:

```bash
cd /home/supasuge/Documents/amat-workspace/amat
AMAT_TRADE_MONITOR_OUT=/tmp/amat-trade-monitor-replay \
  .venv/bin/python outputs/validation/trade-monitor-2026-09-15/verify.py
```

This rebuilds the four backtest cases from a new frozen runtime. The paper rows in the joint example
are separate fixtures; the executor tests verify that adapter without account access.
