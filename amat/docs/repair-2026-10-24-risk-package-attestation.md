# AMAT risk package attestation repair — October 24, 2026

The human requested a fix for the `amat.risk` namespace attestation failure, a full regression rerun and a dashboard update, with paper execution unchanged. This is a manual maintenance task after the finite research campaign closed; it does not reopen that campaign or launch a strategy experiment.

## Cause and minimal repair

`amat/src/amat/risk/` contained `active.py` but lacked `__init__.py`. Python therefore loaded `amat.risk` as a namespace with no single source file. Importing `amat.risk.active` added that parent to `sys.modules`. The managed diagnostic attests all loaded research modules, so its existing fail-closed check correctly refused the fileless parent. The full suite exposed this import-state dependency; an isolated run without the earlier import did not.

The repair adds an explicit, docstring-only `amat/risk/__init__.py`. Both the package and `active.py` now have ordinary files that the existing guard can bind to the registered root and SHA-256 manifest. It exports no policy, imports no policy and changes no trading behavior. It also avoids treating a directory path or namespace metadata as a substitute for registered source bytes.

**The attestation guard is unchanged**, byte SHA-256 `ba60c25305a684b85110994ae05be1b1406a4b9694c9fb0c82e10096272811a8`. Its refusals for missing files, escaped roots, changed/unregistered bytes and empty verification remain in force. All 314 existing native library files are byte-identical to the pre-repair snapshot; the package initializer is the sole new library file, producing a 315-file source set. The default profile remains byte-identical (`4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8`). New research manifests must register the new source prospectively; old frozen manifests, protocols, results and source archives are not retagged.

## Regression and validation evidence

The existing managed-context regression now explicitly imports `amat.risk.active`, making it reproduce the problematic loaded-module state without relying on test order. Before adding the initializer, that test failed with the original `Loaded research module has no source file: amat.risk` error: one failure in 6.28 seconds. The saved failing log/JUnit is distinct from the subsequent repaired runs.

A new attestation test imports the actual package and implementation and verifies both against their exact registered source hashes. The affected suite combines source attestation, managed shadow-plan bindings, improvement policies and minute improvements: **52 passed in 6.84 seconds**. Scoped Ruff lint and format checks pass for all three changed/new files. No failure was hidden by removing an assertion, skipping the managed binding test or relaxing the guard.

The UI production build passed in 7.51 seconds. All **165 API/launcher tests passed in 70.91 seconds**, with the existing Starlette/httpx deprecation warning. A first API launch used a relative log path from the wrong working directory; shell redirection failed before pytest started. That preflight is recorded separately, and the actual API suite ran from the workspace root.

The full AMAT package regression completed with **3,785 passed, 2 skipped and zero failures or errors**. Its JUnit duration is 1081.53 seconds; the exact console duration and JUnit/log hashes remain in the saved output and sealed receipt. The previously failing managed-context regression passed in the full shared process, including the deliberately imported risk module. The earlier campaign run (3,776 passes, two skips and one attestation failure) remains immutable historical evidence; neither an isolated pass nor this maintenance task rewrites it.

## Dashboard and operational scope

The repair dashboard update appends a later validation record to the closed campaign's current display. Its 42 numerical result rows, all cost/curve comparisons, five measured experiments, ten rejected variants, twelve completed cycles and zero promotions remain unchanged. The prior failure remains in the original sealed publication and final report. Only the current status of this specific package-attestation defect is updated after the full regression result is known.

No new backtest, strategy-return claim, default promotion, model request or automatic research schedule is created. Candidate native profile/planning/broker compatibility, prospective agent performance, supported instruments/corporate actions and the remaining trusted promotion integration gates are unaffected by the package repair.

Paper execution remains unchanged: no remote deployment, bot restart/stop, order submission/cancellation/retry, account-state replacement or paper configuration change is part of this task. The independently running paper monitor retains its separate lifecycle. No current remote health or fill verification is inferred from local tests.

## Evidence

Evidence lives in `/home/supasuge/Documents/amat-workspace/amat/outputs/repairs/2026-10-24-risk-package-attestation/`: the 704-file pre-edit source/hash baseline and archive, Git status, the failing reproduction, affected and full JUnit/logs, API/build evidence, source continuity, the sealed repair receipt and browser/publication proof. The receipt records the completed full-suite pass under the repaired source; it preserves the prior failed reproduction and historical campaign outcome.
