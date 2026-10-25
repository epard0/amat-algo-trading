# Research dataset extension — 2026-09-14

The updater creates new datasets. Original raw snapshots and frozen sessions remain intact. The registry in `data/research-datasets.json` lists successfully verified extensions; `data/last-update.json` records failures as well as successes.

## Verified coverage

| Dataset | Previous end | New last session | Added data | Total |
|---|---|---|---|---|
| Daily core, 30 symbols | 2026-08-10 | 2026-09-11 | 23 sessions; 690 daily rows and 690 execution-window rows | 2,688 sessions; 80,640 daily rows; 100% execution-window coverage |
| Minute, SPY/QQQ/IWM/GLD | 2025-12-31 | 2026-09-11 | 174 regular sessions; 592,093 bars including extended hours | 3,560,781 bars; 20 symbol/year partitions |

The added minute period has 696 complete regular-session asset-days and zero excluded asset-days under the existing panel checks. The request end is 2026-09-13 inclusive; the last observed trading session is 2026-09-11. The 92- and 128-symbol daily snapshots were not refreshed in this update.

Selected daily path: `/home/supasuge/Documents/amat-workspace/amat/data/snapshots/alpaca-sip-core-2026-09-14-verified`.

Extended minute path: `/home/supasuge/Documents/amat-workspace/amat/data/minute/alpaca-sip-2022-2026-09-14`.

## Corrected SHY distribution

The freshly downloaded Alpaca action partition repeats the old conflict for SHY on 2021-12-01: 0.018007 and 0.031906. The previous exception ledger selected only 0.018007 based on the surrounding payment schedule. That inference was incorrect.

The [issuer's 2021 distribution summary, page 3](https://www.ishares.com/us/literature/tax-information/2021-ishares-distribution-summary.pdf) explicitly reports **0.049913 per share** for that ex-date and **0.223854** for the year. The reported ex-date amount equals the two vendor components combined. The new derived snapshot replaces those exact two rows with the issuer-confirmed total. Its provenance records the raw snapshot digest, observed values, source URL, and page. Any different source values fail the correction check.

This does not establish the size of the performance effect on earlier strategy runs. Earlier frozen runs retain their original data and exception ledger; their SHY dividend accounting needs reassessment before relying on precise performance comparisons. The new snapshot has a different data identity, so prior runs are not automatically treated as compatible research memory.

## Coverage and research boundaries

Daily raw and adjusted prices and corporate actions are refreshed over the entire history. Only complete, hash-verified historical execution windows are reused. New or previously incomplete windows are fetched. Minute partitions retain their hashes and original timestamps; no missing bars are filled. Incomplete asset-days remain excluded by the existing minute panel rules.

The ordinary daily command retains the original snapshot and development cutoff. `./research run 5 --latest-data` explicitly selects the new daily snapshot and latest available checkpoint end, keeping development end at 2022-12-30. Current intraday sessions still develop on 2022–2024 and validate on 2025; the 2026 extension is saved for a separate validation study.

## Verification

292 targeted tests passed; Ruff passed for changed Python files and mypy passed for the CLI. Re-running the completed update verified and reused the datasets successfully. Tests cover shortcut routing, frozen-runtime selection, no-bytecode imports, explicit dataset selection, tampered source rejection, issuer correction, immutable raw data, snapshot quality, and read-only provider access. CLI help and latest-session status were exercised without starting a strategy batch. No backtest performance is claimed for the extended data.
