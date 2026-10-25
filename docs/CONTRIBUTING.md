# Contributing

Read the [architecture](architecture.md), [strategy contracts](strategy-api.md) and [workspace instructions](../AGENTS.md) before changing behavior. This repository currently retains proprietary package metadata and has no root redistribution license. Coordinate contribution/usage terms with the owner; a GitHub upload alone does not grant an open-source license.

## Make a focused change

Inspect `git status` first. Preserve existing modified/untracked source and take a source/hash baseline before substantial changes. Do not reset, clean, overwrite unrelated work or assume HEAD contains the whole application. Prefer a `codex/` branch prefix for agent-created branches unless the owner specifies otherwise.

Keep one owner at a time for shared UI files, styles/renderers and remote helper/collector contracts. If parallel work is explicitly requested, assign disjoint components/tests and agree interfaces before integration. Remote deployment has one sequential owner.

Describe the concrete problem, final behavior and compatibility implications. Keep research findings separate from implementation claims. Add documentation where the contract changes, and link new cross-system guides from the README table of contents. Use a new dated study/output directory for experiments; preserve unsuccessful candidates and immutable evidence.

## Maintain boundaries

- Research code produces decisions/plans and cannot gain broker authority.
- Executor owns Alpaca order safety, account binding, receipts and uncertain-outcome recovery.
- Kalshi owns its native persistent paper accounts, history identity, funding/order deduplication and singleton writers.
- The terminal uses registered typed capabilities, expiring exact previews and durable idempotency. No arbitrary shell input from the browser.
- Monitoring stays read-only; missing data remains unknown with source times and stale labels.
- Paper/live states, credentials and enablement remain separate. A test or refactor must not activate live behavior.

## Coding and tests

Use the existing package configuration for typing, Ruff formatting/lint and Python/TypeScript conventions. Favor explicit domain units and decimal strings at money/quantity wire boundaries. Validate timestamps, chronology and schemas where external data enters. Keep errors/output escaped and redacted. Do not log environment dictionaries or private configuration.

Run [the applicable gates](testing.md). Add meaningful regressions for a changed contract or failure mode, rather than tests that mirror implementation details. Use temporary state and mock providers/brokers/notifications. Resource optimizations require equivalent signals/accounting evidence, including late funding and restart recovery. UI changes require per-table/record and full-document layout checks at desktop/tablet/mobile widths.

## Pull request content

State the trigger/problem and resulting behavior, followed by validation and material limits. Include exact test scope and baseline failures. Explain schema/source/config compatibility and state migration requirements where relevant. Do not include secrets, account exports, private infrastructure details or fabricated “live” receipts. Historical results should identify data periods, costs and selection assumptions.

Before a commit, run the publication checker, inspect `git diff --cached --stat` and review the staged patch privately. Use `--staged` to check the exact upload candidate. Keep lockfiles and blank templates; exclude environments, dependencies, archives, account databases and generated evidence. See [GitHub preparation](github-publication.md).

Deployment is a separate operational action. Use selective manifests, fresh backup/restore evidence and current deployed identities after local gates. Do not roll back code by overwriting newer account state.
