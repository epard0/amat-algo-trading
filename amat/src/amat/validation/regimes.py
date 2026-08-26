"""Causal market-regime classification, for reporting and for one experiment.

Two consumers, one classifier
-----------------------------
1. **Descriptive reporting.** The validation protocol requires "results by
   major historical market regime". :func:`classify_sessions` labels each
   session from a reference instrument's own past, and
   :func:`regime_performance` slices a finished run's session returns by
   those labels. The labels condition nothing; they only describe.
2. **The registered conditioning experiment.**
   :func:`volatility_state_scalars` turns the volatility half of the
   classifier into a per-session sizing multiplier for the v1 ``weight_scalars``
   hook, to be judged exclusively by
   :func:`amat.validation.falsification.falsify_conditioning`. Two prior
   regime designs died there (v3's fitted KMeans lost to its own static mean;
   v4's breadth blend lost to its shuffled control), so the prior for this one
   is failure, and nothing in this module assumes otherwise.

Causality, stated once
----------------------
Every quantity at session ``t`` -- the trailing return, the realized
volatility, and the expanding quantile threshold -- is computed from rows at
or before ``t``. There is no full-sample statistic anywhere: a threshold
taken from the whole sample would leak the future's volatility distribution
into every session, which is the subtlest and most common regime-study leak.

Attribution is additionally *lagged one session*: the return earned during
session ``t`` is booked under session ``t-1``'s label. The label at ``t``
contains ``t``'s own move (it sits inside the trailing volatility), so
booking ``t``'s return under ``t``'s own label lets large returns select
themselves into the ``volatile`` buckets -- on pure iid noise that
contemporaneous convention manufactures a ~1.27x quiet/volatile volatility
separation where none exists. The lagged label is also the honest one
economically: it is the freshest regime information available when the
position that earned ``t``'s return was actually held.

The four regime labels
----------------------
``bull``/``bear`` is the sign of the reference instrument's trailing
``trend_horizon``-session return; ``quiet``/``volatile`` is its trailing
realized volatility relative to the expanding median of that same series.
Sessions before either measure exists are ``warmup``. The reference defaults
to SPY: the classification is meant to describe the *market environment*,
and one instrument everyone recognizes beats a universe blend nobody can
recompute in their head.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.domain.types import SessionDate
from amat.errors import DataIntegrityError

__all__ = [
    "REGIME_LABELS",
    "RegimeSlice",
    "classify_sessions",
    "regime_performance",
    "volatility_state_scalars",
]

REGIME_LABELS: tuple[str, ...] = (
    "bull-quiet",
    "bull-volatile",
    "bear-quiet",
    "bear-volatile",
    "warmup",
)

#: Annualization convention shared with the rest of the platform.
_SESSIONS_PER_YEAR = 252

#: Fewer labelled sessions than this and a regime's statistics are noise; the
#: slice is still reported, with its size, so the reader can discount it.
_MIN_SLICE_SESSIONS = 2

#: A realized volatility needs at least two returns to be a deviation at all.
_MIN_VOL_WINDOW = 2


def _reference_column(panel: MarketPanel, reference: str) -> np.ndarray:
    symbols = [str(symbol) for symbol in panel.symbols]
    if reference not in symbols:
        message = f"reference symbol {reference!r} is not in the panel ({', '.join(symbols)})"
        raise DataIntegrityError(message)
    column = panel.adjusted_closes[:, symbols.index(reference)]
    if not np.all(np.isfinite(column)) or np.any(column <= 0):
        message = f"reference series {reference!r} contains non-finite or non-positive closes"
        raise DataIntegrityError(message)
    return column


def _trailing_volatility(closes: np.ndarray, window: int) -> np.ndarray:
    """Annualized realized volatility of the trailing ``window`` log returns.

    Entry ``t`` uses returns from sessions ``t-window+1 .. t``; entries before
    the window has filled are ``nan``.
    """
    returns = np.diff(np.log(closes))
    volatility = np.full(closes.shape[0], np.nan)
    for index in range(window, closes.shape[0]):
        segment = returns[index - window : index]
        volatility[index] = float(np.std(segment, ddof=1)) * float(np.sqrt(_SESSIONS_PER_YEAR))
    return volatility


def _expanding_median(values: np.ndarray) -> np.ndarray:
    """Median of ``values[: t + 1]`` at each ``t``, ignoring ``nan`` history."""
    medians = np.full(values.shape[0], np.nan)
    for index in range(values.shape[0]):
        history = values[: index + 1]
        finite = history[np.isfinite(history)]
        if finite.size:
            medians[index] = float(np.median(finite))
    return medians


def classify_sessions(
    panel: MarketPanel,
    *,
    reference: str = "SPY",
    trend_horizon: int = 63,
    vol_window: int = 21,
) -> tuple[str, ...]:
    """Label each panel session with a causal market-regime name.

    Raises:
        DataIntegrityError: if the reference symbol is absent or its series is
            unusable, or if the parameters are not positive.
    """
    if trend_horizon < 1 or vol_window < _MIN_VOL_WINDOW:
        message = (
            f"require trend_horizon >= 1 and vol_window >= {_MIN_VOL_WINDOW}, "
            f"got {trend_horizon}/{vol_window}"
        )
        raise DataIntegrityError(message)

    closes = _reference_column(panel, reference)
    total = closes.shape[0]

    volatility = _trailing_volatility(closes, vol_window)
    threshold = _expanding_median(volatility)

    labels: list[str] = []
    for index in range(total):
        if index < trend_horizon or not np.isfinite(volatility[index]):
            labels.append("warmup")
            continue
        rising = closes[index] >= closes[index - trend_horizon]
        # ">" not ">=": on the first classifiable session the expanding median
        # IS the observation, and a tie must read as the calm state rather
        # than flagging the very first reading as elevated.
        elevated = volatility[index] > threshold[index]
        direction = "bull" if rising else "bear"
        state = "volatile" if elevated else "quiet"
        labels.append(f"{direction}-{state}")
    return tuple(labels)


@dataclass(frozen=True, slots=True)
class RegimeSlice:
    """A finished run's behaviour inside one regime's (non-contiguous) sessions.

    The sessions of one regime are interleaved with the others', so a
    drawdown is undefined here and deliberately absent: concatenating the
    slices would manufacture peak-to-trough paths the account never lived
    through.
    """

    label: str
    sessions: int
    share_of_sample: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    hit_rate: float


def regime_performance(
    equity_curve: Sequence[float],
    session_dates: Sequence[SessionDate],
    panel: MarketPanel,
    labels: Sequence[str],
) -> tuple[RegimeSlice, ...]:
    """Slice a run's session returns by regime label.

    Takes the equity curve and its dates rather than a run object so a
    caller that already paid for the run -- the walk-forward, whose result
    carries both -- does not have to pay for it twice.

    The return attributed to session ``t`` is ``E[t]/E[t-1] - 1`` under the
    label of the *prior* session ``t-1`` -- the most recent label formed
    before the move being measured (see the module docstring for why the
    contemporaneous convention is a selection artifact). Annualization is the
    platform's realized-CAGR convention -- the slice's own returns compounded
    geometrically and scaled to a year -- and volatility scales by
    ``sqrt(252)``.

    Raises:
        DataIntegrityError: if ``labels`` does not cover the panel, or the
            run's sessions cannot be located inside it.
    """
    if len(labels) != len(panel.sessions):
        message = f"labels cover {len(labels)} sessions, panel has {len(panel.sessions)}"
        raise DataIntegrityError(message)

    session_row = {session: row for row, session in enumerate(panel.sessions)}
    equity = [float(value) for value in equity_curve]
    dates = session_dates
    if not equity or dates[0] not in session_row:
        message = "run sessions do not align with the panel the labels describe"
        raise DataIntegrityError(message)

    by_label: dict[str, list[float]] = {}
    for index in range(1, len(equity)):
        prior_row = session_row.get(dates[index - 1])
        if prior_row is None:  # pragma: no cover - runner sessions come from the panel
            message = f"run session {dates[index - 1]} is not in the panel"
            raise DataIntegrityError(message)
        by_label.setdefault(labels[prior_row], []).append(equity[index] / equity[index - 1] - 1.0)

    total_returns = sum(len(values) for values in by_label.values())
    slices: list[RegimeSlice] = []
    for label in REGIME_LABELS:
        values = np.array(by_label.get(label, ()), dtype=float)
        if values.size < _MIN_SLICE_SESSIONS:
            if values.size:
                slices.append(
                    RegimeSlice(
                        label=label,
                        sessions=int(values.size),
                        share_of_sample=values.size / total_returns,
                        annualized_return=0.0,
                        annualized_volatility=0.0,
                        sharpe=0.0,
                        hit_rate=0.0,
                    )
                )
            continue
        deviation = float(values.std(ddof=1))
        # Realized CAGR of the slice's own compounded returns -- the same
        # convention as WindowStatistics.annualized_return, so these rows can
        # sit beside a full-sample row without comparing two different
        # statistics. Compounding the arithmetic mean instead overstates by
        # ~252*sigma^2/2, which is largest exactly in the volatile buckets.
        growth = float(np.prod(1.0 + values))
        years = values.size / _SESSIONS_PER_YEAR
        annualized_return = float(growth ** (1.0 / years) - 1.0)
        annualized_volatility = deviation * float(np.sqrt(_SESSIONS_PER_YEAR))
        slices.append(
            RegimeSlice(
                label=label,
                sessions=int(values.size),
                share_of_sample=values.size / total_returns,
                annualized_return=annualized_return,
                annualized_volatility=annualized_volatility,
                sharpe=(
                    annualized_return / annualized_volatility if annualized_volatility > 0 else 0.0
                ),
                hit_rate=float((values > 0).mean()),
            )
        )
    return tuple(slices)


def volatility_state_scalars(
    panel: MarketPanel,
    *,
    reference: str = "SPY",
    vol_window: int = 21,
    quantile: float = 0.80,
    reduced_scalar: float = 0.50,
    min_history_sessions: int = 252,
) -> np.ndarray:
    """The experiment's candidate: de-risk when reference volatility is elevated.

    ``scalar[t]`` is ``reduced_scalar`` when the reference instrument's
    trailing ``vol_window``-session realized volatility exceeds the
    ``quantile`` of its own expanding history through ``t``, else ``1.0``.
    Sessions before ``min_history_sessions`` carry ``1.0``: "no estimate"
    must not silently mean "smallest estimate", and those sessions fall
    inside the strategy warm-up anyway.

    This is one observable, one threshold rule, zero fitted parameters --
    deliberately the simplest thing that can still be called a regime signal,
    so that when the falsification controls judge it, the verdict is about
    the *idea* and not about an estimator.

    Raises:
        DataIntegrityError: on an unusable reference series or parameters.
    """
    if not 0.0 < quantile < 1.0:
        message = f"quantile must lie strictly inside (0, 1), got {quantile}"
        raise DataIntegrityError(message)
    if not 0.0 < reduced_scalar <= 1.0:
        message = f"reduced_scalar must lie in (0, 1], got {reduced_scalar}"
        raise DataIntegrityError(message)
    if vol_window < _MIN_VOL_WINDOW:
        message = f"vol_window must be at least {_MIN_VOL_WINDOW}, got {vol_window}"
        raise DataIntegrityError(message)

    closes = _reference_column(panel, reference)
    total = closes.shape[0]
    volatility = _trailing_volatility(closes, vol_window)

    scalars = np.ones(total, dtype=float)
    for index in range(max(min_history_sessions, vol_window), total):
        history = volatility[: index + 1]
        finite = history[np.isfinite(history)]
        if finite.size < _MIN_VOL_WINDOW:  # pragma: no cover - min_history dominates
            continue
        threshold = float(np.quantile(finite, quantile))
        if np.isfinite(volatility[index]) and volatility[index] > threshold:
            scalars[index] = reduced_scalar
    return scalars
