# Flexible strategy research: 100-strategy results

Executed after implementation and testing: 1,727 tests passed, one skipped; lint and type checks passed.

```bash
./research 100 --no-open --workspace /home/supasuge/Documents/amat-research/flexible-100-strategies-20260911 --max-model-tokens-per-day 2000000
```

The session also used a frozen research brief covering simple price/volume rules, long-horizon patterns, routing and interactions.

Completed 100 distinct strategy evaluations plus the baseline. 0 candidates were escalated; 0 measurement/proposal attempts failed.

Frozen program: `67b091bdd9e3cf09c66443be21b1dd3b4350a90b9a9d93718b129f2206658634`. Measured development period: 2018-01-02 to 2022-12-30. Extended robustness suite; no validation-period or live-trading evidence.

The batch ran for 3 hours, 25 minutes, 51 seconds. All 100 proposals came from
built-in search after the model proposer chain did not return a usable proposal.
The broader search exercised fresh exploration (30), refinement (22), policy
changes (14), combinations (10), routing (4), patterns (4), ablations (6), and
initial screening (10, including the first fallback recorded as manual).

The baseline was retained. Ten candidates cleared the score hurdle, but none
also passed eligibility. Promotion uncertainty testing was therefore skipped
for all 100; this is not a finding that 100 statistical tests failed.

The median candidate annual return was 2.91%, with median Sharpe 0.546; 11
candidates had negative annual returns. The best headline return came from
experiment 63, a simplified smoothed 76-session trend-agreement signal: 4.46%
annual return, 0.818 Sharpe, -7.19% drawdown, and 0.761 stressed-cost Sharpe.
Its research score was only 0.833, and after matching exposure its Sharpe was
0.745 versus the baseline's 0.765. The highest score (experiment 73) also failed
that exposure-matched comparison. These observations point to a mismatch between
what the search score rewards and the incremental-value requirement; they do not
justify relaxing the promotion checks or claiming an improved strategy.

All 100 unsuccessful candidates have seven recorded analytical standpoints.
There were 57 completed LLM reviews including the baseline; 31 were unavailable
because of providers/invalid responses, and 13 because the remaining daily budget
could not cover another request. Actual known usage was 668,150 input plus
93,707 output tokens. Charged usage was 1,979,791 because some requests had
unknown usage and retained conservative reservations.

The cross-session library now contains 165 unsuccessful attempts from 18
sessions, with no skipped sessions. Existing ledgers and measurements were read
without changing their decisions. Final verification matched all 120 frozen
input hashes, 14,006 measurement hashes, 13 export hashes, all saved review
hashes, and row counts for every CSV table.

## Baseline and ten highest research scores

| Experiment | Stage | Annual return | Sharpe | Drawdown | Stress Sharpe | Score | Decision |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | baseline | 4.01% | 0.765 | -7.89% | 0.660 | 0.8061 | baseline |
| 73 | policy | 3.81% | 0.687 | -7.33% | 0.640 | 1.0554 | discard |
| 97 | combine | 3.91% | 0.710 | -7.22% | 0.662 | 1.0406 | discard |
| 90 | ablate | 3.66% | 0.639 | -8.66% | 0.569 | 1.0203 | discard |
| 88 | combine | 3.58% | 0.651 | -8.19% | 0.594 | 0.9748 | discard |
| 51 | refine | 3.47% | 0.655 | -7.38% | 0.594 | 0.9262 | discard |
| 70 | combine | 3.36% | 0.630 | -7.30% | 0.558 | 0.9148 | discard |
| 78 | refine | 3.29% | 0.629 | -8.11% | 0.557 | 0.9125 | discard |
| 79 | combine | 3.42% | 0.624 | -8.74% | 0.565 | 0.9101 | discard |
| 52 | combine | 3.36% | 0.631 | -7.56% | 0.559 | 0.8950 | discard |
| 48 | refine | 3.34% | 0.637 | -7.66% | 0.563 | 0.8833 | discard |

Ranking here is descriptive. Actual escalation compares each candidate with the incumbent at that point and requires the frozen quality and uncertainty checks. Repeated adaptive trials are not independent validation.

## Failure and resource accounting

```json
{
  "gate_failures": {
    "no_incremental_exposure_matched_information": 97,
    "nearby_parameter_instability": 29,
    "exposure": 5,
    "execution_delay_sensitivity": 16,
    "positive_fold_fraction": 25,
    "stress_sharpe": 19,
    "control_runner_warning": 8,
    "router_has_no_incremental_static_blend_value": 4,
    "drawdown": 2
  },
  "stages": {
    "manual": 1,
    "screen": 9,
    "route": 4,
    "pattern": 4,
    "explore": 30,
    "refine": 22,
    "combine": 10,
    "policy": 14,
    "ablate": 6
  },
  "sources": {
    "built-in": 100
  },
  "reviews": {
    "complete": 57,
    "unavailable": 44
  },
  "score_hurdle_failed": 90,
  "uncertainty_not_run": 100,
  "uncertainty_evaluated_failed": 0
}
```

