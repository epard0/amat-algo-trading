"""Heavy-tailed, regime-switching synthetic markets: the crash the sample never had.

WHAT THIS IS NOT
----------------
Every panel here is **manufactured**. A number computed from it describes the
*process written below*, not any real instrument, and must never appear as
measured performance. Panels carry ``source="synthetic-heavytail"`` so a run
manifest cannot hide the distinction.

Why this exists
---------------
The existing robustness work all resamples observed history. The block
bootstrap in :mod:`amat.validation.montecarlo` reorders the panel's own joint
session moves; the stress battery perturbs costs, timing and universe
membership around the same path. None of it can produce a move the decade
2016-2026 did not contain, so none of it answers the question an operator
actually cares about: *what happens when the market does something worse than
anything in the sample?*

This module answers that by generating markets from an explicit process whose
tails are unbounded, then running the unmodified engine on them.

The process
-----------
Per session ``t`` and symbol ``i``, the log return is

    r[t, i] = mu[i] + sigma[i] * v(S[t]) * z[t, i] + beta[i] * K[t] + e[t, i]

* ``S[t]`` is a four-state Markov regime (calm / normal / stressed / crisis)
  with a persistent transition matrix. ``v(S)`` scales diffusion volatility.
  This is the "regime change" axis: the process itself switches behaviour, so
  volatility estimated on the last 60 sessions is genuinely stale after a
  switch, exactly as it is in a real regime break.
* ``z`` is correlated Gaussian noise using the **empirical** correlation of the
  calibration panel, so cross-asset structure is real rather than invented.
* ``K[t]`` is the *market-wide* jump: zero on most sessions, and on a jump
  session (probability set by the regime) a **Pareto**-distributed magnitude
  with a sign that is deliberately biased downward. ``beta[i]`` is the symbol's
  measured beta to the equal-weight market factor, so a crash propagates with
  the same cross-sectional shape the real market has.
* ``e`` is a smaller idiosyncratic Pareto jump so single names can gap without
  the whole market moving.

Why Pareto. The magnitude is drawn as ``x_min * U**(-1/alpha)``, whose tail
index ``alpha`` sets how fat the tail is. At the default ``alpha = 2.5`` the
variance is finite but the third moment is not: multi-sigma sessions are rare
but *not vanishingly* rare the way a Gaussian makes them. Lowering ``alpha``
toward 2 makes the process progressively wilder, and below 2 the theoretical
variance is infinite -- allowed, and reported, because "what if volatility is
not even well defined" is a legitimate thing to ask a volatility-targeting
strategy.

Why the market-wide jump matters more than the idiosyncratic one: a long-only
book's real hazard is not one asset gapping, it is *everything* falling
together while the trend signal is still long from the prior regime. The
common factor is what reproduces that.

Realistic price levels ("weighted")
-----------------------------------
Nothing here is a generic random walk at an arbitrary price. Each symbol is
calibrated from the real snapshot and keeps its own identity:

* starting price = that symbol's actual last close, so levels, tick sizes and
  notional-per-share are the ones the execution and rebalance-band logic
  actually meet;
* ``sigma[i]`` = that symbol's realized annualized volatility, so UUP stays
  placid and USO stays violent instead of both drawing from one number;
* ``beta[i]`` = that symbol's measured beta to the equal-weight market, which
  weights how hard each jump hits it;
* the full **empirical correlation matrix**, PSD-repaired by eigenvalue
  clipping, so the diversification the allocator relies on is the real one.

Drift is set to zero by default, deliberately. Inheriting the sample's realized
drift would hand the strategy a decade-long bull market and let a long-only
book look good for reasons that have nothing to do with its signal. At zero
drift, anything the strategy earns it earns from the trend signal and the
allocator -- and the equal-weight buy-and-hold control reported beside every
scenario shows what the same market gave a passive holder.

Interpreting the output
-----------------------
These are frequencies within a hand-written process, not probabilities about
the future. The right reading is comparative and conditional: *given* markets
with these tails, does the strategy's drawdown stay in the range its historical
backtest suggests, or does it break? A strategy that survives here has not been
validated; a strategy that fails here has been informed against.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.models import StrategyConfig
from amat.data.synthetic import trading_sessions
from amat.domain.types import Cash, Symbol
from amat.errors import DataIntegrityError
from amat.validation.optimize import window_statistics

if TYPE_CHECKING:  # pragma: no cover - typing only
    from amat.validation.optimize import WindowStatistics

__all__ = [
    "HEAVY_TAIL_SOURCE",
    "REGIMES",
    "HeavyTailStudy",
    "MarketCalibration",
    "RegimeSpec",
    "ScenarioOutcome",
    "calibrate_market",
    "generate_heavy_tail_panel",
    "run_heavy_tail_study",
]

#: Stamped onto every generated panel so no artifact can hide its origin.
HEAVY_TAIL_SOURCE = "synthetic-heavytail"

#: Sessions per year used for annualization, matching the rest of the project.
_SESSIONS_PER_YEAR = 252

#: Minimum jump magnitude, in units of the symbol's own daily sigma. A "jump"
#: below one sigma would be indistinguishable from diffusion.
_JUMP_MIN_SIGMAS = 1.0

#: Idiosyncratic jumps are drawn from the same Pareto family at a fraction of
#: the market jump's scale: single names gap, but not as hard as the market.
_IDIOSYNCRATIC_SCALE = 0.45

#: A correlation needs two symbols before an off-diagonal entry exists.
_MIN_SYMBOLS_FOR_A_PAIR = 2

#: Probability that a jump is downward. Real equity crashes are faster than
#: melt-ups; this is an explicit modelling assumption, not a measured quantity,
#: and it is reported in every study so a reader can discount it.
_DOWNSIDE_PROBABILITY = 0.65


@dataclass(frozen=True, slots=True)
class RegimeSpec:
    """One market state: how volatile it is and how often it jumps.

    Attributes:
        name: Label reported in occupancy tables.
        volatility_multiplier: Scales each symbol's calibrated sigma.
        jump_probability: Per-session probability of a market-wide Pareto jump.
        drift_annual: Annualized drift added while in this state. Crisis drift
            is negative because a crisis is not merely a volatile bull market.
    """

    name: str
    volatility_multiplier: float
    jump_probability: float
    drift_annual: float


#: The four states. Calm and normal dominate; stressed and crisis are rare but
#: reachable, and crisis is deliberately harsher than anything in 2016-2026 --
#: that is the entire point of the exercise.
REGIMES: tuple[RegimeSpec, ...] = (
    RegimeSpec("calm", volatility_multiplier=0.65, jump_probability=0.001, drift_annual=0.0),
    RegimeSpec("normal", volatility_multiplier=1.00, jump_probability=0.004, drift_annual=0.0),
    RegimeSpec("stressed", volatility_multiplier=1.90, jump_probability=0.030, drift_annual=-0.05),
    RegimeSpec("crisis", volatility_multiplier=3.40, jump_probability=0.090, drift_annual=-0.25),
)

#: Regime indices by name, derived from ``REGIMES`` so a reordering of the tuple
#: cannot silently repoint the occupancy statistics at the wrong state.
_REGIME_INDEX: dict[str, int] = {spec.name: index for index, spec in enumerate(REGIMES)}

#: Row-stochastic transition matrix over ``REGIMES``. Strongly persistent: a
#: regime lasts weeks to months, so the strategy's trailing volatility estimate
#: has time to adapt -- and is caught stale at every switch, which is the
#: behaviour under test. Crisis can only be entered from stressed, and decays
#: back through it, so crashes have a run-up and an aftermath rather than
#: appearing and vanishing in one session.
_TRANSITIONS = np.array(
    [
        [0.970, 0.030, 0.000, 0.000],
        [0.020, 0.955, 0.025, 0.000],
        [0.000, 0.120, 0.850, 0.030],
        [0.000, 0.000, 0.180, 0.820],
    ],
    dtype=float,
)


@dataclass(frozen=True, slots=True)
class MarketCalibration:
    """Per-symbol parameters measured from a real panel.

    Every field is what makes generated prices *this* universe's prices rather
    than generic ones. Kept as a value object so a study artifact can record
    exactly what the synthetic market was calibrated to.
    """

    symbols: tuple[str, ...]
    last_prices: np.ndarray
    annualized_volatility: np.ndarray
    market_betas: np.ndarray
    correlation: np.ndarray
    source_snapshot: str
    first_session: dt.date
    last_session: dt.date
    sessions_observed: int

    def __post_init__(self) -> None:
        count = len(self.symbols)
        for name in ("last_prices", "annualized_volatility", "market_betas"):
            array = getattr(self, name)
            if array.shape != (count,):
                message = f"{name} has shape {array.shape}, expected {(count,)}"
                raise DataIntegrityError(message)
        if self.correlation.shape != (count, count):
            message = f"correlation has shape {self.correlation.shape}, expected {(count, count)}"
            raise DataIntegrityError(message)
        if np.any(self.last_prices <= 0.0):
            message = "calibration contains a non-positive starting price"
            raise DataIntegrityError(message)
        if np.any(self.annualized_volatility <= 0.0):
            message = "calibration contains a non-positive volatility"
            raise DataIntegrityError(message)

    def as_dict(self) -> dict[str, Any]:
        """Serializable summary for a study artifact."""
        return {
            "source_snapshot": self.source_snapshot,
            "first_session": self.first_session.isoformat(),
            "last_session": self.last_session.isoformat(),
            "sessions_observed": self.sessions_observed,
            "symbols": list(self.symbols),
            "last_price": {
                symbol: round(float(price), 4)
                for symbol, price in zip(self.symbols, self.last_prices, strict=True)
            },
            "annualized_volatility": {
                symbol: round(float(vol), 6)
                for symbol, vol in zip(self.symbols, self.annualized_volatility, strict=True)
            },
            "market_beta": {
                symbol: round(float(beta), 4)
                for symbol, beta in zip(self.symbols, self.market_betas, strict=True)
            },
            "mean_pairwise_correlation": round(float(_mean_offdiagonal(self.correlation)), 4),
        }


def calibrate_market(panel: MarketPanel) -> MarketCalibration:
    """Measure per-symbol level, volatility, beta and correlation from ``panel``.

    Uses adjusted closes throughout: this is a feature-side measurement of the
    market's statistical shape, never an execution or accounting path.

    Raises:
        DataIntegrityError: if the panel is too short to estimate a covariance.
    """
    closes = np.asarray(panel.adjusted_closes, dtype=float)
    if closes.shape[0] < _SESSIONS_PER_YEAR:
        message = (
            f"calibration needs at least {_SESSIONS_PER_YEAR} sessions, received {closes.shape[0]}"
        )
        raise DataIntegrityError(message)
    if np.any(closes <= 0.0):
        message = "calibration panel contains a non-positive adjusted close"
        raise DataIntegrityError(message)

    returns = np.diff(np.log(closes), axis=0)
    sigma_daily = returns.std(axis=0, ddof=1)
    if np.any(sigma_daily <= 0.0):
        message = "a symbol has zero realized volatility over the calibration window"
        raise DataIntegrityError(message)

    # Equal-weight market factor: the simplest defensible definition, and one
    # that does not privilege whichever asset happened to dominate the sample.
    market = returns.mean(axis=1)
    market_variance = float(market.var(ddof=1))
    betas = np.array(
        [
            float(np.cov(returns[:, column], market, ddof=1)[0, 1] / market_variance)
            for column in range(returns.shape[1])
        ],
        dtype=float,
    )

    return MarketCalibration(
        symbols=tuple(str(symbol) for symbol in panel.symbols),
        last_prices=closes[-1].copy(),
        annualized_volatility=sigma_daily * np.sqrt(_SESSIONS_PER_YEAR),
        market_betas=betas,
        correlation=_psd_correlation(np.corrcoef(returns, rowvar=False)),
        source_snapshot=panel.source,
        first_session=panel.sessions[0],
        last_session=panel.sessions[-1],
        sessions_observed=closes.shape[0],
    )


def _psd_correlation(matrix: np.ndarray) -> np.ndarray:
    """Nearest usable correlation matrix by eigenvalue clipping.

    An empirical correlation matrix can be numerically indefinite, and the
    Cholesky factor the generator needs does not exist for such a matrix.
    Clipping the eigenvalues to a small positive floor and renormalizing the
    diagonal is the standard repair; it moves the matrix as little as the
    constraint allows, and it is applied unconditionally so the code path is
    the same for every input rather than only for the awkward ones.
    """
    symmetric = 0.5 * (matrix + matrix.T)
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    repaired = eigenvectors @ np.diag(np.clip(eigenvalues, 1e-8, None)) @ eigenvectors.T
    scale = np.sqrt(np.diag(repaired))
    normalized: np.ndarray = repaired / np.outer(scale, scale)
    np.fill_diagonal(normalized, 1.0)
    return normalized


def _mean_offdiagonal(matrix: np.ndarray) -> float:
    """Mean of the strictly upper-triangular entries."""
    count = matrix.shape[0]
    if count < _MIN_SYMBOLS_FOR_A_PAIR:
        return 0.0
    rows, columns = np.triu_indices(count, k=1)
    return float(matrix[rows, columns].mean())


def _pareto_magnitudes(
    rng: np.random.Generator, shape: tuple[int, ...], *, tail_index: float
) -> np.ndarray:
    """Pareto magnitudes in units of sigma, minimum ``_JUMP_MIN_SIGMAS``.

    Inverse-transform sampled as ``x_min * U**(-1/alpha)`` rather than via
    ``rng.pareto``: the explicit form makes the tail index visible at the call
    site, which matters because it is the single parameter that decides how
    extreme this whole study is.
    """
    uniform = rng.random(shape)
    return _JUMP_MIN_SIGMAS * uniform ** (-1.0 / tail_index)


def _regime_path(rng: np.random.Generator, sessions: int) -> np.ndarray:
    """Sample a regime index per session from the persistent Markov chain.

    Starts in ``normal`` rather than from the stationary distribution: a
    scenario that opens mid-crisis would spend its warm-up in a state the
    strategy never got to observe, which confounds "reacts badly to a regime
    change" with "was never calibrated in the first place".
    """
    path = np.empty(sessions, dtype=int)
    state = 1
    for index in range(sessions):
        path[index] = state
        state = int(rng.choice(len(REGIMES), p=_TRANSITIONS[state]))
    return path


def generate_heavy_tail_panel(
    calibration: MarketCalibration,
    *,
    seed: int,
    sessions: int = 756,
    tail_index: float = 2.5,
    drift_annual: float = 0.0,
    start: dt.date = dt.date(2016, 1, 4),
    zero_volume_probability: float = 0.002,
) -> tuple[MarketPanel, np.ndarray, np.ndarray]:
    """Generate one synthetic market.

    Args:
        calibration: Per-symbol levels, volatilities, betas and correlation.
        seed: Reproducibility seed; the same seed always yields the same panel.
        sessions: Session count. Must exceed the strategy's warm-up to leave
            any measurable window at all.
        tail_index: Pareto ``alpha``. Lower is wilder; below 2.0 the process
            has infinite theoretical variance, which is permitted and reported.
        drift_annual: Baseline annualized drift added to every regime's own
            drift. Zero by default so the strategy cannot inherit a bull market.
        start: First session date of the synthetic calendar.
        zero_volume_probability: Probability an execution window has no volume,
            exercising the missed-fill path.

    Returns:
        ``(panel, regime_path, market_jumps)`` -- the panel plus the two latent
        series a diagnostic needs, since neither is recoverable from prices.

    Raises:
        DataIntegrityError: on a non-positive tail index or too few sessions.
    """
    if tail_index <= 0.0:
        message = f"tail_index must be strictly positive, received {tail_index}"
        raise DataIntegrityError(message)
    if sessions < _SESSIONS_PER_YEAR + 2:
        message = (
            f"sessions must exceed one year of warm-up; received {sessions}, "
            f"need more than {_SESSIONS_PER_YEAR + 1}"
        )
        raise DataIntegrityError(message)

    rng = np.random.default_rng(seed)
    count = len(calibration.symbols)
    dates = trading_sessions(start, sessions)

    factor = np.linalg.cholesky(calibration.correlation)
    sigma_daily = calibration.annualized_volatility / np.sqrt(_SESSIONS_PER_YEAR)

    regimes = _regime_path(rng, sessions)
    vol_multiplier = np.array([spec.volatility_multiplier for spec in REGIMES])[regimes]
    jump_probability = np.array([spec.jump_probability for spec in REGIMES])[regimes]
    regime_drift = np.array([spec.drift_annual for spec in REGIMES])[regimes]

    # Correlated diffusion, scaled per symbol and per regime.
    normals = rng.standard_normal((sessions, count)) @ factor.T
    diffusion = normals * (sigma_daily[None, :] * vol_multiplier[:, None])

    # Market-wide Pareto jumps. One magnitude and one sign per jump session,
    # propagated across symbols by beta: this is a single market event, not
    # twelve coincidences.
    jump_occurs = rng.random(sessions) < jump_probability
    magnitudes = _pareto_magnitudes(rng, (sessions,), tail_index=tail_index)
    signs = np.where(rng.random(sessions) < _DOWNSIDE_PROBABILITY, -1.0, 1.0)
    market_jumps = np.where(jump_occurs, magnitudes * signs, 0.0)
    # Expressed in the market's own volatility scale, then distributed by beta.
    market_sigma = float(np.mean(sigma_daily))
    common = (market_jumps * market_sigma)[:, None] * calibration.market_betas[None, :]

    # Idiosyncratic gaps, rarer per symbol and smaller.
    idio_occurs = rng.random((sessions, count)) < (jump_probability[:, None] * 0.5)
    idio_magnitudes = _pareto_magnitudes(rng, (sessions, count), tail_index=tail_index)
    idio_signs = np.where(rng.random((sessions, count)) < _DOWNSIDE_PROBABILITY, -1.0, 1.0)
    idiosyncratic = np.where(
        idio_occurs,
        idio_magnitudes * idio_signs * sigma_daily[None, :] * _IDIOSYNCRATIC_SCALE,
        0.0,
    )

    drift = (drift_annual + regime_drift)[:, None] / _SESSIONS_PER_YEAR
    log_returns = drift - 0.5 * (sigma_daily[None, :] ** 2) + diffusion + common + idiosyncratic

    adjusted = calibration.last_prices[None, :] * np.exp(np.cumsum(log_returns, axis=0))
    if not np.all(np.isfinite(adjusted)) or np.any(adjusted <= 0.0):
        # Reachable at a very low tail index: a large enough downward jump
        # underflows the price to zero. Failing closed is correct -- a zero
        # price is a delisting, and this generator has no delisting semantics.
        message = (
            f"seed {seed} at tail_index {tail_index} produced a non-positive or "
            "non-finite price; raise tail_index or choose another seed"
        )
        raise DataIntegrityError(message)

    # No dividends or splits are generated, so raw and adjusted coincide;
    # inventing a divergence would fabricate a corporate-action history.
    raw = adjusted.copy()

    # The execution window opens away from the prior close by its own gap, so a
    # fill price genuinely differs from the price the decision was sized at.
    # The gap inherits the regime's volatility: execution is worst exactly when
    # the market is worst, which is the correlation that hurts in practice.
    gap = rng.normal(0.0, 1.0, size=(sessions, count)) * (
        sigma_daily[None, :] * vol_multiplier[:, None] * 0.5
    )
    window_vwap = np.empty_like(raw)
    window_vwap[0] = raw[0]
    window_vwap[1:] = raw[:-1] * np.exp(gap[1:])

    window_volume = rng.uniform(5.0e5, 5.0e6, size=(sessions, count))
    window_volume[rng.random((sessions, count)) < zero_volume_probability] = 0.0

    # High/low bracket the close using the same realized session move, so the
    # range is consistent with the path instead of being decoration.
    half_range = (
        np.abs(rng.normal(0.0, 1.0, size=(sessions, count)))
        * (sigma_daily[None, :] * vol_multiplier[:, None])
        + np.abs(log_returns) * 0.5
    )
    highs = adjusted * np.exp(half_range)
    lows = adjusted * np.exp(-half_range)

    session_volume = rng.uniform(5.0e6, 5.0e7, size=(sessions, count))

    panel = MarketPanel(
        sessions=dates,
        symbols=tuple(Symbol(symbol) for symbol in calibration.symbols),
        adjusted_closes=adjusted,
        raw_closes=raw,
        window_vwaps=window_vwap,
        window_volumes=window_volume,
        source=HEAVY_TAIL_SOURCE,
        adjusted_highs=highs,
        adjusted_lows=lows,
        session_volumes=session_volume,
    )
    return panel, regimes, market_jumps


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """The strategy's result on one generated market, beside its passive control."""

    seed: int
    strategy: WindowStatistics
    buy_and_hold_total_return: float
    buy_and_hold_max_drawdown: float
    worst_session_return: float
    best_session_return: float
    crisis_session_share: float
    stressed_session_share: float
    jump_sessions: int
    largest_market_jump_sigmas: float
    fills: int
    missed_fills: int

    def as_dict(self) -> dict[str, Any]:
        """Serializable mapping for a study artifact."""
        return {
            "seed": self.seed,
            "total_return": round(self.strategy.total_return, 6),
            "annualized_return": round(self.strategy.annualized_return, 6),
            "annualized_volatility": round(self.strategy.annualized_volatility, 6),
            "sharpe": round(self.strategy.sharpe, 4),
            "max_drawdown": round(self.strategy.max_drawdown, 6),
            "buy_and_hold_total_return": round(self.buy_and_hold_total_return, 6),
            "buy_and_hold_max_drawdown": round(self.buy_and_hold_max_drawdown, 6),
            "worst_session_return": round(self.worst_session_return, 6),
            "best_session_return": round(self.best_session_return, 6),
            "crisis_session_share": round(self.crisis_session_share, 4),
            "stressed_session_share": round(self.stressed_session_share, 4),
            "jump_sessions": self.jump_sessions,
            "largest_market_jump_sigmas": round(self.largest_market_jump_sigmas, 3),
            "fills": self.fills,
            "missed_fills": self.missed_fills,
        }


