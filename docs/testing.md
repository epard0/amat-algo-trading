# Testing and verification

Run commands from the repository root unless shown in a subshell. Tests must use isolated temporary state and mocked broker/notification boundaries. Do not point test fixtures at real paper volumes or use smoke scripts that start/stop engines.

## Required package checks

```sh
npm run build --prefix frontend/web
uv run --project frontend pytest frontend/server/tests frontend/tests -q
(cd executor && uv run pytest -q)
(cd kalshi-perps && uv run pytest -q)
(cd amat && uv run pytest -q)
git diff --check
python scripts/check_publication.py
```

The AMAT default pytest configuration excludes `tests/live`. Explicit live-provider tests require separate opt-in and must not be part of ordinary gates. Evidence-dependent tests explicitly skip when their required local archive is absent. They still run when that archive is restored, and malformed/tampered existing evidence must fail. Source/profile/API/synthetic tests remain active in a fresh clone. Missing local research datasets or historical evidence should be reported honestly rather than downloaded or fabricated merely to make a test pass. Check tests and markers before running expensive study workflows.

Run configured quality checks for affected Python packages:

```sh
(cd amat && uv run ruff check . && uv run ruff format --check . && uv run mypy)
(cd executor && uv run ruff check . && uv run ruff format --check . && uv run mypy)
(cd kalshi-perps && uv run ruff check . && uv run ruff format --check .)
uv run --project amat ruff check frontend/server frontend/scripts frontend/tests
```

The UI build includes TypeScript compilation. Frontend has its own Ruff configuration; run from `frontend/` when checking relative package paths to ensure that policy is selected. Do not bulk-format historical research or unrelated existing changes to make a documentation PR look clean. Record baseline failures and the scope actually checked.

## Behavior-specific regressions

- Strategy changes: completed-bar causality, warm-up, data gaps, source identity, next-window fills and actual research-function parity.
- Accounting/memory changes: equivalent signals and account paths, late funding, event ordering, checkpoints and restart recovery.
- Executor changes: account binding, caps, ownership, receipts, ambiguous submissions and staged cash budgeting.
- Gateway changes: session/CSRF/origin, preview expiry/revision/input binding, durable idempotency, independent-worker recovery, SSE and output redaction.
- Monitoring changes: cache-only reads, bounded single-flight scheduling, stale/unknown data and no lifecycle/order authority.
- Collector/backup changes: manifest attestations, archive-aware readers, checksum/atomic publication and isolated restore verification.

## Browser and layout verification

With the local UI running and the required Playwright module available, the native frontend README lists fixture and browser scripts. Check desktop, tablet and mobile widths, each table/record wrapper, long labels/values, keyboard selection/focus, and document overflow. Use inert actions such as configuration validation. Real provider calls, notifications and account lifecycle controls are outside an ordinary browser smoke test.

## Repository publication checker

`python scripts/check_publication.py` checks tracked and untracked publishable working files. `--staged` reads Git index blobs, so it catches content already staged even when the worktree was later edited. `--history` additionally scans all reachable Git blobs for supported secret patterns. It prints filenames/object IDs and finding types, never matched values.

This is a bounded heuristic, not a complete security audit. It detects common private keys/provider tokens, embedded non-loopback IPv4 addresses, private SSH config/key/trust filenames, generated state/archive candidates, oversized files and unsafe symlinks. Review notebooks, binary assets, personal data and dependency provenance manually. Test the checker using `python -m unittest discover -s scripts/tests -v`.
