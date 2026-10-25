# Project sources and chat-history provenance

This README was checked against the project's own conversation history and repository evidence on October 25, 2026. Conversation titles below are reproduced exactly. They establish design intent and the chronology of decisions; source/configuration and dated measured evidence support implementation and numerical claims.

Private chats are not public citations that a GitHub visitor can open. Their IDs are included for the owner to locate them in Codex. No private conversation was shared or published wholesale. The linked repository documents and curated numeric extract let readers assess the claims without access to those chats.

## S1 — Retained equity strategy and campaign results

**Chat: “Improve AMAT strategy engine”** — `01a151b1-467e-7561-aba1-c8d5f097afae`.

Reviewed the completed October 23–24 campaign and subsequent October 24 namespace/plan-parity maintenance turns. The campaign's final answer retains v14.3.0 after five hypotheses, ten rejected candidate arms and zero promotions. It reports baseline cumulative returns of 296.39%, 259.80% and 221.88% at 2/6/10 bps per side, and distinguishes native QQQ from the idealized total-return reference. Later maintenance verified source attestation and an inert native plan envelope without changing the strategy, paper execution or economic claims.

Public repository sources:

- [Final campaign analysis](research/research-2026-10-24-adaptive-campaign-final.md): sample, costs, all candidates, QQQ comparisons, annual/stress diagnostics and limitations.
- [Retained strategy configuration](../amat/configs/strategy/amat_group_selection_volatility.toml): exact v14.3.0 settings.
- [Published performance explanation](strategy-performance.md) and [curated numerical extract](assets/evidence/strategy-summary.json): the README's selected values and source identities.
- [Post-campaign namespace repair](research/repair-2026-10-24-risk-package-attestation.md) and [native-plan/broker contract audit](research/research-2026-10-24-native-plan-broker-parity.md): later engineering status, separate from investment returns.

The original machine comparison and account histories remain private/generated evidence under `amat/outputs/research-campaigns/2026-10-23-adaptive-12h/`. The curated extract retains their exact hashes and relative provenance paths; it is not a new backtest or a substitute for full account evidence.

## S2 — Selection mechanism and older baseline

The same **“Improve AMAT strategy engine”** history covers the selection and failed challenger studies leading to the retained default. The [October 19 default study](research/volatility-default-next-results-2026-10-19.md) records its earlier results and acceptance rules.

The README deliberately uses the later October 23–24 native accounting/control reproduction. Earlier reports can quote different returns for the same named strategy because their accounting/source/evaluation records differ. Do not splice the strongest value from each report into a single “best” result or treat an accounting correction as alpha promotion.

The actual switching implementation is [adaptive.py](../amat/src/amat/portfolio/adaptive.py); risk/trend/constraint parameters are in the pinned TOML. These source files support the “How the strategy works” explanation.

## S3 — Current UI, monitoring and remote topology

**Chat: “Improve operations UI and health”** — `01a169bf-39f9-7703-a698-3ffe0b6f2b2c`, October 23, 2026.

Reviewed the initial request, the operator's choices and the approved implementation result. The final decision is a guided registered-task CLI, automatic diagnostics only while the local API runs, compression after seven days without archive deletion, and three retained remote applications. Shadow is retired with state preserved; alerts watch the main account. Earlier suggestions in the discussion to use a VPS diagnostic timer were superseded by the operator's explicit API-lifecycle choice.

Public sources: [approved plan](ui-remote-operations-plan-2026-10-23.md), [terminal guide](frontend/README.md), [remote interface](frontend/REMOTE.md), [archive contract](frontend/deploy/paper-monitor/ARCHIVES.md), and [current architecture](architecture.md). Real deployment/restore receipts remain private under `operations/`.

The README screenshots were captured from the actual local UI on October 25. They illustrate implementation; they do not recertify the October 23 deployment. See [asset provenance](assets/README.md).

## S4 — Perpetual strategy and paper/alert boundaries

**Chat: “Assess crypto strategy engine fit”** — `01a156a5-596e-7d73-a01d-544bf2f681d4`, reviewed October 22 paper/entry/notification turns.

The discussion distinguishes completed proxy-candle signals, native execution books/funding, separate paper account identities, frozen profiles and a prospective shadow experiment. Historical startup observations with zero fills are not statistical evidence of profitability. The older two-account notification configuration is superseded by S3's later main-account-only decision; do not revive shadow from the old description.

Public sources: [Kalshi guide](kalshi-perps/README.md), [strategy API](kalshi-perps/STRATEGY_API.md), [trade-alert contract](kalshi-perps/TRADE_ALERTS.md), and the [Kalshi component guide](components/kalshi-perps.md).

## S5 — Frontend/executor integration intent

**Chat: “Plan frontend and executor redesign”** — `01a15a31-006e-7ef2-ac17-bfb213942e06`, reviewed October 20–21 UI/deployment turns.

The discussion establishes a local terminal integrating remote paper execution, removal of dead/redundant controls, readable structured output and reliable task previews. Later S3 decisions define the current interaction/lifecycle. Current route implementations, registered capability schemas and [HTTP documentation](http-api.md) determine what is executable; an old design request alone is not an implemented capability.

## Citation policy

Use dated results with their source identity, exact sample and costs. Report historical failures and later repairs as separate facts. Do not use an old chat's “running/healthy” statement as present telemetry, an old test count as this preparation's test result, or synthetic adapter checks as market performance. This preparation's actual verification belongs in [its own record](publication-verification-2026-10-25.md).
