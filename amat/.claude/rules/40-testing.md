---
paths:
  - "tests/**"
  - "src/**"
---

# Testing rules

- Every defect fix gets a regression test that fails before the fix.
- Prefer small hand-calculated fixtures for signal, sizing, cost, fill, and accounting mathematics.
- Add property tests for bounded signals, nonnegative long-only weights, gross-exposure caps, cash residual, deterministic output, and accounting identities.
- Include future-mutation tests: changing observations after date `t` must not alter outputs through `t`.
- Include synthetic split and dividend tests.
- Include engine-parity fixtures with simplified costs and fills before comparing realistic runners.
- Never weaken tolerances, skip tests, or change fixtures solely to make a failing implementation pass.
- Keep tests independent of live APIs. Network tests require explicit markers and recorded/synthetic fixtures.
- A test that asserts only that code runs is not sufficient for financial logic; assert expected state and invariants.
