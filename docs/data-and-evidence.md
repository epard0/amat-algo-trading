# Data, evidence and reproducibility

Source control holds code, schemas, reviewed configuration and documentation. Market data, account state, private operations receipts and generated research output have separate lifecycles. A source clone cannot reproduce a study without its exact inputs and evaluation contract.

## Storage ownership

| Location | Contents | Publication / preservation |
| --- | --- | --- |
| `amat/data/` | Vendor and normalized immutable snapshots | Ignored; obtain under your own entitlement and preserve manifests/hashes |
| `amat/outputs/` | Research runs, validation, generated reports | Ignored; preserve immutable evidence outside source releases |
| External research workspace | Saved sessions, best-strategy database and controller evidence | Operator-defined durable storage; not a portable source dependency |
| `executor/state`, container state volume | Orders, receipts, interlocks, equity, checkpoints | Private durable account state; never generic cleanup |
| `kalshi-perps/runtime`, paper volumes | History cache, account events, funding, reports | Private durable account state and pinned history |
| `frontend/.runtime` | Local jobs, events, observations and private SSH config | Ignored workstation state |
| `operations/` | Real deployment, repair and backup evidence | Ignored private records; do not edit to sanitize a source upload |
| Tests/fixtures | Deliberate synthetic contract examples | Track small fixtures with clear provenance and no credentials |

## Minimum reproducibility record

A new study should identify the source revision and source hashes (including meaningful untracked code during local development), exact config digest, data manifest/source/coverage, provider/feed and adjustment policy, universe and timestamp conventions, train/validation/holdout chronology, cost model, seeds, resource/budget limits, selection rule and output checksums. Record inactive, duplicate, rejected and failed candidates as well as selected ones.

Freeze selection before evaluating other periods. Repeatedly inspected historical periods are not new holdouts. Distinguish overlapping account-window averages from one-pass full-period P&L. Separate strategy targets, simulated fills and prospective paper fills. State late funding, missing data and unresolved accounting assumptions explicitly.

## Acquiring data

Inspect native CLI help and `amat/src/amat/data` for supported ingestion interfaces. Use your own market-data credentials and feed permissions. Do not assume a historical example's dated snapshot is shipped in Git. Synthetic backtests can verify accounting without credentials, but must be labeled synthetic and never quoted as market performance.

The perpetual runtime can bootstrap supported public proxy history automatically when a paper run is intentionally started. It validates contiguous completed UTC hourly bars, retains original observations and checksums, and keeps the original history anchor. Kraken proxy prices and Kalshi execution prices remain distinct series with explicit instrument scaling. Missing intervals block new risk.

## Retention and archives

Generic cleanup must exclude active databases/WALs, order/funding deduplication, receipts, uncertain outcomes, interlocks, equity, checkpoints, authorizations and immutable evidence. Do not delete retired shadow account state.

Only allowlisted, completed, inactive generated collector detail older than seven days is eligible for compression. Publish archives atomically with manifests/checksums, retain summaries and compressed detail, and use archive-aware readers. Maintenance is bounded and low priority and defers under insufficient headroom. No automatic deletion of retained archives is introduced. Backups have their own documented lifecycle and isolated restore verification.

See [operations](operations.md), [backup contract](executor/scripts/BACKUPS.md), and [collector archive contract](frontend/deploy/paper-monitor/ARCHIVES.md).
