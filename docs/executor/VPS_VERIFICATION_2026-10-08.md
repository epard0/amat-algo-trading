# Executor verification — October 8, 2026

The hardened executor is running locally and passed the checks below. This is not certification of the destination VPS or authorization to submit orders. The executor supports Alpaca paper execution.

## Verified configuration and artifact

- Image: `amat-executor:vps-20261008`
- Image ID: `sha256:80655d451c484129bcd1b2f8b9a754e3f1b6e7ee504880ee589912f32ae4f3fc`
- All 25 installed Python source modules matched SHA-256 hashes of the current source tree.
- `DRY_RUN=true`, `EXECUTOR_DISABLED=true`, all three execution caps unlimited as requested.
- Local container recreated with the final image, restarted, and observed healthy with fresh durable loop progress. No crash restarts were observed after replacement.
- Portfolio reconciled; orders, portfolio and operations observations fresh; zero active operational incidents at verification.
- Existing authorized v13 strategy digest preserved. Operational observation window remains **4 of 20 qualifying sessions**; no additional reliability history was manufactured.
- No broker orders were submitted by this verification work.

## Validation evidence

- Full executor suite: **321 passed**.
- Ruff lint: passed. Ruff formatting: all 40 source/test files already formatted.
- Mypy: passed for all 25 source modules.
- Git whitespace check for executor changes: passed.
- Paper API connectivity and an inert v13 plan were checked with `executor check`; the command did not submit orders.
- Failure tests cover uncertain POST outcomes, invalid acknowledgments, restart-persistent receipts and retry budgets, corrupt state, insufficient storage, stale reconciliation, account identity, session windows, shutdown, process locks, and health/readiness.
- Mailgun transport tests use fake HTTP responses: US/EU endpoints, configuration validation, sanitized failures, redirect refusal, no automatic POST retry, persistent alert suppression and unlimited-cap readiness behavior. No actual email has been sent.

## State preservation

Authoritative Compose volume: `executor_executor-state`.

A consistent backup was made with the old daemon stopped:

`/home/supasuge/.local/state/amat-executor/backups/20261008-vps-hardening/executor-state.tgz`

Archive SHA-256: `6e133236a3d7ed62f94171df1f59fe1244326dce3838e7ff40345d46c7fe01c4`.

The archive contained 45 members and was restored into an isolated temporary directory. JSON records were readable, and the deployment registry SQLite integrity check returned `ok`. Image/source manifests are stored alongside the backup; final manifests are named `final-image-manifest.json` and `final-source-manifest.json`. The backup predates subsequent monitoring observations; make a fresh stopped-state backup for the actual VPS handoff.

## Remaining activation and destination checks

Mailgun support is implemented for daily reports and operational failure alerts. Host credentials were verified using a read-only domain-list request. The account exposes an active custom domain, `grizzhacks8ctf.us`, and an active sandbox, `sandbox277ddd0b68e7415b987ef90bfd200ad9.mailgun.org`. The operator still needs to select the sending domain and recipient address. Mailgun is not activated in the executor environment, and end-to-end inbox delivery remains untested. Credentials are not included in this report or the image.

The final local `executor health --ready` reports exactly three remaining issues: dry-run enabled, executor disabled, and mail alerts not configured. Unlimited caps do not fail readiness.

The destination VPS has not been supplied or tested. Follow `docs/executor/VPS_RUNBOOK.md` to transfer the current source and complete authoritative state, stop the old host before starting the new one, verify clock synchronization, restore and reboot behavior, and configure an independent off-host outage alert. The local singleton lock cannot coordinate two hosts. The executor cannot notify through Mailgun when its entire VPS or network is down.

Leave both submission switches in their current safe states through migration and observation. Enabling paper submissions requires a deliberate operator configuration change after the remaining checks.

## Telegram follow-up (supersedes notification readiness above)

The subsequently deployed image is
`sha256:bfab520f0ff5d9df2b1171da879b77fd1e7f39c13174a396e096656f3d486ded`.
All 26 installed modules matched the working source. Validation: **327 tests
passed**, Ruff and Mypy passed, and the restarted local daemon reported healthy.

Missing notification configuration is now a warning only; it is not an order
readiness failure. Unlimited caps remain unchanged. Telegram is supported as the
primary incident channel and an additional daily-report destination. Transport
failures are nonfatal, requests have a five-second timeout, and duplicate
identical incident alerts are suppressed for an hour. Missed scheduler windows
now emit an incident notification too.

The supplied Telegram credential was verified against `getMe` as
`ShitcoinSeekingBot`, and is stored only in the private ignored `.env` file.
There was no existing webhook and no available destination chat. The operator
was asked to send a unique setup message in the private bot chat. Chat setup
and real notification delivery remain pending; no Telegram messages were sent.
Both dry-run and submission-disabled switches remain enabled. Mailgun setup
is optional and no longer blocks this Telegram-based workflow.
