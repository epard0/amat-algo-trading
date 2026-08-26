"""The robustness stress battery the validation protocol promises.

One function per stress family, one study object carrying all of them, so a
single command produces the entire "Not yet run" list from the registry in one
recorded artifact:

* **cost ladder** -- every configured stress multiple of the one-way cost;
* **execution delays** -- the signal-to-execution lag raised to 2 and 3;
* **missed-fill injection** -- every 20th / 10th would-be fill withheld,
  deterministically (5% and 10% rates);
* **close-fill alternative** -- fills at the execution session's raw close
  instead of the opening-window VWAP. (A true opening-auction alternative is
  not offered: the snapshot carries no auction prints, and the 09:30-09:35
  window VWAP *is* the open proxy the baseline already uses. Fabricating an
  "open" from anything else would be a silent fallback.)
* **leave-one-out** -- the full backtest re-run twelve times, each without
  one universe symbol. This doubles as the measurable survivorship-bias
  bound: it quantifies how much of the result rides on any single listing
  the 2026 universe selection happened to include. What it cannot measure --
  assets that delisted before 2026 and so could never be selected -- stays
  unmeasured and is stated in the caveat rather than approximated.
* **trade concentration** -- the additive restatement of the result with the
  top 5 / top 10 round trips removed;
* **year concentration** -- calendar-year returns recompounded without the
  best year;
* **signal-free allocator** -- the same allocator, covariance, caps, costs,
  and calendar with the trend signal replaced by a constant 1 (inverse-vol,
  vol-targeted, capped long book): what the *signal* adds beyond the
  allocation machinery;
* **benchmark correlation** -- the strategy's session returns correlated
  with SPY and with a daily-rebalanced 60/40 SPY/IEF mix on the same
  sessions.

Restatements versus re-runs, stated honestly: trade and year removals are
arithmetic restatements of the baseline run (no compounding re-run -- removing
a trade would have changed every later position size, which no restatement
can reconstruct). Every other row is a genuine re-run of the full engine.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np

from amat.backtest.attribution import ClosedTrade
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult, run_backtest
from amat.config.models import StrategyConfig
from amat.domain.actions import CorporateAction
from amat.domain.instruments import Universe
from amat.domain.types import Cash, SessionDate, Symbol
from amat.errors import DataIntegrityError
from amat.validation.falsification import _narrow_actions, _without_symbol
from amat.validation.optimize import window_statistics

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "RemovalRestatement",
    "StressRun",
    "StressStudy",
    "run_stress_battery",
]

#: Injection cadences for the deterministic missed-fill stress: every 20th
#: fill is a 5% rate, every 10th is 10%.
_MISSED_FILL_CADENCES = (20, 10)

#: Additional signal-to-execution lags to stress (the baseline is 1).
_DELAY_LAGS = (2, 3)

#: Round-trip counts removed by the trade-concentration restatement.
_TRADE_REMOVALS = (5, 10)


@dataclass(frozen=True, slots=True)
class StressRun:
    """Headline metrics of one stressed re-run (or the baseline)."""

    name: str
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    fills: int
    missed: int
    total_costs: float

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "annualized_volatility": self.annualized_volatility,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "fills": self.fills,
            "missed": self.missed,
            "total_costs": self.total_costs,
        }


@dataclass(frozen=True, slots=True)
class RemovalRestatement:
    """An additive restatement: the result minus its best trades or year.

    ``restated_total_return`` subtracts the removed net P&L from final equity
    (or recompounds without the removed year) without re-running the engine;
    position sizing after the removed element is therefore held fixed, which
    is stated on the artifact rather than hidden.
    """

    name: str
    removed: str
    removed_net_pnl: float
    removed_share_of_net_profit: float
    restated_total_return: float

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "removed": self.removed,
            "removed_net_pnl": self.removed_net_pnl,
            "removed_share_of_net_profit": self.removed_share_of_net_profit,
            "restated_total_return": self.restated_total_return,
        }


@dataclass(frozen=True, slots=True)
class StressStudy:
    """Every stress family's outcome beside the baseline it stresses."""

    strategy: str
    baseline: StressRun
    cost_ladder: tuple[StressRun, ...]
    delays: tuple[StressRun, ...]
    missed_fill_injections: tuple[StressRun, ...]
    close_fill: StressRun
    signal_free: StressRun
    leave_one_out: tuple[StressRun, ...]
    trade_removals: tuple[RemovalRestatement, ...]
    year_removals: tuple[RemovalRestatement, ...]
    correlations: Mapping[str, float]
    caveat: str = (
        "Every row is measured on the same burned sample as the baseline; "
        "surviving stress is necessary, never sufficient. Leave-one-out "
        "bounds only the survivorship of the 2026 universe selection among "
        "listings that still exist -- assets that delisted before selection "
        "could never enter the universe, and that bias is unmeasured here. "
        "Trade/year removals are additive restatements, not re-runs."
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "baseline": self.baseline.as_dict(),
            "cost_ladder": [run.as_dict() for run in self.cost_ladder],
            "delays": [run.as_dict() for run in self.delays],
            "missed_fill_injections": [run.as_dict() for run in self.missed_fill_injections],
            "close_fill": self.close_fill.as_dict(),
            "signal_free": self.signal_free.as_dict(),
            "leave_one_out": [run.as_dict() for run in self.leave_one_out],
            "trade_removals": [entry.as_dict() for entry in self.trade_removals],
            "year_removals": [entry.as_dict() for entry in self.year_removals],
            "correlations": dict(self.correlations),
            "caveat": self.caveat,
        }


