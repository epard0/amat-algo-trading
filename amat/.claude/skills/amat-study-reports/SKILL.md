---
name: amat-study-reports
description: Run AMAT backtest studies (v1, v3, v4, v5) and produce the PDF report artifacts with comparison charts, per-trade rationale, portfolio-value page, episode diagnostics, and the optional Gemini advisory page. Includes the honesty checks to apply before trusting any result.
argument-hint: "[v1|v3|v4|v5] [--window START..END]"
---

# AMAT study reports

Run a backtest study and produce its PDF artifact. The PDF is the primary
report format: headline + concentration gates, one color-coded comparison
chart of every asset's total-return path beside the strategy, cumulative
curves vs frictionless reference baskets, the total-portfolio-value page
(holdings + cash stacked to the equity line in dollars), one page per asset
with every buy/sell marked and its recorded `TradeReason`, individual
statistics, and the episode diagnostics (captured trends, whipsaws,
give-backs, missed rallies).

Funding: the combined portfolio and every sleeve each open with $100,000
(`--initial-cash` / `--sleeve-equity` to override). Results at other funding
levels are not comparable rows in the registry.

## Commands

```bash
# v1 — the frozen 12-symbol candidate, full sample, sleeves included
uv run amat study configs/strategy/amat_v1.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-11 \
  --pdf outputs/reports/amat_v1_study.pdf

# v5 — the 17-symbol breadth candidate, with the leave-one-out battery
# (baseline must differ ONLY in universe; the harness refuses otherwise)
uv run amat study configs/strategy/amat_v5.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-11 \
  --pdf outputs/reports/amat_v5_study.pdf \
  --falsify-universe configs/strategy/amat_v1.toml

# v3 — v1 conditioned on point-in-time news scalars (two steps; see the
# news-scalars skill for the export half)
uv run amat study configs/strategy/amat_v3.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-11 \
  --scalars outputs/scalars/news_v3.csv \
  --pdf outputs/reports/amat_v3_study.pdf

# v4 — stance switching, with the blend controls (REJECTED; reproducible)
uv run amat study-v4 configs/strategy/amat_v4.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-11 \
  --falsify --pdf outputs/reports/amat_v4_study.pdf

# Bounded window (warm-up happens INSIDE the slice: --start 2019-01-02 begins
# measuring ~2019-12-31; set --start ~13 months before the target)
... --start 2019-01-02 --end 2023-12-29 --no-sleeves

# Validation companions (each persists a run record; see the
# walk-forward-validation and strategy-experiment skills):
uv run amat walkforward configs/strategy/amat_v1.toml --snapshot <snap>
uv run amat montecarlo configs/strategy/amat_v1.toml --snapshot <snap> \
  --paths 200 --pdf outputs/reports/amat_v1_montecarlo.pdf
```

Every study invocation writes `outputs/runs/<run_id>/` (manifest +
results) by default; `--no-run-record` skips it, after which the result
cannot be cited by the registry. PDF tables are rendered tables
(`_draw_table`), never monospaced ASCII; the episode diagnostics span two
pages.

Add `--advisory` to any `--pdf` invocation for a clearly labelled Gemini
commentary page. It needs `GEMINI_API_KEY` exported (referenced by name only)
and degrades to "no page, reason recorded on stderr" on any failure. The
payload is aggregate statistics only; nothing in the run derives from the
response.

## Honesty checks before trusting a result

1. Read the caveat block and both concentration gates on page 1 — a FAIL gate
   is a rejection condition, not a footnote.
2. Confirm the config digest on the report matches the file you meant to run
   (`sha256sum configs/strategy/<file>.toml`). Results cite digests, not
   filenames.
3. The episode diagnostics are replayed from fills and reconciled against the
   ledger; if the report refused to render holdings, that is a data problem to
   investigate, never to suppress.
4. Benchmarks in the report are frictionless; the strategy pays costs. State
   that whenever comparing.
5. Any new measured result belongs in `docs/experiment-registry.md` in the
   same change (including failures). Never edit a config file to record a
   verdict — that changes its digest and orphans cited results.

## Related

- `docs/experiment-registry.md` — every measured configuration and verdict.
- `scripts/benchmarks.py` — passive baselines on the identical axis.
- The `strategy-experiment` skill — proposing and judging a new version.
