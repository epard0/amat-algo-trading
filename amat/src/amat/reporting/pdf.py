"""Self-contained PDF study report.

One document, built page by page from the run's own artifacts:

1. headline and caveats;
2. a single color-coded comparison chart of every asset's total-return path
   beside the strategy's equity path;
3. the strategy's cumulative curve against frictionless reference baskets, with
   drawdown;
4. the total portfolio value in currency, split into holdings and cash;
5. one page per asset: price with every buy and sell marked and its recorded
   :class:`~amat.domain.orders.TradeReason`, the replayed portfolio weight, and
   that asset's individual statistics;
6. the per-asset statistics table;
7. two pages of episode diagnostics (captured trends and whipsaws, then
   give-backs and missed rallies);
8. optionally, a clearly labelled LLM-generated advisory page.

Design rules applied throughout: one y-axis per chart, colors follow the
instrument (grouped by cluster hue) and never its rank, the all-asset
comparison chart carries both a legend and direct end labels so identity
never rides on color alone (charts with two or three series distinguished by
line style carry a legend only), and wherever a benchmark is frictionless it
says so. Tabular data is drawn as real tables -- header row, gridlines, and
right-aligned numerals -- never as monospaced text with ASCII borders; the
only remaining text pages are prose (the advisory commentary).
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.ticker import FuncFormatter

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import RunResult
from amat.domain.fills import Fill
from amat.domain.orders import TradeReason
from amat.domain.types import SessionDate, Side
from amat.errors import DataIntegrityError
from amat.metrics import PerformanceSummary
from amat.reporting import (
    MAX_SYMBOL_PNL_SHARE,
    MAX_YEAR_PNL_SHARE,
    PerformanceReport,
)
from amat.reporting.insights import (
    CAPTURED_TREND_RETURN,
    StrategyInsights,
    _panel_offset,
)

if TYPE_CHECKING:
    from amat.reporting.sleeves import SleeveReport

__all__ = ["render_study_pdf"]

#: Fixed per-symbol colors, hue-grouped by cluster so that related instruments
#: read as families. Assignment is by identity: a filtered or reordered chart
#: never repaints a symbol.
_SYMBOL_COLORS: dict[str, str] = {
    # us_equity: blues
    "SPY": "#0b4f9e",
    "QQQ": "#2f7fd1",
    "IWM": "#7fb3e3",
    # international: purples
    "EFA": "#6a3fb5",
    "EEM": "#a07fd6",
    "FXI": "#c4a9e8",
    # rates: greens
    "TLT": "#1b7837",
    "IEF": "#63b06f",
    "TIP": "#a6d96a",
    # credit (v5): magentas
    "HYG": "#ad1457",
    "EMB": "#e377c2",
    # metals: gold and silver
    "GLD": "#b8860b",
    "SLV": "#8c8c8c",
    # commodities: warm browns
    "DBC": "#b35806",
    "USO": "#e08214",
    # real_estate (v5): dark brown
    "VNQ": "#6d4534",
    # us_dollar: teal
    "UUP": "#2a8f85",
}
_FALLBACK_COLORS = ("#555555", "#777777", "#999999")
_STRATEGY_COLOR = "#111111"
_BUY_COLOR = "#1a7d1a"
_SELL_COLOR = "#c62828"
_GRID = {"color": "#d9d9d9", "linewidth": 0.6}
_PAGE = (11.69, 8.27)  # A4 landscape, inches

#: Above this many fills on one asset page, per-marker rationale text is
#: replaced by a reason summary; a page of overlapping labels reads as noise.
_MAX_ANNOTATED_FILLS = 28

#: Weight of equity above which the asset counts as "held" for time-in-market.
_HELD_WEIGHT = 0.001

#: Fewer recorded sessions than this cannot be charted.
_MIN_CHART_SESSIONS = 2

_REASON_SHORT = {
    TradeReason.WEIGHT_DELTA: "rebalance: weight drifted past the 2pp band",
    TradeReason.POSITION_TRANSITION: "signal crossed the deadband (entry/exit)",
    TradeReason.CONSTRAINT_BREACH: "cap breach forced a reduction",
    TradeReason.CORPORATE_ACTION: "instruction rescaled across a split",
    TradeReason.RETRY_MISSED_FILL: "retry of an earlier missed fill",
}


def _color_for(symbol: str, position: int) -> str:
    return _SYMBOL_COLORS.get(symbol, _FALLBACK_COLORS[position % len(_FALLBACK_COLORS)])


def _style(axis: Axes) -> None:
    axis.grid(True, **_GRID)
    axis.set_axisbelow(True)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    axis.tick_params(labelsize=7)


def _date_axis(axis: Axes) -> None:
    axis.xaxis.set_major_locator(mdates.AutoDateLocator())
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))


def _text_page(pdf: PdfPages, title: str, lines: Sequence[str], *, mono: bool = True) -> None:
    """A prose page. Tabular data must use :func:`_draw_table` instead."""
    figure = plt.figure(figsize=_PAGE)
    figure.text(0.06, 0.94, title, fontsize=15, fontweight="bold", va="top")
    body = "\n".join(lines)
    figure.text(
        0.06,
        0.88,
        body,
        fontsize=7.6,
        va="top",
        family="monospace" if mono else "sans-serif",
        linespacing=1.35,
    )
    pdf.savefig(figure)
    plt.close(figure)


_TABLE_EDGE = "#c9d2dc"
_TABLE_HEADER_FILL = "#e8edf3"
_TABLE_STRIPE_FILL = "#f5f7f9"
_GATE_MET_FILL = "#e2f0e2"
_GATE_MISSED_FILL = "#f6dfdf"


def _draw_table(
    axis: Axes,
    *,
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
    numeric_columns: Sequence[int] = (),
    col_widths: Sequence[float] | None = None,
    font_size: float = 7.0,
    row_scale: float = 1.45,
    cell_fills: Mapping[tuple[int, int], str] | None = None,
) -> None:
    """Render one real table onto ``axis``: header row, gridlines, striping.

    Numbers belong in ``numeric_columns`` so they right-align and read down a
    column; identity columns stay left-aligned. Row indices in ``cell_fills``
    are 1-based body rows, matching matplotlib's table cell grid where row 0 is
    the header.

    An empty ``rows`` renders a labelled absence instead of a table --
    matplotlib cannot draw a zero-row table, and a silently skipped section
    would read as a rendering defect rather than an empty result.
    """
    axis.axis("off")
    if not rows:
        axis.text(0.0, 1.0, "none in this sample", fontsize=font_size, va="top", color="#666666")
        return

    table = axis.table(
        cellText=[list(row) for row in rows],
        colLabels=list(columns),
        loc="upper left",
        cellLoc="left",
        colWidths=list(col_widths) if col_widths is not None else None,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    table.scale(1.0, row_scale)

    numeric = set(numeric_columns)
    fills = cell_fills or {}
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor(_TABLE_EDGE)
        cell.set_linewidth(0.5)
        if row == 0:
            cell.set_facecolor(_TABLE_HEADER_FILL)
            cell.get_text().set_fontweight("bold")
        else:
            if row % 2 == 0:
                cell.set_facecolor(_TABLE_STRIPE_FILL)
            if column in numeric:
                cell.get_text().set_horizontalalignment("right")
        override = fills.get((row, column))
        if override is not None:
            cell.set_facecolor(override)


def _headline_page(pdf: PdfPages, report: PerformanceReport, metrics: PerformanceSummary) -> None:
    """Run identity, measured results, and the concentration gates, as tables."""
    gates = report.concentration
    figure = plt.figure(figsize=_PAGE)
    figure.text(0.06, 0.95, "AMAT performance report", fontsize=15, fontweight="bold", va="top")

    grid = figure.add_gridspec(
        2,
        2,
        left=0.06,
        right=0.94,
        top=0.88,
        bottom=0.40,
        wspace=0.08,
        height_ratios=(5, 2),
    )

    identity_rows = [
        ("strategy", f"{report.strategy}  [{report.engine}]"),
        ("config sha256", report.config_sha256[:16]),
        ("data source", report.data_source),
        ("sample", f"{report.first_session} .. {report.last_session}"),
        ("sessions", f"{report.sessions:,}"),
        ("cost model", f"{report.one_way_bps:.1f} bps one-way"),
        ("primary result", str(report.is_primary)),
    ]
    identity_axis = figure.add_subplot(grid[0, 0])
    _draw_table(
        identity_axis,
        columns=("run", "value"),
        rows=identity_rows,
        col_widths=(0.32, 0.68),
        font_size=7.5,
    )

    result_rows = [
        ("starting equity", f"{report.initial_equity:,.2f}"),
        ("ending equity", f"{report.final_equity:,.2f}"),
        ("total return", f"{report.total_return:+.2%}"),
        ("annualized return", f"{metrics.annualized_return:+.2%}"),
        ("annualized volatility", f"{metrics.annualized_volatility:.2%}"),
        ("Sharpe", f"{metrics.sharpe:.2f}"),
        ("max drawdown", f"{report.max_drawdown:.2%}"),
        ("total costs", f"{report.total_costs:,.2f}"),
        ("fills / missed", f"{report.total_fills:,} / {report.total_missed:,}"),
    ]
    results_axis = figure.add_subplot(grid[0, 1])
    _draw_table(
        results_axis,
        columns=("measured result", "value"),
        rows=result_rows,
        numeric_columns=(1,),
        col_widths=(0.45, 0.55),
        font_size=7.5,
    )

    gate_rows = [
        (
            "best symbol share of gross positive P&L",
            "PASS" if gates.symbol_gate_passed else "FAIL",
            gates.best_symbol,
            f"{gates.best_symbol_share:.1%}",
            f"{MAX_SYMBOL_PNL_SHARE:.0%}",
        ),
        (
            "best year share of gross positive P&L",
            "PASS" if gates.year_gate_passed else "FAIL",
            gates.best_year,
            f"{gates.best_year_share:.1%}",
            f"{MAX_YEAR_PNL_SHARE:.0%}",
        ),
    ]
    gate_fills = {
        (row, 1): _GATE_MET_FILL if verdict == "PASS" else _GATE_MISSED_FILL
        for row, (_gate, verdict, _who, _share, _limit) in enumerate(gate_rows, start=1)
    }
    gates_axis = figure.add_subplot(grid[1, :])
    gates_axis.set_title(
        "Concentration gates (promotion-gates.md)", fontsize=8.5, loc="left", pad=2
    )
    _draw_table(
        gates_axis,
        columns=("gate", "status", "carried by", "measured", "limit"),
        rows=gate_rows,
        numeric_columns=(3, 4),
        col_widths=(0.44, 0.10, 0.14, 0.16, 0.16),
        font_size=7.5,
        cell_fills=gate_fills,
    )

    caveat_lines: list[str] = []
    for caveat in report.caveats:
        caveat_lines.extend(textwrap.wrap(f"! {caveat}", width=130, subsequent_indent="  "))
    figure.text(0.06, 0.335, "CAVEATS", fontsize=8.5, fontweight="bold", va="top")
    figure.text(
        0.06,
        0.31,
        "\n".join(caveat_lines),
        fontsize=7.2,
        va="top",
        linespacing=1.4,
        color="#333333",
    )
    pdf.savefig(figure)
    plt.close(figure)


def _comparison_page(
    pdf: PdfPages, result: RunResult, panel: MarketPanel, report: PerformanceReport
) -> None:
    """The single color-coded chart: every asset against the strategy.

    Assets are plotted as total-return growth of 1 (adjusted closes) over the
    measured sample; the strategy is its equity curve on the same basis. A log
    axis keeps a 5x asset and a 0.2x asset legible on one chart.
    """
    offset = _panel_offset(result, panel)
    rows = len(result.sessions)
    dates = [record.session for record in result.sessions]
    adjusted = panel.adjusted_closes[offset : offset + rows]

    figure, axis = plt.subplots(figsize=_PAGE)
    for position, symbol in enumerate(panel.symbols):
        series = adjusted[:, position] / adjusted[0, position]
        color = _color_for(str(symbol), position)
        axis.plot(dates, series, color=color, linewidth=1.1, label=str(symbol))
        axis.annotate(
            f"{symbol} {series[-1]:.2f}x",
            xy=(dates[-1], series[-1]),
            xytext=(4, 0),
            textcoords="offset points",
            fontsize=6.5,
            color=color,
            va="center",
        )

    equity = np.array([float(record.equity) for record in result.sessions])
    strategy_growth = equity / equity[0]
    axis.plot(
        dates,
        strategy_growth,
        color=_STRATEGY_COLOR,
        linewidth=2.2,
        label="AMAT strategy",
    )
    axis.annotate(
        f"AMAT {strategy_growth[-1]:.2f}x",
        xy=(dates[-1], strategy_growth[-1]),
        xytext=(4, 6),
        textcoords="offset points",
        fontsize=7.5,
        fontweight="bold",
        color=_STRATEGY_COLOR,
        va="center",
    )

    axis.set_yscale("log")
    axis.set_title(
        "Every asset vs the strategy -- total-return growth of 1, "
        f"{report.first_session} .. {report.last_session} (log scale)",
        fontsize=11,
    )
    axis.set_ylabel("growth of 1 (dividends reinvested)", fontsize=8)
    _style(axis)
    _date_axis(axis)
    axis.legend(loc="upper left", fontsize=6.5, ncol=3, frameon=False)
    figure.text(
        0.06,
        0.015,
        "Asset lines are frictionless total-return paths of the instruments themselves; "
        "the strategy line is the simulated account net of its modelled costs.",
        fontsize=6.5,
        color="#555555",
    )
    pdf.savefig(figure)
    plt.close(figure)


def _cumulative_page(
    pdf: PdfPages, result: RunResult, panel: MarketPanel, report: PerformanceReport
) -> None:
    """Strategy equity against frictionless reference baskets, plus drawdown."""
    offset = _panel_offset(result, panel)
    rows = len(result.sessions)
    dates = [record.session for record in result.sessions]
    adjusted = panel.adjusted_closes[offset : offset + rows]

    equity = np.array([float(record.equity) for record in result.sessions])
    strategy = equity / equity[0]

    figure, (top, bottom) = plt.subplots(2, 1, figsize=_PAGE, height_ratios=(3, 1), sharex=True)

    top.plot(dates, strategy, color=_STRATEGY_COLOR, linewidth=2.0, label="AMAT strategy (net)")
    symbols = [str(symbol) for symbol in panel.symbols]
    if "SPY" in symbols:
        spy = adjusted[:, symbols.index("SPY")]
        top.plot(
            dates,
            spy / spy[0],
            color="#0b4f9e",
            linewidth=1.2,
            linestyle="--",
            label="SPY total return (frictionless)",
        )
    growth = adjusted / adjusted[0]
    equal_weight = growth.mean(axis=1)
    top.plot(
        dates,
        equal_weight,
        color="#8c6bb1",
        linewidth=1.2,
        linestyle=":",
        label=f"equal-weight {len(symbols)}-asset basket (frictionless, no rebalance)",
    )
    top.set_title(
        "Cumulative growth -- strategy vs reference baskets "
        "(references bear no costs; the comparison is biased in their favor)",
        fontsize=10,
    )
    top.legend(loc="upper left", fontsize=7, frameon=False)
    _style(top)

    running_peak = np.maximum.accumulate(equity)
    drawdown = equity / running_peak - 1.0
    bottom.fill_between(dates, drawdown, 0.0, color="#c62828", alpha=0.35, linewidth=0)
    bottom.plot(dates, drawdown, color="#c62828", linewidth=1.0)
    bottom.set_ylabel("drawdown", fontsize=8)
    bottom.annotate(
        f"trough {report.max_drawdown:.2%}",
        xy=(0.01, 0.06),
        xycoords="axes fraction",
        fontsize=7,
        color="#c62828",
    )
    _style(bottom)
    _date_axis(bottom)
    pdf.savefig(figure)
    plt.close(figure)


def _portfolio_value_page(pdf: PdfPages, result: RunResult, report: PerformanceReport) -> None:
    """Total portfolio value in currency over time, split into holdings and cash.

    The growth charts answer "how did it compound?"; this page answers "what was
    the account worth?" in the ledger's own units. Holdings are drawn as
    ``equity - cash`` from the session records, so the stack meets the equity
    line by construction; the identity itself is enforced upstream by the
    accountant, not testable from this chart.
    """
    dates = [record.session for record in result.sessions]
    equity = np.array([float(record.equity) for record in result.sessions])
    cash = np.array([float(record.cash) for record in result.sessions])
    holdings = equity - cash

    figure, axis = plt.subplots(figsize=_PAGE)
    axis.stackplot(
        dates,
        holdings,
        cash,
        labels=("invested (marked holdings)", "cash"),
        colors=("#2f7fd1", "#c9c9c9"),
        alpha=0.55,
        linewidth=0,
    )
    axis.plot(dates, equity, color=_STRATEGY_COLOR, linewidth=2.0, label="total portfolio value")
    axis.axhline(
        float(result.initial_equity),
        color="#666666",
        linewidth=0.8,
        linestyle="--",
        label=f"initial funding {float(result.initial_equity):,.0f}",
    )

    final = equity[-1]
    net = final - float(result.initial_equity)
    axis.annotate(
        f"final {final:,.0f}  (net {net:+,.0f})",
        xy=(dates[-1], final),
        xytext=(-8, 10),
        textcoords="offset points",
        ha="right",
        fontsize=8,
        fontweight="bold",
        color=_STRATEGY_COLOR,
    )
    peak_row = int(np.argmax(equity))
    # Annotate the peak only when it is a distinct earlier event; when the run
    # ends at its high the "final" label already says so and the two labels
    # would overprint each other.
    if peak_row < len(equity) - max(1, len(equity) // 20):
        axis.annotate(
            f"peak {equity[peak_row]:,.0f} ({dates[peak_row].isoformat()})",
            xy=(dates[peak_row], equity[peak_row]),
            xytext=(6, 6),
            textcoords="offset points",
            fontsize=7,
            color="#555555",
        )

    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:,.0f}"))
    axis.set_ylabel("portfolio value (account currency)", fontsize=9)
    axis.set_title(
        f"Total portfolio value over time -- {report.strategy} "
        f"(net of {result.one_way_bps:.1f} bps one-way costs)",
        fontsize=10,
    )
    axis.legend(loc="upper left", fontsize=7, frameon=False)
    _style(axis)
    _date_axis(axis)
    pdf.savefig(figure)
    plt.close(figure)


def _fills_by_symbol(result: RunResult) -> dict[str, list[tuple[SessionDate, Fill]]]:
    by_symbol: dict[str, list[tuple[SessionDate, Fill]]] = {}
    for record in result.sessions:
        for fill in record.fills:
            by_symbol.setdefault(str(fill.symbol), []).append((record.session, fill))
    return by_symbol


def _asset_page(
    pdf: PdfPages,
    symbol: str,
    position: int,
    *,
    result: RunResult,
    panel: MarketPanel,
    insights: StrategyInsights,
    report: PerformanceReport,
    sleeve_lookup: Mapping[str, SleeveReport],
) -> None:
    offset = _panel_offset(result, panel)
    rows = len(result.sessions)
    dates = [record.session for record in result.sessions]
    series = panel.adjusted_closes[offset : offset + rows, position]
    color = _color_for(symbol, position)

    figure, (price_axis, weight_axis) = plt.subplots(
        2, 1, figsize=_PAGE, height_ratios=(3, 1), sharex=True
    )
    price_axis.plot(dates, series, color=color, linewidth=1.2)
    price_axis.set_title(f"{symbol} -- adjusted close with every executed trade", fontsize=11)
    price_axis.set_ylabel("adjusted close", fontsize=8)

    fills = _fills_by_symbol(result).get(symbol, [])
    legend_lines = _plot_fills(price_axis, fills, dates, series)
    _style(price_axis)

    weight_axis.fill_between(
        dates, insights.weights[:, position], 0.0, color=color, alpha=0.4, linewidth=0
    )
    weight_axis.set_ylabel("weight of equity", fontsize=8)
    weight_axis.set_ylim(bottom=0.0)
    _style(weight_axis)
    _date_axis(weight_axis)

    stat_bits = _asset_stat_lines(
        symbol,
        position,
        series=series,
        insights=insights,
        report=report,
        sleeve_lookup=sleeve_lookup,
    )
    figure.text(0.06, 0.055, "\n".join(legend_lines), fontsize=6.4, color="#444444")
    figure.text(0.06, 0.012, "\n".join(stat_bits), fontsize=6.4, color="#222222")
    figure.subplots_adjust(bottom=0.17)
    pdf.savefig(figure)
    plt.close(figure)


def _plot_fills(
    price_axis: Axes,
    fills: Sequence[tuple[SessionDate, Fill]],
    dates: Sequence[SessionDate],
    series: np.ndarray,
) -> list[str]:
    """Mark every fill on the price line; return the page's legend lines."""
    date_row = {date: row for row, date in enumerate(dates)}
    annotate = len(fills) <= _MAX_ANNOTATED_FILLS
    for session, fill in fills:
        row = date_row.get(session)
        if row is None:
            continue
        buy = fill.side == Side.BUY
        marker_color = _BUY_COLOR if buy else _SELL_COLOR
        transition = fill.reason == TradeReason.POSITION_TRANSITION
        price_axis.scatter(
            [session],
            [series[row]],
            marker="^" if buy else "v",
            s=42 if transition else 16,
            facecolor=marker_color if transition else "none",
            edgecolor=marker_color,
            linewidths=1.0,
            zorder=5,
        )
        if annotate:
            reason = _REASON_SHORT.get(fill.reason, "reason unrecorded")
            price_axis.annotate(
                f"{'BUY' if buy else 'SELL'} {float(fill.quantity):,.0f} @ "
                f"{float(fill.price):,.2f}\n{reason}",
                xy=(session, series[row]),
                xytext=(0, 14 if buy else -22),
                textcoords="offset points",
                fontsize=5.2,
                ha="center",
                color=marker_color,
            )

    legend_lines = [
        "markers: triangle up = buy, triangle down = sell; "
        "filled = deadband entry/exit, hollow = rebalance. Annotated prices are "
        "raw execution VWAPs; the line is the adjusted series",
    ]
    reason_counts: dict[str, int] = {}
    for _session, fill in fills:
        label = _REASON_SHORT.get(fill.reason, "reason unrecorded")
        reason_counts[label] = reason_counts.get(label, 0) + 1
    if reason_counts:
        summary = "; ".join(f"{count}x {label}" for label, count in sorted(reason_counts.items()))
        legend_lines.append(f"rationale: {summary}")
    if not annotate and fills:
        legend_lines.append(
            f"({len(fills)} fills -- per-marker labels suppressed above "
            f"{_MAX_ANNOTATED_FILLS}; rationale counts above are complete)"
        )
    return legend_lines


