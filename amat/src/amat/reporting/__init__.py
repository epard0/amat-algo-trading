"""Reporting stage: per-asset attribution, concentration gates, and rendering.

Implements :class:`amat.boundaries.ReportingBoundary`.

Contract for this package:

* a report reads a run manifest and the artifacts that manifest references; it
  never recomputes strategy results, because recomputation can drift from the
  result being reported;
* every report states configuration, data hashes, code revision, cost
  assumptions, benchmark, sample, failed experiments, and caveats;
* no report may present a zero-cost run as expected performance, and none may
  describe a result as proven, safe, or profitable.

How attribution stays faithful to the run
-----------------------------------------
Per-asset figures are reconstructed from the *fills the run actually produced*,
replayed through :class:`~amat.risk.trades.TradeLedger`. That is reading the run's
artifacts, not re-running the strategy: no signal, allocation, or execution
decision is recomputed here, so a report can never disagree with the run it
describes.

The concentration gates are computed and reported whether or not they pass.
``.claude/references/promotion-gates.md`` makes "best symbol contributes less
than 25% of total P&L" and "best year less than 40%" rejection conditions, so a
report that omitted them when they failed would be the specific kind of
selective reporting the guardrails forbid.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from amat.backtest.attribution import ClosedTrade, TradeLedger
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.types import SessionDate, Symbol
from amat.errors import DataIntegrityError
from amat.metrics import PerformanceSummary

__all__ = [
    "AssetAttribution",
    "ConcentrationGates",
    "PerformanceReport",
    "attribute_by_asset",
    "build_report",
    "render_html",
    "render_text",
]

#: Promotion-gate thresholds from ``.claude/references/promotion-gates.md``.
MAX_SYMBOL_PNL_SHARE = 0.25
MAX_YEAR_PNL_SHARE = 0.40

#: A report needs at least this many sessions to describe anything.
_MIN_REPORT_SESSIONS = 2


@dataclass(frozen=True, slots=True)
class AssetAttribution:
    """Measured contribution of one instrument to the run.

    Every field describes *this sample*. Nothing here is an expectation.
    """

    symbol: Symbol
    trades: int
    wins: int
    win_rate: float
    gross_pnl: float
    costs: float
    net_pnl: float
    pnl_share: float
    best_trade: float
    worst_trade: float
    average_r: float | None
    average_hold_sessions: float
    profit_factor: float
    first_entry: str
    last_exit: str

    def as_dict(self) -> dict[str, object]:
        """Flat mapping for serialization."""
        return {
            "symbol": self.symbol,
            "trades": self.trades,
            "wins": self.wins,
            "win_rate": self.win_rate,
            "gross_pnl": self.gross_pnl,
            "costs": self.costs,
            "net_pnl": self.net_pnl,
            "pnl_share": self.pnl_share,
            "best_trade": self.best_trade,
            "worst_trade": self.worst_trade,
            "average_r": self.average_r,
            "average_hold_sessions": self.average_hold_sessions,
            "profit_factor": self.profit_factor,
            "first_entry": self.first_entry,
            "last_exit": self.last_exit,
        }


@dataclass(frozen=True, slots=True)
class ConcentrationGates:
    """Whether the run's P&L is dangerously concentrated.

    A result that depends on one instrument or one year is a rejection condition,
    not a caveat, so these are reported alongside the headline figures rather
    than buried.
    """

    best_symbol: str
    best_symbol_share: float
    symbol_gate_passed: bool
    best_year: str
    best_year_share: float
    year_gate_passed: bool
    contributing_symbols: int

    @property
    def all_passed(self) -> bool:
        """Whether both concentration gates passed."""
        return self.symbol_gate_passed and self.year_gate_passed


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """Everything needed to assess one run, from that run's own artifacts."""

    strategy: str
    config_sha256: str
    data_source: str
    engine: str
    initial_equity: float
    final_equity: float
    one_way_bps: float
    is_primary: bool
    first_session: str
    last_session: str
    sessions: int
    equity_curve: tuple[float, ...]
    session_dates: tuple[str, ...]
    drawdown_curve: tuple[float, ...]
    annual_returns: Mapping[str, float]
    annual_pnl: Mapping[str, float]
    assets: tuple[AssetAttribution, ...]
    concentration: ConcentrationGates
    total_fills: int
    total_missed: int
    total_costs: float
    caveats: tuple[str, ...]

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_equity - 1.0

    @property
    def max_drawdown(self) -> float:
        return min(self.drawdown_curve) if self.drawdown_curve else 0.0

    def as_dict(self) -> dict[str, object]:
        """Serializable mapping for a JSON artifact."""
        return {
            "strategy": self.strategy,
            "config_sha256": self.config_sha256,
            "data_source": self.data_source,
            "engine": self.engine,
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "one_way_bps": self.one_way_bps,
            "is_primary": self.is_primary,
            "sample": {
                "first_session": self.first_session,
                "last_session": self.last_session,
                "sessions": self.sessions,
            },
            "annual_returns": dict(self.annual_returns),
            "assets": [asset.as_dict() for asset in self.assets],
            "concentration": {
                "best_symbol": self.concentration.best_symbol,
                "best_symbol_share": self.concentration.best_symbol_share,
                "symbol_gate_passed": self.concentration.symbol_gate_passed,
                "best_year": self.concentration.best_year,
                "best_year_share": self.concentration.best_year_share,
                "year_gate_passed": self.concentration.year_gate_passed,
            },
            "total_fills": self.total_fills,
            "total_missed": self.total_missed,
            "total_costs": self.total_costs,
            "caveats": list(self.caveats),
        }


