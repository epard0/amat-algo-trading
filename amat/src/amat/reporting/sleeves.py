"""Reporting for a per-asset sleeve study.

Chart form, and why it is small multiples
-----------------------------------------
Twenty-nine equity curves cannot be twenty-nine coloured lines. The categorical
palette runs to eight slots and only its first three survive an all-pairs
colourblind check, so a 29-series overlay would be unreadable regardless of
which hues were chosen -- and the lines would occlude each other anyway. The
form that fits "one series, repeated across many subjects" is small multiples:
one panel per asset, every panel the same single hue, ordered by profit so the
ranking is readable without a legend.

The profit-and-loss ranking is a diverging bar chart, which is the right form
for a signed magnitude: blue above zero, red below, a neutral zero rule between
them. Both hues are validated against both surfaces (worst-pair CVD deltaE 23.8
light / 19.2 dark, well clear of the 8.0 target), and sign is additionally
carried by bar direction and by a signed label, so colour is never the only
channel.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from amat.backtest.attribution import ClosedTrade, TradeLedger
from amat.backtest.runner import RunResult
from amat.backtest.sleeves import ENGINE_LABELS, SleeveStudy
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.types import SessionDate, Side
from amat.errors import DataIntegrityError
from amat.metrics import PerformanceSummary
from amat.metrics import summarize as summarize_performance
from amat.reporting import (
    _HTML_STYLE,
    MAX_SYMBOL_PNL_SHARE,
    MAX_YEAR_PNL_SHARE,
    PerformanceReport,
    _annual_bars,
    _asset_bars,
    _asset_table,
    _drawdown_chart,
    _equity_chart,
    _polyline,
)

__all__ = ["SleeveReport", "build_sleeve_reports", "render_study_html", "render_study_text"]

#: Points retained per sparkline. A 2,664-session curve drawn at full
#: resolution across 29 panels puts ~77,000 coordinates into the document for no
#: visible gain; every panel is under 200 pixels wide.
_SPARK_POINTS = 140


@dataclass(frozen=True, slots=True)
class SleeveReport:
    """One symbol's independent account, summarized."""

    symbol: str
    ran: bool
    failure: str | None
    initial_equity: float
    final_equity: float
    net_pnl: float
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    #: Round trips reconstructed from the sleeve's own fills. Zero is a real
    #: measurement -- the sleeve opened nothing, or opened and never closed.
    trades: int
    #: ``None`` only when there are no closed trades, where a rate is undefined
    #: rather than zero.
    win_rate: float | None
    total_costs: float
    missed_fills: int
    equity_curve: tuple[float, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "ran": self.ran,
            "failure": self.failure,
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "net_pnl": self.net_pnl,
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "annualized_volatility": self.annualized_volatility,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "trades": self.trades,
            "win_rate": self.win_rate,
            "total_costs": self.total_costs,
            "missed_fills": self.missed_fills,
        }


def _downsample(values: Sequence[float], points: int = _SPARK_POINTS) -> tuple[float, ...]:
    """Reduce a curve to ``points`` samples, always keeping the final value.

    The last point is preserved explicitly because it is the one the reader
    compares across panels; a stride that happened to drop it would understate
    or overstate the final result by whatever the last few sessions did.
    """
    if len(values) <= points:
        return tuple(float(value) for value in values)
    indices = np.linspace(0, len(values) - 1, points).round().astype(int)
    return tuple(float(values[index]) for index in indices)


