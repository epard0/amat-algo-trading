---
name: anti-lookahead-audit
description: Detect and prevent temporal leakage in AMAT features, universe, covariance, actions, benchmarks, fills, metrics, and validation splits.
user-invocable: false
---

Audit every transformation as an information-availability question: “Could this exact value have been known at the recorded decision time?”

Check:

- adjusted data and corporate-action metadata availability;
- rolling/window alignment and accidental centered/future-filled operations;
- target-to-fill shifting and same-bar access;
- covariance and universe cutoffs;
- benchmark and cash-rate publication timing;
- train/validation/holdout boundaries and preprocessing fit scope;
- report calculations that select periods or trades using future outcomes.

Required evidence includes future-mutation tests, explicit timestamp ordering, synthetic delayed-publication cases, and audit-friendly intermediate artifacts. A single confirmed leak invalidates affected performance results until rerun.
