# P7 current status: completed implementation, suspended shadow campaign

Verified read-only at **2026-09-30 02:39 UTC** (September 29, 10:39 p.m. EDT),
after P11. This note describes retained evidence and the current checkout; it
does not poll the provider, change the campaign, append an observation, or reset
its monitor state.

**P7 is complete as an implementation milestone. Its shadow campaign remains
suspended, and its frozen artifact is incompatible with the current runtime.**
These are separate facts. P8–P11 research did not reactivate P7 or supply it with
new prospective observations.

## What P7 completed

P7 reconciled the adjusted-history changes for SPY, QQQ, and IWM against retained
corporate-action evidence. All 29,568 overlapping raw OHLCV rows were unchanged;
all 8,064 adjusted-close revisions were explained within the predeclared rounding
bound. It created a reconciled snapshot and artifact, registered a campaign, and
successfully appended the September 28 diagnostic observation. Its repeated poll
was idempotent. See [P7 verification](p7-verification.md) and the retained
[refresh report](../../../amat/outputs/genetic-patterns/2026-09-29-p7/refresh/refresh.json).

The daily expression and calibration interval were preserved. The refreshed
artifact grants neither historical approval nor execution authority.

## Actual ledger and alert

The current [P7 ledger](../../../amat/outputs/genetic-patterns/2026-09-29-p7/ledger.json)
passes the ledger reader's hash-chain, chronology, identity, and suspension checks.
It contains exactly **one observation**, sequence **0**, for **September 28, 2026**.
The observation is a historical backfill, not prospective evidence.

Its alert is **`positive_prevalence_drift` for IEF**. The effective state is
**`suspended`**. For **all 11 assets**, `bounded_contribution` is exactly zero and
`reported_score` equals `baseline_score`. Pattern values and candidate-score
calculations remain in the diagnostic report; retaining them does not authorize
using their contribution.

P8 reproduced the alert from the retained data:

| Quantity | Verified result |
|---|---:|
| Frozen reference window | December 29, 2017–December 31, 2018; 252 sessions |
| Positive reference values | 72/252 = 28.5714% |
| Observation window | August 31–September 28, 2026; 20 sessions |
| Positive observed values | 15/20 = 75% |
| Absolute prevalence shift | 46.4286 percentage points |
| Frozen alert threshold | 35 percentage points |
| Amount above threshold | 11.4286 percentage points |

The separate mean-shift statistic was approximately **0.940**, below its threshold
of 4. The prevalence alert alone is sufficient. This is a distribution-shift
heuristic, not a profitability or statistical-significance test. See the
[P8 calculation](../../../amat/outputs/genetic-patterns/2026-09-29-p8/ief-drift-analysis.json).

P8 also extended minute history. Usable hourly coverage improved from 5/11 to
6/11 assets, while usable 15-minute coverage stayed at 7/11. IEF's uninterrupted
calibration runs remained shorter than the expression's required 84 bars. Those
coverage results do not repair or invalidate the separate **daily** IEF alert.
Later native-aggregate research uses a separately declared input contract; it does
not retroactively replace P7's strict minute inputs or frozen calibration.

## Prospective qualification

The [campaign](../../../amat/outputs/genetic-patterns/2026-09-29-p7/campaign.json) was
registered at **2026-09-29 18:00:35.369321 UTC**. Its rule requires a session's
open to be **strictly later** than actual campaign registration. September 29
opened earlier than registration, so **September 30 is the earliest eligible
prospective session for this particular campaign**. An observation must also
wait until the required market bars are completed.

At this audit, the September 30 session had not opened. The ledger has **one
historical backfill and zero prospective observations**. The retained report's
`forward_sessions=1945` counts the historical extension beyond calibration; it
is **not** a prospective observation count. An output date or a
historical backtest cannot establish prospective status. A newly registered
replacement campaign must use its own real registration time and must not inherit
an earlier start date merely because P7 existed.

