# Strategy, execution, data and dashboard update — 2026-10-19

## Implemented

The default frontend now uses three plain-language sections: Overview,
Investments and Activity. Finding assets, historical testing and consistency
checks use guided inputs and a review step. Advanced settings and raw output
remain expandable. The analysis is visual: growth and benchmark paths, drawdowns,
monthly returns, nine-approach comparisons, asset ranks, trend/volatility scatter,
selected asset price history, allocation, and recorded account history when
sufficient observations exist. Research simulations and saved paper-account
records carry separate labels and dates.

Performance charts read the existing frozen October 14 study, verify record and
protocol checksums, and verify the benchmark source partition against that study.
The charts do not represent a newly executed experiment or promotion. Period-end
returns and deepest drawdowns agree with the retained study metrics across all
five displayed periods. Changing period, strategy, or cost reads retained evidence.

`amat scan CONFIG --snapshot SNAPSHOT --condition-sessions N --json` now discovers
assets meeting the native trend conditions for consecutive past trading days.
Duration is bounded to 1–252 sessions. Each asset uses its own contiguous history;
a missing reference-calendar session restarts warm-up, and a late listing does
not truncate other assets' histories. The screen uses completed historical data
only and includes a trailing dollar-volume filter. It does not forecast how long
conditions will persist, expand the trading allowlist, or create orders.

The executor now has an opt-in `execution-batch/1` checkpoint and a versioned cash
policy. It retains original authorization and account/policy identity, cumulative
fills, remaining intent, exact order identities, cash commitments, expiry and
cancellation state. Sales must reach broker-confirmed terminal states before buy
admission; actual cash bounds proportional buys. New buy submissions use bounded
DAY limits. Restart and unknown-ack behavior never create replacement authority.
See [the execution contract](../executor/execution-batch-v1.md).
`EXECUTION_MODE=staged-v1` selects it; the default remains legacy. No running
executor environment was modified and no orders were placed during this work.

## Larger provider dataset

The frozen curated universe requested 561 symbols from the configured Alpaca SIP
source. The final usable snapshot is
`data/snapshots/alpaca-sip-diverse561-2026-10-19-verified`:

| Evidence | Actual coverage |
|---|---:|
| Validated assets | 509 |
| Daily rows | 1,347,447 |
| Actual daily dates | 2016-01-04 through 2026-10-16 |
| Observed opening windows | 350,964 |
| Opening-window requested period | 2024-01-02 through 2026-10-16, 701 sessions |
| Window coverage on observed daily cells in that period | 99.18% |
| Quarantined symbols | 52 |
| Retained snapshot disk footprint | Approximately 44 MiB |

Daily history was requested from 2010; the actual supplied history starts in 2016.
Missing earlier data and opening windows were not manufactured. Before 2024 this
snapshot supports daily screening; absent windows cannot generate backtest fills.
The manifest's whole-history rectangular window-coverage denominator differs from
the execution-period observed-cell denominator above; both are retained explicitly.

The universe spans domestic and international equities, sectors, styles, bonds,
commodities, real estate, currencies and thematic exposures. It includes individual
stocks and some leveraged products for discovery, without granting trading access.
This is a current-symbol curated dataset, not a delisting-complete historical
stock universe. It cannot establish survivorship-free broad-stock performance.

All 509 assets passed native daily-quality and corporate-action ingestion checks;
the native panel loader also accepted the original core symbols over 2,713 sessions.
Exact duplicate corporate actions collapse as required by the existing native
loader; distinct conflicting event values stay quarantined. An initial provisional
snapshot incorrectly excluded QQQ for an identical repeated dividend. The corrected
snapshot replaces that provisional version; native integrity rules were preserved.

The retained five-day screen has 213 matches from 509 screened assets as of October
16. Only the top 20 are shown. Those are historical signal matches, not portfolio
weights, broker-tradability decisions, or evidence of a realized return improvement.
The research registry adds `screening` while keeping the original `daily` reference.

## Storage cleanup and structure

The compaction command defaults to dry-run and validates snapshot hashes before
hard-linking byte-identical partitions. It preserves referenced snapshot paths and
unfinished pulls. Its October 18 audit records 143,320,447 bytes reclaimed. After
validation of the final expanded snapshot, superseded provisional partitions and
completed download checkpoints reclaimed another net 156,511,276 bytes, with rejected
raw/adjusted/action evidence preserved separately. Combined: about 286 MiB.
The earlier duplicate server archive removal is additional to that figure.

Audits remain in `data/governance/snapshot-compaction-2026-10-18.json` and
`data/governance/snapshot-retirement-2026-10-19.json`. Quarantine evidence remains in
`data/governance/diverse561-quarantine-2026-10-19/`. Existing research source snapshots
and active uncommitted code were preserved. Broad deletion of apparently unused
research runtimes would break reproducibility without a reference-aware archive.

Chart projections, frontend visualization components, causal condition screening,
durable execution continuation and snapshot maintenance now have separate modules.
The existing executor package boundary is retained; it does not import AMAT's
research dependency graph. A newly exposed >200-symbol default-quote bug was fixed
without removing the limit on explicit quote requests. Job state changes and
their activity events now publish atomically, fixing a separate-reader race.

## Verification and remaining scope

- Complete executor suite: **353 passed**; executor source mypy: **28 files passed**.
- Complete frontend API suite: **45 passed**, with one existing Starlette/httpx
  deprecation warning. Five additional projection tests cover evidence corruption,
  period/month calculations, traversal rejection and sparse account history.
- Targeted AMAT screening, scheduled selection, execution-plan and accounting suites:
  **113 passed**. These include causal prefix, missing-history and duplicate corporate-action checks.
- Production frontend TypeScript/Vite build passed. Browser checks passed for the
  simple task flow, uncertain-request identity reuse, advanced view, actual chart
  sources, period/strategy controls, keyboard inspection, asset price selection,
  and 390/1024/1440-pixel layouts. Action POSTs in UI tests were intercepted.
- Targeted Ruff checks passed. Provider ingestion used read-only market-data calls;
  no broker-order integration test or full AMAT suite was run.

The execution authorization contract is implemented. Exact historical/broker fill
parity remains unproven: the retained daily study uses an aggregate opening-window
proxy, whereas actual broker fills, limit admission and cancellation races are
asynchronous. Event-level replay against the same contract and matched prospective
broker evidence is still required before claiming identical fills or activating
this policy in a running executor. The original strategy remains the reference;
no challenger or expanded trading universe was promoted.
