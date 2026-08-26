---
name: market-data-integrity
description: Apply immutable, point-in-time market-data ingestion, normalization, calendar, adjustment, corporate-action, provenance, and quality-gate practices. Use for any AMAT data pipeline or data defect.
user-invocable: false
---

Apply these rules whenever market data is read, stored, normalized, or validated:

- distinguish event time, ingestion time, as-of time, and exchange session date;
- snapshot source/feed/request/pagination metadata and partition checksums;
- make ingestion idempotent and raw evidence append-only;
- validate completeness, duplicates, OHLC consistency, stale bars, session membership, and adjustment discontinuities;
- use adjusted prices only for configured features and raw prices plus explicit actions for execution/accounting;
- never double-count dividends or synthesize missing execution prices;
- fail closed on partial pagination, ambiguous actions, or schema drift;
- test with synthetic splits/dividends, missing pages/bars, duplicate rows, and deterministic reruns.

Read `${CLAUDE_PROJECT_DIR}/.claude/references/strategy-contract.md` and the data-integrity path rule for exact project semantics.