def _asset_stat_lines(
    symbol: str,
    position: int,
    *,
    series: np.ndarray,
    insights: StrategyInsights,
    report: PerformanceReport,
    sleeve_lookup: Mapping[str, SleeveReport],
) -> list[str]:
    """The individual statistics footer for one asset page."""
    stats = next((asset for asset in report.assets if str(asset.symbol) == symbol), None)
    stat_bits: list[str] = []
    if stats is not None:
        stat_bits.append(
            f"round trips {stats.trades} | win rate {stats.win_rate:.0%} | "
            f"net P&L {stats.net_pnl:,.0f} ({stats.pnl_share:+.1%} of total) | "
            f"avg hold {stats.average_hold_sessions:.0f} sessions"
        )
    else:
        stat_bits.append("no closed round trips for this symbol in the sample")
    time_in_market = float(np.mean(insights.weights[:, position] > _HELD_WEIGHT))
    growth = float(series[-1] / series[0])
    stat_bits.append(
        f"time in market {time_in_market:.0%} | buy-and-hold over sample {growth:.2f}x"
    )
    sleeve = sleeve_lookup.get(symbol)
    if sleeve is not None and sleeve.ran:
        stat_bits.append(
            f"independent single-asset sleeve: {sleeve.total_return:+.1%} total, "
            f"Sharpe {sleeve.sharpe:.2f}, max DD {sleeve.max_drawdown:.1%} (diagnostic only)"
        )
    return stat_bits