## Current source binding prevents direct reuse

The retained artifact's payload checksum remains correct. Its source snapshot
contains **168 runtime files**. At the audit, before the P12 component module was added, the checkout contained
**178**: ten added
research/diagnostic modules, plus changed `cli.py` and `symbolic.py`. Recorded
runtime dependency versions still match.

The symbolic registry binding also changed because it hashes implementation
source:

- P7 registry SHA-256: `828da5b9d029d423610082122afc3a0ad841e8c6edfdcd570b4990c85e689c95`
- Current registry SHA-256: `f5e6e0cf194fe65e3fc0f6073b4f4b9f525e09564942bb404783753324592a0c`

Calling `load_shadow_artifact` on P7's retained artifact with the current checkout
raises a candidate validation error containing **`symbolic feature registry
binding mismatch`**. The source-hash set would also fail exact equality. This is
an expected refusal to reuse frozen evidence under a changed implementation; it
is not evidence that the old files were corrupted or that the formula changed.
Do not edit the artifact's checksums or weaken either check to force a poll.

## Requirements for continuing shadow observation

1. **Choose an explicit runtime lineage.** Reproduce P7 in an isolated environment
   using its archived source and matching dependencies, or create a separately
   versioned migration to the current runtime. Keep the original bundle intact.
2. **Prove numerical and identity continuity.** Verify the original artifact,
   campaign, ledger, calibration panel, formula, universe, monitor policy, reference
   statistics, and retained probes. A current-runtime migration must explicitly
   record old/new source and registry identities and its parity results. A new
   hash alone does not establish equivalence.
3. **Preserve the suspension latch.** Continuing the original ledger under its
   original identity retains its suspended state. A replacement artifact/campaign
   needs an explicit, validated predecessor link and inherited suspended state;
   simply starting a fresh observing ledger would erase the material alert.
   Existing same-artifact ledger append logic preserves suspension automatically;
   this audit does not assert that a cross-artifact migration is already available.
4. **Collect only closed, correctly classified observations.** Use new immutable
   receipt/report paths, reconcile any later daily adjustment changes, validate
   intraday history against the declared contract, and retain idempotent retries.
   The applicable campaign's registration/session timestamps determine whether
   an observation is a backfill or prospective.
5. **Keep reporting neutral while suspended.** A later quiet window cannot
   automatically clear the latch. Any changed monitor, formula, calibration, or
   input contract is a separately evaluated research change with its own evidence;
   P10/P11 results do not supply P7 with approval.

These requirements support continued diagnostics while keeping the existing
suspension visible. They do not entail broker access, live trading, or an
automatic scheduler.

## Integrity evidence from this audit

Every file and external reference in the P7 and P8 evidence manifests matched:
**31 P7 files + 12 external references**, and **28 P8 files + 9 external
references**, with no failures. The P7 campaign payload checksum and artifact
payload checksum were independently recomputed; the ledger reader verified its
chain.

| Retained file | SHA-256 |
|---|---|
| P7 `ledger.json` | `49223c4abf93f79023e1c9cdb14d51644ff29c2f91f20ff73bb03bba0d3bfdf3` |
| P7 `campaign.json` | `ba849a02b9362ba069c056eb1bf9cd409ec79e2c90ab29c8af67871cd00df571` |
| P7 `refresh/artifact.json` | `90a52f8026f2239a45111307a4f9fd86770c7f509cef9515e0105f8064390e99` |
| P8 `ief-drift-analysis.json` | `df27537223cf962e3738f2f4c55e367f58ab55129777926ea4e2de4a0fe1537f` |

P7 artifact payload SHA-256:
`b25c9a628646a36f174a1ea364164c6b3283c20df933f5b58a9c76ab8b632072`.
Campaign payload SHA-256:
`5d38c7fdc449306e9ac3904c1ca05dfb2582125aa727f22cd58fc4fef83aa4c5`.
File hashes and payload hashes are distinct identities.
