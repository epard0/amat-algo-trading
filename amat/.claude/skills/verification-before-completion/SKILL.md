---
name: verification-before-completion
description: Require fresh, scope-appropriate command evidence before an AMAT implementation or review is described as complete.
user-invocable: false
---

Before claiming completion:

1. inspect the final diff and list affected invariants;
2. run bundle validation when `.claude/` changed;
3. run the narrowest relevant tests and reproduce any fixed defect;
4. run configured lint, format, type, and full tests appropriate to scope;
5. run leakage/accounting/parity checks when scientific behavior changed;
6. confirm raw/validated artifacts and credentials were not modified or exposed;
7. record exact commands, exit status, and skipped/unavailable checks;
8. report remaining uncertainty and pre-existing failures separately.

Never say “passes,” “verified,” or “complete” based on code inspection alone, stale output, a subagent summary, or a test that was not run.