def attribute_by_asset(
    trades: Sequence[ClosedTrade],
    *,
    session_index: Mapping[dt.date, int] | None = None,
) -> tuple[AssetAttribution, ...]:
    """Per-instrument attribution, ordered by net contribution descending.

    Costs are apportioned by the trade records themselves, so gross and net stay
    separately attributable exactly as the metrics contract requires.

    ``session_index`` maps a session date to its position in the run's calendar.
    When supplied, holding period is measured as the distance between the entry
    and exit sessions, which is well defined for every trade. Trades replayed
    from a fill stream carry ``sessions_held = 0`` because they were never marked
    session by session, and reporting that as a zero-day hold would be false.
    """
    by_symbol: dict[Symbol, list[ClosedTrade]] = {}
    for trade in trades:
        by_symbol.setdefault(trade.symbol, []).append(trade)

    total_net = sum(float(trade.net_pnl) for trade in trades)
    attributions: list[AssetAttribution] = []

    for symbol, symbol_trades in by_symbol.items():
        net_values = np.array([float(trade.net_pnl) for trade in symbol_trades])
        wins = net_values[net_values > 0]
        losses = net_values[net_values <= 0]
        gross_profit = float(wins.sum())
        gross_loss = float(abs(losses.sum()))
        r_values = [t.r_multiple for t in symbol_trades if t.r_multiple is not None]

        # Share of total P&L. Signed against the total so that, when the run is
        # profitable overall, a losing instrument shows a negative share rather
        # than a misleading positive magnitude.
        share = float(net_values.sum() / total_net) if total_net != 0 else 0.0

        attributions.append(
            AssetAttribution(
                symbol=symbol,
                trades=len(symbol_trades),
                wins=int(wins.size),
                win_rate=float(wins.size / len(symbol_trades)),
                gross_pnl=float(sum(float(t.gross_pnl) for t in symbol_trades)),
                costs=float(sum(float(t.costs) for t in symbol_trades)),
                net_pnl=float(net_values.sum()),
                pnl_share=share,
                best_trade=float(net_values.max()),
                worst_trade=float(net_values.min()),
                average_r=float(np.mean(r_values)) if r_values else None,
                average_hold_sessions=float(
                    np.mean([_hold_sessions(t, session_index) for t in symbol_trades])
                ),
                profit_factor=(gross_profit / gross_loss if gross_loss > 0 else float("inf")),
                first_entry=min(t.entry_session for t in symbol_trades).isoformat(),
                last_exit=max(t.exit_session for t in symbol_trades).isoformat(),
            )
        )

    return tuple(sorted(attributions, key=lambda item: item.net_pnl, reverse=True))