def _episode_table_page(
    pdf: PdfPages,
    title: str,
    sections: Sequence[tuple[str, str, Sequence[str], Sequence[Sequence[str]], Sequence[int]]],
    *,
    footer: str = "",
) -> None:
    """One diagnostics page: stacked sections, each a caption and a real table.

    Each section is ``(heading, explanation, columns, rows, numeric_columns)``.
    Vertical space is split by row count so a long table does not overrun a
    short one; the +4 per section covers its heading, explanation, and header
    row.
    """
    figure = plt.figure(figsize=_PAGE)
    figure.text(0.06, 0.95, title, fontsize=13, fontweight="bold", va="top")

    weights = [len(rows) + 4 for _h, _e, _c, rows, _n in sections]
    # top leaves room above the FIRST table for its heading plus a wrapped
    # explanation of up to three lines; the annotations below hang upward from
    # each axes' top edge in point offsets.
    grid = figure.add_gridspec(
        len(sections),
        1,
        left=0.06,
        right=0.94,
        top=0.82,
        bottom=0.10 if footer else 0.06,
        hspace=0.55,
        height_ratios=weights,
    )
    for position, (heading, explanation, columns, rows, numeric) in enumerate(sections):
        axis = figure.add_subplot(grid[position, 0])
        explanation_lines = textwrap.wrap(explanation, width=170)
        # Offsets are in points, not axes fractions: the sections have unequal
        # heights, so a fractional offset would space the two headings
        # differently and the taller section's heading would collide with its
        # own explanation.
        axis.annotate(
            heading,
            xy=(0.0, 1.0),
            xycoords="axes fraction",
            xytext=(0, 8 + 9 * len(explanation_lines)),
            textcoords="offset points",
            fontsize=8.5,
            fontweight="bold",
            va="bottom",
        )
        axis.annotate(
            "\n".join(explanation_lines),
            xy=(0.0, 1.0),
            xycoords="axes fraction",
            xytext=(0, 4),
            textcoords="offset points",
            fontsize=6.8,
            va="bottom",
            color="#444444",
        )
        _draw_table(
            axis,
            columns=columns,
            rows=rows,
            numeric_columns=numeric,
            font_size=6.8,
            row_scale=1.3,
        )
    if footer:
        figure.text(0.06, 0.035, footer, fontsize=6.8, color="#555555", va="top")
    pdf.savefig(figure)
    plt.close(figure)


