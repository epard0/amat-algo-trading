"""Cross-sectional selection: which assets to hold, out of many.

What this changes, and why it is a different question from the ones that failed
----------------------------------------------------------------------------
Three conditioning mechanisms have died in this repository's falsification
battery (v3 news scalars, v4 breadth blend, experiment 0002 volatility-state
conditioning), and each died the same way: against the *static-mean* control.
That control replaces a time-varying multiplier with its own average, costs no
turnover, and keeps winning -- because "cut size when the signal says so" is,
on this sample, an expensive way to say "hold less on average".

Cross-sectional selection is not that mechanism. It does not decide *how much*
to hold; it decides *which* of N candidates to hold, at unchanged total size.
Its static-mean analogue -- hold all N at reduced weight -- is a genuinely
different portfolio, not an equivalent one, so the control that killed the
timing overlays does not trivially kill this. That is the whole reason it is
worth measuring, and it is also why the control here has to be *random-K with
matched turnover*: the question is whether ranking picks better than a coin
does, not whether concentration changes the answer.

Why a wide universe makes this tractable rather than worse
----------------------------------------------------------
The obvious objection to widening from 12 to 92 assets is estimation error: a
120-session covariance lookback over 92 assets is badly conditioned, with more
assets than half the observations. Selection removes most of that problem.
Weights are non-zero only on the selected ``k``, so the quadratic form that
sets portfolio risk reads only the ``k x k`` block of the covariance -- at
``k = 10`` and a 120-session lookback that block sees twelve observations per
asset, which is the same regime the frozen 12-asset core already runs in.
The unselected 82 columns still enter the shrinkage intensity, so this is a
mitigation and not an exemption; the wide-without-selection arm exists in
experiment 0006 precisely to measure what it costs.

Determinism
-----------
Ties are broken by universe index, ascending. This matters more than it looks:
scores are ``tanh`` outputs that collide exactly at the clip boundary, and a
tie broken by ``argsort``'s implementation detail would make the backtest
depend on the NumPy version. Universe order is part of the frozen
configuration, so it is a defensible arbitrary rule rather than an accidental
one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from amat.config.models import CrossSectionConfig
from amat.errors import DataIntegrityError

__all__ = [
    "CrossSectionOutcome",
    "horizon_agreement",
    "select_cross_section",
    "select_on_schedule",
]


@dataclass(frozen=True, slots=True)
class CrossSectionOutcome:
    """The selection for one session, with the reasons attached.

    Attributes:
        scores: ``(N,)`` scores after selection -- the input score for a chosen
            asset, exactly ``0.0`` for every other. This is what the allocator
            consumes; every downstream cap and the volatility target are
            unchanged.
        rank: ``(N,)`` 1-based cross-sectional rank among eligible assets, or
            ``0`` for an asset that was never eligible. Reported, not used.
        selected: ``(N,)`` boolean mask of what survived.
        eligible: ``(N,)`` boolean mask of what competed -- a positive
            dead-banded score, and enough agreeing horizons if that rule is on.
        binding: Which rules actually removed something this session.
    """

    scores: np.ndarray
    rank: np.ndarray
    selected: np.ndarray
    eligible: np.ndarray
    binding: tuple[str, ...]


def horizon_agreement(horizon_z: np.ndarray) -> np.ndarray:
    """How many horizons are strictly positive, per asset.

    Args:
        horizon_z: ``(H, N)`` per-horizon normalized trends for one session, as
            produced by :func:`amat.features.horizon_trend_scores`.

    Returns:
        ``(N,)`` integer count. A ``nan`` horizon counts as disagreement rather
        than agreement -- an unavailable horizon is not evidence of a trend.
    """
    if horizon_z.ndim != 2:  # noqa: PLR2004 - (H, N) is the documented shape
        message = f"horizon_z must be (H, N) for one session, received {horizon_z.shape}"
        raise DataIntegrityError(message)
    positive = np.where(np.isfinite(horizon_z), horizon_z, 0.0) > 0.0
    return np.asarray(positive.sum(axis=0), dtype=int)


def select_cross_section(
    long_score: np.ndarray,
    config: CrossSectionConfig,
    *,
    horizon_z: np.ndarray | None = None,
) -> CrossSectionOutcome:
    """Keep the strongest ``max_positions`` trends; zero the rest.

    The score is passed through unchanged for selected assets rather than
    replaced by a rank weight. Rank weighting would be a second, confounded
    change -- it alters relative sizing as well as membership -- and the
    experiment is about membership.

    Args:
        long_score: ``(N,)`` dead-banded, long-or-cash scores for one session.
        config: Selection rules.
        horizon_z: ``(H, N)`` per-horizon trends, required when
            ``min_horizon_agreement`` is set. Absent, that rule cannot be
            evaluated and its configuration is a contradiction, so it raises
            rather than silently skipping the filter.

    Raises:
        DataIntegrityError: on a non-1-D score vector, or a horizon-agreement
            rule with no horizons to read.
    """
    if long_score.ndim != 1:
        message = f"long_score must be a 1-D (N,) vector, received {long_score.shape}"
        raise DataIntegrityError(message)

    count = long_score.shape[0]
    safe = np.where(np.isfinite(long_score), long_score, 0.0)
    eligible = safe > 0.0
    binding: list[str] = []

    if config.min_horizon_agreement > 0:
        if horizon_z is None:
            message = (
                "min_horizon_agreement is set but no per-horizon panel was supplied; the "
                "filter cannot be evaluated and must not be silently skipped"
            )
            raise DataIntegrityError(message)
        if horizon_z.ndim != 2:  # noqa: PLR2004 - (H, N) contract
            raise DataIntegrityError("horizon_z must be (H, N) for one session")
        if horizon_z.shape[1] != count:
            message = (
                f"horizon panel covers {horizon_z.shape[1]} assets but the score vector "
                f"covers {count}"
            )
            raise DataIntegrityError(message)
        agreeing = horizon_agreement(horizon_z)
        confirmed = agreeing >= config.min_horizon_agreement
        if np.any(eligible & ~confirmed):
            binding.append("horizon_agreement")
        eligible = eligible & confirmed

    # Descending score, ties to the lower universe index. `lexsort` reads its
    # keys last-first, so this sorts by score first and index second.
    order = np.lexsort((np.arange(count), -safe))
    rank = np.zeros(count, dtype=int)
    eligible_in_order = [index for index in order if eligible[index]]
    for position, index in enumerate(eligible_in_order, start=1):
        rank[index] = position

    keep = eligible_in_order
    if config.max_positions is not None and len(keep) > config.max_positions:
        keep = keep[: config.max_positions]
        binding.append("max_positions")

    selected = np.zeros(count, dtype=bool)
    selected[list(keep)] = True

    return CrossSectionOutcome(
        scores=np.where(selected, safe, 0.0),
        rank=rank,
        selected=selected,
        eligible=eligible,
        binding=tuple(binding),
    )


def select_on_schedule(
    scores: np.ndarray,
    sigma: np.ndarray,
    config: CrossSectionConfig,
    *,
    decision_index: int,
    first_decision_index: int,
    horizon_scores: np.ndarray | None = None,
) -> np.ndarray:
    """Refresh relative ranking periodically; apply absolute eligibility daily.

    The fixed schedule starts after warm-up and a complete longest horizon.
    Between refreshes an eligible member can retain its allocation even when
    another asset overtakes its rank. A negative/unavailable score or failed
    horizon confirmation can still exit immediately. Sizing and caps continue
    to update every session. The default is the original daily ranking.
    """
    if scores.ndim != 2 or sigma.shape != scores.shape:  # noqa: PLR2004
        raise DataIntegrityError("scores and sigma must be aligned (T, N) panels")
    if horizon_scores is not None and (
        horizon_scores.ndim != 3 or horizon_scores.shape[1:] != scores.shape  # noqa: PLR2004
    ):
        raise DataIntegrityError("horizon_scores must be an aligned (H, T, N) panel")
    if not 0 <= decision_index < len(scores) or first_decision_index < 0:
        raise DataIntegrityError("selection schedule indices are outside available history")

    def selection(index: int) -> CrossSectionOutcome:
        usable = np.isfinite(scores[index]) & np.isfinite(sigma[index]) & (sigma[index] > 0)
        return select_cross_section(
            np.where(usable, scores[index], 0.0),
            config,
            horizon_z=horizon_scores[:, index, :] if horizon_scores is not None else None,
        )

    current = selection(decision_index)
    interval = config.selection_interval_sessions
    if interval == 1:
        return current.scores
    if decision_index < first_decision_index:
        return np.zeros_like(current.scores)
    refresh = first_decision_index + (decision_index - first_decision_index) // interval * interval
    retained = selection(refresh).selected & current.eligible
    return np.where(retained, scores[decision_index], 0.0)
