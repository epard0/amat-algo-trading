# Live runtime and data-quality audit — 2026-08-26

Scope: production live ledger, decision log, run log, active processes, and the
source loaded by the 2026-08-26 01:53 UTC live session. All production checks
were read-only. Counts below are point-in-time observations through roughly
02:17 UTC and will increase while that process remains active.

## Executive finding

Kalshi REST was intermittently slow, but the largest failure multiplier was
local: the websocket callback synchronously wrote extremely high-rate telemetry
to the same SQLite connection and asyncio thread used by discovery, balances,
position service, and orders. The current live run accumulated 1,138,509 raw
book deltas and 53,012 public trades in about 24 minutes. Its 3.5 GiB ledger had
a 181 MiB WAL. A BTC order was recorded as submitted at 02:10:15.741 UTC and
was rejected for `header_timestamp_expired` at 02:10:52.233 UTC, a 36.49-second
local/transport delay. Host NTP was synchronized.

The running process started at 21:53:09 EDT. The prior reliability edits to
`kalshi_client.py` and `bot.py` were saved at 21:54:17 and 21:54:43, so that PID
cannot contain those changes; Python does not hot-reload imported modules.

## Severity and trade impact

| Finding | Severity | Direct effect |
| --- | --- | --- |
| Synchronous raw websocket persistence | Critical runtime | Starves all async work, stretches passes beyond entry windows, and ages signed requests |
| Indexed balance divided by 100 | Critical execution | Displayed real ~$68/~$13 shard balances as $0.68/$0.13 and could falsely suppress affordable orders |
| Total discovery timeout | High availability | Existing positions remain supervised, but every new entry is deliberately blocked until fresh discovery |
| Partial discovery timeout | High opportunity | Previously removed failed assets from the cycle even when their cached contract was still open |
| `header_timestamp_expired` | High opportunity | One observed eligible BTC order was rejected without a fill |
| Balance timeout | Medium/high opportunity | Position service continues; only new scalp entries are blocked for that pass |
| Commodity candle timeout/stale candle | Medium opportunity | Only the affected asset is skipped; crypto and held-position supervision continue |
| Settlement-status timeout | Low/temporary | Accounting is deferred; the observed BTC position later settled WON for +$3.26 |
| Strict telemetry capture timeout | Low direct, high analytic | Trading continues, but snapshot/portfolio completeness suffers; old code also let this call delay the cycle |

## Live trade evidence

The live ledger contains 33 decided/exited trades: 14 WON, 7 LOST, and 12
EXITED. Recorded net P&L is +$18.36. Scalper attribution is 25 trades and
+$15.84; predictor attribution is 8 trades and +$2.52. Settled-only win rate is
66.7% for both strategies, but these samples are too small for promotion or
stake increases.

By asset, GOLD is +$47.23, BTC +$21.95, ETH +$7.97, NEAR +$1.66, WTI -$1.64,
and SILVER -$58.82. SILVER is the dominant adverse concentration and has zero
settled wins in five live trades. This is a warning, not a statistically stable
asset ranking.

The newest BTC position was monitored correctly: 15 NO contracts filled at an
actual average 0.77 (the log's 0.83 was the submitted limit), total debit
$11.74, and settled WON for +$3.26. The position-evaluation table contains 670
reassessments. This verifies that the active-winner hold controls were reached;
the outage delayed settlement status but did not strand or prematurely sell
the position.

Twelve historical exits realized +$7.30. Perfect hold-to-settlement would have
produced +$34.44, a +$27.14 difference; $18.46 of that difference is the single
SILVER `confirmed_reversal` loss whose production path is now disabled. The
remaining take-profit difference is +$8.68 across eleven trades. It is useful
counterfactual evidence, not proof that every exit should be removed: an exit
also releases capital and can avoid losses in a different sample.

## Decision and market-data quality

The current-run JSONL is valid and contains 1,460 relevant records: 431 full
predictor decisions, 662 take-profit evaluations, 358 pair-arb scans, one scalp
fill, and four discovery outage/recovery events. Predictor gates were dominated
by `entry_window` (339), followed by model/market divergence (26), stale candle
(24), minimum probability (20), minimum time (12), minimum edge (7), degraded
discovery (2), and settlement margin (1). Twenty-two decisions took over three
seconds; the maximum was 24.4101 seconds.

The normalized SQLite `signal_evaluations` table has only 60 rows for the same
run, or 13.9% of the 431 JSONL decisions. This is expected for early gates that
run before a probability exists, but it means SQL-only funnel analysis is
incomplete. Use `decisions.jsonl` for full refusal analysis and SQLite for
strict probability-bearing rows.

Periodic snapshot coverage is balanced across all 12 assets: 27–32 snapshots
per asset (347 total). Most are websocket-derived. Average book age is 2.7–3.9
seconds by asset; the maximum is about 13.8 seconds, beyond the configured
10-second stale threshold. The live run's raw events have complete sequence
numbers; public trades have complete exchange timestamps and trade IDs.

Three old `data_runs` rows remain marked RUNNING after their processes ended.
This is a lifecycle-quality issue for run-level reporting, not evidence that
three live bots are active. At audit time there was one live process and one
paper process; their state directories and SQLite files were separate.

## Implemented remediation

1. Raw websocket deltas still update and trigger the in-memory executable book,
   but persistence defaults off. Periodic full-depth snapshots remain enabled
   for every configured asset.
2. Public trades remain enabled and are committed in one-second/500-row batches
   rather than one FULL SQLite transaction per print.
3. Current fixed-point dollar strings in `balance_breakdown` are no longer
   divided by 100. The selected exchange-index value is authoritative at the
   final serialized order boundary.
4. A pre-admission `header_timestamp_expired` rejection is retried once with
   fresh auth headers and the unchanged deduplicating `client_order_id`.
5. Partial discovery failures retain still-open cached markets only for failed
   assets. Those assets remain supervision-only; fresh assets can still enter.
6. Position-service phases now identify fallback pricing, strategy-lab work,
   salvage, settlement, exit reassessment, quote service, balance verification,
   and new-entry assessment separately.
7. Strict-capture warnings now include the exception class even when
   `TimeoutError.__str__()` is empty.

Defaults:

```dotenv
RAW_BOOK_EVENT_RECORDING_ENABLED=false
PUBLIC_TRADE_RECORDING_ENABLED=true
ORDERBOOK_RECORDING_ASSETS=ALL
```

Use raw deltas only in a dedicated collector with an isolated
`KX15_STATE_DIR`; do not combine that workload with live execution.

## Verification

- Focused reliability/data tests: pass.
- Full project regression suite: pass.
- Ruff on changed Python/tests/docs: pass.
- System clock: synchronized by NTP.
- Official SDK model checked locally: aggregate `balance` is integer cents;
  nested indexed `balance` is a fixed-point dollar string.

## Post-restart smoke observation

The operator subsequently stopped the affected PID and started a new live run
at 02:28:39 UTC, after the fixes were saved. Startup now reports aggregate
$84.08 and correctly scaled indexed balances of $67.73 and $16.36. In the first
roughly 107 seconds the run recorded 147 periodic market snapshots, 157 strict
signal rows, 950 batched public trades, **zero** raw book-event rows, and zero
warning/critical data-quality events. The WAL was about 4.1 MiB rather than the
prior 181 MiB. No HTTP error or strict-capture warning appeared in that initial
window. This is a smoke check, not a long-duration availability claim; leave
the current process running and judge sustained behavior from later hourly
summaries and the phase heartbeats.
