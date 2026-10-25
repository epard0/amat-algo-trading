# Configuration and API keys

Credentials belong to the operator's process environment or permission-checked private files. Use your own accounts, entitlements and provider quotas. Never put credentials into strategy TOML, command arguments, research prompts, screenshots, issue reports, notebooks or committed output.

## Integration map

| Integration | Configuration | Used by | When needed |
| --- | --- | --- | --- |
| Alpaca historical market data | `ALPACA_MARKET_API_KEY`, `ALPACA_MARKET_API_SECRET` | AMAT data client; executor plan refresh | Authenticated data reads, with your feed entitlement |
| Alpaca paper trading | `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET` | Executor paper broker | Paper account observations/submissions |
| Alpaca live adapter | Separate `ALPACA_LIVE_*` fields in live example | Optional executor live path | Separate explicit live setup; outside approved deployment |
| OpenAI | `OPENAI_API_KEY` | Model router | Selected OpenAI targets |
| Gemini | `GEMINI_API_KEY`; router also accepts `GOOGLE_API_KEY` | Model router | Selected Gemini targets |
| OpenRouter | `OPENROUTER_API_KEY` | Model router | Selected OpenRouter targets |
| NVIDIA / Groq | `NVIDIA_API_KEY` / `GROQ_API_KEY` | Model router | Selected compatible targets |
| Kalshi authenticated venue transport | `KALSHI_PERPS_KEY_ID`, `KALSHI_PERPS_PRIVATE_KEY_PATH` by default | Optional authenticated venue client | Explicit demo/live venue integration |
| Public proxy history and native public books | No key for supported public reads | Kalshi paper runtime | Public forward paper observations; network still required |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional executor notifications and perps alerts | Explicitly enabled delivery |
| Mailgun | `MAILGUN_DOMAIN`, `MAILGUN_API_KEY`, `MAILGUN_REGION` | Executor report/alert transport | Optional HTTPS mail delivery |
| SMTP | `SMTP_*`, `REPORT_SENDER`, `REPORT_RECIPIENTS` | Executor reports | Optional SMTP delivery |
| Remote SSH | Private `remote.toml`, encrypted key and trusted `known_hosts` | Local gateway / remote helpers | Explicit remote engine observations and controls |

Alpaca paper and market-data credentials are distinct roles. A successful data request does not authorize trading. The newsdesk client reads credential variable names from its provider configuration; inspect that configuration before supplying keys rather than assuming every provider uses the AMAT defaults.

## Research environment and model policy

Use [amat/.env.example](../amat/.env.example) as a blank starting point. The project does not assume a globally installed dotenv loader. Pass `uv run --project amat --env-file amat/.env …`, export variables in your shell/session manager, or use the `./research` wrapper, which opts into `amat/.env` when no `UV_ENV_FILE` is supplied.

`AMAT_MODEL_CHAIN` selects a reviewed TOML provider/model chain; [the example](../amat/configs/autoresearch/llm-chain.example.toml) shows its shape. Model names in dated research reports record prior configuration. Confirm availability and pricing in your own provider account before running. Failed requests and exhausted quota are recorded evidence, not successful reviews. Bound campaign and model budgets before unattended work. `AMAT_COST_LEDGER` can relocate the SQLite usage ledger from its default outside the checkout.

## Executor configuration

The complete field commentary is in [the paper example](../executor/.env.example). Set `AMAT_ROOT`, `STRATEGY_CONFIG`, `SNAPSHOT`, and `STATE_ROOT` to valid paths in the environment that actually runs the daemon. Container paths differ from workstation paths.

Compute `AUTHORIZED_CONFIG_SHA256` from the exact strategy TOML with `sha256sum`; it is an allowlist, not an arbitrary strategy version label. Review finite session, symbol and order limits. Empty/malformed caps fail validation; the literal `unlimited` is an explicit policy choice in supported fields. Leave `DRY_RUN=true` and `EXECUTOR_DISABLED=true` while configuring a new installation. Runtime `STOP_SUBMISSIONS` blocks future submissions without claiming cancellation or liquidation.

Mail is disabled when unconfigured. Partial notification configuration may fail validation. Mailgun takes precedence over SMTP when configured. Telegram destinations and recipients are private operational data. Ordinary verification mocks all notification boundaries.

The optional [live example](../executor/.env.live.example) is separately trackable and contains no credentials. Its presence does not authorize a live deployment. Keep account binding, live enablement, dry run, caps and receipts separate from paper state.

## Terminal configuration

| Setting | Purpose |
| --- | --- |
| `TERMINAL_STORAGE` | Local jobs/events/SQLite storage; defaults to `frontend/.runtime` |
| `TERMINAL_EXECUTOR_STATE` | Local executor evidence path, separate from authoritative remote state |
| `TERMINAL_PERPS_TARGET=remote` | Select remote adapters and API-owned health scheduling |
| `TERMINAL_REMOTE_CONFIG` | Override private SSH configuration path |
| `TERMINAL_ENABLE_BROKER_READS=1` | Explicit optional broker read access |
| `TERMINAL_ENABLE_EXECUTOR_ACTIONS=1` | Explicit optional executor controls; native safeguards still apply |
| `TERMINAL_ALLOWED_ORIGINS` | Local origin allowlist; launcher fills its own loopback origins |

Do not enable controls to make a read-only dashboard populate. Missing data should stay unknown. Local host/origin/session/CSRF checks remain required even on loopback.

Connection identity has **no source defaults**: `host`, `port`, `username`, `key_path` and `known_hosts` must all be present in the private file. Missing fields fail before connecting. See the complete [private remote configuration guide](remote-configuration.md).

The blank [SSH template](../frontend/config/remote.example.toml) uses placeholder infrastructure values. Copy it to `frontend/.runtime/remote.toml`, edit locally and set mode `600`. The transport rejects private files with unsafe permissions and unknown/changed host keys. Obtain the server fingerprint independently. The key passphrase decrypts the private key; it is not the SSH login password. Do not echo the file during diagnosis.

## Kalshi configuration and account identity

`kalshi-perps/configs/paper.toml` separates mode, account/database, instruments, strategy, paper economics, venue and risk settings. Relative paths resolve against the config file. The strategy points to a source-pinned profile and the sibling AMAT interpreter. `[venue]` selects environment-variable names for credentials; the paper transport does not acquire venue order-write authority.

Changing a policy/source selection requires an explicit new account identity/state path, rather than reinterpreting old account events. Preserve the fixed-origin history anchor and source hashes. The risk overlay, actual integer fills, fees and native funding can make paper results differ from research targets. Configure notifications separately; alert account selection defaults to the main account.

See [security](../SECURITY.md) for exposure response and [operations](operations.md) for safe release handling.
