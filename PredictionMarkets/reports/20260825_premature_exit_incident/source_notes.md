# Source notes and report QA

Snapshot time: 2026-08-25 21:17:29 UTC (5:17:29 PM America/Detroit).

Audience: technical. Delivery mode: portable HTML because no report-rendering MCP or Sites lifecycle is available in this runtime.

## Required-structure map

1. Title: artifact title and first markdown block.
2. Technical summary: `technical_summary`.
3. Key findings with evidence: `cause`, `economics_intro`, `pnl_comparison_chart`, `economics_table`, `venue_result`, and `timeline`.
4. Scope, data, and definitions: `scope`.
5. Methodology: `method`.
6. Limitations and robustness: `limitations`.
7. Recommended next steps: `next_steps`.
8. Further questions: `further_questions`.

## Metric definitions and reconciliation

- Realized exit P&L: `(35 * 0.49) - 0.6125 - 28.40 = -11.8625`.
- Finalized-YES hold P&L: `(35 * 1.00) - 28.40 = 6.60`.
- Value destroyed by the exit: `6.60 - (-11.8625) = 18.4625`.
- The user described roughly $31 of profit. The normalized ledger supports 35 contracts, a $35 gross settlement payout, and $6.60 net settlement profit. The report uses the auditable ledger values.

## Evidence and caveats

- Controlling trade source: `trade_ledger.db`, trade id `0ecc68ca-915f-48b9-a8d4-83ed80330da3`.
- Policy reason source: `decisions.jsonl`, exact `confirmed_reversal` verdict at `2026-08-25T17:53:40.362469Z`.
- Result source: Kalshi public market endpoint for `KXSILVER15M-26AUG251400-00`, read at report snapshot time.
- The market result is authoritative. The intermediate model probabilities and bids are observations and do not guarantee an outcome.

## Chart map

1. Economics section — analytical question: how did the realized early-exit loss compare with the now-known finalized-settlement profit? Takeaway: the exit converted a $6.60 winning settlement into an $11.8625 realized loss, a difference of $18.4625. Family: comparison; canonical type: bar. Fields: scenario and absolute dollar magnitude, with gross value, signed P&L, debit, fees, and difference retained for tooltips. Row count: two intentional scenarios, each explicitly labeled as loss or profit; a trend or scatter would be misleading. Delivery: `report.html`.

## Validation

- `tests/test_take_profit.py`, `tests/test_mode_switch.py`, and `tests/test_salvage_runtime.py`: 60 passed.
- Full pytest suite: reached 100% with no failures.
- Targeted Ruff check for changed Python files: passed.
- Repository-wide Ruff remains non-clean due to 48 pre-existing legacy/notebook findings outside this change; none are in the changed Python files.
- The running simulation process was not stopped or restarted.