def _episode_pages(pdf: PdfPages, insights: StrategyInsights, *, limit: int = 10) -> None:
    """The episode diagnostics, as two pages of tables. Behaviour, not advice."""
    trend_rows = [
        (
            episode.symbol,
            str(episode.entry_session),
            str(episode.exit_session),
            f"{episode.sessions_held:,}",
            f"{episode.net_pnl:,.0f}",
            f"{episode.return_pct:+.1%}",
        )
        for episode in sorted(
            insights.captured_trends, key=lambda item: item.net_pnl, reverse=True
        )[:limit]
    ]
    whipsaw_rows = [
        (
            episode.symbol,
            str(episode.entry_session),
            str(episode.exit_session),
            f"{episode.sessions_held:,}",
            f"{episode.net_pnl:,.0f}",
        )
        for episode in sorted(insights.whipsaws, key=lambda item: item.net_pnl)[:limit]
    ]
    _episode_table_page(
        pdf,
        "Episode diagnostics (1 of 2) -- behaviour, not advice",
        [
            (
                "Captured trends -- where the strategy did what it is designed to do",
                f"{len(insights.captured_trends)} round trips returned >= "
                f"{CAPTURED_TREND_RETURN:.0%} net on entry notional; the top {limit} by net "
                "P&L are shown.",
                ("symbol", "entry", "exit", "sessions", "net P&L", "return"),
                trend_rows,
                (3, 4, 5),
            ),
            (
                "Whipsaws -- the known cost of being a trend follower",
                f"{len(insights.whipsaws)} losing round trips of <= 21 sessions, total "
                f"{insights.whipsaw_total_cost:,.0f}. This is the fee for acting on trends "
                "that immediately reversed; the deadband already filters weaker ones.",
                ("symbol", "entry", "exit", "sessions", "net P&L"),
                whipsaw_rows,
                (3, 4),
            ),
        ],
    )

    giveback_rows = [
        (
            episode.symbol,
            str(episode.entry_session),
            str(episode.exit_session),
            f"{episode.peak_open_pnl:,.0f}",
            f"{episode.net_pnl:,.0f}",
            f"{episode.peak_open_pnl - episode.net_pnl:,.0f}",
        )
        for episode in sorted(
            insights.givebacks,
            key=lambda item: item.peak_open_pnl - item.net_pnl,
            reverse=True,
        )[:limit]
    ]
    rally_rows = [
        (
            rally.symbol,
            str(rally.start_session),
            str(rally.end_session),
            f"{rally.asset_return:+.1%}",
            f"{rally.mean_weight:.1%}",
        )
        for rally in insights.missed_rallies[:limit]
    ]
    rally_notes = "  ".join(
        f"{rally.symbol}: {rally.note}" for rally in insights.missed_rallies[:limit] if rally.note
    )
    footer_bits = [
        f"(holdings replay reconciled against the ledger to within "
        f"{insights.reconciliation_max_error:.2e} of equity)"
    ]
    if rally_notes:
        footer_bits.insert(
            0, "\n".join(textwrap.wrap(f"Missed-rally notes -- {rally_notes}", width=170))
        )
    _episode_table_page(
        pdf,
        "Episode diagnostics (2 of 2) -- behaviour, not advice",
        [
            (
                "Give-backs -- open profit surrendered before the exit",
                f"{len(insights.givebacks)} trades surrendered >= 50% of a peak open profit, "
                f"{insights.giveback_total_surrendered:,.0f} in total. This bounds what a "
                "different exit rule could AT BEST have kept in this sample; it is not "
                "evidence that a tighter exit would have kept it, because every exit rule "
                "that cuts give-backs also cuts the long holds that produced the captured "
                "trends. Testing one is a new strategy version.",
                ("symbol", "entry", "exit", "peak open P&L", "kept", "surrendered"),
                giveback_rows,
                (3, 4, 5),
            ),
            (
                "Missed rallies -- moves the portfolio largely sat out",
                "Instrument rallies of >= 20% within 63 sessions during which the portfolio's "
                "mean weight stayed below 2%.",
                ("symbol", "start", "end", "asset return", "mean weight"),
                rally_rows,
                (3, 4),
            ),
        ],
        footer="\n".join(footer_bits),
    )