Each of 100 unsuccessful attempts has seven evidence-based standpoints. Missing LLM reviews remain explicitly unavailable. Proposal sources and review availability are counted above.

```json
{
  "budget_charged_tokens": 1979791,
  "event_count": 1261,
  "experiment": null,
  "known_input_tokens": 668150,
  "known_output_tokens": 93707,
  "pending_calls": 0,
  "phase": "finished",
  "phase_elapsed_seconds": null,
  "phase_started_at": "2026-09-12T05:20:58.081489+00:00",
  "provider_calls": 126,
  "provider_errors": 69,
  "status": "batch_completed",
  "unknown_usage_calls": 45,
  "writer_active": false
}
```

Token budget charges can include conservative reservations; they are not dollar costs or a substitute for known actual usage.

## Verified artifacts

```json
{
  "program_sha256": "67b091bdd9e3cf09c66443be21b1dd3b4350a90b9a9d93718b129f2206658634",
  "batch": {
    "completed": 100,
    "escalated": 0,
    "experiment_ids": [
      2,
      3,
      4,
      5,
      6,
      7,
      8,
      9,
      10,
      11,
      12,
      13,
      14,
      15,
      16,
      17,
      18,
      19,
      20,
      21,
      22,
      23,
      24,
      25,
      26,
      27,
      28,
      29,
      30,
      31,
      32,
      33,
      34,
      35,
      36,
      37,
      38,
      39,
      40,
      41,
      42,
      43,
      44,
      45,
      46,
      47,
      48,
      49,
      50,
      51,
      52,
      53,
      54,
      55,
      56,
      57,
      58,
      59,
      60,
      61,
      62,
      63,
      64,
      65,
      66,
      67,
      68,
      69,
      70,
      71,
      72,
      73,
      74,
      75,
      76,
      77,
      78,
      79,
      80,
      81,
      82,
      83,
      84,
      85,
      86,
      87,
      88,
      89,
      90,
      91,
      92,
      93,
      94,
      95,
      96,
      97,
      98,
      99,
      100,
      101
    ],
    "failed_attempts": 0,
    "requested": 100
  },
  "frozen_hashes_verified": 120,
  "measurement_hashes_verified": 14006,
  "export_hashes_verified": 13,
  "failure_records": 100,
  "rows": {
    "daily": 254318,
    "fills": 249400,
    "folds": 505,
    "missed": 4982,
    "providers": 126,
    "scenarios": 2222,
    "strategies": 101,
    "timings": 402
  }
}
```

[Comparison report](/home/supasuge/Documents/amat-research/flexible-100-strategies-20260911/report.html) · [Failure analysis](/home/supasuge/Documents/amat-research/flexible-100-strategies-20260911/analysis/failures.html) · [Strategy CSV](/home/supasuge/Documents/amat-research/flexible-100-strategies-20260911/analysis/strategies.csv)

## All completed strategies in evaluation order

