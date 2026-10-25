# Repository publication preparation — October 25, 2026

This record covers source/documentation preparation on the local workstation. It is not remote deployment or current trading verification. No engine lifecycle, broker order, real notification, account migration or GitHub push was performed.

## Changes

- Replaced the historical-results-first root README with a current component map, installation overview and linked documentation table of contents.
- Added current architecture, installation, credential/configuration, strategy API, HTTP API, per-component, data/evidence, operations, testing, troubleshooting, contribution, security and publication guides.
- Documented all six Python packages shipped by AMAT, the executor, the perpetual runtime and trade alerts, the local UI/API, independent workers, SSH helpers, collector, and optional desktop/reference material.
- Made maintained documentation trackable; excluded private keys, local skills/browser captures, server archives, runtime state, generated research JSON/CSV/screenshots and private operations receipts. Blank paper/live environment templates remain trackable.
- Added a blank research environment example and replaced infrastructure-specific SSH template/diagnostic-guide values with placeholders. Corrected the remote guide's obsolete browser-owned scheduling description.
- Updated the native strategy API guide to include the current ADX/shadow engine families and link the cross-system reference.
- Removed eleven verified empty scaffolding files: unused AMAT JavaScript/package-manager manifests/settings, empty submodule/ripgrep settings and a workspace test marker. Kept the intentional Python `py.typed` marker.
- Added a pull-request template and a standard-library publication checker with working-file, exact-index, all-ref-history and clean-source-export modes.
- Added an original generated repository logo, three actual October 25 UI captures, retained-strategy statistics/explanation, a curated nine-row numerical extract and a source map built from four project chat histories. Source metrics and exact configuration identity were checked without rerunning investment research.
- Fresh-copy regressions exposed seven frontend tests and one Kalshi test requiring excluded local research artifacts. Made those dependencies explicit: skip only when their local archive is absent; continue checking present evidence and all source/profile/synthetic contracts. The Kalshi schema/allocation check remains active separately from original selection-artifact integrity.

A pre-edit baseline outside the repository retains status, tracked binary diff and source hashes including untracked files. Existing application source, frozen strategy hashes, accounts, runtime state and immutable research evidence were preserved.

## Publication/security findings

The working-file scan passes its supported filename/content checks. A supplementary literal credential-assignment scan found no candidate assignments in publication-eligible files. These are heuristics, not a complete public-data audit; older research/deployment documents retain historical filesystem/infrastructure references.

The all-ref history scan finds an SSH private-key blob under a local Codex snapshot ref. The blob was absent from the branch/tag object set checked during preparation. This does not establish public exposure. Do not mirror-push this checkout or upload its `.git` directory. Keep local history intact, and use the prepared source-only export for a fresh repository or coordinate explicit history cleanup separately. Rotate the key if it has been exposed outside its trusted local storage.

An apparent provider-token match in an old skills-document anchor was a false positive; the checker now requires a token boundary and has a regression for that prose case. The excluded local skills tree remains preserved.

Existing proprietary package metadata and `Private :: Do Not Upload` distribution classifiers remain. No root open-source license was invented. The owner must choose redistribution/contribution terms before presenting this as an open-source project.

## Completed validation

| Check | Result |
| --- | --- |
| UI: `npm run build --prefix frontend/web` | Passed; TypeScript and Vite production build |
| Gateway/launcher: `uv run --project frontend pytest frontend/server/tests frontend/tests -q` | 165 passed; one existing Starlette TestClient deprecation warning |
| Executor: `(cd executor && uv run pytest -q)` | 396 passed |
| Full AMAT: `(cd amat && uv run pytest -q)` | **3,822 passed, 2 skipped, zero failures**, 1,378.36 seconds |
| Kalshi: `(cd kalshi-perps && uv run pytest -q)` | 92 passed after splitting source/profile and private-evidence checks |
| Publication checker isolated Git tests | 7 passed, including exact staged content, deleted history, ignored secrets, external symlinks, export exclusion and overwrite refusal |
| New publication Python lint/format | Passed |
| Strategy config validation | Passed for `amat_group_selection_volatility.toml`, v14.3.0, 11 symbols |
| Local doctor | Passed interpreter/server modules, Node/npm and installed web dependencies |
| Executor mypy | Passed: 30 source files |
| Maintained documentation link targets | Passed: 171 local links across 22 maintained documents |
| `git diff --check` | Passed |