def _stress_run(name: str, result: BacktestResult, strategy: StrategyConfig) -> StressRun:
    # `window_statistics` rather than `amat.metrics.summarize`: metrics is
    # layer 5 and this module is layer 4; the validation layer's own summary
    # implements the identical conventions (the sweep and falsification
    # batteries already rely on it).
    stats = window_statistics(
        result.equity_curve,
        result.session_dates,
        trades=result.total_fills,
        win_rate=0.0,
        expectancy_r=0.0,
        sessions_per_year=strategy.volatility.annualization_sessions,
    )
    return StressRun(
        name=name,
        total_return=stats.total_return,
        annualized_return=stats.annualized_return,
        annualized_volatility=stats.annualized_volatility,
        sharpe=stats.sharpe,
        max_drawdown=stats.max_drawdown,
        fills=result.total_fills,
        missed=result.total_missed,
        total_costs=float(result.total_costs),
    )


def _with_lag(strategy: StrategyConfig, lag: int) -> StrategyConfig:
    payload = strategy.model_dump()
    payload["execution"]["signal_to_execution_lag_sessions"] = lag
    return StrategyConfig.model_validate(payload)


def _close_fill_panel(panel: MarketPanel) -> MarketPanel:
    """The panel with every execution window priced at the session's raw close.

    Volumes are untouched, so tradability (and every missed-fill rule) is
    identical to the baseline; only the fill price moves.
    """
    return MarketPanel(
        sessions=panel.sessions,
        symbols=panel.symbols,
        adjusted_closes=panel.adjusted_closes,
        raw_closes=panel.raw_closes,
        window_vwaps=panel.raw_closes.copy(),
        window_volumes=panel.window_volumes,
        source=panel.source,
        adjusted_highs=panel.adjusted_highs,
        adjusted_lows=panel.adjusted_lows,
        session_volumes=panel.session_volumes,
    )


def _trade_removals(
    result: BacktestResult, trades: Sequence[ClosedTrade]
) -> tuple[RemovalRestatement, ...]:
    if not trades:
        return ()
    by_profit = sorted(trades, key=lambda trade: trade.net_pnl, reverse=True)
    total_profit = float(sum(float(t.net_pnl) for t in trades if t.net_pnl > 0))
    initial = float(result.initial_equity)
    final = float(result.final_equity)
    restatements = []
    for count in _TRADE_REMOVALS:
        removed = by_profit[:count]
        removed_pnl = float(sum(float(t.net_pnl) for t in removed))
        restatements.append(
            RemovalRestatement(
                name=f"top {count} trades removed",
                removed=", ".join(
                    f"{t.symbol} {t.exit_session.isoformat()} {float(t.net_pnl):+,.0f}"
                    for t in removed[:count]
                ),
                removed_net_pnl=removed_pnl,
                removed_share_of_net_profit=(
                    removed_pnl / total_profit if total_profit > 0 else float("nan")
                ),
                restated_total_return=(final - removed_pnl) / initial - 1.0,
            )
        )
    return tuple(restatements)


def _year_removals(result: BacktestResult) -> tuple[RemovalRestatement, ...]:
    """Calendar-year returns recompounded without the best year."""
    equity = [float(value) for value in result.equity_curve]
    dates = list(result.session_dates)
    year_end: dict[int, float] = {}
    for date, value in zip(dates, equity, strict=True):
        year_end[date.year] = value
    years = sorted(year_end)
    opening = {years[0]: equity[0]}
    for previous, current in itertools.pairwise(years):
        opening[current] = year_end[previous]
    yearly = {year: year_end[year] / opening[year] - 1.0 for year in years}
    best_year = max(yearly, key=lambda year: yearly[year])
    without_best = 1.0
    for year, value in yearly.items():
        if year != best_year:
            without_best *= 1.0 + value
    total = equity[-1] / equity[0] - 1.0
    return (
        RemovalRestatement(
            name="best calendar year removed",
            removed=f"{best_year} ({yearly[best_year]:+.2%})",
            removed_net_pnl=float("nan"),
            removed_share_of_net_profit=(yearly[best_year] / total if total > 0 else float("nan")),
            restated_total_return=without_best - 1.0,
        ),
    )