def _trade_statistics(
    result: RunResult,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> tuple[int, float | None]:
    """Round trips and win rate, reconstructed from the run's own fills.

    These columns used to read the rejected v2 engine's trade journal and print
    an em dash for v1, on the reasoning that v1 "holds continuous target weights
    and has no notion of a closed trade". That was half right: v1 keeps no
    journal, but it does emit fills, and a fill stream reconstructs into round
    trips exactly as :func:`amat.reporting.build_report` already does to compute
    the concentration gates. The statistic was always available; nothing was
    reading it.

    Sells are applied before buys within a session so a same-session flip closes
    the old trade before opening the new one instead of averaging the two.
    """
    ledger = TradeLedger()
    closed: list[ClosedTrade] = []
    actions = actions_by_session or {}
    for record in result.sessions:
        # Before the session's fills, as the accountant does. A fill stream does
        # not carry the split, so without this a position open across a 2:1
        # sells twice what the replay believes it holds.
        for action in actions.get(record.session, ()):
            if action.kind == ActionKind.SPLIT:
                ledger.apply_split(action.symbol, action.ratio)
        for fill in sorted(record.fills, key=lambda item: (item.side == Side.BUY, item.symbol)):
            done = ledger.apply(fill)
            if done is not None:
                closed.append(done)
    if not closed:
        return 0, None
    wins = sum(1 for trade in closed if trade.is_win)
    return len(closed), wins / len(closed)


def build_sleeve_reports(
    study: SleeveStudy,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> tuple[SleeveReport, ...]:
    """Summarize every sleeve, including the ones that failed.

    A failed sleeve is retained with its reason rather than filtered out. A table
    of 26 rows where 29 symbols were requested silently misreports coverage;
    a table of 29 rows where 3 say "did not run" does not.
    """
    reports: list[SleeveReport] = []
    for outcome in study.sleeves:
        if outcome.result is None:
            reports.append(
                SleeveReport(
                    symbol=outcome.symbol,
                    ran=False,
                    failure=outcome.failure,
                    initial_equity=float(study.sleeve_equity),
                    final_equity=float(study.sleeve_equity),
                    net_pnl=0.0,
                    total_return=0.0,
                    annualized_return=0.0,
                    annualized_volatility=0.0,
                    sharpe=0.0,
                    max_drawdown=0.0,
                    trades=0,
                    win_rate=None,
                    total_costs=0.0,
                    missed_fills=0,
                    equity_curve=(),
                )
            )
            continue

        result = outcome.result
        metrics = summarize_performance(result)
        trade_count, win_rate = _trade_statistics(result, actions_by_session)
        reports.append(
            SleeveReport(
                symbol=outcome.symbol,
                ran=True,
                failure=None,
                initial_equity=float(result.initial_equity),
                final_equity=float(result.final_equity),
                net_pnl=float(outcome.net_pnl),
                total_return=outcome.total_return,
                annualized_return=metrics.annualized_return,
                annualized_volatility=metrics.annualized_volatility,
                sharpe=metrics.sharpe,
                max_drawdown=metrics.max_drawdown,
                trades=trade_count,
                win_rate=win_rate,
                total_costs=float(result.total_costs),
                missed_fills=result.total_missed,
                equity_curve=_downsample([float(value) for value in result.equity_curve]),
            )
        )
    return tuple(sorted(reports, key=lambda report: report.net_pnl, reverse=True))


# -- rendering ---------------------------------------------------------------

#: Printed wherever a runner does not possess a statistic, so that "absent" is
#: visually distinct from "measured as zero".
_ABSENT = "—"


def _count(value: int | None) -> str:
    return _ABSENT if value is None else f"{value}"


def _rate(value: float | None) -> str:
    return _ABSENT if value is None else f"{value:.1%}"


def _engine_description(engine: str) -> str:
    """Spell out which runner produced a table, for readers who did not run it."""
    return ENGINE_LABELS.get(engine, engine)


def _sleeve_pnl_bars(reports: Sequence[SleeveReport]) -> str:
    """Diverging bars of net profit per sleeve, sorted best to worst."""
    ran = [report for report in reports if report.ran]
    if not ran:
        return '<p style="color:var(--muted)">No sleeve produced a result.</p>'

    scale = max(abs(report.net_pnl) for report in ran) or 1.0
    row_h, label_w, bar_w = 24, 52, 880
    height = row_h * len(ran)
    mid = label_w + bar_w / 2
    rows: list[str] = []
    for index, report in enumerate(ran):
        y = index * row_h + 4
        magnitude = abs(report.net_pnl) / scale * (bar_w / 2)
        positive = report.net_pnl >= 0
        x = mid if positive else mid - magnitude
        colour = "--pos" if positive else "--neg"
        anchor = "start" if positive else "end"
        text_x = mid + magnitude + 8 if positive else mid - magnitude - 8
        rows.append(
            f'<text x="0" y="{y + 12}" fill="var(--text-secondary)" font-size="12">'
            f"{report.symbol}</text>"
            f'<rect x="{x:.1f}" y="{y}" width="{magnitude:.1f}" height="14" rx="4" '
            f'fill="var({colour})"/>'
            f'<text x="{text_x:.1f}" y="{y + 12}" fill="var(--text-secondary)" '
            f'font-size="11" text-anchor="{anchor}">'
            f"{report.net_pnl:+,.0f} ({report.total_return:+.1%})</text>"
        )
    zero_rule = (
        f'<line x1="{mid}" y1="0" x2="{mid}" y2="{height}" stroke="var(--axis)" stroke-width="1"/>'
    )
    return (
        f'<svg viewBox="0 0 {label_w + bar_w} {height}" role="img" '
        f'aria-label="Net profit and loss by sleeve, sorted best to worst">'
        f"{zero_rule}{''.join(rows)}</svg>"
    )


def _sleeve_small_multiples(reports: Sequence[SleeveReport]) -> str:
    """One equity panel per sleeve, same hue, ordered by profit."""
    ran = [report for report in reports if report.ran and len(report.equity_curve) > 1]
    if not ran:
        return '<p style="color:var(--muted)">No sleeve produced an equity curve.</p>'

    panels: list[str] = []
    for report in ran:
        values = list(report.equity_curve)
        base = values[0] or 1.0
        indexed = [value / base * 100 for value in values]
        low, high = min([*indexed, 100.0]), max([*indexed, 100.0])
        pad = (high - low) * 0.10 or 1.0
        low, high = low - pad, high + pad
        points = _polyline(indexed, 200, 64, low=low, high=high)
        # The starting level is drawn so a panel that lost money is legible as
        # "below its own start", not merely as a line with an unknown baseline.
        start_y = 64 - (100.0 - low) / ((high - low) or 1.0) * 64
        tone = "--pos" if report.net_pnl >= 0 else "--neg"
        panels.append(
            f'<figure class="spark">'
            f"<figcaption><b>{report.symbol}</b>"
            f'<span class="{"up" if report.net_pnl >= 0 else "down"}">'
            f"{report.total_return:+.1%}</span></figcaption>"
            f'<svg viewBox="0 0 200 64" role="img" '
            f'aria-label="{report.symbol} equity indexed to 100, '
            f'total return {report.total_return:.1%}">'
            f'<line x1="0" y1="{start_y:.1f}" x2="200" y2="{start_y:.1f}" '
            f'stroke="var(--grid)" stroke-width="1" stroke-dasharray="3 3"/>'
            f'<polyline points="{points}" fill="none" stroke="var({tone})" '
            f'stroke-width="1.6" stroke-linejoin="round"/></svg>'
            f"</figure>"
        )
    return f'<div class="sparks">{"".join(panels)}</div>'


def _sleeve_table(reports: Sequence[SleeveReport]) -> str:
    header = (
        "<tr><th>Symbol</th><th>End equity</th><th>Net P&amp;L</th><th>Total</th>"
        "<th>Ann.</th><th>Vol</th><th>Sharpe</th><th>Max DD</th>"
        "<th>Trades</th><th>Win</th><th>Costs</th><th>Missed</th></tr>"
    )
    rows: list[str] = []
    for report in reports:
        if not report.ran:
            rows.append(
                f"<tr><td>{report.symbol}</td>"
                f'<td colspan="11" style="color:var(--critical)">did not run — '
                f"{report.failure or 'unknown reason'}</td></tr>"
            )
            continue
        tone = "up" if report.net_pnl >= 0 else "down"
        rows.append(
            f"<tr><td>{report.symbol}</td>"
            f"<td>{report.final_equity:,.0f}</td>"
            f'<td class="{tone}">{report.net_pnl:+,.0f}</td>'
            f'<td class="{tone}">{report.total_return:+.2%}</td>'
            f"<td>{report.annualized_return:.2%}</td>"
            f"<td>{report.annualized_volatility:.2%}</td>"
            f"<td>{report.sharpe:.2f}</td>"
            f"<td>{report.max_drawdown:.2%}</td>"
            f"<td>{_count(report.trades)}</td>"
            f"<td>{_rate(report.win_rate)}</td>"
            f"<td>{report.total_costs:,.0f}</td>"
            f"<td>{report.missed_fills}</td></tr>"
        )
    return f"<table>{header}{''.join(rows)}</table>"


_SLEEVE_STYLE = """
.sparks { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 10px; }
.spark { margin: 0; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px 4px; }
.spark figcaption { display: flex; justify-content: space-between; align-items: baseline;
  font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 2px;
  font-variant-numeric: tabular-nums; }
.spark svg { width: 100%; height: auto; display: block; }
.up { color: var(--pos); }
.down { color: var(--neg); }
.scroll { overflow-x: auto; }
"""


def render_study_text(
    combined: PerformanceReport,
    metrics: PerformanceSummary,
    sleeves: Sequence[SleeveReport],
    *,
    sleeve_equity: float,
) -> str:
    """Plain-text rendering of the combined run and every sleeve."""
    lines: list[str] = []
    lines.append("=" * 92)
    lines.append(f"AMAT PERFORMANCE REPORT — {combined.strategy}")
    lines.append(f"ENGINE: {combined.engine} — {_engine_description(combined.engine)}")
    lines.append("=" * 92)
    for caveat in combined.caveats:
        lines.append(f"  ! {caveat}")
    lines.append("")
    lines.append(
        f"  sample     {combined.first_session} .. {combined.last_session} "
        f"({combined.sessions} sessions)"
    )
    lines.append(f"  engine     {combined.engine}")
    lines.append(f"  data       {combined.data_source}")
    lines.append(f"  config     {combined.config_sha256[:16]}")
    lines.append(f"  costs      {combined.one_way_bps:.1f} bps one-way")
    lines.append("")
    lines.append("COMBINED PORTFOLIO")
    lines.append(
        f"  {combined.initial_equity:,.0f} -> {combined.final_equity:,.0f} "
        f"({combined.total_return:+.2%})"
    )
    lines.append(
        f"  ann {metrics.annualized_return:+.2%}  vol {metrics.annualized_volatility:.2%}  "
        f"sharpe {metrics.sharpe:.2f}  maxdd {combined.max_drawdown:.2%}"
    )
    gates = combined.concentration
    lines.append(
        f"  gate symbol {'PASS' if gates.symbol_gate_passed else 'FAIL'} "
        f"({gates.best_symbol} {gates.best_symbol_share:.1%} of "
        f"{MAX_SYMBOL_PNL_SHARE:.0%})   "
        f"year {'PASS' if gates.year_gate_passed else 'FAIL'} "
        f"({gates.best_year} {gates.best_year_share:.1%} of {MAX_YEAR_PNL_SHARE:.0%})"
    )
    lines.append("")
    lines.append(f"PER-ASSET SLEEVES — each funded independently with {sleeve_equity:,.0f}")
    lines.append(
        f"  {'SYM':<6}{'END EQUITY':>13}{'NET P&L':>13}{'TOTAL':>10}"
        f"{'ANN':>9}{'SHARPE':>8}{'MAXDD':>9}{'TRADES':>8}{'WIN':>7}"
    )
    for report in sleeves:
        if not report.ran:
            lines.append(f"  {report.symbol:<6}  did not run — {report.failure}")
            continue
        lines.append(
            f"  {report.symbol:<6}{report.final_equity:>13,.0f}{report.net_pnl:>+13,.0f}"
            f"{report.total_return:>+10.2%}{report.annualized_return:>+9.2%}"
            f"{report.sharpe:>8.2f}{report.max_drawdown:>9.2%}"
            f"{_count(report.trades):>8}{_rate(report.win_rate):>7}"
        )
    ran = [report for report in sleeves if report.ran]
    if ran:
        winners = sum(1 for report in ran if report.net_pnl > 0)
        lines.append("")
        lines.append(
            f"  {winners}/{len(ran)} sleeves profitable; "
            f"median total return {float(np.median([r.total_return for r in ran])):+.2%}"
        )
        lines.append(
            "  Sleeve results are single-instrument diagnostics. They are not additive "
            "and must not be read as portfolio performance."
        )
    lines.append("=" * 92)
    return "\n".join(lines)


def render_study_html(
    combined: PerformanceReport,
    metrics: PerformanceSummary,
    sleeves: Sequence[SleeveReport],
    *,
    sleeve_equity: float,
) -> str:
    """Render the full study as one self-contained HTML page.

    Raises:
        DataIntegrityError: if no sleeve reports were supplied.
    """
    if not sleeves:
        message = "render_study_html requires at least one sleeve report"
        raise DataIntegrityError(message)

    pair = [(combined, metrics)]
    caveats = "".join(f'<div class="caveat">{item}</div>' for item in combined.caveats)
    gates = combined.concentration
    ran = [report for report in sleeves if report.ran]
    winners = sum(1 for report in ran if report.net_pnl > 0)
    median_return = float(np.median([report.total_return for report in ran])) if ran else 0.0

    tiles = (
        f'<div class="tiles">'
        f'<div class="tile"><div class="label">Start</div>'
        f'<div class="value">{combined.initial_equity:,.0f}</div></div>'
        f'<div class="tile"><div class="label">End</div>'
        f'<div class="value">{combined.final_equity:,.0f}</div></div>'
        f'<div class="tile"><div class="label">Total return</div>'
        f'<div class="value">{combined.total_return:+.2%}</div></div>'
        f'<div class="tile"><div class="label">Ann. return</div>'
        f'<div class="value">{metrics.annualized_return:+.2%}</div></div>'
        f'<div class="tile"><div class="label">Ann. vol</div>'
        f'<div class="value">{metrics.annualized_volatility:.2%}</div></div>'
        f'<div class="tile"><div class="label">Sharpe</div>'
        f'<div class="value">{metrics.sharpe:.2f}</div></div>'
        f'<div class="tile"><div class="label">Max drawdown</div>'
        f'<div class="value">{combined.max_drawdown:.2%}</div></div>'
        f'<div class="tile"><div class="label">Costs paid</div>'
        f'<div class="value">{combined.total_costs:,.0f}</div></div>'
        f"</div>"
    )

    sleeve_tiles = (
        f'<div class="tiles">'
        f'<div class="tile"><div class="label">Sleeves run</div>'
        f'<div class="value">{len(ran)}/{len(sleeves)}</div></div>'
        f'<div class="tile"><div class="label">Profitable</div>'
        f'<div class="value">{winners}/{len(ran) if ran else 0}</div></div>'
        f'<div class="tile"><div class="label">Median total return</div>'
        f'<div class="value">{median_return:+.2%}</div></div>'
        f'<div class="tile"><div class="label">Funding per sleeve</div>'
        f'<div class="value">{sleeve_equity:,.0f}</div></div>'
        f"</div>"
    )

    return (
        f"<title>AMAT performance report — {combined.strategy} ({combined.engine})</title>"
        f"<style>{_HTML_STYLE}{_SLEEVE_STYLE}</style>"
        f'<div class="viz-root">'
        f"<h1>AMAT performance report — {combined.engine}</h1>"
        f'<p style="color:var(--text-secondary);margin:0 0 14px">'
        f"{combined.strategy} · <b>{combined.engine}</b>: "
        f"{_engine_description(combined.engine)} · config "
        f"{combined.config_sha256[:16]} · {combined.first_session} .. "
        f"{combined.last_session} ({combined.sessions} sessions) · "
        f"{combined.one_way_bps:.1f} bps one-way · {combined.data_source}</p>"
        f"{caveats}"
        f"<h2>Combined portfolio</h2>"
        f'<div class="card">{tiles}</div>'
        f'<div class="card">{_equity_chart(pair)}</div>'
        f"<h2>Drawdown</h2>"
        f'<div class="card">{_drawdown_chart(combined)}</div>'
        f"<h2>Concentration gates</h2>"
        f'<div class="gate {"pass" if gates.symbol_gate_passed else "fail"}">'
        f"{'PASS' if gates.symbol_gate_passed else 'FAIL'} — best symbol "
        f"{gates.best_symbol} contributes {gates.best_symbol_share:.1%} of gross "
        f"positive P&amp;L (limit {MAX_SYMBOL_PNL_SHARE:.0%})</div>"
        f'<div class="gate {"pass" if gates.year_gate_passed else "fail"}">'
        f"{'PASS' if gates.year_gate_passed else 'FAIL'} — best year "
        f"{gates.best_year} contributes {gates.best_year_share:.1%} of gross "
        f"positive P&amp;L (limit {MAX_YEAR_PNL_SHARE:.0%})</div>"
        f"<h2>Portfolio net P&amp;L by asset</h2>"
        f'<div class="card scroll">{_asset_bars(combined)}</div>'
        f"<h2>Portfolio by calendar year</h2>"
        f'<div class="card scroll">{_annual_bars(combined)}</div>'
        f"<h2>Portfolio per-asset detail</h2>"
        f'<div class="card scroll">{_asset_table(combined)}</div>'
        f"<h2>Per-asset sleeves</h2>"
        f'<div class="caveat">Each sleeve is an independent single-instrument account '
        f"funded with {sleeve_equity:,.0f}. Sleeve results are diagnostics: they are not "
        f"additive, they carry none of the portfolio's diversification, and they must not "
        f"be presented as portfolio performance.</div>"
        f'<div class="card">{sleeve_tiles}</div>'
        f'<div class="card">{_sleeve_small_multiples(sleeves)}</div>'
        f"<h2>Sleeve net P&amp;L</h2>"
        f'<div class="card scroll">{_sleeve_pnl_bars(sleeves)}</div>'
        f"<h2>Sleeve detail</h2>"
        f'<div class="card scroll">{_sleeve_table(sleeves)}</div>'
        f"</div>"
    )
