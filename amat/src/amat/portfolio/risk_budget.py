"""Concentrate risk in the assets whose signal has actually been paying.

The problem this addresses
--------------------------
The allocator sizes inverse to volatility, ``q = long_score / sigma``. That is
a statement that every asset earns the same reward per unit of risk, and on
this sample it is false. Measured over 2016-12-30..2026-08-10 on the v6
universe, the two calmest assets took the largest weights and earned almost
nothing:

    UUP   7.60% average weight,  6.7% vol,  +$1,518 over 118 round trips
    IEF   7.09% average weight,  6.6% vol,    -$980 over 199 round trips
    GLD   5.86% average weight, 15.1% vol, +$14,803 over 176 round trips

Together UUP and IEF held about a quarter of the invested book and contributed
$538. Inverse-volatility sizing put the most capital where the least reward was.

Why this is not simply "drop UUP and IEF"
-----------------------------------------
Because that reads the answer off the end of the sample. Deleting the two
symbols that happened to lose is the hindsight selection that got v5 rejected,
and that v6's own configuration carries a written warning about for TLT. A rule
built that way cannot fail a backtest of the period it was built from, and
therefore that backtest cannot tell anyone anything.

So the budget here never names a symbol. At each decision it measures how well
*the strategy's own signal* has been paying on each asset **using only sessions
at or before that decision**, ranks the assets on it, and shifts risk from the
bottom of the ranking to the top. If UUP starts paying, it gets its weight back
without anyone editing a file; if SPY stops paying, it loses weight.

What is measured
----------------
The per-session payoff the signal earned on each asset::

    payoff[t, i] = long_score[t-1, i] * return[t, i]

which is the return the strategy actually captured on asset ``i`` that session,
zero when it was not holding it. Its trailing mean over standard deviation is an
information ratio for *the signal on that asset* -- not the asset's own return,
which would reward buy-and-hold performance the strategy never collected.

How it is applied
-----------------
Multipliers are rank-based and mean-normalised, so the budget **reallocates**
risk rather than adding or removing it. The volatility target still sets total
risk; this only changes which assets carry it. Rank-based rather than
proportional because a trailing information ratio is a noisy estimate and its
*ordering* is far more stable than its magnitude -- scaling by the raw number
would let one asset's lucky quarter dominate the book.

Bounds are hard. No asset can be zeroed by the budget (a floor keeps it in the
portfolio at reduced size) and none can be more than ``max_multiplier`` times
its inverse-volatility weight before the symbol and cluster caps apply. A rule
that can drive a weight to zero is a selection rule wearing a sizing rule's
clothes, and 0006 and 0007 both established that selection does not help here.
"""

from __future__ import annotations

import numpy as np

from amat.config.models import RiskBudgetConfig
from amat.errors import DataIntegrityError

__all__ = ["budget_multipliers", "signal_payoffs", "trailing_quality"]


def signal_payoffs(long_scores: np.ndarray, returns: np.ndarray) -> np.ndarray:
    """``(T, N)`` per-session return the signal captured on each asset.

    Row ``t`` pairs the score formed at the close of ``t-1`` with the return
    realised over ``t``. That one-session offset is the whole causality
    argument: the score is known before the return it is multiplied by.
    """
    if long_scores.shape != returns.shape:
        message = f"score panel {long_scores.shape} and return panel {returns.shape} must match"
        raise DataIntegrityError(message)
    payoff = np.zeros_like(returns, dtype=float)
    payoff[1:] = np.nan_to_num(long_scores[:-1]) * np.nan_to_num(returns[1:])
    return payoff


