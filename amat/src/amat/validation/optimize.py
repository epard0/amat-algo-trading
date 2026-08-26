"""Parameter search for the managed overlay, with out-of-sample discipline.

The search protocol
-------------------
1. The panel is split once into a **training** window and a later **validation**
   window. The split date is chosen before any candidate is evaluated and never
   moved.
2. Every candidate is scored on the training window only.
3. Ranking uses :func:`fold_score`, which is deliberately *not* in-sample Sharpe.
   A configuration that earns its Sharpe in one lucky year and loses money in
   the other three is not a better configuration than a steady one, and ranking
   by the aggregate cannot tell them apart.
4. Only after the ranking is fixed are the top candidates run on validation.
5. The neighbourhood of the winner is probed. A maximum sitting on a cliff is an
   artefact of the sample; a maximum on a plateau might be a property of the
   strategy. Reporting the neighbourhood is what lets a reader tell which one
   this is.

What this cannot do
-------------------
It cannot make an overfit result honest. Every candidate evaluated on the
training window consumes some of the sample's capacity to surprise, and the
validation window stops being genuinely out-of-sample the moment a result from
it changes a choice. :class:`SearchOutcome` therefore records how many
candidates were evaluated so a reader can discount accordingly, and the
validation window is read exactly once, at the end.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.domain.types import Cash
from amat.errors import DataIntegrityError

__all__ = [
    "WindowStatistics",
    "expand_grid",
    "fold_score",
    "split_panel",
    "window_statistics",
]

#: Folds the training window is cut into when scoring consistency.
_FOLDS = 4

#: A candidate that trades less than this over the training window has not
#: demonstrated anything; its statistics are noise with a good-looking mean.
_MIN_TRAINING_TRADES = 40

#: What a candidate may override. Guard limits are numeric; the Kelly switches
#: are the only non-numeric entries, which is why the alias is a union rather
#: than ``object`` -- an untyped override map hid two float() conversions from
#: the type checker.
type OverrideValue = float | int | bool

#: Fewest sessions that make any return statistic defined.
_MIN_WINDOW_SESSIONS = 2


@dataclass(frozen=True, slots=True)
class WindowStatistics:
    """Performance of one run over one window."""

    sessions: int
    first_session: dt.date
    last_session: dt.date
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    trades: int
    win_rate: float
    expectancy_r: float
    fold_sharpes: tuple[float, ...]

    @property
    def calmar(self) -> float:
        """Annualized return per unit of worst drawdown."""
        if self.max_drawdown >= 0.0:
            return 0.0
        return self.annualized_return / abs(self.max_drawdown)


def split_panel(panel: MarketPanel, train_end: dt.date) -> tuple[MarketPanel, MarketPanel]:
    """Cut ``panel`` into a training panel and a validation panel.

    The validation panel keeps the full history up to its end rather than
    starting at the split: the strategy needs its 252-session warm-up and its
    covariance lookback, and a validation run that began cold at the split date
    would spend its first year warming up instead of trading. Validation
    statistics are therefore computed over the post-split *slice* of a run that
    saw everything, which is what a real deployment would experience.

    Raises:
        DataIntegrityError: if the split leaves either side empty.
    """
    sessions = panel.sessions
    cut = sum(1 for session in sessions if session <= train_end)
    if cut <= 0 or cut >= len(sessions):
        message = (
            f"train_end {train_end} does not split the panel "
            f"({sessions[0]}..{sessions[-1]}, {len(sessions)} sessions)"
        )
        raise DataIntegrityError(message)

    training = MarketPanel(
        sessions=sessions[:cut],
        symbols=panel.symbols,
        adjusted_closes=panel.adjusted_closes[:cut],
        raw_closes=panel.raw_closes[:cut],
        window_vwaps=panel.window_vwaps[:cut],
        window_volumes=panel.window_volumes[:cut],
        source=panel.source,
        adjusted_highs=None if panel.adjusted_highs is None else panel.adjusted_highs[:cut],
        adjusted_lows=None if panel.adjusted_lows is None else panel.adjusted_lows[:cut],
        session_volumes=None if panel.session_volumes is None else panel.session_volumes[:cut],
    )
    return training, panel


def expand_grid(grid: Mapping[str, Sequence[OverrideValue]]) -> Iterator[dict[str, OverrideValue]]:
    """Yield every combination of ``grid`` in a deterministic order."""
    names = sorted(grid)
    for combination in itertools.product(*(grid[name] for name in names)):
        yield dict(zip(names, combination, strict=True))


def window_statistics(
    equity: Sequence[Cash],
    sessions: Sequence[dt.date],
    *,
    trades: int,
    win_rate: float,
    expectancy_r: float,
    sessions_per_year: int = 252,
) -> WindowStatistics:
    """Summarize one equity slice, including per-fold Sharpe consistency."""
    if len(equity) < _MIN_WINDOW_SESSIONS:
        message = f"a window needs at least 2 sessions, received {len(equity)}"
        raise DataIntegrityError(message)

    values = np.array([float(value) for value in equity], dtype=float)
    if np.any(values <= 0):
        message = "equity reached zero or below within the window"
        raise DataIntegrityError(message)

    returns = values[1:] / values[:-1] - 1.0
    years = len(returns) / sessions_per_year
    total_return = float(values[-1] / values[0] - 1.0)
    annualized = float((values[-1] / values[0]) ** (1.0 / years) - 1.0) if years > 0 else 0.0
    volatility = (
        float(returns.std(ddof=1) * np.sqrt(sessions_per_year)) if len(returns) > 1 else 0.0
    )
    sharpe = annualized / volatility if volatility > 0 else 0.0
    drawdowns = values / np.maximum.accumulate(values) - 1.0

    return WindowStatistics(
        sessions=len(values),
        first_session=sessions[0],
        last_session=sessions[-1],
        total_return=total_return,
        annualized_return=annualized,
        annualized_volatility=volatility,
        sharpe=sharpe,
        max_drawdown=float(drawdowns.min()),
        trades=trades,
        win_rate=win_rate,
        expectancy_r=expectancy_r,
        fold_sharpes=_fold_sharpes(returns, sessions_per_year=sessions_per_year),
    )


def _fold_sharpes(returns: np.ndarray, *, sessions_per_year: int) -> tuple[float, ...]:
    """Sharpe of each contiguous fold, for consistency scoring.

    Deliberately the arithmetic convention -- ``mean/std * sqrt(252)`` of the
    fold's session returns -- not the CAGR-over-volatility convention that
    :attr:`WindowStatistics.sharpe` uses for whole windows: a quarter-window
    fold is too short for a compounding-rate annualization to be stable. The
    two differ by roughly ``ann_vol/2``, with the CAGR form lower; the gates
    consume the (conservative) CAGR statistic, while these arithmetic values
    feed only *relative* fold-consistency ranking, where a uniform convention
    cancels. Tables printing both columns are printing two different
    statistics, and readers comparing them across columns are warned here.
    """
    if len(returns) < _FOLDS * 2:
        return ()
    folds = np.array_split(returns, _FOLDS)
    scores: list[float] = []
    for fold in folds:
        if len(fold) < _MIN_WINDOW_SESSIONS:
            continue
        deviation = float(fold.std(ddof=1))
        if deviation <= 0:
            scores.append(0.0)
            continue
        scores.append(float(fold.mean() / deviation * np.sqrt(sessions_per_year)))
    return tuple(scores)


def fold_score(statistics: WindowStatistics) -> float:
    """Rank candidates by worst-fold and median-fold consistency.

    ``0.5 * median + 0.5 * minimum`` of the per-fold Sharpe ratios. The minimum
    term is the point: it refuses to reward a configuration that was carried by
    a single favourable stretch, which an aggregate Sharpe over the whole window
    cannot distinguish from steady performance.

    A candidate below the minimum trade count scores ``-inf`` rather than a
    small number. It is not a weak candidate, it is an unmeasured one, and
    letting it compete on a three-trade sample is how a search selects noise.
    """
    if statistics.trades < _MIN_TRAINING_TRADES or not statistics.fold_sharpes:
        return float("-inf")
    folds = np.array(statistics.fold_sharpes, dtype=float)
    return float(0.5 * np.median(folds) + 0.5 * folds.min())
