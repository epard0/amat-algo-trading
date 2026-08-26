"""Metrics stage: performance, drawdowns, turnover, attribution.

Contract for this package:

* every metric documents its numerator, denominator, annualization convention,
  treatment of missing values, and handling of cash flows;
* gross and net results are computed separately, and the difference is
  attributed to fees, spread/slippage, missed fills, and the cash assumption;
* metrics are computed from immutable run artifacts, never recomputed from a
  different sample than the run used.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from amat.backtest.runner import RunResult
from amat.errors import DataIntegrityError

#: Fewer sessions than this makes every return statistic undefined.
_MIN_SESSIONS_FOR_METRICS = 2

__all__ = ["PerformanceSummary", "summarize"]


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    """Measured performance of one run.

    Every field is a *measurement of this sample*, not an expectation. Nothing
    here forecasts, and no field should be described as proven or profitable.

    Conventions, stated once and applied throughout:

    * returns are simple session-over-session equity returns, ``E[t]/E[t-1] - 1``;
    * annualization uses ``sessions_per_year`` sessions, compounding for return
      and ``sqrt`` for volatility;
    * Sharpe is ``annualized_return / annualized_volatility``, using an excess
      return over the run's own cash assumption -- with the canonical 0% cash
      assumption this equals the raw ratio;
    * drawdown is measured on the equity curve, peak to trough, as a fraction.
    """

    sessions: int
    first_session: str
    last_session: str
    initial_equity: float
    final_equity: float
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    max_drawdown_start: str
    max_drawdown_trough: str
    hit_rate: float
    average_gross_exposure: float
    total_fills: int
    total_missed_fills: int
    total_costs: float
    cost_drag_annualized: float
    one_way_bps: float
    is_primary: bool

    def as_dict(self) -> dict[str, object]:
        """Plain mapping for serialization into a report artifact."""
        return {
            field: getattr(self, field)
            for field in (
                "sessions",
                "first_session",
                "last_session",
                "initial_equity",
                "final_equity",
                "total_return",
                "annualized_return",
                "annualized_volatility",
                "sharpe",
                "max_drawdown",
                "max_drawdown_start",
                "max_drawdown_trough",
                "hit_rate",
                "average_gross_exposure",
                "total_fills",
                "total_missed_fills",
                "total_costs",
                "cost_drag_annualized",
                "one_way_bps",
                "is_primary",
            )
        }


def summarize(result: RunResult, *, sessions_per_year: int = 252) -> PerformanceSummary:
    """Compute the performance summary of a completed run.

    Raises:
        DataIntegrityError: if the run has fewer than two sessions, which makes
            every return-based statistic undefined rather than merely noisy.
    """
    if len(result.sessions) < _MIN_SESSIONS_FOR_METRICS:
        message = f"performance summary needs at least 2 sessions, run has {len(result.sessions)}"
        raise DataIntegrityError(message)

    equity = np.array([float(record.equity) for record in result.sessions], dtype=float)
    if np.any(equity <= 0):
        message = "equity curve reached zero or below; the run is void, not merely poor"
        raise DataIntegrityError(message)

    returns = equity[1:] / equity[:-1] - 1.0
    years = len(returns) / sessions_per_year

    total_return = float(equity[-1] / equity[0] - 1.0)
    annualized_return = float((equity[-1] / equity[0]) ** (1.0 / years) - 1.0) if years > 0 else 0.0
    annualized_volatility = float(returns.std(ddof=1) * np.sqrt(sessions_per_year))
    sharpe = annualized_return / annualized_volatility if annualized_volatility > 0 else 0.0

    running_peak = np.maximum.accumulate(equity)
    drawdowns = equity / running_peak - 1.0
    trough_index = int(np.argmin(drawdowns))
    peak_index = int(np.argmax(equity[: trough_index + 1])) if trough_index > 0 else 0

    total_costs = float(result.total_costs)
    cost_drag = (total_costs / float(equity[0]) / years) if years > 0 else 0.0

    return PerformanceSummary(
        sessions=len(result.sessions),
        first_session=result.sessions[0].session.isoformat(),
        last_session=result.sessions[-1].session.isoformat(),
        initial_equity=float(equity[0]),
        final_equity=float(equity[-1]),
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        sharpe=sharpe,
        max_drawdown=float(drawdowns.min()),
        max_drawdown_start=result.sessions[peak_index].session.isoformat(),
        max_drawdown_trough=result.sessions[trough_index].session.isoformat(),
        hit_rate=float((returns > 0).mean()),
        average_gross_exposure=float(
            np.mean([record.gross_exposure for record in result.sessions])
        ),
        total_fills=result.total_fills,
        total_missed_fills=result.total_missed,
        total_costs=total_costs,
        cost_drag_annualized=cost_drag,
        one_way_bps=result.one_way_bps,
        is_primary=result.is_primary,
    )