| Experiment | Stage | Annual return | Sharpe | Drawdown | Stress Sharpe | Score | Decision |
|---|---|---:|---:|---:|---:|---:|---|
| 2 | manual | 3.72% | 0.746 | -7.52% | 0.630 | 0.7149 | discard |
| 3 | screen | 3.54% | 0.630 | -9.36% | 0.569 | 0.5063 | discard |
| 4 | screen | 3.80% | 0.709 | -7.40% | 0.611 | 0.8372 | discard |
| 5 | screen | 2.36% | 0.512 | -7.97% | 0.325 | 0.3774 | discard |
| 6 | screen | 3.05% | 0.580 | -8.08% | 0.476 | 0.7398 | discard |
| 7 | screen | 1.66% | 0.592 | -2.83% | 0.071 | 0.7131 | discard |
| 8 | screen | 1.80% | 0.375 | -7.47% | 0.305 | 0.2747 | discard |
| 9 | screen | 3.06% | 0.605 | -8.84% | 0.434 | -0.0636 | discard |
| 10 | screen | 0.89% | 0.164 | -10.34% | -0.545 | -0.8099 | discard |
| 11 | screen | 2.39% | 0.332 | -15.55% | 0.327 | -0.6228 | discard |
| 12 | route | 0.02% | 0.003 | -10.08% | -0.140 | -1.2747 | discard |
| 13 | pattern | 0.84% | 0.329 | -3.64% | -0.032 | 0.1565 | discard |
| 14 | explore | -0.52% | -0.074 | -15.08% | -0.153 | -1.2017 | discard |
| 15 | refine | 4.16% | 0.794 | -7.84% | 0.693 | 0.8551 | discard |
| 16 | combine | 3.17% | 0.609 | -7.89% | 0.508 | 0.7867 | discard |
| 17 | explore | 1.94% | 0.305 | -10.48% | 0.221 | -0.5705 | discard |
| 18 | policy | 3.23% | 0.603 | -7.41% | 0.520 | 0.5740 | discard |
| 19 | policy | 2.67% | 0.499 | -7.41% | 0.425 | 0.4594 | discard |
| 20 | explore | -1.17% | -0.265 | -11.10% | -0.857 | -1.1189 | discard |
| 21 | route | 1.86% | 0.330 | -8.34% | 0.263 | -0.3013 | discard |
| 22 | pattern | 0.72% | 0.407 | -2.71% | 0.142 | -0.6556 | discard |
| 23 | explore | -2.40% | -0.338 | -23.62% | -0.431 | -0.8687 | discard |
| 24 | refine | 3.76% | 0.721 | -7.94% | 0.602 | 0.7039 | discard |
| 25 | combine | 3.41% | 0.656 | -7.70% | 0.558 | 0.8551 | discard |
| 26 | explore | 2.22% | 0.317 | -14.30% | 0.312 | -0.5580 | discard |
| 27 | policy | 3.23% | 0.624 | -7.81% | 0.540 | 0.8246 | discard |
| 28 | policy | 3.44% | 0.660 | -7.71% | 0.593 | 0.8402 | discard |
| 29 | explore | -0.75% | -0.147 | -11.78% | -0.626 | -0.2143 | discard |
| 30 | route | 0.02% | 0.003 | -10.08% | -0.140 | -1.2747 | discard |
| 31 | pattern | 0.84% | 0.329 | -3.64% | -0.032 | 0.1565 | discard |
| 32 | explore | 1.45% | 0.220 | -13.03% | 0.063 | -0.7642 | discard |
| 33 | refine | 3.14% | 0.597 | -7.98% | 0.478 | 0.6727 | discard |
| 34 | combine | 3.17% | 0.609 | -7.89% | 0.508 | 0.7867 | discard |
| 35 | explore | 1.09% | 0.193 | -11.37% | 0.108 | -0.3821 | discard |
| 36 | policy | 2.80% | 0.514 | -9.28% | 0.441 | 0.6580 | discard |
| 37 | policy | 1.96% | 0.276 | -14.62% | 0.265 | -0.5186 | discard |
| 38 | explore | -1.68% | -0.295 | -21.86% | -0.708 | -1.2190 | discard |
| 39 | route | 1.86% | 0.330 | -8.34% | 0.263 | -0.3013 | discard |
| 40 | pattern | 0.72% | 0.407 | -2.71% | 0.142 | -0.6556 | discard |
| 41 | explore | 1.49% | 0.217 | -15.87% | 0.125 | -0.9701 | discard |
| 42 | refine | 3.44% | 0.650 | -7.72% | 0.559 | 0.8255 | discard |
| 43 | combine | 3.41% | 0.656 | -7.70% | 0.558 | 0.8551 | discard |
| 44 | explore | 2.13% | 0.353 | -11.53% | 0.332 | -0.3297 | discard |
| 45 | policy | 3.10% | 0.541 | -9.53% | 0.414 | 0.5507 | discard |
| 46 | policy | 2.25% | 0.312 | -15.65% | 0.307 | -0.6754 | discard |
| 47 | explore | 0.78% | 0.116 | -14.06% | -0.112 | -0.7583 | discard |
| 48 | refine | 3.34% | 0.637 | -7.66% | 0.563 | 0.8833 | discard |
| 49 | refine | 3.02% | 0.577 | -8.48% | 0.486 | 0.6291 | discard |
| 50 | explore | 3.52% | 0.655 | -11.34% | 0.533 | 0.4011 | discard |
| 51 | refine | 3.47% | 0.655 | -7.38% | 0.594 | 0.9262 | discard |
| 52 | combine | 3.36% | 0.631 | -7.56% | 0.559 | 0.8950 | discard |
| 53 | explore | -2.76% | -0.407 | -19.85% | -0.531 | -1.8145 | discard |
| 54 | ablate | 2.94% | 0.551 | -8.31% | 0.467 | 0.7958 | discard |
| 55 | policy | 3.61% | 0.683 | -7.17% | 0.622 | 0.8556 | discard |
| 56 | explore | 1.14% | 0.168 | -14.15% | -0.009 | -0.9337 | discard |
| 57 | refine | 3.05% | 0.581 | -8.18% | 0.499 | 0.8282 | discard |
| 58 | refine | 2.98% | 0.551 | -7.47% | 0.493 | 0.6930 | discard |
| 59 | explore | 0.65% | 0.134 | -12.95% | 0.083 | -0.3373 | discard |
| 60 | refine | 2.81% | 0.572 | -9.87% | 0.474 | 0.5278 | discard |
| 61 | combine | 3.39% | 0.636 | -7.50% | 0.565 | 0.8541 | discard |
| 62 | explore | -0.62% | -0.093 | -13.96% | -0.526 | -0.9192 | discard |
| 63 | ablate | 4.46% | 0.818 | -7.19% | 0.761 | 0.8330 | discard |
| 64 | policy | 3.31% | 0.619 | -7.04% | 0.568 | 0.8341 | discard |
| 65 | explore | 1.66% | 0.240 | -15.13% | 0.096 | -0.6692 | discard |
| 66 | refine | 2.51% | 0.507 | -8.29% | 0.339 | 0.2432 | discard |
| 67 | refine | 2.28% | 0.438 | -9.44% | 0.276 | 0.4897 | discard |
| 68 | explore | -1.75% | -0.239 | -18.71% | -0.373 | -1.1821 | discard |
| 69 | refine | 3.25% | 0.637 | -6.74% | 0.534 | 0.6700 | discard |
| 70 | combine | 3.36% | 0.630 | -7.30% | 0.558 | 0.9148 | discard |
| 71 | explore | 1.75% | 0.254 | -13.13% | 0.233 | -0.4399 | discard |
| 72 | ablate | 3.13% | 0.585 | -7.43% | 0.519 | 0.8205 | discard |
| 73 | policy | 3.81% | 0.687 | -7.33% | 0.640 | 1.0554 | discard |
| 74 | explore | 3.25% | 0.623 | -7.94% | 0.494 | 0.0973 | discard |
| 75 | refine | 3.15% | 0.576 | -8.46% | 0.510 | 0.7034 | discard |
| 76 | refine | 3.81% | 0.674 | -7.55% | 0.637 | 0.8302 | discard |
| 77 | explore | -2.16% | -0.313 | -15.52% | -0.479 | -1.5139 | discard |
| 78 | refine | 3.29% | 0.629 | -8.11% | 0.557 | 0.9125 | discard |
| 79 | combine | 3.42% | 0.624 | -8.74% | 0.565 | 0.9101 | discard |
| 80 | explore | 0.87% | 0.166 | -9.48% | -0.059 | -0.4189 | discard |
| 81 | ablate | 3.39% | 0.628 | -8.06% | 0.562 | 0.7089 | discard |
| 82 | policy | 2.22% | 0.307 | -15.57% | 0.303 | -0.6730 | discard |
| 83 | explore | 3.00% | 0.526 | -9.91% | 0.470 | 0.3368 | discard |
| 84 | refine | 2.74% | 0.523 | -8.49% | 0.418 | 0.5031 | discard |
| 85 | refine | 2.65% | 0.483 | -9.51% | 0.379 | 0.6150 | discard |
| 86 | explore | -2.20% | -0.310 | -19.93% | -0.418 | -1.0404 | discard |
| 87 | refine | 3.68% | 0.705 | -7.10% | 0.633 | 0.6395 | discard |
| 88 | combine | 3.58% | 0.651 | -8.19% | 0.594 | 0.9748 | discard |
| 89 | explore | 3.31% | 0.568 | -8.48% | 0.535 | 0.3338 | discard |
| 90 | ablate | 3.66% | 0.639 | -8.66% | 0.569 | 1.0203 | discard |
| 91 | policy | 4.14% | 0.757 | -7.19% | 0.702 | 0.8379 | discard |
| 92 | explore | 2.85% | 0.462 | -9.85% | 0.421 | 0.0272 | discard |
| 93 | refine | 3.34% | 0.593 | -9.30% | 0.517 | 0.5653 | discard |
| 94 | refine | 3.56% | 0.614 | -8.01% | 0.549 | 0.6639 | discard |
| 95 | explore | -1.74% | -0.236 | -15.87% | -0.338 | -1.6354 | discard |
| 96 | refine | 2.75% | 0.542 | -9.59% | 0.433 | 0.6959 | discard |
| 97 | combine | 3.91% | 0.710 | -7.22% | 0.662 | 1.0406 | discard |
| 98 | explore | 1.67% | 0.274 | -13.53% | 0.190 | -0.2360 | discard |
| 99 | ablate | 2.88% | 0.529 | -10.28% | 0.461 | 0.6423 | discard |
| 100 | policy | 4.01% | 0.729 | -7.22% | 0.676 | 0.5157 | discard |
| 101 | explore | 3.85% | 0.751 | -6.56% | 0.423 | 0.5743 | discard |