def _benchmark_correlations(result: BacktestResult, panel: MarketPanel) -> dict[str, float]:
    """Correlation of the strategy's session returns with SPY and 60/40.

    Convention: simple session-over-session returns; the 60/40 mix is
    rebalanced every session (0.6 * SPY + 0.4 * IEF daily returns), which is
    the correlation-appropriate convention -- monthly rebalancing changes the
    weights path, not meaningfully the daily correlation.
    """
    columns = {str(symbol): position for position, symbol in enumerate(panel.symbols)}
    for needed in ("SPY", "IEF"):
        if needed not in columns:
            return {}
    offset = panel.sessions.index(result.sessions[0].session)
    equity = np.array([float(value) for value in result.equity_curve], dtype=np.float64)
    strategy_returns = equity[1:] / equity[:-1] - 1.0
    spy = panel.adjusted_closes[:, columns["SPY"]]
    ief = panel.adjusted_closes[:, columns["IEF"]]
    spy_returns = (spy[1:] / spy[:-1] - 1.0)[offset : offset + strategy_returns.size]
    ief_returns = (ief[1:] / ief[:-1] - 1.0)[offset : offset + strategy_returns.size]
    mixed = 0.6 * spy_returns + 0.4 * ief_returns
    return {
        "SPY": float(np.corrcoef(strategy_returns, spy_returns)[0, 1]),
        "60/40 SPY/IEF": float(np.corrcoef(strategy_returns, mixed)[0, 1]),
    }


def run_stress_battery(
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    initial_cash: Cash = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
    round_trip_loader: Callable[[BacktestResult], Sequence[ClosedTrade]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> StressStudy:
    """Run every stress family against one strategy on one panel.

    ``round_trip_loader`` turns the baseline result into coalesced round
    trips for the trade-concentration restatement. It is injected rather than
    imported because the replay/coalesce implementation lives in the
    reporting layer, which this validation module may not depend on; the CLI
    supplies it. ``None`` skips the trade restatement (recorded as absent,
    never fabricated).

    Raises:
        DataIntegrityError: if the configured stress ladder is empty -- a
            configuration that cannot be stressed cannot satisfy the protocol.
    """
    say = progress or (lambda _message: None)
    actions = actions_by_session or {}
    universe = strategy.to_universe()

    def engine(
        name: str,
        *,
        run_strategy: StrategyConfig | None = None,
        run_panel: MarketPanel | None = None,
        run_universe: Universe | None = None,
        run_actions: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
        one_way_bps: float | None = None,
        drop_every_nth_fill: int | None = None,
        signal_override: np.ndarray | None = None,
    ) -> tuple[StressRun, BacktestResult]:
        say(f"stress: {name}")
        result = run_backtest(
            run_strategy if run_strategy is not None else strategy,
            run_panel if run_panel is not None else panel,
            run_universe if run_universe is not None else universe,
            initial_cash=initial_cash,
            actions_by_session=run_actions if run_actions is not None else actions,
            one_way_bps=one_way_bps,
            drop_every_nth_fill=drop_every_nth_fill,
            signal_override=signal_override,
        )
        return _stress_run(name, result, strategy), result

    baseline_run, baseline = engine(
        f"baseline ({strategy.costs.baseline_one_way_bps:g} bps, lag 1)"
    )

    baseline_bps = strategy.costs.baseline_one_way_bps
    ladder_bps = tuple(bps for bps in strategy.costs.stress_one_way_bps if bps != baseline_bps)
    if not ladder_bps:
        message = "the configured stress_one_way_bps ladder is empty after removing the baseline"
        raise DataIntegrityError(message)
    cost_ladder = tuple(
        engine(f"costs {bps:g} bps one-way", one_way_bps=bps)[0] for bps in ladder_bps
    )

    delays = tuple(
        engine(f"execution delayed to lag {lag}", run_strategy=_with_lag(strategy, lag))[0]
        for lag in _DELAY_LAGS
    )

    missed_fill_injections = tuple(
        engine(
            f"missed fills injected: every {cadence}th ({1 / cadence:.0%})",
            drop_every_nth_fill=cadence,
        )[0]
        for cadence in _MISSED_FILL_CADENCES
    )

    close_fill, _ = engine(
        "fills at session close (window VWAP replaced by raw close)",
        run_panel=_close_fill_panel(panel),
    )

    ones = np.ones_like(panel.adjusted_closes, dtype=np.float64)
    signal_free, _ = engine("signal-free allocator (score = 1 everywhere)", signal_override=ones)

    leave_one_out = []
    for symbol in (str(entry) for entry in universe.symbols):
        reduced = _without_symbol(strategy, symbol)
        reduced_universe = reduced.to_universe()
        kept = [Symbol(str(entry)) for entry in reduced_universe.symbols]
        leave_one_out.append(
            engine(
                f"without {symbol}",
                run_strategy=reduced,
                run_universe=reduced_universe,
                run_panel=panel.slice_symbols(kept),
                run_actions=_narrow_actions(actions, [str(entry) for entry in kept]) or {},
            )[0]
        )

    return StressStudy(
        strategy=f"{strategy.name} v{strategy.version}",
        baseline=baseline_run,
        cost_ladder=cost_ladder,
        delays=delays,
        missed_fill_injections=missed_fill_injections,
        close_fill=close_fill,
        signal_free=signal_free,
        leave_one_out=tuple(leave_one_out),
        trade_removals=(
            _trade_removals(baseline, round_trip_loader(baseline))
            if round_trip_loader is not None
            else ()
        ),
        year_removals=_year_removals(baseline),
        correlations=_benchmark_correlations(baseline, panel),
    )
