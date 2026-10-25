# Strategy-development engine verification — 2026-09-11

The new `autoresearch` engine completed proposal generation, candidate/control
backtests, persistent selection, restart, explicit validation and catalog review
on the available Alpaca SIP snapshot. Operating instructions and limitations are
in [the engine guide](strategy-development-engine.md).

## Reproducible workspace

- Workspace: `/home/supasuge/Documents/amat-research/v6-development-final-20260911`
- Program: `cd322cdb4dfc04e0aed293ac05b8480aadf47209e1782c693d13b138fcc4876c`
- Snapshot: `alpaca-sip-2026-08-11`
- Baseline: `configs/strategy/amat_v6.toml`
- Training: valuation anchor 2016-12-30 through 2022-12-30
- Validation: returns from 2023-01-03 through 2026-08-10, anchored at 2022-12-30
- Frozen limits: 30 experiments, 20 consecutive non-improvements, 240 seconds
  per worker, 50,000 model tokens per UTC day

`verification.json` in that workspace records the outcomes below. `report.md`
and `report.json` retain the full experiment ledger; `checkpoint.json` contains
the later-period measurements. The source, configuration, data and dependency
checks passed after the run.

## End-to-end outcome

1. A live model-proposal invocation measured the baseline, encountered an OpenAI
   HTTP 429 response, failed over to Gemini, validated a structured volatility
   feature/config proposal, backtested it and discarded it on training score.
2. A separate invocation resumed the workspace and ran nine built-in experiments.
   It retained one deadband change; later trials compared against that retained
   parent rather than the original baseline.
3. `run-one` evaluated the supplied momentum-plus-volume recipe, exercising
   combined features, smoothing and explicit thresholds. It was discarded.
4. The resulting ledger has one baseline plus **11 experiments: one keep,
   ten discards, zero crashes**. No engineered feature was retained in this run.
5. An explicit checkpoint evaluated baseline and incumbent on validation.
   Repeating it produced identical cached results and artifact hashes, and did
   not change the incumbent.
6. `pmteam cycle --provider fake` completed all 11 comparisons. Replaying the
   catalog dispatched zero new reviews and returned all 11 from cache. These
   are scripted orchestration checks, not independent model approvals.

The retained candidate changes `trend.deadband_abs` from **0.20 to 0.15**, with
no added feature gates. Its digest is
`26ff18373fbaef1c4a3db2eb632f56a540dfebe757a1378cf5af9c31aa3b9c2c`.

| Metric | Baseline | Retained candidate |
|---|---:|---:|
| Training objective | 0.8953 | 0.9654 |
| Validation annualized return, 2 bps | 7.63% | 7.88% |
| Validation Sharpe, 2 bps | 1.317 | 1.356 |
| Validation maximum drawdown | −4.94% | −5.05% |
| Validation Sharpe, 6 bps | 1.201 | 1.247 |

Selection used training evidence only. The validation period was previously
inspected elsewhere in the project. The improvement margin is a fixed heuristic;
these results do not establish statistical significance, future profitability or
readiness for deployment. The candidate's drawdown is slightly worse.

## Provider accounting

The chain attempted `openai/gpt-5.4-mini` once, then
`gemini/gemini-3.6-flash` once. Gemini reported 2,569 input plus 171 output tokens.
OpenAI supplied no usage for the rate-limit response, so the ledger conservatively
retained its 12,081-token reservation. Total budget charge was 14,821 tokens;
that number is not a claim that the failed request consumed those tokens or
incurred a particular dollar cost. The remaining providers were not called.

An earlier smoke workspace caught an oversized prompt before dispatch, consuming
zero calls. The proposer now sends compact training summaries linked to the
actual tested recipes, excluding full artifacts and validation. The final live
call used this corrected context path.

## Quality gates

The final source passed all local gates, run from each package directory:

```text
amat:     1,589 passed, 1 existing skip
executor:   160 passed

uv run --locked pytest -q
uv run --locked mypy src
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
```

The AMAT skip is the existing `__init__.py` dependency-layer exclusion. The
45 new research tests cover prefix-invariant features, protected configuration,
frozen-input tampering, matched real runs, retained-parent recovery, budgets,
provider failover/schema handling/cooldowns, subprocess deadlines, cache integrity,
year-fold and validation anchors, and the dependency boundary with executor.

The full test suite excludes general live-provider tests; the bounded two-attempt
proposer call above was verified separately. No new executor build, strategy
configuration, broker order, scheduler or automatic promotion was deployed.

## Resume this exact program

```bash
/home/supasuge/Documents/amat-research/v6-development-final-20260911/research.sh status
/home/supasuge/Documents/amat-research/v6-development-final-20260911/research.sh loop --iterations 5
```

This launcher uses the frozen implementation and persistent limits. New feature
families or changes to the evaluation policy belong in a new program.
