"""Regime conditioning as a precomputed, auditable scalar series.

The v2 lesson, encoded structurally
-----------------------------------
The rejected v2 overlay conditioned sizing from *inside* the session loop, where
guard state, a trade ledger, a Kelly estimate and a regime label all mutated
together. That made two things impossible: reading out what the overlay did on
any given session without replaying from inception, and testing the regime part
in isolation from the guard part. It destroyed 94% of v1's return and nobody
could say which component was responsible.

So v3 does not add an engine. It computes **one number per session, in advance**:
a multiplier in ``(0, 1]`` applied to the target weights the v1 allocator already
produced. That has three consequences worth the constraint:

* the series is a plain array, so a leak test can assert directly that row ``t``
  is unchanged by mutating rows after ``t``;
* the conditioning can be replaced by a shuffled or random control of identical
  turnover, which is the only way to tell a real regime signal from a coincidence;
* v1 is exactly v3 with a series of ones, so the comparison is attributable to
  this one number and nothing else.

What the evidence says before we start
--------------------------------------
Both papers this design was asked to follow fail to demonstrate out-of-sample
value. Shumway et al.'s recommended jump model posts Sharpe 0.50 against SPY's
0.51 -- it loses to buy-and-hold -- and the models that beat SPY there are
rejected by the authors as unusable at 12-15 regime flips a year, with no
transaction costs modelled anywhere in the paper. SSGA's note is explicitly
contemporaneous, runs no backtest, reports no Sharpe, fits on the full sample,
and is legally a marketing communication.

That is the prior this module is built against. It exists to be *measured*, and
:mod:`amat.validation.falsification` supplies the controls designed to show it is
worthless. Nothing here should be read as a claim that it works.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.context import build_context_panels
from amat.errors import CausalityError, DataIntegrityError
from amat.regime import CausalRegimeModel

__all__ = ["regime_scalars", "session_features"]


@contextmanager
def _quiet_warmup() -> Iterator[None]:
    """Silence the all-NaN warm-up rows, and nothing else.

    Warm-up rows are entirely NaN by construction -- no instrument has a
    252-session trend on session 10 -- so reducing an empty slice is the expected
    path there, not a defect. Scoped to these two messages rather than suppressed
    globally because the project runs pytest with ``filterwarnings = ["error"]``,
    and a blanket filter would also hide an all-NaN column appearing *later* in
    the sample, which would be a genuine data failure worth stopping for.
    """
    with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
        warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)
        warnings.filterwarnings(
            "ignore", message="Degrees of freedom <= 0", category=RuntimeWarning
        )
        yield


def session_features(
    panel: MarketPanel, *, horizons: tuple[int, ...] = (21, 63, 252)
) -> np.ndarray:
    """Reduce per-symbol context to one feature row per session.

    :class:`~amat.context.ContextPanels` is ``(T, N)`` per feature -- one value
    per symbol. A regime describes the *market*, not an instrument, so the cross
    section has to collapse. Both moments are kept for every feature:

    * the **mean** across symbols, which says where the market is;
    * the **dispersion** (standard deviation) across symbols, which says whether
      instruments agree.

    Dispersion is not decoration. A market where everything trends up together
    and one where half the universe is falling can share an identical mean and
    are plainly different states; keeping only the mean would merge them, and the
    merged cluster would then be labelled by whichever happened to dominate the
    fitting window.

    Returns:
        ``(T, 2F)`` array: every context feature's cross-sectional mean followed
        by its cross-sectional dispersion. Rows before the longest horizon are
        ``nan`` and the regime model skips them.
    """
    closes, highs, lows, volumes = panel.context_inputs()
    panels = build_context_panels(closes, highs, lows, volumes, horizons=horizons)

    columns: list[np.ndarray] = []
    for name in (
        "alignment",
        "support_distance",
        "resistance_distance",
        "volume_z",
        "volume_trend",
        "reversal",
    ):
        columns.append(getattr(panels, name))
    for view in panels.views:
        columns.append(view.trend)
        columns.append(view.location)

    with _quiet_warmup():
        means = np.column_stack([np.nanmean(column, axis=1) for column in columns])
        spreads = np.column_stack([np.nanstd(column, axis=1) for column in columns])
    return np.column_stack([means, spreads])


def regime_scalars(
    panel: MarketPanel,
    *,
    n_regimes: int = 3,
    fit_window: int = 504,
    refit_every: int = 21,
    risk_floor: float = 0.50,
    unknown_scalar: float = 1.0,
    seed: int = 0,
    horizons: tuple[int, ...] = (21, 63, 252),
) -> np.ndarray:
    """One sizing multiplier per session, each formed from that session's past.

    The model is refitted every ``refit_every`` sessions on the trailing
    ``fit_window``, and the scalar for session ``t`` is read from the assessment
    of row ``t`` under a model that has never seen row ``t + 1``. Walking the
    loop forward rather than fitting once is the entire point; a single
    full-sample fit would encode the future's distribution in the standardization
    alone, which is the subtlest and most common way a regime study leaks.

    ``risk_floor`` defaults to 0.50 rather than the model's own 0.25. A quarter
    of target size is a very large intervention to justify from a clustering
    label, and v2's failure was in substantial part the size of its cuts rather
    than their timing.

    Returns:
        ``(T,)`` array of multipliers in ``(0, 1]``. Sessions before a regime can
        be established carry ``unknown_scalar``, which defaults to 1.0 -- "no
        estimate" must not silently mean "smallest estimate".

    Raises:
        DataIntegrityError: if the panel carries no context inputs.
    """
    if not panel.has_context_inputs:
        message = (
            "regime conditioning needs session highs, lows and volume; "
            "load the panel with load_panel(..., with_context=True)"
        )
        raise DataIntegrityError(message)

    features = session_features(panel, horizons=horizons)
    with _quiet_warmup():
        returns = np.diff(np.log(panel.adjusted_closes), axis=0, prepend=np.nan)
        realized = np.nanstd(returns, axis=1)

    model = CausalRegimeModel(
        n_regimes=n_regimes,
        fit_window=fit_window,
        refit_every=refit_every,
        risk_floor=risk_floor,
        unknown_scalar=unknown_scalar,
        seed=seed,
    )

    total = features.shape[0]
    scalars = np.full(total, unknown_scalar, dtype=float)
    last_fit: int | None = None
    for index in range(total):
        if not np.isfinite(features[index]).all():
            continue
        if last_fit is None or index - last_fit >= refit_every:
            try:
                model.fit_through(features, realized, index=index)
            except (DataIntegrityError, CausalityError):
                # Too little usable history yet. The scalar stays at
                # `unknown_scalar` rather than falling back to the floor.
                continue
            last_fit = index
        assessment = model.assess(features[index])
        # `assess` is a pure query by design; the label history only advances
        # when `observe` is called. Omitting it leaves `stability` pinned at 0.0
        # forever, which multiplies the scalar by 0.5 on every single session and
        # clamps it to the floor -- measured on the real snapshot as 0.5 for
        # 2,161 of 2,664 sessions with exactly one change. That is a constant
        # de-risking wearing a regime model's clothes, and it would have been
        # reported as "regime conditioning" had the distribution not been
        # printed.
        model.observe(assessment)
        scalars[index] = assessment.risk_scalar
    return scalars
