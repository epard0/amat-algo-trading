"""Forward projection of performance metrics from a run's own realized returns.

What a projection is here, stated exactly
-----------------------------------------
The question answered: *if the next ``h`` sessions were exchangeable draws
from the same short-memory return process this run realized, what
distribution of terminal equity, return, and drawdown would a given funding
produce?* Every number is a **within-resampling frequency**. It is not a
forecast of any market, it inherits every bias of the burned sample it
resamples (survivorship of the universe, the specific 2016--2026 regime mix),
and a reader who wants "what will I make next year" cannot have that number
from this or any other backtesting tool.

Method
------
Stationary bootstrap (Politis & Romano 1994) over the run's realized
per-session **net** simple returns -- net of the run's own costs, missed
fills, and cash drag, because the equity curve is what actually compounded.
Geometric blocks of expected length ``mean_block_sessions`` preserve
volatility clustering within a block and destroy the historical sequence
across blocks; dependence longer than a typical block does not survive, which
is conservative for a trend strategy (its premium, if any, *is* long memory).

Why strategy returns rather than resampled panels through the engine: a
forward horizon is not the source sample's length, so an engine re-run would
require fabricating future *panels* and re-warming the strategy on them --
compounding fabrication -- while the return bootstrap resamples the one
series that already embeds the engine's execution, costs, and constraints.
The trade-off is honest and recorded: conditioning of position size on
resampled prices is frozen at what history realized.

Horizons are nested by construction: path ``i`` draws one index sequence of
the longest horizon's length, and every shorter horizon reads a prefix of it,
so "the 1-year outcome of path i" and "the first year of path i's 5-year
outcome" are the same event, path by path.

Determinism: path ``i`` uses seed ``seed + i`` and nothing else varies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import SupportsFloat

import numpy as np

from amat.errors import DataIntegrityError
from amat.validation.montecarlo import _stationary_block_indices

__all__ = [
    "PROJECTION_PERCENTILES",
    "HorizonProjection",
    "ProjectionStudy",
    "project_forward",
    "session_returns",
]

#: The percentile grid every projection table reports.
PROJECTION_PERCENTILES: tuple[int, ...] = (5, 25, 50, 75, 95)

#: A projection needs enough realized sessions for blocks to resample
#: meaningfully; below one trading year the "process" being resampled is
#: mostly noise about its own mean.
_MIN_SOURCE_SESSIONS = 252

#: The drawdown exceedance threshold reported alongside each horizon.
_DRAWDOWN_ALERT = -0.20


@dataclass(frozen=True, slots=True)
class HorizonProjection:
    """The resampled outcome distribution at one forward horizon."""

    label: str
    sessions: int
    terminal_equity: Mapping[str, float]
    total_return: Mapping[str, float]
    annualized_return: Mapping[str, float]
    max_drawdown: Mapping[str, float]
    probability_below_funding: float
    probability_drawdown_worse_20pct: float


@dataclass(frozen=True, slots=True)
class ProjectionStudy:
    """Forward outcome distributions for one run, at several horizons."""

    strategy: str
    funding: float
    paths: int
    seed: int
    mean_block_sessions: int
    source_sessions: int
    source_annualized_return: float
    source_annualized_volatility: float
    horizons: tuple[HorizonProjection, ...]
    #: Per-session wealth percentile bands to the longest horizon, for the fan
    #: chart: percentile label -> one value per projected session.
    wealth_bands: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    caveat: str = (
        "Within-resampling frequencies, not forecasts: the bootstrap replays "
        "the burned sample's own net session returns with weakened long "
        "memory. It cannot see regimes the sample never contained, and it "
        "inherits the sample's universe-selection bias unchanged."
    )

    def as_dict(self) -> dict[str, object]:
        """JSON-ready payload for run records and the CLI."""
        return {
            "strategy": self.strategy,
            "funding": self.funding,
            "paths": self.paths,
            "seed": self.seed,
            "mean_block_sessions": self.mean_block_sessions,
            "source_sessions": self.source_sessions,
            "source_annualized_return": self.source_annualized_return,
            "source_annualized_volatility": self.source_annualized_volatility,
            "caveat": self.caveat,
            "horizons": [
                {
                    "label": horizon.label,
                    "sessions": horizon.sessions,
                    "terminal_equity": dict(horizon.terminal_equity),
                    "total_return": dict(horizon.total_return),
                    "annualized_return": dict(horizon.annualized_return),
                    "max_drawdown": dict(horizon.max_drawdown),
                    "probability_below_funding": horizon.probability_below_funding,
                    "probability_drawdown_worse_20pct": (horizon.probability_drawdown_worse_20pct),
                }
                for horizon in self.horizons
            ],
        }


def session_returns(equity_curve: Sequence[SupportsFloat]) -> np.ndarray:
    """Per-session net simple returns of a run's equity curve.

    Raises:
        DataIntegrityError: on a curve too short to resample or a
            non-positive equity value (which would make a relative
            meaningless -- and can only mean the run itself was broken).
    """
    equity = np.array([float(value) for value in equity_curve], dtype=np.float64)
    if equity.size < _MIN_SOURCE_SESSIONS + 1:
        message = (
            f"projection needs at least {_MIN_SOURCE_SESSIONS + 1} equity sessions, "
            f"received {equity.size}; a shorter sample resamples noise"
        )
        raise DataIntegrityError(message)
    if not np.all(equity > 0.0):
        message = "equity curve contains a non-positive value; refusing to project from it"
        raise DataIntegrityError(message)
    return np.asarray(equity[1:] / equity[:-1] - 1.0, dtype=np.float64)


def _percentile_map(values: np.ndarray) -> dict[str, float]:
    return {
        f"p{percentile:02d}": float(np.percentile(values, percentile))
        for percentile in PROJECTION_PERCENTILES
    }


def project_forward(
    returns: np.ndarray,
    *,
    strategy: str,
    funding: float,
    horizon_years: Sequence[float],
    paths: int,
    seed: int,
    annualization_sessions: int = 252,
    mean_block_sessions: int = 21,
) -> ProjectionStudy:
    """Resample ``returns`` forward and summarize outcomes per horizon.

    Args:
        returns: Realized per-session net simple returns of the source run.
        strategy: Label recorded on the study (name + version).
        funding: Starting equity every path compounds from.
        horizon_years: Forward horizons in years, e.g. ``(1, 2, 5, 10)``.
        paths: Resampled paths; percentile resolution is ``1/paths``.
        seed: Base seed; path ``i`` uses ``seed + i``.
        annualization_sessions: Sessions per year for horizon lengths and
            annualized figures.
        mean_block_sessions: Expected stationary-bootstrap block length.

    Raises:
        DataIntegrityError: on a non-positive funding, an empty horizon list,
            a non-positive horizon, or fewer than two paths.
    """
    if funding <= 0:
        message = f"funding must be strictly positive, received {funding}"
        raise DataIntegrityError(message)
    if not horizon_years:
        message = "at least one projection horizon is required"
        raise DataIntegrityError(message)
    if any(years <= 0 for years in horizon_years):
        message = f"projection horizons must be positive years, received {list(horizon_years)}"
        raise DataIntegrityError(message)
    if paths < 2:  # noqa: PLR2004 - a distribution needs more than one draw
        message = f"paths must be at least 2, received {paths}"
        raise DataIntegrityError(message)

    ordered_years = sorted(dict.fromkeys(float(years) for years in horizon_years))
    horizon_sessions = [max(1, round(years * annualization_sessions)) for years in ordered_years]
    longest = horizon_sessions[-1]
    source_count = int(returns.size)

    # One wealth matrix, horizons read as prefixes: (paths, longest).
    wealth = np.empty((paths, longest), dtype=float)
    for index in range(paths):
        rng = np.random.default_rng(seed + index)
        rows = _stationary_block_indices(
            rng, source_count=source_count, length=longest, mean_block=mean_block_sessions
        )
        wealth[index] = funding * np.cumprod(1.0 + returns[rows])

    horizons: list[HorizonProjection] = []
    for years, sessions in zip(ordered_years, horizon_sessions, strict=True):
        window = wealth[:, :sessions]
        terminal = window[:, -1]
        total_return = terminal / funding - 1.0
        annualized = (terminal / funding) ** (annualization_sessions / sessions) - 1.0
        # Drawdown measured against the running peak including the funding
        # level itself, so a path that only falls still shows its loss.
        peaks = np.maximum.accumulate(
            np.concatenate([np.full((paths, 1), funding), window], axis=1), axis=1
        )
        drawdowns = (window / peaks[:, 1:]) - 1.0
        max_drawdown = drawdowns.min(axis=1)
        label = f"{years:g}y"
        horizons.append(
            HorizonProjection(
                label=label,
                sessions=sessions,
                terminal_equity=_percentile_map(terminal),
                total_return=_percentile_map(total_return),
                annualized_return=_percentile_map(annualized),
                max_drawdown=_percentile_map(max_drawdown),
                probability_below_funding=float(np.mean(terminal < funding)),
                probability_drawdown_worse_20pct=float(np.mean(max_drawdown < _DRAWDOWN_ALERT)),
            )
        )

    bands = {
        f"p{percentile:02d}": tuple(
            float(value) for value in np.percentile(wealth, percentile, axis=0)
        )
        for percentile in PROJECTION_PERCENTILES
    }
    mean_return = float(np.mean(returns))
    volatility = float(np.std(returns, ddof=1)) * float(np.sqrt(annualization_sessions))
    annualized_source = float((1.0 + mean_return) ** annualization_sessions - 1.0)
    return ProjectionStudy(
        strategy=strategy,
        funding=float(funding),
        paths=paths,
        seed=seed,
        mean_block_sessions=mean_block_sessions,
        source_sessions=source_count,
        source_annualized_return=annualized_source,
        source_annualized_volatility=volatility,
        horizons=tuple(horizons),
        wealth_bands=bands,
    )
