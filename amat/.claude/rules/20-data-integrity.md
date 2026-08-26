---
paths:
  - "src/**/data/**"
  - "src/**/calendar/**"
  - "src/**/corporate_actions/**"
  - "data/**"
  - "configs/data/**"
---

# Market-data integrity rules

- Raw snapshots are append-only and checksum-addressed. Never patch vendor data in place.
- Persist provider, feed, adjustment mode, ingestion time, as-of time, request parameters, pagination state, and coverage.
- Distinguish event timestamp, ingestion timestamp, and exchange session date.
- Use adjusted prices for research features only as specified; use raw prices and explicit corporate-action ledgers for execution and accounting.
- Never credit a dividend while also counting the same dividend through adjusted portfolio returns.
- Verify all pagination tokens and fail if a response is partial.
- Missing execution bars produce a missed trade, not a prior-close or forward-filled fill.
- Flag duplicates, impossible OHLC relationships, stale bars, non-session dates, unexplained split-like jumps, and unexpected coverage gaps.
- Every normalization transform must be deterministic, versioned, and independently testable.
