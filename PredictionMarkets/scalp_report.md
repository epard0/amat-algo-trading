# Scalping Mode — Paper Session Report

Generated 2026-08-19T23:34:50+00:00  
Mode: **paper**  
Ledger: `/home/supasuge/Documents/Polymarket-BTC-15-Minute-Trading-Bot/trade_ledger.db`  
Band: **0.60–0.92**, min EV/$ 0.015, max concurrent 5

---

## Results by strategy

### predictor

| Metric | Value |
| --- | --- |
| Trades opened | 1 |
| Settled | 1 |
| Still open | 0 |
| Early exits | 0 |
| Wins / Losses | 0 / 1 |
| **Win rate** | **0.0%** |
| 95% CI on win rate | 0.0% – 79.3% |
| Clears the 55% target? | no (but the CI spans it) |
| Net P&L | $-0.9700 |
| Total fees | $0.0352 |
| Capital staked | $0.9700 |
| Return on stake | -100.00% |

Entry prices ranged 0.57–0.57 (mean 0.570).

| Ticker | Entry | Status | P&L |
| --- | --- | --- | --- |
| KXSOL15M-26AUG191800-00 | 0.57 | LOST | $-0.9700 |

### scalper

| Metric | Value |
| --- | --- |
| Trades opened | 3 |
| Settled | 3 |
| Still open | 0 |
| Early exits | 3 |
| Wins / Losses | 3 / 0 |
| **Win rate** | **100.0%** |
| 95% CI on win rate | 43.8% – 100.0% |
| Clears the 55% target? | yes (but the CI spans it) |
| Net P&L | $5.8188 |
| Total fees | $1.1432 |
| Capital staked | $32.1100 |
| Return on stake | 18.12% |

Entry prices ranged 0.64–0.71 (mean 0.683).

| Ticker | Entry | Status | P&L |
| --- | --- | --- | --- |
| KXBTC15M-26AUG191745-45 | 0.70 | EXITED | $2.8420 |
| KXETH15M-26AUG191815-15 | 0.64 | EXITED | $1.4552 |
| KXBTC15M-26AUG191845-45 | 0.71 | EXITED | $1.5216 |

> **Session integrity.** The last trade was placed 77 minutes into a 138-minute wall-clock window, so the process was not trading for most of that span — on a laptop the usual cause is the host suspending. The trades below are valid; the *exposure window* is the shorter figure, and any per-hour rate should be computed against it.

---

## Band audit — independent of the fill model

| Metric | Value |
| --- | --- |
| Quoted markets that settled | 8 |
| Chosen side won | 6 |
| **Band hit rate** | **75.0%** |
| 95% CI | 40.9% – 92.9% |

| Ticker | Side quoted | Price | Settled | Correct |
| --- | --- | --- | --- | --- |
| KXBTC15M-26AUG191730-30 | YES | 0.67 | YES | yes |
| KXETH15M-26AUG191745-45 | YES | 0.63 | NO | no |
| KXBTC15M-26AUG191745-45 | NO | 0.69 | NO | yes |
| KXBTC15M-26AUG191800-00 | YES | 0.73 | YES | yes |
| KXETH15M-26AUG191815-15 | NO | 0.63 | NO | yes |
| KXBTC15M-26AUG191815-15 | NO | 0.78 | NO | yes |
| KXBTC15M-26AUG191830-30 | NO | 0.83 | NO | yes |
| KXBTC15M-26AUG191845-45 | NO | 0.70 | YES | no |

---

## Scalping funnel

| Event | Count |
| --- | --- |
| `blocked:already_traded` | 31 |
| `blocked:book_depth` | 910 |
| `blocked:min_ev` | 156 |
| `blocked:pattern` | 879 |
| `blocked:price_band` | 90 |
| `blocked:quote_window` | 294 |
| `blocked:session_tail` | 37 |
| `blocked:size_zero` | 20 |
| `blocked:spread` | 119 |
| `retired:cancel` | 3 |
| `retired:requote` | 9 |
| `scalp_exit` | 3 |
| `scalp_fill` | 3 |
| `scalp_funnel` | 2 |
| `scalp_quote` | 15 |
| `scalp_quote_retired` | 12 |

**Why the other markets were passed over** (2536 refusals). On a strategy that declines most of what it sees, this breakdown is the result — a quiet session is only informative once it says which gate was doing the work.

| Gate | Refusals |
| --- | --- |
| `book_depth` | 910 |
| `pattern` | 879 |
| `quote_window` | 294 |
| `min_ev` | 156 |
| `spread` | 119 |
| `price_band` | 90 |
| `session_tail` | 37 |
| `already_traded` | 31 |
| `size_zero` | 20 |

**Fill rate:** 3/15 (20.0%) — the share of resting quotes that were filled. Paper fills are credited only when the offer trades *through* our price, so this understates a real queue position rather than flattering it.

---

## How to read this

- **The price band is doing the win-rate work.** Break-even needs `price + fee`, so a floor of 0.60 forces any EV-positive strategy above a 60% win rate. A high win rate here is therefore expected and is *not* by itself evidence of an edge — net P&L is.
- **The band was seeded from the reference paper, not fitted to this bot's own history.** Every quote is logged with the fields needed to re-fit it once enough fills exist.
- **A 90-minute session cannot settle the question.** The 15-minute markets produce at most ~6 settlements per asset per session, so treat the confidence interval above as the real result.

### The paper fill model biases against this strategy — deliberately

A resting quote is credited with a fill only when the book trades *through* its price. For a resting bid that means the offer had to fall to us, which is to say **every simulated fill happens on an adverse move**. Real maker fills are a mix: some are adverse, some are a counterparty with a liquidity need and no view at all.

So the P&L here is a **lower bound**, not an estimate. It measures roughly the worst case — what this strategy earns if it is picked off every single time. Two consequences:

1. A negative result at this stage does **not** by itself refute the maker edge; it is consistent with the model doing what it was built to do.
2. A *positive* result here would be strong evidence, because it would mean the strategy paid for its own adverse selection.

The fields logged per quote (`fill_latency_seconds`, `bid_at_placement` vs `bid_at_fill`, `spread_at_quote`) are what a real fill-quality distribution would be estimated from once live fills exist.