def _hold_sessions(trade: ClosedTrade, session_index: Mapping[dt.date, int] | None) -> float:
    """Sessions a trade was open, preferring the run calendar over the mark count."""
    if session_index is not None:
        entry = session_index.get(trade.entry_session)
        exit_ = session_index.get(trade.exit_session)
        if entry is not None and exit_ is not None:
            return float(exit_ - entry)
    return float(trade.sessions_held)


def _annual_breakdown(
    dates: Sequence[dt.date], equity: Sequence[float]
) -> tuple[dict[str, float], dict[str, float]]:
    """Calendar-year returns and P&L from the equity curve."""
    returns: dict[str, float] = {}
    pnl: dict[str, float] = {}
    if not dates:
        return returns, pnl

    year_bounds: dict[int, list[int]] = {}
    for index, date in enumerate(dates):
        year_bounds.setdefault(date.year, []).append(index)

    for year, indices in sorted(year_bounds.items()):
        start = max(indices[0] - 1, 0)
        end = indices[-1]
        opening, closing = equity[start], equity[end]
        returns[str(year)] = (closing / opening - 1.0) if opening > 0 else 0.0
        pnl[str(year)] = closing - opening
    return returns, pnl


def _concentration(
    assets: Sequence[AssetAttribution], annual_pnl: Mapping[str, float]
) -> ConcentrationGates:
    """Evaluate the promotion-gate concentration limits."""
    total_positive = sum(asset.net_pnl for asset in assets if asset.net_pnl > 0)
    best_asset = max(assets, key=lambda item: item.net_pnl, default=None)
    symbol_share = best_asset.net_pnl / total_positive if best_asset and total_positive > 0 else 0.0

    positive_years = {year: value for year, value in annual_pnl.items() if value > 0}
    total_year = sum(positive_years.values())
    best_year = max(positive_years, key=lambda key: positive_years[key], default="n/a")
    year_share = positive_years.get(best_year, 0.0) / total_year if total_year > 0 else 0.0

    return ConcentrationGates(
        best_symbol=best_asset.symbol if best_asset else "n/a",
        best_symbol_share=symbol_share,
        symbol_gate_passed=symbol_share < MAX_SYMBOL_PNL_SHARE,
        best_year=best_year,
        best_year_share=year_share,
        year_gate_passed=year_share < MAX_YEAR_PNL_SHARE,
        contributing_symbols=sum(1 for asset in assets if asset.trades > 0),
    )


