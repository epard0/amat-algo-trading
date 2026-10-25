# October 22 planning incident and October 23 readiness

## October 22 cause

The remote journal records three submit/planning failures at 09:20, 09:25 and 09:30 Detroit on October 22. Each stopped at `require_reconciled_portfolio` before account-driven plan construction, with `fresh reconciled portfolio observation required`. After three attempts, that session exhausted its retry budget. There was no October 22 execution plan; this was not a valid no-trade strategy decision.

The reconciliation journal first became discrepant October 21 at 09:36, with no share differences and a cash difference of **-$0.01644815129**. Broker fractional fills posted independently rounded cent cash, while the original reconciliation accumulated unrounded quantity times price. The discrepancy exceeded the existing $0.01 cash tolerance, so the fail-closed submission guard correctly blocked planning and orders on October 22.

A per-fill, nearest/ties-to-even cent-rounding repair was already deployed October 22 at approximately 20:46 Detroit, restoring exact matching. This review verified that repair against the preserved October 8 baseline and 18 incremental activities, reproduced the old -$0.01644815129 difference, and confirmed the repaired cash difference is $0.00 with no position differences. The original baseline is unchanged; no tolerance was widened, no account was rebased and no historical plan or order was backfilled. The recorded-fill, rounding-context and discrepancy regression tests pass.

## Additional freshness issue found and fixed

An October 23 preparation rehearsal explicitly requesting October 22 data received HTTP 403, `subscription does not permit querying recent SIP data`, with a date-only daily-bars end. The same SIP credentials and endpoint returned October 22's completed SPY bar with an explicit daily-bar timestamp (`2026-10-22T04:00:00Z`), and with `2026-10-22T20:15:00Z`. The date-only October 21 control also returned HTTP 200. Server Date headers confirmed the investigation was after the October 22 close.

The daily adapter now sends exchange-local midnight session stamps converted to explicit UTC timestamps for inclusive daily-bar selection. Daily bars are indexed by these timestamps; caller-side completed-session checks remain required. This avoids requesting still-future intraday time through date-only expansion. SIP, raw/adjusted separation, original completion checks and failure-on-missing coverage remain intact. Two DST-sensitive request-boundary tests plus the existing read-only boundary suite pass (270 tests total). The remote source was patched atomically with an original source backup and before/after hashes; no executor restart or trading-mode change was needed.

## October 23 verified plan

The verified plan was saved at **23:01:47 Detroit on October 22**, before the next session. It is at `STATE_ROOT/plans/2026-10-23.json`, where the daemon reuses the frozen original authorization. A verification receipt is stored separately.

| Check | Result |
|---|---|
| Decision session | October 22, 2026 |
| Execution session | October 23, 2026 |
| Raw and adjusted SIP coverage | All 11 strategy assets; 24 completed sessions appended to the September 19 snapshot |
| Current best | v14.3.0 all groups + volatility-triggered switching |
| Configuration SHA256 | `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8` |
| Native decision parity | Same original intents as native scheduler at configured cost |
| Planned orders | **0 — valid hold decision** |
| Account reconciliation | Exact cash and share match; $0.00 cash difference |
| Execution policy | Enabled paper, `staged-v1`, whole-share purchases |
| Policy SHA256 | `8c7ddc218b1f8c1e3cc6dafe031543588d5d59a1ccbb664914268a4ac10e9ad2` |
| Original-plan SHA256 | `0454430804e9c8e08e8abb7c2485087fcc351391d4972c3aabe023417a1a786e` |
| Orders placed in preparation | 0 |

The strategy's existing positions remain within its rebalance thresholds, so no unnecessary trades were manufactured. Existing fractional holdings remain exit-able, and the execution policy rounds new purchases to whole shares. A zero-order session does not demonstrate realized whole-share fill parity; that remains an execution observation rather than a claim from this plan.

## Dashboard and ongoing monitoring

The dashboard has an October 23 readiness card with the verified data date, asset count, zero-order hold decision, exact account match and native decision comparison. Root-cause and request-boundary details are expandable. The older October 22 gap remains in the audit trail and is not relabeled as a successful or no-trade session.

The VPS minute collector now includes portfolio reconciliation status and freshness, flags readiness blocks before they can silently become missing plans, and checks October 23's plan digest against the verified authorization. Existing default/account/cutoff/whole-share and missed/duplicate/overfill checks remain active. The next session opens October 23 at 09:30 Detroit. Its actual outcomes are pending; collection does not depend on the desktop or Codex staying open.

Evidence is under `outputs/paper-monitor/2026-10-23-preflight/`, including the original failures, cent-rounding reproduction, failed and successful SIP requests, deployed adapter, complete plan/verification receipt, and monitoring requirements.