## Pre-existing quality-gate debt

Production application Python files were not edited during preparation. Evidence-dependent test boundaries were clarified after fresh-copy verification. Broad gates expose existing failures; this cleanup does not claim to resolve them:

| Gate | Observed baseline result |
| --- | --- |
| AMAT Ruff lint | 5,519 findings across existing research/scripts/tests |
| AMAT Ruff format | 121 files would be reformatted |
| AMAT mypy | 877 errors in 55 files; 316 source files checked |
| Executor Ruff lint | 79 findings, including the VPS helper and existing tests |
| Executor Ruff format | 2 existing test files would be reformatted |
| Frontend Ruff (`server scripts tests`) | 109 findings in existing scripts/server/tests |

Kalshi lint passed; its strategy-guide code example was formatted as part of the documentation change. Large automatic rewrites of unrelated research or execution code were avoided. Native Electron packaging, responsive browser behavior, remote deployment/backup continuity and fresh live provider behavior were not retested because their implementations were not changed.

## Final source/export verification

A final source-only export is prepared outside the working checkout, containing 1,349 publication-eligible files and no `.git` directory. A disposable copy of that export successfully completed all four locked Python dependency installations and a fresh npm install/production build. Its strategy configuration validated, the local doctor passed, and the source-pinned perpetual facade/worker correctly returned an unready decision for deliberately short synthetic history. No provider request or account write was used for that strategy check.

Generated datasets, private credentials and existing account state are intentionally absent from the export; their workflows require operator-owned inputs. The export is intended for a fresh source repository. It does not contain or rewrite existing Git history. Fresh source-copy regressions passed after making private-evidence dependencies explicit: frontend **158 passed / 7 skipped**, Kalshi **91 passed / 1 skipped**, executor **392 passed / 4 existing skips**. The ordinary working checkout retains its original private evidence, so those optional evidence checks still execute there. An initial fresh-copy attempt failed seven frontend evidence tests and one Kalshi evidence test; those failures exposed the implicit local-data dependency and were preserved as the reason for the test-boundary change. No evidence was fabricated or copied into tests to conceal the absence.

The original-checkout API rerun under simultaneous full-research/fresh-copy load recorded one timing failure in `test_worker_survives_service_restart` (164 other tests passed). That unrelated worker test has a four-second completion budget; its earlier full run and the fresh-copy run passed. The focused rerun passed (one test), and the subsequent complete original-checkout API/launcher rerun passed all **165 tests** with only the existing deprecation warning. No worker implementation or timeout was changed.


The final working-source publication scan and a direct contents scan of the 1,349-file export both report zero supported findings. The all-ref scan intentionally remains nonzero for the preserved local snapshot key. The curated statistics were checked against the canonical seal of the original final comparison, and all nine selected baseline/benchmark record identities matched their original evidence. Production implementation files remain byte-identical to the pre-task baseline; only documentation, publication tooling/templates and explicit test-data boundaries changed.


## Final completed checks and deliverables

The full original-checkout AMAT regression finished with **3,822 passed, two explicitly reported layer-inventory skips, and zero failures/errors in 1,378.36 seconds (22 minutes 58 seconds)**. The final original-checkout API/launcher run passed 165 tests; Kalshi passed 92; executor passed 396. The new publication-tool suite passed seven isolated tests. Counts from repeated runs are not added together.

The final source export contains all 1,349 eligible files, including the logo, current UI screenshots, curated numeric evidence, linked guides and clarified evidence tests. A sibling SHA-256 source manifest and source-only tar archive identify the exact upload candidate. Existing Git refs, credentials, account databases, downloaded datasets and private operational receipts are absent. The source-only export was not initialized, committed, pushed or deployed.


## Follow-up: private SSH identity hardening — October 25, 2026

This follow-up supersedes the original source-export count and production-source-continuity statement above: the local gateway's SSH configuration loader was deliberately changed to remove operator-specific defaults. No remote engine, strategy, order or account code changed.

