# Backtest scenario contract

Goal: expose research candidates to historical regimes, alternate joint price paths,
and execution failures using the same signal and accounting engine.

Owner: AMAT research. No executor changes or trading authority.

Baseline: the frozen program baseline, rerun on exactly the same scenario as each
candidate. Existing historical score and promotion rules remain the primary gates.

Implementation: historical bull/bear × quiet/volatile attribution in every new
program; extended suite with 17 declared synthetic market/distribution scenarios,
joint block bootstrap paths, and six execution stresses. The short research command
uses extended coverage by default; explicit initialization may choose historical.
All scenario seeds, paths,
settings, source digests, fits, and outcomes must be reproducible and inspectable.

Data: only the program development panel through train_end. Synthetic scenarios
use pre-measurement volatility calibration and the frozen exchange calendar.
Bootstrap paths resample whole joint rows, including context inputs. Rebuilt
total-return worlds have raw=adjusted prices and no separate corporate actions.
Real historical execution stresses retain the real corporate-action ledger.

Unacceptable errors: reuse original signals on alternate prices; count cash
dividends twice; leak checkpoint history into scenario construction; label
hypothetical returns as historical evidence; fabricate probabilities from a handful
of scenarios; silently omit failures; splice disjoint regime days into a drawdown.

Evaluation: paired candidate/baseline outcomes, coverage, worst drawdown, exposure,
missed fills, and warnings. Scenario failures are diagnostics, not a new reward for
winning invented markets. Accounting/runtime failures fail closed. The suite is
frozen at initialization so agents cannot edit scenarios to flatter a candidate.

Generator version 2 adds Gaussian, Student's t (df 3 and 5), Laplace, skewed factors,
Gaussian variance mixtures, GJR-GARCH volatility clustering, gap up/down, flat
markets, and a liquidity crisis. The suite can repeat each synthetic recipe with
1–8 independently seeded paths. Defaults produce 25 paired cases. Incomplete
scheduled-event coverage fails integrity; extreme numerical paths fail explicitly
without clipping. Return-shape diagnostics and exact empirical 95%/99% tail losses
accompany both market paths and candidate/baseline account returns.

Verification: distribution variance/correlation, tail frequency, skew, causal
volatility clustering, deterministic generator/context resampling, price/range/volume
invariants, calibration cutoff, baseline pairing, missed-fill injection, alternate
path signal recomputation, gap/halt/recovery behavior, short-window coverage,
corporate-action normalization, exact tail probability mass, regime attribution lag,
and a real frozen-workspace
experiment. Source remains fixed while integration tests run.

Additional user requirement: every completed research experiment receives an LLM
explanation of whether its results are good/bad and why. This runs after the
durable keep/discard decision, uses verified development evidence, shares the
existing model budget, and records provider failures without fabricating prose or
changing the measurement. It also explains hypothetical-scenario limitations.
