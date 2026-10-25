# Research workflow tools: implementation and verification

Implemented on 2026-09-12. This is the first implementation slice from
[the GitHub assessment](research-workflow-improvements-2026-09-12.md), with standard
diagnostic charts added. The operating guide is [research-tools.md](research-tools.md).

Subsequent live study: [four-run results and analysis](workflow-four-run-results-2026-09-12.md).
That study completed three live model-generated candidate measurements and one
built-in fallback, superseding the live-provider acceptance limitation recorded
below. It also found important gaps between model explanations and executable
recipes; successful structured execution does not establish mechanism correctness.

## Delivered behavior

- One locally guided proposal repair across the provider chain, with normal token
  accounting, safe validation feedback and explicit failure provenance.
- Frozen, hash-verified memory from compatible prior development sessions, with
  checkpoint/time/evaluator/data/baseline/gate exclusions. Relevant records reach
  the proposer, and exact repeated recipes are excluded from automatic search.
- An approved `matched_backtest` adapter that reproduces candidate and control,
  registers the comparison, persists completed sides across interruption and
  limits execution attempts to two.
- Equity, drawdown and exposure charts generated from verified daily tables,
  with all plotted values and source hashes included in the adjacent JSON file.

The existing frozen scoring and selection rules remain authoritative. No upstream
framework was installed. Existing unrelated workspace changes were preserved.

## Automated checks

- Full regression: **1,809 passed, 1 skipped in 289.16 seconds**. The existing
  skip is `test_dependency_direction.py:117` (`__init__.py` has no declared layer).
- After the final empty-session inspection fix: **3 targeted tests passed in
  40.35 seconds**, covering pre-backtest inspection, compatible evidence retrieval
  and immutable snapshots, and chart reconciliation/tamper detection with a real
  backtest. The complete suite was run before this small startup fix; the targeted
  checks cover the affected behavior afterward.
- Ruff checks and formatting pass; mypy reports no issues in 155 source files.
- `git diff --check` passes.

## Live provider check

Fallback search was disabled for both provider checks. Neither produced a model
proposal or completed a candidate experiment; only their baseline measurements
completed.

| Session | Outcome |
|---|---|
| `workflow-live-20260912-141526` | Gemini `gemini-3.8-flash` returned HTTP 429 quota; `openrouter/free` reached its 60-second deadline. The next reservation exceeded the session budget, producing `paused_budget`. |
| `workflow-canary-20260912-142144` | A separate bounded check of `nvidia/nemotron-3-ultra-550b-a55b` reached its 60-second deadline, producing `paused_no_provider`. |

Both sessions are under `/home/supasuge/Documents/amat-research/`. Provider usage
was unknown for all three calls; reservations were conservatively charged in the
ledger (32,563, 34,248 and 30,521 respectively). Those are budget-accounting
amounts, not confirmed provider token usage or monetary charges. Existing budgets
and failed records were not reset.

**The P0 acceptance criterion of a real model-generated, validated and measured
proposal remains unfulfilled.** Scripted proposal repair is verified separately;
it does not establish current provider availability.

## Deterministic end-to-end verification

An explicitly supplied 63-session momentum confirmation recipe was evaluated in
`/home/supasuge/Documents/amat-research/workflow-tools-verification-20260912-142421`.
It used the frozen development period ending 2022-12-30 and made zero model calls.
Candidate identity:

`3a955c6bc85b41b98271b110a34771f29f1d12d420c510813655f1a4b00012cb`

Its incremental score was 0.007390210243442894, below the 0.05 improvement margin,
and its outcome was `discard`.
Standard charts were generated from the primary, stressed and exposure-matched
daily artifacts. This run demonstrates the measurement/visual tooling, not a new
discovered strategy or an investment recommendation.

A subsequent compatible session froze exactly one eligible record: this completed
candidate. Its identity was present in the exact-repeat exclusion set. Earlier
incompatible programs were excluded rather than mixed into the new evidence.

An inspection before the first backtest exposed a missing-lock startup case.
The final implementation handles an empty research ledger explicitly, and a
regression check covers both related-evidence lookup and unavailable charts.
The final fresh session,
`/home/supasuge/Documents/amat-research/workflow-memory-final-20260912-142921`,
successfully retrieved that candidate through both the Python tool and its frozen
`research.sh related --query "momentum confirmation"` command. It still had no
experiment ledger afterward. Its memory contains one eligible record and exact
repeat exclusion recognizes the prior candidate.

## Scope and remaining work

The matched-run test uses a scripted review decision with real candidate/control
backtests. It verifies review identity, interruption after one completed side,
artifact reuse, registry import, idempotent completion and rejection of a changed
approval. It does not demonstrate a live committee resolving an objection.

Charts are static diagnostics. Additional feature-inspection wrappers, interactive
parameter heatmaps, four curated research packages, and an equal-budget comparison
of research quality remain later work. The new failure memory reduces exact
repetition and supplies relevant evidence; it does not guarantee correct causal
interpretation or make reused development periods into untouched holdouts.