def build_report(
    result: object,
    *,
    strategy: str,
    config_sha256: str,
    data_source: str,
    engine: str,
    caveats: Sequence[str] = (),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> PerformanceReport:
    """Build a full report from a completed run's artifacts.

    Args:
        result: A ``BacktestResult`` or ``ManagedBacktestResult``.
        strategy: Strategy name and version.
        config_sha256: Digest of the exact configuration bytes.
        data_source: Provider identifier from the panel.
        engine: Which runner produced the result.
        caveats: Additional caveats to record on the report.
        actions_by_session: The run's corporate actions. Required whenever a
            position can be open across a split. The replay below rebuilds
            round trips from fills alone, and a fill stream does not contain
            the split -- the accountant applied it to the *ledger*, so after a
            2:1 the run legitimately sells twice what the replay thinks it
            bought. Omitting this on a universe that splits raises "sell
            exceeds held" on a forward split and, worse, silently mis-attributes
            on a reverse one. Only the trade-level statistics are affected;
            equity, returns, and drawdown come from the accountant.

    Raises:
        DataIntegrityError: if the run is too short to describe.
    """
    sessions = getattr(result, "sessions", ())
    if len(sessions) < _MIN_REPORT_SESSIONS:
        message = f"a report needs at least 2 sessions, run has {len(sessions)}"
        raise DataIntegrityError(message)

    equity = [float(record.equity) for record in sessions]
    dates = [record.session for record in sessions]

    running_peak = np.maximum.accumulate(np.array(equity))
    drawdown = (np.array(equity) / running_peak - 1.0).tolist()

    # Prefer the run's own trade ledger when it has one (the managed runner);
    # otherwise replay the fills it recorded. Either way this reads artifacts.
    trades: Sequence[ClosedTrade] = getattr(result, "trades", ())
    if not trades:
        ledger = TradeLedger()
        replayed: list[ClosedTrade] = []
        actions = actions_by_session or {}
        for record in sessions:
            # Before the session's fills, matching the accountant's order of
            # operations: it applies actions to opening positions and only then
            # settles. Quantity scales by the ratio and the average price by its
            # inverse, so cost basis and realised P&L are unchanged.
            for action in actions.get(record.session, ()):
                if action.kind == ActionKind.SPLIT:
                    ledger.apply_split(action.symbol, action.ratio)
            for fill in sorted(record.fills, key=lambda item: (item.side == "buy", item.symbol)):
                closed = ledger.apply(fill)
                if closed is not None:
                    replayed.append(closed)
        trades = replayed

    assets = attribute_by_asset(
        trades, session_index={date: index for index, date in enumerate(dates)}
    )
    annual_returns, annual_pnl = _annual_breakdown(dates, equity)

    return PerformanceReport(
        strategy=strategy,
        config_sha256=config_sha256,
        data_source=data_source,
        engine=engine,
        initial_equity=float(getattr(result, "initial_equity", equity[0])),
        final_equity=equity[-1],
        one_way_bps=float(getattr(result, "one_way_bps", 0.0)),
        is_primary=bool(getattr(result, "is_primary", False)),
        first_session=dates[0].isoformat(),
        last_session=dates[-1].isoformat(),
        sessions=len(sessions),
        equity_curve=tuple(equity),
        session_dates=tuple(date.isoformat() for date in dates),
        drawdown_curve=tuple(drawdown),
        annual_returns=annual_returns,
        annual_pnl=annual_pnl,
        assets=assets,
        concentration=_concentration(assets, annual_pnl),
        total_fills=int(getattr(result, "total_fills", 0)),
        total_missed=int(getattr(result, "total_missed", 0)),
        total_costs=float(getattr(result, "total_costs", Decimal(0))),
        caveats=tuple(caveats),
    )


def _sparkline(values: Sequence[float], width: int = 60) -> str:
    """A single-line ASCII chart, for terminals without graphics."""
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    step = max(1, len(values) // width)
    sampled = list(values[::step])[:width]
    low, high = min(sampled), max(sampled)
    span = high - low
    if span <= 0:
        return blocks[0] * len(sampled)
    return "".join(blocks[min(int((value - low) / span * 8), 7)] for value in sampled)


def _bar(value: float, scale: float, width: int = 28) -> str:
    """A horizontal bar for a signed value, centred on zero."""
    if scale <= 0:
        return " " * width
    half = width // 2
    magnitude = min(int(abs(value) / scale * half), half)
    if value >= 0:
        return " " * half + "█" * magnitude + " " * (half - magnitude)
    return " " * (half - magnitude) + "█" * magnitude + " " * half


def render_text(  # noqa: PLR0915 - one linear renderer; splitting hurts readability
    report: PerformanceReport,
) -> str:
    """Render the full report as terminal text with ASCII charts."""
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"  PERFORMANCE REPORT — {report.strategy} [{report.engine}]")
    add("=" * 78)
    for caveat in report.caveats:
        add(f"  ! {caveat}")
    if report.caveats:
        add("")
    add(f"  config sha256 : {report.config_sha256[:16]}")
    add(f"  data source   : {report.data_source}")
    add(
        f"  sample        : {report.first_session} .. {report.last_session} "
        f"({report.sessions} sessions)"
    )
    add(
        f"  cost model    : {report.one_way_bps:.1f} bps one-way   "
        f"primary result: {report.is_primary}"
    )
    add("")

    add("-" * 78)
    add("  HEADLINE")
    add("-" * 78)
    add(f"  starting equity   {report.initial_equity:>16,.2f}")
    add(f"  ending equity     {report.final_equity:>16,.2f}")
    add(f"  total return      {report.total_return:>16.2%}")
    add(f"  max drawdown      {report.max_drawdown:>16.2%}")
    add(f"  total costs       {report.total_costs:>16,.2f}")
    add(f"  fills / missed    {report.total_fills:>10,} / {report.total_missed:,}")
    add("")

    add("-" * 78)
    add("  EQUITY CURVE")
    add("-" * 78)
    add(f"  {_sparkline(report.equity_curve)}")
    add(f"  {report.initial_equity:<,.0f}{' ' * 30}{report.final_equity:>,.0f}")
    add("")
    add("  DRAWDOWN")
    add(f"  {_sparkline(report.drawdown_curve)}")
    add(f"  trough {report.max_drawdown:.2%}")
    add("")

    if report.annual_returns:
        add("-" * 78)
        add("  BY YEAR")
        add("-" * 78)
        scale = max((abs(value) for value in report.annual_pnl.values()), default=1.0)
        add(f"  {'year':<8}{'return':>10}{'P&L':>16}   {'':<28}")
        for year, value in report.annual_returns.items():
            pnl = report.annual_pnl[year]
            add(f"  {year:<8}{value:>10.2%}{pnl:>16,.0f}   {_bar(pnl, scale)}")
        add("")

    add("-" * 78)
    add("  BY ASSET  (net P&L descending)")
    add("-" * 78)
    if not report.assets:
        add("  no closed trades in this run")
    else:
        scale = max(abs(asset.net_pnl) for asset in report.assets)
        add(
            f"  {'sym':<6}{'trades':>7}{'win%':>7}{'net P&L':>13}{'share':>8}"
            f"{'avgR':>7}{'hold':>6}  {'':<28}"
        )
        for asset in report.assets:
            average_r = f"{asset.average_r:>7.3f}" if asset.average_r is not None else "      -"
            add(
                f"  {asset.symbol:<6}{asset.trades:>7}{asset.win_rate:>7.1%}"
                f"{asset.net_pnl:>13,.0f}{asset.pnl_share:>8.1%}{average_r}"
                f"{asset.average_hold_sessions:>6.1f}  {_bar(asset.net_pnl, scale)}"
            )
    add("")

    add("-" * 78)
    add("  CONCENTRATION GATES  (promotion-gates.md)")
    add("-" * 78)
    concentration = report.concentration
    symbol_mark = "PASS" if concentration.symbol_gate_passed else "FAIL"
    year_mark = "PASS" if concentration.year_gate_passed else "FAIL"
    add(
        f"  [{symbol_mark}] best symbol {concentration.best_symbol} contributes "
        f"{concentration.best_symbol_share:.1%} of gross positive P&L "
        f"(limit {MAX_SYMBOL_PNL_SHARE:.0%})"
    )
    add(
        f"  [{year_mark}] best year {concentration.best_year} contributes "
        f"{concentration.best_year_share:.1%} of gross positive P&L "
        f"(limit {MAX_YEAR_PNL_SHARE:.0%})"
    )
    add(f"         {concentration.contributing_symbols} instruments traded")
    add("")

    add("=" * 78)
    return "\n".join(lines)


_HTML_STYLE = """
.viz-root {
  color-scheme: light;
  --surface-1: #fcfcfb; --page: #f9f9f7;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834;
  --pos: #2a78d6; --neg: #e34948; --mid: #f0efec;
  --good: #0ca30c; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19; --page: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926;
    --pos: #3987e5; --neg: #e66767; --mid: #383835;
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --surface-1: #1a1a19; --page: #0d0d0d;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926;
  --pos: #3987e5; --neg: #e66767; --mid: #383835;
}
.viz-root { background: var(--page); color: var(--text-primary);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: 24px; max-width: 1100px; margin: 0 auto; line-height: 1.5; }
.viz-root h1 { font-size: 1.5rem; margin: 0 0 4px; }
.viz-root h2 { font-size: 1.05rem; margin: 28px 0 10px; font-weight: 600; }
.card { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 16px 18px; margin-bottom: 14px; }
.caveat { border-left: 3px solid var(--critical); padding: 10px 14px;
  background: var(--surface-1); border-radius: 6px; margin-bottom: 8px;
  color: var(--text-secondary); font-size: 0.86rem; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap: 10px; }
.tile { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px; }
.tile .label { font-size: 0.72rem; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.04em; }
.tile .value { font-size: 1.4rem; font-weight: 600; margin-top: 2px; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem;
  font-variant-numeric: tabular-nums; }
th { text-align: right; color: var(--muted); font-weight: 500; padding: 6px 8px;
  border-bottom: 1px solid var(--axis); font-size: 0.76rem; text-transform: uppercase;
  letter-spacing: 0.03em; }
th:first-child, td:first-child { text-align: left; }
td { padding: 6px 8px; border-bottom: 1px solid var(--grid); }
.scroll { overflow-x: auto; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.82rem;
  color: var(--text-secondary); margin-bottom: 8px; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin-right: 6px; vertical-align: middle; }
.gate { font-size: 0.86rem; padding: 8px 12px; border-radius: 6px;
  background: var(--surface-1); border: 1px solid var(--border); margin-bottom: 6px; }
.gate.pass { border-left: 3px solid var(--good); }
.gate.fail { border-left: 3px solid var(--critical); }
svg { display: block; width: 100%; height: auto; overflow: visible; }
"""


def _polyline(
    values: Sequence[float],
    width: float,
    height: float,
    *,
    low: float | None = None,
    high: float | None = None,
) -> str:
    """Map a series onto an SVG polyline point string."""
    if not values:
        return ""
    low = min(values) if low is None else low
    high = max(values) if high is None else high
    span = (high - low) or 1.0
    step = width / max(len(values) - 1, 1)
    return " ".join(
        f"{index * step:.2f},{height - (value - low) / span * height:.2f}"
        for index, value in enumerate(values)
    )


def _equity_chart(reports: Sequence[tuple[PerformanceReport, PerformanceSummary]]) -> str:
    """Indexed equity curves. Indexed to 100 so both engines share one axis."""
    width, height = 1000, 240
    series = []
    for (report, _), colour in zip(reports, ("--series-1", "--series-2"), strict=False):
        base = report.equity_curve[0] or 1.0
        series.append((report.engine, [v / base * 100 for v in report.equity_curve], colour))

    every = [value for _, values, _ in series for value in values]
    low, high = min(every), max(every)
    pad = (high - low) * 0.08 or 1.0
    low, high = low - pad, high + pad

    grid = "".join(
        f'<line x1="0" y1="{height - f * height:.1f}" x2="{width}" '
        f'y2="{height - f * height:.1f}" stroke="var(--grid)" stroke-width="1"/>'
        for f in (0, 0.25, 0.5, 0.75, 1.0)
    )
    lines = "".join(
        f'<polyline points="{_polyline(values, width, height, low=low, high=high)}" '
        f'fill="none" stroke="var({colour})" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        for _, values, colour in series
    )
    legend = "".join(
        f'<span><span class="swatch" style="background:var({colour})"></span>{name}</span>'
        for name, _, colour in series
    )
    return (
        f'<div class="legend">{legend}</div>'
        f'<svg viewBox="0 -6 {width} {height + 24}" role="img" '
        f'aria-label="Indexed equity curve, start = 100">{grid}{lines}'
        f'<text x="0" y="{height + 18}" fill="var(--muted)" font-size="11">'
        f"{reports[0][0].first_session}</text>"
        f'<text x="{width}" y="{height + 18}" fill="var(--muted)" font-size="11" '
        f'text-anchor="end">{reports[0][0].last_session}</text>'
        f'<text x="0" y="-2" fill="var(--muted)" font-size="11">'
        f"index (start = 100), high {high:.0f}</text></svg>"
    )


def _drawdown_chart(report: PerformanceReport) -> str:
    width, height = 1000, 120
    values = list(report.drawdown_curve)
    low = min([*values, 0.0])
    points = _polyline(values, width, height, low=low, high=0.0)
    return (
        f'<svg viewBox="0 -6 {width} {height + 22}" role="img" '
        f'aria-label="Drawdown from running peak">'
        f'<line x1="0" y1="0" x2="{width}" y2="0" stroke="var(--grid)" stroke-width="1"/>'
        f'<polyline points="{points}" fill="none" stroke="var(--neg)" stroke-width="2" '
        f'stroke-linejoin="round"/>'
        f'<text x="0" y="{height + 16}" fill="var(--muted)" font-size="11">'
        f"trough {min(values):.2%}</text></svg>"
    )


def _asset_bars(report: PerformanceReport) -> str:
    """Diverging horizontal bars: blue for positive P&L, red for negative."""
    if not report.assets:
        return '<p style="color:var(--muted)">No closed trades in this run.</p>'
    scale = max(abs(a.net_pnl) for a in report.assets) or 1.0
    row_h, label_w, bar_w = 26, 54, 900
    height = row_h * len(report.assets)
    mid = label_w + bar_w / 2
    rows = []
    for index, asset in enumerate(report.assets):
        y = index * row_h + 5
        magnitude = abs(asset.net_pnl) / scale * (bar_w / 2)
        x = mid if asset.net_pnl >= 0 else mid - magnitude
        colour = "--pos" if asset.net_pnl >= 0 else "--neg"
        anchor = "start" if asset.net_pnl >= 0 else "end"
        text_x = mid + magnitude + 8 if asset.net_pnl >= 0 else mid - magnitude - 8
        rows.append(
            f'<text x="0" y="{y + 12}" fill="var(--text-secondary)" font-size="12">'
            f"{asset.symbol}</text>"
            f'<rect x="{x:.1f}" y="{y}" width="{magnitude:.1f}" height="14" rx="4" '
            f'fill="var({colour})"/>'
            f'<text x="{text_x:.1f}" y="{y + 12}" fill="var(--text-secondary)" '
            f'font-size="11" text-anchor="{anchor}">{asset.net_pnl:,.0f}</text>'
        )
    return (
        f'<svg viewBox="0 0 {label_w + bar_w} {height + 6}" role="img" '
        f'aria-label="Net profit and loss by asset">'
        f'<line x1="{mid}" y1="0" x2="{mid}" y2="{height}" stroke="var(--axis)" '
        f'stroke-width="1"/>{"".join(rows)}</svg>'
    )


def _annual_bars(report: PerformanceReport) -> str:
    if not report.annual_pnl:
        return ""
    scale = max(abs(v) for v in report.annual_pnl.values()) or 1.0
    row_h, label_w, bar_w = 26, 54, 900
    height = row_h * len(report.annual_pnl)
    mid = label_w + bar_w / 2
    rows = []
    for index, (year, value) in enumerate(report.annual_pnl.items()):
        y = index * row_h + 5
        magnitude = abs(value) / scale * (bar_w / 2)
        x = mid if value >= 0 else mid - magnitude
        colour = "--pos" if value >= 0 else "--neg"
        anchor = "start" if value >= 0 else "end"
        text_x = mid + magnitude + 8 if value >= 0 else mid - magnitude - 8
        rows.append(
            f'<text x="0" y="{y + 12}" fill="var(--text-secondary)" font-size="12">{year}</text>'
            f'<rect x="{x:.1f}" y="{y}" width="{magnitude:.1f}" height="14" rx="4" '
            f'fill="var({colour})"/>'
            f'<text x="{text_x:.1f}" y="{y + 12}" fill="var(--text-secondary)" font-size="11" '
            f'text-anchor="{anchor}">{value:,.0f} '
            f"({report.annual_returns[year]:.1%})</text>"
        )
    return (
        f'<svg viewBox="0 0 {label_w + bar_w} {height + 6}" role="img" '
        f'aria-label="Profit and loss by calendar year">'
        f'<line x1="{mid}" y1="0" x2="{mid}" y2="{height}" stroke="var(--axis)" '
        f'stroke-width="1"/>{"".join(rows)}</svg>'
    )


def _asset_table(report: PerformanceReport) -> str:
    head = (
        "<tr><th>Symbol</th><th>Trades</th><th>Win rate</th><th>Gross P&amp;L</th>"
        "<th>Costs</th><th>Net P&amp;L</th><th>Share</th><th>Avg R</th>"
        "<th>Hold</th><th>Best</th><th>Worst</th></tr>"
    )
    rows = ""
    for a in report.assets:
        average_r = f"{a.average_r:.3f}" if a.average_r is not None else "-"
        rows += (
            f"<tr><td>{a.symbol}</td><td>{a.trades}</td><td>{a.win_rate:.1%}</td>"
            f"<td>{a.gross_pnl:,.0f}</td><td>{a.costs:,.0f}</td><td>{a.net_pnl:,.0f}</td>"
            f"<td>{a.pnl_share:.1%}</td><td>{average_r}</td>"
            f"<td>{a.average_hold_sessions:.1f}</td><td>{a.best_trade:,.0f}</td>"
            f"<td>{a.worst_trade:,.0f}</td></tr>"
        )
    return f'<div class="scroll"><table>{head}{rows}</table></div>'


def render_html(reports: Sequence[tuple[PerformanceReport, PerformanceSummary]]) -> str:
    """Render a self-contained HTML report for one or more runs.

    Args:
        reports: ``(PerformanceReport, PerformanceSummary)`` pairs.

    Returns:
        An HTML fragment: styles plus body content, no external assets.
    """
    if not reports:
        message = "render_html requires at least one report"
        raise DataIntegrityError(message)

    primary = reports[0][0]
    caveats = "".join(f'<div class="caveat">{c}</div>' for c in primary.caveats)

    tiles = []
    for report, metrics in reports:
        tiles.append(
            f'<div class="card"><h2>{report.engine}</h2><div class="tiles">'
            f'<div class="tile"><div class="label">Start</div>'
            f'<div class="value">{report.initial_equity:,.0f}</div></div>'
            f'<div class="tile"><div class="label">End</div>'
            f'<div class="value">{report.final_equity:,.0f}</div></div>'
            f'<div class="tile"><div class="label">Total return</div>'
            f'<div class="value">{report.total_return:.2%}</div></div>'
            f'<div class="tile"><div class="label">Ann. return</div>'
            f'<div class="value">{metrics.annualized_return:.2%}</div></div>'
            f'<div class="tile"><div class="label">Ann. vol</div>'
            f'<div class="value">{metrics.annualized_volatility:.2%}</div></div>'
            f'<div class="tile"><div class="label">Sharpe</div>'
            f'<div class="value">{metrics.sharpe:.2f}</div></div>'
            f'<div class="tile"><div class="label">Max drawdown</div>'
            f'<div class="value">{report.max_drawdown:.2%}</div></div>'
            f'<div class="tile"><div class="label">Costs</div>'
            f'<div class="value">{report.total_costs:,.0f}</div></div>'
            f"</div></div>"
        )

    sections = []
    for report, _ in reports:
        gates = report.concentration
        symbol_class = "pass" if gates.symbol_gate_passed else "fail"
        year_class = "pass" if gates.year_gate_passed else "fail"
        sections.append(
            f"<h2>{report.engine} — net P&amp;L by asset</h2>"
            f'<div class="card">{_asset_bars(report)}</div>'
            f"<h2>{report.engine} — by calendar year</h2>"
            f'<div class="card">{_annual_bars(report)}</div>'
            f"<h2>{report.engine} — drawdown</h2>"
            f'<div class="card">{_drawdown_chart(report)}</div>'
            f"<h2>{report.engine} — per-asset detail</h2>"
            f'<div class="card">{_asset_table(report)}</div>'
            f"<h2>{report.engine} — concentration gates</h2>"
            f'<div class="gate {symbol_class}">'
            f"{'PASS' if gates.symbol_gate_passed else 'FAIL'} — best symbol "
            f"{gates.best_symbol} contributes {gates.best_symbol_share:.1%} of gross "
            f"positive P&amp;L (limit {MAX_SYMBOL_PNL_SHARE:.0%})</div>"
            f'<div class="gate {year_class}">'
            f"{'PASS' if gates.year_gate_passed else 'FAIL'} — best year "
            f"{gates.best_year} contributes {gates.best_year_share:.1%} of gross "
            f"positive P&amp;L (limit {MAX_YEAR_PNL_SHARE:.0%})</div>"
        )

    return (
        f"<title>AMAT backtest report — {primary.strategy}</title>"
        f"<style>{_HTML_STYLE}</style>"
        f'<div class="viz-root">'
        f"<h1>AMAT performance report</h1>"
        f'<p style="color:var(--text-secondary);margin:0 0 14px">'
        f"{primary.strategy} · config {primary.config_sha256[:16]} · "
        f"{primary.first_session} .. {primary.last_session} "
        f"({primary.sessions} sessions) · {primary.one_way_bps:.1f} bps one-way</p>"
        f"{caveats}"
        f"<h2>Headline</h2>{''.join(tiles)}"
        f"<h2>Equity curves (indexed to 100)</h2>"
        f'<div class="card">{_equity_chart(reports)}</div>'
        f"{''.join(sections)}"
        f"</div>"
    )
