"""Session-pattern research with honest costs and multiple-testing control.

The pattern scanner is deliberately separate from strategy execution.  It asks
whether a repeated session leg exists in an immutable snapshot; it does not
turn a positive in-sample mean into an order.  A pattern becomes a candidate
only when it is positive in both the fixed development and validation slices
and survives Benjamini-Hochberg correction in both slices.

The snapshot currently records the 09:30--09:35 VWAP, not a 15:55--16:00 bar.
Consequently ``overnight`` means *official prior adjusted close to next opening
window VWAP*.  It is evidence about that observable leg, never evidence for a
4:55 PM fill that the dataset does not contain.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.errors import DataIntegrityError

__all__ = ["PatternMetrics", "SessionPattern", "scan_session_patterns"]

_BPS = 10_000.0
_MIN_OBSERVATIONS = 60
_MINIMUM_VALID_SAMPLE = 2


@dataclass(frozen=True, slots=True)
class PatternMetrics:
    """Cost-adjusted distribution and trade-quality statistics for one slice."""

    observations: int
    mean_net_bps: float
    median_net_bps: float
    win_rate: float
    average_win_bps: float
    average_loss_bps: float
    payoff_ratio: float
    profit_factor: float
    t_statistic: float
    one_sided_p_value: float
    bh_q_value: float
    compounded_return: float
    max_drawdown: float


@dataclass(frozen=True, slots=True)
class SessionPattern:
    """One symbol/leg hypothesis evaluated on fixed chronological slices."""

    symbol: str
    leg: str
    train: PatternMetrics
    validation: PatternMetrics
    robust_candidate: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _MutableMetrics:
    observations: int
    mean_net_bps: float
    median_net_bps: float
    win_rate: float
    average_win_bps: float
    average_loss_bps: float
    payoff_ratio: float
    profit_factor: float
    t_statistic: float
    one_sided_p_value: float
    bh_q_value: float
    compounded_return: float
    max_drawdown: float

    def freeze(self) -> PatternMetrics:
        return PatternMetrics(**asdict(self))


def _metrics(values: np.ndarray) -> _MutableMetrics:
    finite = np.asarray(values[np.isfinite(values)], dtype=np.float64)
    count = int(finite.size)
    if count == 0:
        return _MutableMetrics(
            observations=0,
            mean_net_bps=math.nan,
            median_net_bps=math.nan,
            win_rate=math.nan,
            average_win_bps=math.nan,
            average_loss_bps=math.nan,
            payoff_ratio=math.nan,
            profit_factor=math.nan,
            t_statistic=math.nan,
            one_sided_p_value=math.nan,
            bh_q_value=math.nan,
            compounded_return=math.nan,
            max_drawdown=math.nan,
        )

    wins = finite[finite > 0.0]
    losses = finite[finite <= 0.0]
    average_win = float(np.mean(wins)) if wins.size else 0.0
    average_loss = float(np.mean(losses)) if losses.size else 0.0
    loss_magnitude = abs(average_loss)
    payoff = average_win / loss_magnitude if loss_magnitude > 0.0 else math.inf
    loss_sum = abs(float(np.sum(losses)))
    profit_factor = float(np.sum(wins)) / loss_sum if loss_sum > 0.0 else math.inf

    mean = float(np.mean(finite))
    sample_std = float(np.std(finite, ddof=1)) if count > 1 else 0.0
    if sample_std > 0.0:
        t_statistic = mean / (sample_std / math.sqrt(count))
        # Normal approximation is conservative enough at the enforced n >= 60
        # and avoids adding a statistics dependency to the execution package.
        p_value = 0.5 * math.erfc(t_statistic / math.sqrt(2.0))
    else:
        t_statistic = math.inf if mean > 0.0 else -math.inf if mean < 0.0 else 0.0
        p_value = 0.0 if mean > 0.0 else 1.0

    wealth = np.cumprod(1.0 + finite)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], wealth)))
    path = np.concatenate(([1.0], wealth))
    drawdown = path / peaks - 1.0
    return _MutableMetrics(
        observations=count,
        mean_net_bps=mean * _BPS,
        median_net_bps=float(np.median(finite)) * _BPS,
        win_rate=float(np.mean(finite > 0.0)),
        average_win_bps=average_win * _BPS,
        average_loss_bps=average_loss * _BPS,
        payoff_ratio=payoff,
        profit_factor=profit_factor,
        t_statistic=t_statistic,
        one_sided_p_value=p_value,
        bh_q_value=math.nan,
        compounded_return=float(wealth[-1] - 1.0),
        max_drawdown=float(np.min(drawdown)),
    )


def _assign_bh_q_values(metrics: list[_MutableMetrics]) -> None:
    """Assign Benjamini-Hochberg q-values over one complete hypothesis family."""
    valid = [
        (index, metric.one_sided_p_value)
        for index, metric in enumerate(metrics)
        if math.isfinite(metric.one_sided_p_value)
    ]
    ordered = sorted(valid, key=lambda pair: pair[1])
    family_size = len(ordered)
    running = 1.0
    for reverse_rank in range(family_size - 1, -1, -1):
        index, p_value = ordered[reverse_rank]
        rank = reverse_rank + 1
        running = min(running, p_value * family_size / rank)
        metrics[index].bh_q_value = min(1.0, running)


def scan_session_patterns(
    panel: MarketPanel,
    *,
    train_end: dt.date,
    round_trip_cost_bps: float = 4.0,
    false_discovery_rate: float = 0.10,
    minimum_observations: int = _MIN_OBSERVATIONS,
) -> list[SessionPattern]:
    """Evaluate close-to-open and open-to-close effects for every symbol.

    The opening price is the snapshot's raw 09:30--09:35 VWAP rescaled onto the
    adjusted basis using that session's ``adjusted_close / raw_close`` factor.
    Costs are subtracted once per completed leg.  BH correction spans every
    symbol and both legs independently in each chronological slice.
    """
    if round_trip_cost_bps < 0.0:
        raise DataIntegrityError("round_trip_cost_bps must be non-negative")
    if not 0.0 < false_discovery_rate <= 1.0:
        raise DataIntegrityError("false_discovery_rate must be in (0, 1]")
    if minimum_observations < _MINIMUM_VALID_SAMPLE:
        raise DataIntegrityError("minimum_observations must be at least 2")

    adjusted = np.asarray(panel.adjusted_closes, dtype=np.float64)
    raw = np.asarray(panel.raw_closes, dtype=np.float64)
    window = np.asarray(panel.window_vwaps, dtype=np.float64)
    volume = np.asarray(panel.window_volumes, dtype=np.float64)
    usable = (
        np.isfinite(adjusted)
        & np.isfinite(raw)
        & np.isfinite(window)
        & np.isfinite(volume)
        & (adjusted > 0.0)
        & (raw > 0.0)
        & (window > 0.0)
        & (volume > 0.0)
    )
    adjusted_open = np.full_like(adjusted, np.nan)
    adjusted_open[usable] = window[usable] * adjusted[usable] / raw[usable]
    cost = round_trip_cost_bps / _BPS
    session_dates = np.asarray(panel.sessions, dtype=object)
    train_mask = session_dates <= train_end
    validation_mask = session_dates > train_end

    train_metrics: list[_MutableMetrics] = []
    validation_metrics: list[_MutableMetrics] = []
    identities: list[tuple[str, str]] = []
    for column, typed_symbol in enumerate(panel.symbols):
        symbol = str(typed_symbol)
        overnight = np.full(len(panel), np.nan)
        valid_overnight = usable[1:, column] & np.isfinite(adjusted[:-1, column])
        valid_overnight &= adjusted[:-1, column] > 0.0
        overnight_rows = np.flatnonzero(valid_overnight) + 1
        overnight[overnight_rows] = (
            adjusted_open[overnight_rows, column] / adjusted[overnight_rows - 1, column]
            - 1.0
            - cost
        )
        intraday = np.full(len(panel), np.nan)
        intraday_rows = np.flatnonzero(usable[:, column])
        intraday[intraday_rows] = (
            adjusted[intraday_rows, column] / adjusted_open[intraday_rows, column] - 1.0 - cost
        )
        for leg, values in (("overnight", overnight), ("intraday", intraday)):
            identities.append((symbol, leg))
            train_metrics.append(_metrics(values[train_mask]))
            validation_metrics.append(_metrics(values[validation_mask]))

    _assign_bh_q_values(train_metrics)
    _assign_bh_q_values(validation_metrics)
    results: list[SessionPattern] = []
    for identity, train, validation in zip(
        identities, train_metrics, validation_metrics, strict=True
    ):
        enough = (
            train.observations >= minimum_observations
            and validation.observations >= minimum_observations
        )
        robust = bool(
            enough
            and train.mean_net_bps > 0.0
            and validation.mean_net_bps > 0.0
            and train.profit_factor > 1.0
            and validation.profit_factor > 1.0
            and train.bh_q_value <= false_discovery_rate
            and validation.bh_q_value <= false_discovery_rate
        )
        results.append(
            SessionPattern(
                symbol=identity[0],
                leg=identity[1],
                train=train.freeze(),
                validation=validation.freeze(),
                robust_candidate=robust,
            )
        )
    return results