The earlier audit found a real endpoint/user/key-path fallback in `frontend/server/remote.py`, plus operator values in test fixtures and three historical operational guides. The loader now requires `host`, `port`, `username`, `key_path` and `known_hosts` explicitly in the private file. It rejects missing fields and empty/invalid key/trust paths before any connection. Existing ownership/mode/size checks, pinned host keys, agent/key-discovery prohibition and bounded SSH behavior remain intact. The existing mode-600 private config already had all required fields and validated without modification or a connection.

Test fixtures now use an example-domain host and generic operator. The three original operational notes remain byte-preserved and Git-ignored, rather than changing historical receipts. The release candidate excludes them. Private SSH configs (`remote.toml`, `ssh.toml`, `*.local.toml`), key files and trust stores are ignored at any repository depth. The public template contains only placeholders. [The dedicated configuration guide](remote-configuration.md) documents the full boundary and is linked from the README.

Publication checking now rejects embedded non-loopback IPv4 addresses and private SSH config/key/trust filenames, with values suppressed. A separate audit confirms that no original deployment address, operator or key identifier remains in eligible source/docs. The pre-follow-up source baseline and superseded publication artifacts are kept outside the repository in a private baseline directory; existing local Git snapshot refs were not rewritten.

Actual follow-up checks:

| Check | Result |
| --- | --- |
| Focused remote/configuration/adapters/collector-reader tests | 43 passed |
| Complete API/launcher regression | **182 passed**, one existing deprecation warning |
| Complete executor regression | **396 passed** |
| UI production build | Passed |
| Publication-tool isolated tests | **9 passed** |
| Scoped Ruff lint/format for changed loader/config tests/tooling | Passed |
| Existing private config compatibility and ignore rules | Passed; no private values printed |
| Publication identifiers and supported content checks | Passed; no original deployment identifiers in eligible files |

The earlier full AMAT/Kalshi results remain dated preparation evidence, not newly rerun checks in this follow-up. Their implementations were not changed. No remote deployment, SSH probe, account operation, local API restart or GitHub upload was performed. The source export/archive/checksums were refreshed after these checks; earlier public-facing artifact paths now refer to the hardened source candidate.

## Follow-up: documentation consolidation — October 25, 2026

Moved 214 publishable Markdown documents and three ignored private operator notes into the root `docs/` tree. The root README remains the landing page. Component guides and their historical documentation now live in `docs/research/`, `docs/executor/`, `docs/frontend/` and `docs/kalshi-perps/`; shared contribution/security standards, change history and command reference also live under `docs/`. The README links the documentation index and complete 245-document catalog. Tool-discovered instructions/templates, licenses, runtime-required examples/protocols and immutable generated research evidence retain their functional locations.

Updated relative Markdown links, inline documentation pointers and the research shortcut's cheatsheet path. Both Python packages now carry a short inline metadata overview rather than requiring a component-local README; the executor Docker COPY and selective packaging allowlist follow the new layout. The deployment package includes the relocated VPS runbook. Private notes retain their original bytes and remain ignored at both their new paths and legacy paths. Original strategy report identity is retained separately from its published path/hash; measurements and evidence identities are unchanged.

A private baseline outside the checkout records all 1,347 source files present before this relocation, Git status and a documentation/source archive. Concurrent unrelated source work was preserved. The source-only upload directory, tarball, manifest and checksum are refreshed from current eligible working files after these changes.

| Relocation check | Result |
| --- | --- |
| All publishable local Markdown links | 815 existing targets; zero unresolved targets |
| Documentation placement | Only root `README.md` and operational `AGENTS.md` remain outside `docs/` and tool-configuration directories |
| Private notes | Three original byte sequences preserved; new and legacy locations ignored |
| Runtime documentation fixtures/protocol | Five example/protocol files unchanged at original paths |
| AMAT and executor distribution builds | Source distributions and wheels built successfully; no component-local README dependency |
| Focused research shortcuts/model-policy regressions | 20 passed |
| API/launcher regressions | 182 passed; one existing Starlette TestClient deprecation warning |
| Executor regressions | 398 passed |
| UI production build | TypeScript/Vite passed |
| Publication-tool regressions | Nine passed |
| Research shortcut lint | Passed |
| Packaging shell syntax and Git whitespace | Passed |

The full AMAT research suite and remote/container deployment were not repeated for documentation relocation. No remote state or service lifecycle was changed. Historical test counts earlier in this document describe their own runs rather than this follow-up.
