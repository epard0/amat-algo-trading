---
paths:
  - "docs/**"
  - "README.md"
  - "**/*.md"
  - "configs/**"
---

# Documentation rules

- Write answer-first documentation with exact commands, inputs, outputs, and failure behavior.
- Separate normative contracts from explanatory material. Mark assumptions and non-goals explicitly.
- Every metric defines numerator, denominator, annualization, treatment of missing values, and cash flows.
- Every data field documents timezone, adjustment status, units, source, and nullability.
- Every experiment report records configuration, data hash, code revision, costs, benchmark, sample, and caveats.
- Use absolute dates and explicit session timing; avoid “today,” “next bar,” or “market open” without timezone and calendar semantics.
- Update documentation in the same change that alters public behavior or scientific interpretation.