def _individual_stats_page(
    pdf: PdfPages,
    *,
    report: PerformanceReport,
    sleeves: Sequence[SleeveReport],
    insights: StrategyInsights,
    panel: MarketPanel,
    result: RunResult,
) -> None:
    offset = _panel_offset(result, panel)
    rows = len(result.sessions)
    adjusted = panel.adjusted_closes[offset : offset + rows]
    symbols = [str(symbol) for symbol in panel.symbols]
    sleeve_lookup = {sleeve.symbol: sleeve for sleeve in sleeves}
    by_symbol = {str(asset.symbol): asset for asset in report.assets}

    table_rows: list[tuple[str, ...]] = []
    for position, symbol in enumerate(symbols):
        asset = by_symbol.get(symbol)
        sleeve = sleeve_lookup.get(symbol)
        growth = float(adjusted[-1, position] / adjusted[0, position])
        in_market = float(np.mean(insights.weights[:, position] > _HELD_WEIGHT))
        table_rows.append(
            (
                symbol,
                f"{asset.trades if asset else 0:,}",
                f"{asset.win_rate:.0%}" if asset else "-",
                f"{asset.net_pnl:,.0f}" if asset else "0",
                f"{asset.pnl_share:+.1%}" if asset else "-",
                f"{asset.average_hold_sessions:.0f}" if asset else "-",
                f"{in_market:.0%}",
                f"{growth:.2f}x",
                f"{sleeve.total_return:+.0%}" if sleeve and sleeve.ran else "-",
            )
        )

    figure = plt.figure(figsize=_PAGE)
    figure.text(
        0.06, 0.95, "Individual statistics by asset", fontsize=13, fontweight="bold", va="top"
    )
    axis = figure.add_axes((0.06, 0.16, 0.88, 0.72))
    _draw_table(
        axis,
        columns=(
            "symbol",
            "round trips",
            "win rate",
            "net P&L",
            "P&L share",
            "avg hold (sessions)",
            "time in market",
            "buy & hold",
            "sleeve return",
        ),
        rows=table_rows,
        numeric_columns=(1, 2, 3, 4, 5, 6, 7, 8),
        font_size=7.2,
        row_scale=1.35,
    )
    figure.text(
        0.06,
        0.10,
        "buy & hold = frictionless total-return growth of the instrument over the measured "
        "sample. sleeve return = the instrument run alone in its own account under the same "
        "engine (diagnostic; sleeve returns must never be summed into a portfolio).",
        fontsize=6.8,
        color="#555555",
        va="top",
        wrap=True,
    )
    pdf.savefig(figure)
    plt.close(figure)