@dataclass(frozen=True, slots=True)
class HeavyTailStudy:
    """Every scenario, the calibration behind them, and the caveats."""

    calibration: MarketCalibration
    outcomes: tuple[ScenarioOutcome, ...]
    tail_index: float
    sessions: int
    drift_annual: float
    initial_cash: Cash
    failed_seeds: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        """Serializable artifact payload."""
        return {
            "process": {
                "tail_index": self.tail_index,
                "sessions_per_scenario": self.sessions,
                "baseline_drift_annual": self.drift_annual,
                "downside_jump_probability": _DOWNSIDE_PROBABILITY,
                "idiosyncratic_scale": _IDIOSYNCRATIC_SCALE,
                "regimes": [
                    {
                        "name": spec.name,
                        "volatility_multiplier": spec.volatility_multiplier,
                        "jump_probability": spec.jump_probability,
                        "drift_annual": spec.drift_annual,
                    }
                    for spec in REGIMES
                ],
                "transition_matrix": _TRANSITIONS.tolist(),
            },
            "calibration": self.calibration.as_dict(),
            "initial_cash": str(self.initial_cash),
            "scenarios": [outcome.as_dict() for outcome in self.outcomes],
            "failed_seeds": list(self.failed_seeds),
            "summary": self.summary(),
            "caveats": [
                "SYNTHETIC DATA. Every number describes a hand-written process, "
                "not any real instrument, and is not a backtest result.",
                "Frequencies within this process are not probabilities about the "
                "future; the tail index, regime matrix and downside bias were "
                "chosen, not estimated.",
                "Surviving these markets does not validate the strategy. Failing "
                "them is informative; passing them is not evidence of an edge.",
                "Drift is zero by default, so the strategy cannot inherit the "
                "sample's bull market. Compare against the buy-and-hold column, "
                "which faced the identical generated market.",
            ],
        }

    def summary(self) -> dict[str, Any]:
        """Distribution across scenarios, plus the passive control's."""
        if not self.outcomes:
            return {}
        returns = np.array([o.strategy.total_return for o in self.outcomes])
        drawdowns = np.array([o.strategy.max_drawdown for o in self.outcomes])
        sharpes = np.array([o.strategy.sharpe for o in self.outcomes])
        passive_returns = np.array([o.buy_and_hold_total_return for o in self.outcomes])
        passive_drawdowns = np.array([o.buy_and_hold_max_drawdown for o in self.outcomes])
        worst = np.array([o.worst_session_return for o in self.outcomes])
        return {
            "scenarios": len(self.outcomes),
            "total_return": _percentiles(returns),
            "max_drawdown": _percentiles(drawdowns),
            "sharpe": _percentiles(sharpes),
            "buy_and_hold_total_return": _percentiles(passive_returns),
            "buy_and_hold_max_drawdown": _percentiles(passive_drawdowns),
            "worst_single_session_market_return": _percentiles(worst),
            "scenarios_with_a_loss": int(np.sum(returns < 0.0)),
            "scenarios_beating_buy_and_hold": int(np.sum(returns > passive_returns)),
            "scenarios_with_smaller_drawdown_than_buy_and_hold": int(
                np.sum(drawdowns > passive_drawdowns)
            ),
            "worst_strategy_drawdown": round(float(drawdowns.min()), 6),
            "worst_buy_and_hold_drawdown": round(float(passive_drawdowns.min()), 6),
        }


