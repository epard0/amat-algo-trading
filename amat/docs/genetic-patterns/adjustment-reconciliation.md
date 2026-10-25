# Adjustment reconciliation and new shadow calibration

The P6 daily poll detected changed historical adjusted prices and refused to
append them to the frozen snapshot. P7 adds an explicit `shadow-refresh` workflow
that reconciles new data before creating a separate snapshot and artifact.
It preserves the expression and original calibration interval, resets historical
approval, and leaves the original data and evidence immutable.

## What must reconcile

The reconciliation checks every requested symbol and XNYS session. Raw and
adjusted inputs must have the same complete coverage. All overlapping raw OHLCV
values and historical corporate-action records must remain unchanged. Exact
repeated action records collapse; conflicting records fail. New stock splits,
unknown revisions, missing bars, and unsupported changes refuse the refresh.

For a newly documented cash dividend, the expected factor is
`1 - dividend / raw_close_before_ex_date`; multiple factors compound. The old and
new adjusted prices are rounded independently, so the fixed comparison tolerance
is `0.005 * (1 + factor) + 1e-10`. Without a new documented action, adjusted prices
must match exactly. This is a consistency check against independently recorded
cash events, not a factor fitted to the changed price series. Refreshed provider
prices are retained directly; the workflow does not synthesize replacement prices
from these factors.

Provider receipts are immutable checksum envelopes for `raw`, `all`, and `actions`.
Their universe, provider, UTC receipt times, and coverage are verified. The raw
provider response is never edited to insert missing events. Optional
`action_supplements.json` carries separately attributed issuer/exchange evidence:
event date, amount, source URL, captured source file and hash, and the extracted
source record. Supplements may add only missing new events after the old snapshot;
they cannot replace historical actions or bypass price reconciliation. File hashes
bind the reviewed evidence; they do not authenticate a remote provider.

## Running the refresh

From the `amat` directory:

```sh
uv run --locked autoresearch patterns shadow-refresh \
  --previous-artifact outputs/genetic-patterns/2026-09-29-p6/artifact.json \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --receipts data/research/genetic-patterns-p7-2026-09-29 \
  --root . --snapshot-id alpaca-sip-shadow-2026-09-29-reconciled \
  --baseline-end 2026-09-25 \
  --output outputs/genetic-patterns/2026-09-29-p7/refresh
```

The retained run already owns these immutable output names. A later refresh must
use new names and new receipt coverage. If new sessions are included, the CLI uses
the existing read-only SIP client to fetch opening execution windows. Old windows
are retained unchanged with explicit provenance. No broker requests are made.

The output directory contains `reconciliation.json`, `artifact.json`, and
`refresh.json`. The new snapshot is under `data/snapshots/<snapshot-id>`. Original
artifact checksums and calibration-panel identity are verified before reuse. The
candidate must validate under the current registry; this command does not silently
replace its registry or evolve the expression. The refreshed artifact binds the
new source code, snapshot, calibration, and provenance.

Register a new campaign against the new artifact/snapshot using `shadow-register`.
The real smoke test intentionally ended the new baseline on September 25 and
polled September 28 as a **backfill**. This exercises a complete append without
misrepresenting it as prospective evidence. Future observations qualify as
prospective only when their session opens after registration.

See [p7-verification.md](p7-verification.md) for the measured reconciliation and
[multi-timeframe-shadow.md](multi-timeframe-shadow.md) for the five-timeframe
observation commands. Each later distribution or provider revision may require
another explicit reconciliation; unchanged-history checks remain enforced.