def _advisory_page(pdf: PdfPages, advisory: str, provenance: str) -> None:
    header = (
        "LLM-GENERATED ADVISORY COMMENTARY -- NOT A MEASUREMENT\n"
        f"{provenance}\n"
        "This page is narrative interpretation produced by a language model from the\n"
        "aggregate statistics in this report. No signal, weight, fill, or accounting\n"
        "entry derives from it. Verify any claim against the measured pages before\n"
        "acting on it; treat suggestions as hypotheses that would each need a new\n"
        "strategy version and the full falsification protocol.\n"
    )
    wrapped: list[str] = []
    for paragraph in advisory.splitlines():
        if not paragraph.strip():
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(paragraph, width=118))
    # Paginate: a long commentary must not silently truncate.
    per_page = 52
    pages = [wrapped[start : start + per_page] for start in range(0, len(wrapped), per_page)]
    for number, body in enumerate(pages, start=1):
        title = "Advisory analysis (Gemini)" + (f" -- page {number}" if len(pages) > 1 else "")
        _text_page(pdf, title, [*header.splitlines(), "", *body])


def render_study_pdf(
    path: Path,
    *,
    report: PerformanceReport,
    metrics: PerformanceSummary,
    sleeves: Sequence[SleeveReport],
    result: RunResult,
    panel: MarketPanel,
    insights: StrategyInsights,
    advisory: str | None = None,
    advisory_provenance: str = "",
) -> None:
    """Write the full study report to ``path`` as a PDF.

    Raises:
        DataIntegrityError: if the run and panel disagree, or the run is too
            short to chart.
    """
    if len(result.sessions) < _MIN_CHART_SESSIONS:
        message = "a PDF report needs at least 2 recorded sessions"
        raise DataIntegrityError(message)

    sleeve_lookup = {sleeve.symbol: sleeve for sleeve in sleeves}
    path.parent.mkdir(parents=True, exist_ok=True)

    with PdfPages(path) as pdf:
        _headline_page(pdf, report, metrics)
        _comparison_page(pdf, result, panel, report)
        _cumulative_page(pdf, result, panel, report)
        _portfolio_value_page(pdf, result, report)
        for position, symbol in enumerate(panel.symbols):
            _asset_page(
                pdf,
                str(symbol),
                position,
                result=result,
                panel=panel,
                insights=insights,
                report=report,
                sleeve_lookup=sleeve_lookup,
            )
        _individual_stats_page(
            pdf, report=report, sleeves=sleeves, insights=insights, panel=panel, result=result
        )
        _episode_pages(pdf, insights)
        if advisory is not None:
            _advisory_page(pdf, advisory, advisory_provenance)

        info = pdf.infodict()
        info["Title"] = f"AMAT study report -- {report.strategy}"
        info["Subject"] = (
            f"{report.first_session}..{report.last_session}; config {report.config_sha256[:16]}"
        )
