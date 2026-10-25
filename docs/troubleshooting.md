# Troubleshooting

| Symptom | Check and remedy |
| --- | --- |
| `uv sync --locked` rejects the project | Confirm sibling layout and matching source/lockfiles. Review intentional dependency changes; do not delete the lockfile to bypass the mismatch. |
| CLI missing/import error | Use `uv run --project PACKAGE` or that package's environment, not an unrelated system interpreter. Kalshi also needs the configured AMAT interpreter. |
| UI dependencies missing | Run `npm ci --prefix frontend/web`, then doctor. Use a supported Node runtime. |
| Production launcher asks for a build | Run `npm run build --prefix frontend/web` before `start.py --production`. |
| Local port already occupied | Identify the process and use the launcher's port options or stop your own prior launcher. Keep loopback binding. |
| Empty research/performance views | A fresh clone has no vendor snapshots or generated outputs. Ingest/restore your own verified data; missing results are not zero returns. |
| Provider authentication/quota error | Check variable names, explicit environment loading, feed entitlement, provider quota and the selected model chain. Do not print key values. |
| Strategy source hash mismatch | Compare the pinned source set/configuration with the intended policy. Use a new reviewed profile/account for a policy change; never rewrite a hash just to bypass validation. |
| `HistoryUnavailable` / warm-up | Inspect completed UTC hourly coverage and configured symbols/history anchor. Gaps must not be forward-filled. Short valid history can return `ready=False`. |
| Remote unavailable | Configure your own mode-600 private SSH file, valid key/passphrase, verified known-host entry and installed compatible helper. Reachable SSH alone does not prove engine readiness. |
| Old account values after refresh | Inspect the source observation timestamp and stale label. Cached API reads and browser refreshes do not establish a new broker observation. |
| Preview conflict/expiry | Refresh the exact preview under the same local session. If dispatch may have been accepted, recover with the original idempotency key and exact inputs. |
| Job outcome unknown | Inspect native receipts and reconciliation; do not submit a second request to make the UI show success. |
| Executor not ready | Read native reasons: dry run, disablement, account binding, interlocks and uncertain outcomes can intentionally block action. Do not clear safeguards for a smoke test. |
| Alert sends fail | Validate private notification setup and delivery state without exposing tokens/destinations. Preserve cursor/outbox receipts; do not replay historical notifications. |
| Missing historical report link | Dated docs can point to intentionally excluded private/generated evidence. See the documentation index and recover only your own verified artifact. |

For reproducible bugs, include package/source identity, command shape, a redacted error and synthetic reproduction. Use [contribution standards](CONTRIBUTING.md) and [private security reporting](SECURITY.md) as appropriate.