def _percentiles(values: np.ndarray) -> dict[str, float]:
    """The five-number summary used everywhere else in this project."""
    return {
        f"p{int(point):02d}": round(float(np.percentile(values, point)), 6)
        for point in (5, 25, 50, 75, 95)
    }


def _equal_weight_buy_and_hold(panel: MarketPanel) -> tuple[float, float]:
    """Total return and max drawdown of an equal-weight, never-rebalanced book.

    The passive control every scenario is judged against. Frictionless by
    construction -- it never trades after inception, so it pays no costs, which
    is stated rather than corrected for: the comparison is deliberately
    unfavourable to the strategy.
    """
    closes = np.asarray(panel.adjusted_closes, dtype=float)
    normalized = closes / closes[0]
    curve = normalized.mean(axis=1)
    drawdowns = curve / np.maximum.accumulate(curve) - 1.0
    return float(curve[-1] / curve[0] - 1.0), float(drawdowns.min())


def run_heavy_tail_study(
    strategy: StrategyConfig,
    calibration: MarketCalibration,
    *,
    scenarios: int = 24,
    sessions: int = 756,
    tail_index: float = 2.5,
    drift_annual: float = 0.0,
    initial_cash: Cash = Decimal("100000"),
    seed: int = 20260815,
    progress: Callable[[str], None] | None = None,
) -> HeavyTailStudy:
    """Run ``strategy`` unchanged across ``scenarios`` generated markets.

    Seeds are ``seed + index`` so the whole study reproduces from one number and
    an individual scenario can be regenerated on its own for inspection.

    A scenario whose prices underflow (possible at a low tail index) is recorded
    in ``failed_seeds`` and skipped rather than silently retried with a
    different seed, which would bias the sample toward survivable markets.
    """

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    if scenarios <= 0:
        message = f"scenarios must be strictly positive, received {scenarios}"
        raise DataIntegrityError(message)

    universe = strategy.to_universe()
    if tuple(str(symbol) for symbol in universe.symbols) != calibration.symbols:
        message = (
            "calibration symbols do not match the strategy universe; "
            f"strategy={[str(s) for s in universe.symbols]} "
            f"calibration={list(calibration.symbols)}"
        )
        raise DataIntegrityError(message)

    outcomes: list[ScenarioOutcome] = []
    failed: list[int] = []
    for index in range(scenarios):
        scenario_seed = seed + index
        try:
            panel, regimes, market_jumps = generate_heavy_tail_panel(
                calibration,
                seed=scenario_seed,
                sessions=sessions,
                tail_index=tail_index,
                drift_annual=drift_annual,
            )
        except DataIntegrityError as exc:
            failed.append(scenario_seed)
            say(f"scenario {index + 1}/{scenarios} seed {scenario_seed}: SKIPPED ({exc})")
            continue

        result = run_backtest(
            strategy,
            panel,
            universe,
            initial_cash=initial_cash,
        )
        stats = window_statistics(
            result.equity_curve,
            result.session_dates,
            trades=result.total_fills,
            win_rate=0.0,
            expectancy_r=0.0,
        )
        passive_return, passive_drawdown = _equal_weight_buy_and_hold(panel)
        market_returns = np.diff(np.log(np.asarray(panel.adjusted_closes, dtype=float)), axis=0)
        market_daily = np.expm1(market_returns.mean(axis=1))

        outcomes.append(
            ScenarioOutcome(
                seed=scenario_seed,
                strategy=stats,
                buy_and_hold_total_return=passive_return,
                buy_and_hold_max_drawdown=passive_drawdown,
                worst_session_return=float(market_daily.min()),
                best_session_return=float(market_daily.max()),
                crisis_session_share=float(np.mean(regimes == _REGIME_INDEX["crisis"])),
                stressed_session_share=float(np.mean(regimes == _REGIME_INDEX["stressed"])),
                jump_sessions=int(np.count_nonzero(market_jumps)),
                largest_market_jump_sigmas=float(np.abs(market_jumps).max()),
                fills=result.total_fills,
                missed_fills=result.total_missed,
            )
        )
        say(
            f"scenario {index + 1}/{scenarios} seed {scenario_seed}: "
            f"strategy {stats.total_return:+.2%} (DD {stats.max_drawdown:.2%}) "
            f"vs buy-and-hold {passive_return:+.2%} (DD {passive_drawdown:.2%})"
        )

    return HeavyTailStudy(
        calibration=calibration,
        outcomes=tuple(outcomes),
        tail_index=tail_index,
        sessions=sessions,
        drift_annual=drift_annual,
        initial_cash=initial_cash,
        failed_seeds=tuple(failed),
    )


