---
name: engine-parity
description: Establish and preserve numerical/state parity between AMAT's fast research runner and authoritative event-driven runner under shared simplified assumptions.
user-invocable: false
---

Parity is tested in layers:

1. identical normalized inputs and calendars;
2. identical features and signal timestamps;
3. identical target weights and quantities;
4. identical simplified fill prices with no partial fills/costs/actions;
5. identical positions, cash, and gross equity within declared precision;
6. controlled reintroduction of fees, actions, missed fills, and richer order state.

Compare artifacts per session and stop at the first divergence. Do not hide differences behind aggregate metric tolerances. Classify whether the cause is input, timing, rounding, constraints, execution, or accounting. Framework-specific behavior must be adapted to the shared contract, not allowed to redefine it.
