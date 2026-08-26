"""Monte Carlo robustness: the strategy on markets that could have happened.

Why bootstrap, not a parametric generator
-----------------------------------------
A single backtest is one draw from history. The question a Monte Carlo answers
is narrower than "will it work" and worth answering precisely: *how much of
the measured result depends on this exact ordering of sessions?* A stationary
block bootstrap (Politis & Romano 1994) resamples the panel's own joint
session moves in random-length blocks, which preserves the two structures a
multi-asset trend strategy actually trades on:

* **cross-sectional dependence** -- a resampled session is one whole row of
  simultaneous per-symbol moves, never a per-symbol shuffle, so correlations
  and cluster structure survive exactly;
* **short-horizon dependence** -- blocks of expected length
  ``mean_block_sessions`` keep volatility clustering and local trend intact
  *within* a block while destroying the specific historical sequence across
  blocks.

What it deliberately destroys is dependence *longer* than the typical block.
That makes the exercise conservative for a trend follower: 252-session trends
survive only by luck of block placement, so the resampled distribution should
be read as "this strategy's mechanics on markets with this joint move
distribution but weaker long memory", not as an unbiased forecast
distribution. A candidate whose observed result sits far above its own
bootstrap distribution earned that gap from long-memory structure -- which is
what a trend premium is -- or from luck; the bootstrap cannot tell those
apart, and no claim made from it should pretend to.

The resampled world's accounting
--------------------------------
Bootstrap prices are built from *adjusted* (total-return) relatives, so
dividends live inside the price path and the resampled panels carry no
corporate actions -- crediting cash dividends on top would double-count them
(the same rule the data-integrity contract states for real panels). Raw and
adjusted series therefore coincide, execution VWAPs are rebuilt from each
sampled session's own overnight-gap relative, and a sampled session with a
missing or zero-volume window stays untradable, so missed-fill realism
survives resampling. Costs, warm-up, and the session calendar are identical
to the source run's.

Determinism
-----------
Path ``i`` uses seed ``seed + i`` and nothing else varies, so any path can be
regenerated alone and the whole study reproduces bit-for-bit at any worker
count -- results are collected by path index, never by completion order.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.models import StrategyConfig
from amat.domain.actions import CorporateAction
from amat.domain.instruments import Universe
from amat.domain.types import SessionDate
from amat.errors import DataIntegrityError
from amat.validation.optimize import window_statistics

__all__ = [
    "MonteCarloStudy",
    "PathOutcome",
    "bootstrap_panel",
    "run_monte_carlo",
]

#: The distribution is summarized at these percentiles, symmetric about the
#: median so both tails are always on the record.
_PERCENTILES = (5, 25, 50, 75, 95)

#: Metrics carried per path and summarized across paths.
_METRICS = (
    "total_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe",
    "max_drawdown",
)

#: Research gate threshold reused for the drawdown exceedance probability.
_DRAWDOWN_GATE = -0.20

#: Two sessions give one relative to resample; three is the honest minimum
#: for a path that is more than a coin flip repeated.
_MIN_SOURCE_SESSIONS = 3


@dataclass(frozen=True, slots=True)
class PathOutcome:
    """Headline statistics of one resampled run (or of the observed run)."""

    name: str
    seed: int | None
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    fills: int
    missed: int


@dataclass(frozen=True, slots=True)
class MonteCarloStudy:
    """The observed run beside the bootstrap distribution it came from."""

    strategy: str
    paths: int
    seed: int
    mean_block_sessions: int
    initial_cash: float
    observed: PathOutcome
    outcomes: tuple[PathOutcome, ...]
    metric_percentiles: Mapping[str, Mapping[str, float]]
    observed_percentile: Mapping[str, float]
    """Fraction of resampled paths at or below the observed value, per metric.

    For returns and Sharpe a HIGH value means the observed history was kind;
    for ``max_drawdown`` (a negative number) a high value means the observed
    drawdown was *shallower* than most resampled worlds.
    """
    probabilities: Mapping[str, float]
    band_dates: tuple[SessionDate, ...]
    equity_bands: Mapping[str, tuple[float, ...]]
    observed_equity: tuple[float, ...]
    caveat: str = (
        "Resampled distribution, not a forecast: the bootstrap preserves the "
        "sample's joint session moves and short-horizon clustering but breaks "
        "dependence longer than its blocks, which is conservative for a trend "
        "strategy. Probabilities describe the resampled worlds only."
    )


def _joint_relatives(panel: MarketPanel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-session joint building blocks: price relatives, gaps, volumes.

    Row ``k`` describes source session ``k + 1``: its adjusted close relative
    to the prior session, its execution-window VWAP relative to the prior raw
    close, and its window volume. The three stay together under resampling --
    a session's gap without that session's volume would break the missed-fill
    joint distribution.
    """
    adjusted = panel.adjusted_closes
    if not np.all(np.isfinite(adjusted)) or np.any(adjusted <= 0):
        message = "bootstrap requires finite, positive adjusted closes throughout the panel"
        raise DataIntegrityError(message)
    relatives = adjusted[1:] / adjusted[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        gaps = panel.window_vwaps[1:] / panel.raw_closes[:-1]
    return relatives, gaps, panel.window_volumes[1:]


def _stationary_block_indices(
    rng: np.random.Generator, *, source_count: int, length: int, mean_block: int
) -> np.ndarray:
    """Stationary-bootstrap row indices: geometric block lengths, circular wrap."""
    restart_probability = 1.0 / float(mean_block)
    indices = np.empty(length, dtype=np.int64)
    position = int(rng.integers(source_count))
    for slot in range(length):
        indices[slot] = position
        if rng.random() < restart_probability:
            position = int(rng.integers(source_count))
        else:
            position = (position + 1) % source_count
    return indices


def bootstrap_panel(
    source: MarketPanel, *, seed: int, mean_block_sessions: int = 21
) -> MarketPanel:
    """One resampled market, on the source panel's own calendar.

    Raises:
        DataIntegrityError: if the source has too few sessions to resample or
            an unusable adjusted-close series, or the block length is invalid.
    """
    if mean_block_sessions < 1:
        message = f"mean_block_sessions must be at least 1, got {mean_block_sessions}"
        raise DataIntegrityError(message)
    if len(source) < _MIN_SOURCE_SESSIONS:
        message = (
            f"bootstrap needs at least {_MIN_SOURCE_SESSIONS} source sessions, "
            f"panel has {len(source)}"
        )
        raise DataIntegrityError(message)

    relatives, gaps, volumes = _joint_relatives(source)
    rng = np.random.default_rng(seed)
    rows = _stationary_block_indices(
        rng,
        source_count=relatives.shape[0],
        length=relatives.shape[0],
        mean_block=mean_block_sessions,
    )

    adjusted = np.empty_like(source.adjusted_closes)
    adjusted[0] = source.adjusted_closes[0]
    np.multiply.accumulate(relatives[rows], axis=0, out=adjusted[1:])
    adjusted[1:] *= source.adjusted_closes[0]

    # Total-return world: dividends live inside the price path, so raw and
    # adjusted coincide and the resampled run carries no corporate actions.
    raw = adjusted.copy()

    window_vwaps = np.empty_like(raw)
    window_vwaps[0] = raw[0]
    window_vwaps[1:] = raw[:-1] * gaps[rows]

    window_volumes = np.empty_like(raw)
    window_volumes[0] = source.window_volumes[0]
    window_volumes[1:] = volumes[rows]

    return MarketPanel(
        sessions=source.sessions,
        symbols=source.symbols,
        adjusted_closes=adjusted,
        raw_closes=raw,
        window_vwaps=window_vwaps,
        window_volumes=window_volumes,
        source=f"bootstrap({source.source}, seed={seed}, mean_block={mean_block_sessions})",
    )


def _outcome_from_run(
    name: str,
    seed: int | None,
    equity: Sequence[Decimal],
    dates: Sequence[SessionDate],
    *,
    fills: int,
    missed: int,
) -> PathOutcome:
    summary = window_statistics(equity, dates, trades=fills, win_rate=0.0, expectancy_r=0.0)
    return PathOutcome(
        name=name,
        seed=seed,
        total_return=summary.total_return,
        annualized_return=summary.annualized_return,
        annualized_volatility=summary.annualized_volatility,
        sharpe=summary.sharpe,
        max_drawdown=summary.max_drawdown,
        fills=fills,
        missed=missed,
    )


def _simulate_path(
    args: tuple[StrategyConfig, MarketPanel, int, int, Decimal],
) -> tuple[PathOutcome, tuple[float, ...]]:
    """Worker: one resampled run. Top-level so it can cross a process boundary.

    The universe is rebuilt from the strategy configuration inside the worker
    rather than shipped across: :class:`~amat.domain.instruments.Universe`
    carries a ``MappingProxyType`` (a deliberate defensive copy), which cannot
    be pickled, and the rebuild is deterministic from the same frozen source.
    """
    strategy, panel, seed, mean_block, initial_cash = args
    resampled = bootstrap_panel(panel, seed=seed, mean_block_sessions=mean_block)
    result = run_backtest(strategy, resampled, strategy.to_universe(), initial_cash=initial_cash)
    outcome = _outcome_from_run(
        f"path seed={seed}",
        seed,
        result.equity_curve,
        result.session_dates,
        fills=result.total_fills,
        missed=result.total_missed,
    )
    return outcome, tuple(float(value) for value in result.equity_curve)


def run_monte_carlo(
    strategy: StrategyConfig,
    panel: MarketPanel,
    universe: Universe,
    *,
    paths: int = 200,
    seed: int = 20260812,
    mean_block_sessions: int = 21,
    initial_cash: Decimal = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
    workers: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> MonteCarloStudy:
    """Run the observed backtest and ``paths`` resampled ones, and summarize.

    The observed run applies the real corporate actions; the resampled runs
    carry none because their prices are total-return by construction. Both
    are total-return accounts, so their statistics are commensurable.

    Args:
        workers: Process count for the resampled runs. ``None`` uses all but
            two of the machine's cores; ``1`` runs inline. The result is
            identical at any worker count.

    Raises:
        DataIntegrityError: if ``paths`` is not strictly positive, or any
            underlying run fails its own integrity checks.
    """
    if paths < 1:
        message = f"paths must be strictly positive, got {paths}"
        raise DataIntegrityError(message)
    if workers is not None and workers < 1:
        message = f"workers must be strictly positive when given, got {workers}"
        raise DataIntegrityError(message)
    # Workers rebuild the universe from the strategy (a MappingProxyType does
    # not pickle), so a caller-supplied universe that differs -- say, a
    # leave-one-out variant -- would put an observed run of one universe
    # inside a bootstrap distribution of another and call them commensurable.
    if tuple(universe.symbols) != tuple(strategy.to_universe().symbols):
        message = (
            "universe does not match strategy.to_universe(); the observed run and "
            "the resampled paths would price different symbol sets"
        )
        raise DataIntegrityError(message)

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    say(f"observed run: {len(panel)} sessions, {len(panel.symbols)} symbols")
    observed_result = run_backtest(
        strategy,
        panel,
        universe,
        initial_cash=initial_cash,
        actions_by_session=actions_by_session,
    )
    observed = _outcome_from_run(
        "observed",
        None,
        observed_result.equity_curve,
        observed_result.session_dates,
        fills=observed_result.total_fills,
        missed=observed_result.total_missed,
    )
    observed_equity = tuple(float(value) for value in observed_result.equity_curve)

    tasks = [
        (strategy, panel, seed + index, mean_block_sessions, initial_cash) for index in range(paths)
    ]
    effective_workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 2)
    say(f"resampling: {paths} paths, {effective_workers} worker(s), base seed {seed}")

    results: list[tuple[PathOutcome, tuple[float, ...]]]
    if effective_workers == 1:
        results = []
        for number, task in enumerate(tasks, start=1):
            results.append(_simulate_path(task))
            if number % 10 == 0 or number == paths:
                say(f"  {number}/{paths} paths complete")
    else:
        with ProcessPoolExecutor(max_workers=effective_workers) as pool:
            # `map` preserves task order, so the collected results are ordered
            # by path index regardless of completion order.
            results = list(pool.map(_simulate_path, tasks, chunksize=1))
        say(f"  {paths}/{paths} paths complete")

    outcomes = tuple(outcome for outcome, _curve in results)
    curves = np.array([curve for _outcome, curve in results], dtype=float)

    metric_percentiles: dict[str, dict[str, float]] = {}
    observed_percentile: dict[str, float] = {}
    for metric in _METRICS:
        values = np.array([getattr(outcome, metric) for outcome in outcomes], dtype=float)
        metric_percentiles[metric] = {
            f"p{percentile:02d}": float(np.percentile(values, percentile))
            for percentile in _PERCENTILES
        }
        observed_percentile[metric] = float((values <= getattr(observed, metric)).mean())

    total_returns = np.array([outcome.total_return for outcome in outcomes])
    drawdowns = np.array([outcome.max_drawdown for outcome in outcomes])
    probabilities = {
        "negative_total_return": float((total_returns < 0.0).mean()),
        "max_drawdown_worse_than_20pct": float((drawdowns < _DRAWDOWN_GATE).mean()),
    }

    equity_bands = {
        f"p{percentile:02d}": tuple(
            float(value) for value in np.percentile(curves, percentile, axis=0)
        )
        for percentile in _PERCENTILES
    }

    return MonteCarloStudy(
        strategy=f"{strategy.name} v{strategy.version}",
        paths=paths,
        seed=seed,
        mean_block_sessions=mean_block_sessions,
        initial_cash=float(initial_cash),
        observed=observed,
        outcomes=outcomes,
        metric_percentiles=metric_percentiles,
        observed_percentile=observed_percentile,
        probabilities=probabilities,
        band_dates=observed_result.session_dates,
        equity_bands=equity_bands,
        observed_equity=observed_equity,
    )
