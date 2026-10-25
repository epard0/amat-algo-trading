# AMAT quick commands

Run from `/home/supasuge/Documents/amat-workspace`. Credentials load automatically from `amat/.env`; set `UV_ENV_FILE` to use a different file.

```bash
cd /home/supasuge/Documents/amat-workspace
./research                        # Show this command menu
./research run 5                  # Research five daily strategies
./research run 5 --latest-data    # Use refreshed daily data and its latest checkpoint date
./research intraday 5             # Research five minute-based strategies
./research status                 # Latest session progress
./research report                 # Open results
./research analyze                # Rebuild metrics and failure analysis
./research charts                 # Verified daily equity/drawdown/exposure charts
./research memory                 # Related daily evidence and previous mistakes
./research failures               # Open the latest session's failure report
./research data                   # Dataset coverage
./research data update            # Download newer daily core + minute bars
./research paths                  # Print important paths/configs
./research returns --help         # Explicit annual-return target research
```

For an earlier session, add `--session /absolute/session/path` to a results command. Results commands use that session's frozen controller. If the latest session is intraday, use `report`; `charts` and `memory` require a daily session.

Three useful options:

```bash
./research run 1 --robustness historical --no-open
./research run 5 --brief "Test one causal change; consult prior failures first."
./research data update --only daily    # Or --only minute; daily calendar must be current
```

`run` keeps the original daily snapshot and 2022 development cutoff for comparability. `--latest-data` explicitly selects the updated daily snapshot and advances its checkpoint end; it does not move the development cutoff. Intraday research still develops on 2022–2024 and validates on 2025. The added 2026 minute data is available for a separate validation study. No strategy is automatically promoted. Prefer `--latest-data` for new daily studies: it also corrects an issuer-confirmed SHY dividend error in the old data. [Coverage and correction details](/home/supasuge/Documents/amat-workspace/amat/docs/data-extension-2026-09-14.md).

| What to change/find | Path |
|---|---|
| Model order, provider settings | [llm-chain.example.toml](/home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/llm-chain.example.toml) |
| Strategy baseline config | [amat_v6.toml](/home/supasuge/Documents/amat-workspace/amat/configs/strategy/amat_v6.toml) |
| Credentials | [amat/.env](/home/supasuge/Documents/amat-workspace/amat/.env) |
| Downloaded data and latest update report | [data/](/home/supasuge/Documents/amat-workspace/amat/data) |
| Sessions, latest-session.json, active-strategy.json | [amat-research/](/home/supasuge/Documents/amat-research) |
| Full commands: resume, reviews, follow-ups, promotion, executor | [Advanced reference](/home/supasuge/Documents/amat-workspace/amat/docs/commands-reference.md) |

Existing `./research 5` still works. All underlying commands remain available through `./research advanced --help`. No arguments now shows help instead of launching a batch. Updates create new datasets and preserve earlier snapshots; downloads may take several minutes and can be retried. Historical minute partitions resume individually.

## Annual-return target study

The [September 14 performance review](research/return-target-review-2026-09-14.md)
records the original baseline, all three development rounds, later historical
validation, costs, drawdowns, benchmarks, and limits. The selected research
configuration is [amat_v13_return_target.toml](../amat/configs/strategy/amat_v13_return_target.toml).

Reproduce the final fixed candidate set in a **new** evidence directory:

```bash
./research returns register --recipes configs/experiments/return-target-selection-round3.json --output outputs/return-target-replay
./research returns screen --output outputs/return-target-replay
./research returns validate --output outputs/return-target-replay
```

The command works from the workspace root and resolves relative paths inside
`amat/`. It freezes the recipes and source hashes before measuring development,
selects at most one recipe for later validation, and records every result.
Existing protocols reject changed source or data. Repeated research on the same
history remains development evidence, even when a later date range is reported
separately. The target is compounded annual growth after modeled costs; it is
not a promise of a positive return in every calendar year.
