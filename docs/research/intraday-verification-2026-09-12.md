# Intraday research implementation verification — 2026-09-12

Implementation supports model-authored full replacement recipes, strict retained-best
ancestry, 1/5/10/15/30/45/60-minute decision and holding horizons, causal minute
features, subsequent-open execution, normal/cost/delay comparisons, trade-level
metrics, outcome reviews, signal archives, and post-search checks.

- Broad regression pass: **169 passed**, one slow integration test deselected.
- After the final hosted-provider compatibility fix: **42 focused tests passed**.
- Changed production modules pass Ruff and mypy.
- Tests cover future-data isolation for every feature, all seven holding horizons,
  next-open costs, gap-aware stop execution, short P&L and fee accounting,
  incomplete asset-days, missing calendar days, data hashes, exact recipe identity,
  best-parent ancestry, escalation feedback, deferred validation and resume
  checkpoint integrity.
- Live NVIDIA check returned a complete valid full strategy recipe after removing
  the unsupported self-hosted thinking-budget parameter. Hosted low-effort calls
  use supported no-thinking mode to preserve structured response capacity.
- Data: 2,968,688 cached SIP minute bars including extended hours. The development
  panel uses 753 regular trading sessions in 2022–2024, with 2,895 complete
  asset-days and 117 exclusions across SPY, QQQ, IWM and GLD (96.12% coverage).
  The existing daily SPY session calendar exposes wholly missing days; minute
  prices are not forward-filled.
- Daily E73 remains the operator-selected daily research baseline. The minute
  benchmark is a separately measured E73-inspired recipe, not E73's daily results.

The first smoke attempt caught a raw-column mapping mistake, which was corrected
and covered by a real-Parquet test. A subsequent smoke run exercised the full
reporting path. A two-candidate calibration batch exposed NVIDIA hosted API
incompatibility; it was stopped and preserved at
`/home/supasuge/Documents/amat-research/intraday-50-20260912` and is not part of the
requested final 50. Its program and measurements were not rewritten.

The clean authorized batch is at:

`/home/supasuge/Documents/amat-research/intraday-50-final-20260912`

This document records implementation verification, not completion of that batch.
The batch's final `verification.json` and `results-overview.html` will record the
actual completion count, independent artifact/accounting checks and results.