def trailing_quality(
    payoffs: np.ndarray, *, lookback: int, min_periods: int, measure: str = "mean_payoff"
) -> np.ndarray:
    """``(T, N)`` trailing quality of ``payoffs``, strictly backward-looking.

    ``measure="mean_payoff"`` is the default and the right one here: the
    allocator already divides by volatility, so a risk-adjusted measure would
    divide by it a second time and penalise assets for being volatile twice
    over. Measured on this sample that double-count is not hypothetical -- the
    information ratio ranks SLV and USO *worst* despite both out-earning UUP,
    purely because their payoffs are noisy. Against realised ten-year P&L the
    mean payoff correlates +0.893 and the information ratio +0.687.

    ``measure="information_ratio"`` is retained as a sensitivity, not as an
    option to pick between after seeing results.

    Row ``t`` reads rows ``t-lookback+1 .. t`` inclusive and nothing after, so a
    full-panel pass and a pass over the prefix agree at every row. ``nan`` until
    ``min_periods`` observations exist -- an information ratio from twenty
    sessions is noise with a decimal point, and the caller treats ``nan`` as
    "no opinion" rather than as a bad score.
    """
    if lookback < min_periods:
        message = f"lookback {lookback} must be at least min_periods {min_periods}"
        raise DataIntegrityError(message)

    rows, columns = payoffs.shape
    ratio = np.full((rows, columns), np.nan, dtype=float)
    # Cumulative sums give O(T*N) rolling moments; the alternative is O(T*N*L).
    padded = np.vstack([np.zeros((1, columns)), payoffs])
    cumulative = np.cumsum(padded, axis=0)
    cumulative_square = np.cumsum(padded**2, axis=0)

    for end in range(rows):
        start = max(0, end - lookback + 1)
        count = end - start + 1
        if count < min_periods:
            continue
        total = cumulative[end + 1] - cumulative[start]
        total_square = cumulative_square[end + 1] - cumulative_square[start]
        mean = total / count
        variance = np.maximum(total_square / count - mean**2, 0.0)
        deviation = np.sqrt(variance * count / max(count - 1, 1))
        if measure == "mean_payoff":
            ratio[end] = mean
        else:
            with np.errstate(invalid="ignore", divide="ignore"):
                ratio[end] = np.where(deviation > 0, mean / deviation, 0.0)
    return ratio


def budget_multipliers(quality: np.ndarray, config: RiskBudgetConfig) -> np.ndarray:
    """``(N,)`` mean-normalised multipliers for one session's quality row.

    Assets are ranked worst-to-best and mapped linearly onto
    ``[min_multiplier, max_multiplier]``, then rescaled so the mean over
    *rankable* assets is exactly 1. The rescaling is what makes this a
    reallocation: the volatility target sets total risk and this only decides
    who carries it.

    An asset with no usable quality estimate receives exactly 1.0 -- the
    unmodified inverse-volatility weight. Absence of evidence is not evidence
    of a bad asset, and defaulting it to the floor would quietly turn a warm-up
    gap into a permanent underweight.
    """
    if quality.ndim != 1:
        message = f"quality must be a 1-D (N,) row, received {quality.shape}"
        raise DataIntegrityError(message)

    multipliers = np.ones_like(quality, dtype=float)
    rankable = np.isfinite(quality)
    count = int(rankable.sum())
    if count < 2:  # noqa: PLR2004 - a ranking needs two things to rank
        return multipliers

    values = quality[rankable]
    # Ties share the average rank, so identical quality gets identical weight
    # rather than an ordering decided by column position.
    order = values.argsort()
    ranks = np.empty(count, dtype=float)
    ranks[order] = np.arange(count, dtype=float)
    unique, inverse = np.unique(values, return_inverse=True)
    if len(unique) < count:
        averaged = np.array([ranks[values == value].mean() for value in unique])
        ranks = averaged[inverse]

    spread = config.max_multiplier - config.min_multiplier
    scaled = config.min_multiplier + spread * (ranks / (count - 1))
    scaled = scaled / scaled.mean()
    # Renormalising can push the extremes past their configured bounds; clip
    # last so the stated limits are the limits actually applied.
    multipliers[rankable] = np.clip(scaled, config.min_multiplier, config.max_multiplier)
    return multipliers