def render_heavy_tail_text(study: HeavyTailStudy) -> str:
    """Human-readable study report."""
    lines: list[str] = []
    summary = study.summary()
    lines.append("=" * 108)
    lines.append(
        f"HEAVY-TAIL REGIME STRESS — SYNTHETIC MARKETS (Pareto alpha={study.tail_index}, "
        f"{study.sessions} sessions x {summary.get('scenarios', 0)} scenarios)"
    )
    lines.append("=" * 108)
    lines.append("  ! SYNTHETIC DATA. Not a backtest result and not a forecast. The tail index,")
    lines.append("  ! regime matrix and downside bias were chosen, not estimated. Passing this")
    lines.append("  ! is not evidence of an edge; failing it is evidence of fragility.")
    lines.append("")
    lines.append(
        f"  calibrated to {study.calibration.source_snapshot} "
        f"({study.calibration.first_session}..{study.calibration.last_session}, "
        f"{len(study.calibration.symbols)} symbols); baseline drift "
        f"{study.drift_annual:+.2%}/yr"
    )
    lines.append("")

    lines.append(
        f"  {'SEED':>10}{'STRATEGY':>11}{'DD':>9}{'SHARPE':>8}"
        f"{'BUY&HOLD':>11}{'B&H DD':>9}{'WORST DAY':>11}{'CRISIS':>8}{'JUMPS':>7}{'MAXJMP':>8}"
    )
    for outcome in study.outcomes:
        lines.append(
            f"  {outcome.seed:>10}{outcome.strategy.total_return:>+11.2%}"
            f"{outcome.strategy.max_drawdown:>9.2%}{outcome.strategy.sharpe:>8.2f}"
            f"{outcome.buy_and_hold_total_return:>+11.2%}"
            f"{outcome.buy_and_hold_max_drawdown:>9.2%}"
            f"{outcome.worst_session_return:>+11.2%}"
            f"{outcome.crisis_session_share:>8.1%}"
            f"{outcome.jump_sessions:>7}{outcome.largest_market_jump_sigmas:>8.1f}"
        )
    lines.append("")

    if summary:
        lines.append("  DISTRIBUTION ACROSS SCENARIOS")
        lines.append(f"  {'METRIC':<34}{'p05':>11}{'p25':>11}{'p50':>11}{'p75':>11}{'p95':>11}")
        for label, key in (
            ("strategy total return", "total_return"),
            ("strategy max drawdown", "max_drawdown"),
            ("buy-and-hold total return", "buy_and_hold_total_return"),
            ("buy-and-hold max drawdown", "buy_and_hold_max_drawdown"),
            ("worst single session (market)", "worst_single_session_market_return"),
        ):
            row = summary[key]
            lines.append(
                f"  {label:<34}"
                + "".join(f"{row[f'p{point:02d}']:>+11.2%}" for point in (5, 25, 50, 75, 95))
            )
        lines.append(
            f"  {'strategy sharpe':<34}"
            + "".join(
                f"{summary['sharpe'][f'p{point:02d}']:>11.2f}" for point in (5, 25, 50, 75, 95)
            )
        )
        lines.append("")
        total = summary["scenarios"]
        lines.append(
            f"  losing scenarios: {summary['scenarios_with_a_loss']}/{total}    "
            f"beat buy-and-hold: {summary['scenarios_beating_buy_and_hold']}/{total}    "
            "smaller drawdown than buy-and-hold: "
            f"{summary['scenarios_with_smaller_drawdown_than_buy_and_hold']}/{total}"
        )
        lines.append(
            f"  worst drawdown — strategy {summary['worst_strategy_drawdown']:.2%}, "
            f"buy-and-hold {summary['worst_buy_and_hold_drawdown']:.2%}"
        )
    if study.failed_seeds:
        lines.append("")
        lines.append(
            f"  seeds skipped (price underflow at this tail index): "
            f"{', '.join(str(seed) for seed in study.failed_seeds)}"
        )
    return "\n".join(lines)
