# Source notes and chart map

Snapshot time: 2026-08-25 17:03:12 UTC (1:03:12 PM America/Detroit).

The report is technical and read-only. No runtime process, setting, ledger row, or order was changed. The current live run continued while the audit was performed.

## Cohorts and definitions

- Baseline: live data runs starting from `2026-08-24T21:12:49Z` and before `2026-08-25T06:09:41Z`; 8.89 observed hours.
- Post-change: live data runs starting at or after `2026-08-25T06:52:00Z`; 10.16 observed hours at the snapshot.
- Filled entry: an `orders.intent='entry'` row whose latest `order_transitions.state='filled'`.
- Current run: `8e1c44d9-9c3a-4364-bc59-4f2355fd1010`.
- Touch mismatch: `max(abs(discovery_yes_bid - max(depth_yes_bids)), abs(discovery_yes_ask - (1 - max(depth_no_bids))))`.

The exact pre-change settings cannot be reconstructed because `data_runs` persists only a config hash, not the secret-free canonical configuration payload. This is a provenance limitation and one of the recommendations.

## Chart map

1. Throughput section — question: did actual trade fills decline after the implementation changes? Family: horizontal bar. Fields: cohort and filled entries per observed hour, with attempts/rejections/hours retained in tooltips. Claim: throughput fell about 87%. Palette: single-root blue. Artifact: `report.html`.

The predictor, book-consistency, and scalp-funnel datasets remain embedded in `artifact.json` for auditability; their conclusions are presented as prose in the portable report to keep the validated reader within its responsive width constraints.

All bar scales start at zero. Category bars are single-series with no redundant color legend. Each chart has an adjacent explanatory paragraph in the report.

## Validation notes

- Targeted tests: `tests/test_kalshi_book_stream.py`, `tests/test_safe_mode.py`, `tests/test_scalp_runtime.py`, and `tests/test_placement.py` passed (36 tests).
- A direct strict-ledger reproduction of `record_data_quality_event(event_type='websocket_connected', severity='info')` raised the expected SQLite `IntegrityError`, confirming the fake-ledger test gap.
- Ruff reported 34 unused imports, concentrated in legacy/unreachable clusters.
- Full regression suite: `1244 passed in 248.54s`.
