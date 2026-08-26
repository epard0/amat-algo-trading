"""Reproducible random-window robustness measurements.

The strategy is run once with all history needed for warm-up.  Measurements are
then cut from the resulting immutable session and fill stream.  This preserves
positions carried into a window and avoids the flattering cold-start convention
where every random sample begins in cash.
"""

from __future__ import annotations

import bisect
import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np

from amat.backtest.attribution import ClosedTrade
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import RunResult
from amat.errors import DataIntegrityError

__all__ = [
    "RandomWindow",
    "WindowMeasurement",
    "measure_window",
    "sample_random_windows",
]

_VOLATILITY_BUCKETS = ("quiet", "normal", "volatile")
_SMALL_WIN_MAX_EQUITY_FRACTION = 0.0025
_MONTHS_PER_YEAR = 12
_MIN_VOLATILITY_RETURNS = 2


@dataclass(frozen=True, slots=True)
class RandomWindow:
    """One sampled calendar window and its ex-post market-volatility stratum."""

    window_id: str
    duration_months: int
    start: dt.date
    end: dt.date
    sessions: int
    spy_annualized_volatility: float
    volatility_bucket: str
    selection_split: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WindowMeasurement:
    """Net performance, trading quality, cost, and liquidity for one window."""

    window_id: str
    duration_months: int
    start: str
    end: str
    sessions: int
    volatility_bucket: str
    selection_split: str
    spy_annualized_volatility: float
    start_equity: float
    end_equity: float
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    fills: int
    missed_fills: int
    costs: float
    cost_fraction_of_start: float
    completed_round_trips: int
    profitable_round_trips: int
    win_rate: float
    profit_factor: float | None
    expectancy: float
    median_win: float
    median_loss: float
    small_profitable_round_trips: int
    small_win_rate: float
    median_fill_notional: float
    median_window_participation: float
    p95_window_participation: float
    max_window_participation: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _add_months(value: dt.date, months: int) -> dt.date:
    month_index = value.year * _MONTHS_PER_YEAR + value.month - 1 + months
    year, zero_based_month = divmod(month_index, _MONTHS_PER_YEAR)
    month = zero_based_month + 1
    following = (
        dt.date(year + 1, 1, 1) if month == _MONTHS_PER_YEAR else dt.date(year, month + 1, 1)
    )
    last_day = (following - dt.timedelta(days=1)).day
    return dt.date(year, month, min(value.day, last_day))


def _window_volatility(prices: np.ndarray, start: int, end: int) -> float:
    base = max(0, start - 1)
    values = prices[base : end + 1]
    returns = values[1:] / values[:-1] - 1.0
    if len(returns) < _MIN_VOLATILITY_RETURNS:
        return 0.0
    return float(returns.std(ddof=1) * np.sqrt(252.0))


def sample_random_windows(
    sessions: Sequence[dt.date],
    spy_adjusted_closes: np.ndarray,
    *,
    last_years: int = 3,
    durations_months: Sequence[int] = (1, 3, 6),
    samples_per_duration: int = 12,
    seed: int = 20_260_813,
) -> tuple[RandomWindow, ...]:
    """Sample equally from quiet, normal, and volatile candidate windows.

    Volatility buckets are ex-post diagnostics, not inputs to the strategy. The
    stratification prevents a random draw from accidentally omitting turbulent
    markets. Half of each bucket is declared development and half validation
    before any strategy or parameter result is measured.
    """
    if len(sessions) != len(spy_adjusted_closes):
        raise DataIntegrityError("SPY prices must align exactly to the session axis")
    bucket_count = len(_VOLATILITY_BUCKETS)
    if samples_per_duration < bucket_count or samples_per_duration % bucket_count:
        raise DataIntegrityError("samples_per_duration must be a multiple of three")
    if not sessions:
        raise DataIntegrityError("cannot sample windows from an empty session axis")

    rng = np.random.default_rng(seed)
    last = sessions[-1]
    try:
        cutoff = last.replace(year=last.year - last_years)
    except ValueError:  # February 29
        cutoff = last.replace(year=last.year - last_years, day=28)
    first_candidate = bisect.bisect_left(sessions, cutoff)
    per_bucket = samples_per_duration // bucket_count
    sampled: list[RandomWindow] = []

    for duration in durations_months:
        candidates: list[tuple[int, int, float]] = []
        for start_index in range(first_candidate, len(sessions)):
            target_end = _add_months(sessions[start_index], duration)
            end_index = bisect.bisect_right(sessions, target_end) - 1
            if end_index <= start_index or end_index >= len(sessions):
                continue
            volatility = _window_volatility(spy_adjusted_closes, start_index, end_index)
            candidates.append((start_index, end_index, volatility))
        if len(candidates) < samples_per_duration:
            raise DataIntegrityError(
                f"only {len(candidates)} valid {duration}-month windows; "
                f"cannot sample {samples_per_duration}"
            )

        ranked = sorted(candidates, key=lambda item: item[2])
        bucket_edges = np.array_split(np.arange(len(ranked)), len(_VOLATILITY_BUCKETS))
        for bucket_name, positions in zip(_VOLATILITY_BUCKETS, bucket_edges, strict=True):
            choices = [ranked[int(position)] for position in positions]
            selected = [
                choices[int(index)] for index in rng.choice(len(choices), per_bucket, False)
            ]
            for ordinal, (start_index, end_index, volatility) in enumerate(selected, start=1):
                split = "development" if ordinal <= math.ceil(per_bucket / 2) else "validation"
                sampled.append(
                    RandomWindow(
                        window_id=(
                            f"m{duration}-{bucket_name}-{ordinal:02d}-"
                            f"{sessions[start_index].isoformat()}"
                        ),
                        duration_months=duration,
                        start=sessions[start_index],
                        end=sessions[end_index],
                        sessions=end_index - start_index + 1,
                        spy_annualized_volatility=volatility,
                        volatility_bucket=bucket_name,
                        selection_split=split,
                    )
                )
    return tuple(sorted(sampled, key=lambda item: (item.duration_months, item.window_id)))


