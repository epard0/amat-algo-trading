# Real-provider iteration contract

Goal: complete evidence-grounded shadow committee reviews using the existing
Gemini SDK, and measure whether a committee finds more supported flaws than a
single reviewer. Owner: AMAT research operator. Execution authority remains none.

Success: structured, cited findings; recorded inputs/responses/usage; reproducible
offline replay; bounded request lifetime and durable daily/review usage limits.
Unacceptable mistakes: fabricated citations, a provider outage interpreted as an
investment verdict, stale response acceptance, hidden provider retries, missing
usage charged as zero, or relabeling scripted runs as model judgments.

Baseline: deterministic checks, then one general reviewer. Candidate: independent
research/portfolio/risk reviewers followed by a chief. Evaluation distinguishes
evidence faults from honest missing evidence and includes valid controls. Labels
are withheld from model inputs. Synthetic fault detection is not investment
performance evidence; reference validity does not prove semantic correctness.

Constraints: single-host SQLite coordinator, aggregate historical evidence,
explicit model ID, existing provider credential, no broker operations. Unknown
provider usage retains its reservation. No automatic model fallback. Missing
credentials/configuration stops only real-provider work; offline operation remains.

Fallback: select the fake provider for integration/replay; no model result is
fabricated when a real call fails. The prior immutable reports stay available.