def _participation_rates(
    result: RunResult,
    panel: MarketPanel,
    *,
    start: dt.date,
    end: dt.date,
) -> tuple[list[float], list[float]]:
    session_index = {session: row for row, session in enumerate(panel.sessions)}
    symbol_index = {symbol: column for column, symbol in enumerate(panel.symbols)}
    notionals: list[float] = []
    participation: list[float] = []
    for record in result.sessions:
        if not start <= record.session <= end:
            continue
        row = session_index[record.session]
        for fill in record.fills:
            notional = float(fill.gross_notional)
            notionals.append(notional)
            column = symbol_index[fill.symbol]
            market_notional = panel.window_vwaps[row, column] * panel.window_volumes[row, column]
            if np.isfinite(market_notional) and market_notional > 0.0:
                participation.append(notional / float(market_notional))
    return notionals, participation


def measure_window(
    result: RunResult,
    panel: MarketPanel,
    trades: Sequence[ClosedTrade],
    window: RandomWindow,
) -> WindowMeasurement:
    """Measure a window from an already completed, cost-bearing run."""
    result_dates = [record.session for record in result.sessions]
    first = bisect.bisect_left(result_dates, window.start)
    last = bisect.bisect_right(result_dates, window.end)
    if first <= 0 or first >= last:
        raise DataIntegrityError(
            f"window {window.window_id} lacks a prior equity mark or measured sessions"
        )

    measured_records = result.sessions[first:last]
    equity = np.asarray(
        [float(result.sessions[first - 1].equity), *(float(row.equity) for row in measured_records)]
    )
    returns = equity[1:] / equity[:-1] - 1.0
    years = len(returns) / 252.0
    total_return = float(equity[-1] / equity[0] - 1.0)
    annualized_return = (
        float((equity[-1] / equity[0]) ** (1.0 / years) - 1.0) if years > 0.0 else 0.0
    )
    annualized_volatility = float(returns.std(ddof=1) * np.sqrt(252.0)) if len(returns) > 1 else 0.0
    sharpe = annualized_return / annualized_volatility if annualized_volatility > 0.0 else 0.0
    drawdown = equity / np.maximum.accumulate(equity) - 1.0

    fills = [fill for record in measured_records for fill in record.fills]
    missed = sum(len(record.missed) for record in measured_records)
    costs = float(sum((record.attribution.fees for record in measured_records), start=0))
    completed = [
        trade
        for trade in trades
        if window.start <= trade.entry_session and trade.exit_session <= window.end
    ]
    pnl = np.asarray([float(trade.net_pnl) for trade in completed], dtype=float)
    wins = pnl[pnl > 0.0]
    losses = pnl[pnl <= 0.0]
    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0
    small_limit = equity[0] * _SMALL_WIN_MAX_EQUITY_FRACTION
    small_wins = int(np.sum((pnl > 0.0) & (pnl <= small_limit))) if pnl.size else 0
    notionals, participation = _participation_rates(
        result, panel, start=window.start, end=window.end
    )

    return WindowMeasurement(
        window_id=window.window_id,
        duration_months=window.duration_months,
        start=window.start.isoformat(),
        end=window.end.isoformat(),
        sessions=len(measured_records),
        volatility_bucket=window.volatility_bucket,
        selection_split=window.selection_split,
        spy_annualized_volatility=window.spy_annualized_volatility,
        start_equity=float(equity[0]),
        end_equity=float(equity[-1]),
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        sharpe=sharpe,
        max_drawdown=float(drawdown.min()),
        fills=len(fills),
        missed_fills=missed,
        costs=costs,
        cost_fraction_of_start=costs / float(equity[0]),
        completed_round_trips=len(completed),
        profitable_round_trips=int(wins.size),
        win_rate=float(wins.size / pnl.size) if pnl.size else 0.0,
        profit_factor=(gross_profit / gross_loss if gross_loss > 0.0 else None),
        expectancy=float(pnl.mean()) if pnl.size else 0.0,
        median_win=float(np.median(wins)) if wins.size else 0.0,
        median_loss=float(np.median(losses)) if losses.size else 0.0,
        small_profitable_round_trips=small_wins,
        small_win_rate=float(small_wins / pnl.size) if pnl.size else 0.0,
        median_fill_notional=float(np.median(notionals)) if notionals else 0.0,
        median_window_participation=float(np.median(participation)) if participation else 0.0,
        p95_window_participation=(
            float(np.percentile(participation, 95.0)) if participation else 0.0
        ),
        max_window_participation=max(participation, default=0.0),
    )
