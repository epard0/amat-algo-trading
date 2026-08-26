"""Monte Carlo study PDF: the observed run inside its bootstrap distribution.

Three pages, all drawn from a finished
:class:`~amat.validation.montecarlo.MonteCarloStudy` without recomputing it:

1. the equity fan -- percentile bands of the resampled equity paths with the
   observed curve overlaid;
2. metric distributions -- histograms of total return, Sharpe, and max
   drawdown across paths, each with the observed value marked;
3. the summary tables -- percentile table per metric, exceedance
   probabilities, and the study's own caveat.

The fan chart's bands share one funding and one calendar with the observed
run, so the single overlaid line is comparable by construction.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.ticker import FuncFormatter

from amat.reporting.pdf import _PAGE, _STRATEGY_COLOR, _date_axis, _draw_table, _style
from amat.validation.montecarlo import MonteCarloStudy

__all__ = ["render_montecarlo_pdf"]

_BAND_OUTER = "#c7d8ec"
_BAND_INNER = "#8fb4dc"
_MEDIAN_COLOR = "#2f6db3"
_OBSERVED_HIST_COLOR = "#b23b3b"

#: Display metadata per metric: title and value formatter.
_METRIC_PRESENTATION: tuple[tuple[str, str, str], ...] = (
    ("total_return", "total return", "percent"),
    ("annualized_return", "annualized return", "percent"),
    ("annualized_volatility", "annualized volatility", "percent"),
    ("sharpe", "Sharpe", "ratio"),
    ("max_drawdown", "max drawdown", "percent"),
)


def _format_value(value: float, kind: str) -> str:
    return f"{value:+.2%}" if kind == "percent" else f"{value:.2f}"


def _fan_page(pdf: PdfPages, study: MonteCarloStudy) -> None:
    figure, axis = plt.subplots(figsize=_PAGE)
    dates = list(study.band_dates)
    axis.fill_between(
        dates,
        study.equity_bands["p05"],
        study.equity_bands["p95"],
        color=_BAND_OUTER,
        linewidth=0,
        label="resampled 5th-95th percentile",
    )
    axis.fill_between(
        dates,
        study.equity_bands["p25"],
        study.equity_bands["p75"],
        color=_BAND_INNER,
        linewidth=0,
        label="resampled 25th-75th percentile",
    )
    axis.plot(
        dates,
        study.equity_bands["p50"],
        color=_MEDIAN_COLOR,
        linewidth=1.2,
        linestyle="--",
        label="resampled median",
    )
    axis.plot(
        dates,
        study.observed_equity,
        color=_STRATEGY_COLOR,
        linewidth=2.0,
        label="observed history",
    )
    axis.axhline(study.initial_cash, color="#666666", linewidth=0.8, linestyle=":")
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:,.0f}"))
    axis.set_ylabel("portfolio value (account currency)", fontsize=9)
    axis.set_title(
        f"Equity fan -- {study.strategy}, {study.paths} stationary-bootstrap paths "
        f"(mean block {study.mean_block_sessions} sessions, base seed {study.seed})",
        fontsize=10,
    )
    axis.legend(loc="upper left", fontsize=7, frameon=False)
    _style(axis)
    _date_axis(axis)
    figure.text(
        0.06,
        0.015,
        "Bands are per-session percentiles across resampled paths, not any single path's "
        "trajectory. " + study.caveat,
        fontsize=6.5,
        color="#555555",
        wrap=True,
    )
    pdf.savefig(figure)
    plt.close(figure)


def _histogram_page(pdf: PdfPages, study: MonteCarloStudy) -> None:
    figure, axes = plt.subplots(1, 3, figsize=_PAGE)
    figure.suptitle(
        f"Metric distributions across {study.paths} resampled paths -- observed value marked",
        fontsize=10,
    )
    shown = [entry for entry in _METRIC_PRESENTATION if entry[0] in ("total_return", "sharpe")]
    shown.append(("max_drawdown", "max drawdown", "percent"))
    for axis, (metric, title, kind) in zip(axes, shown, strict=True):
        values = [getattr(outcome, metric) for outcome in study.outcomes]
        observed = getattr(study.observed, metric)
        axis.hist(values, bins=25, color=_BAND_INNER, edgecolor="white", linewidth=0.4)
        axis.axvline(observed, color=_OBSERVED_HIST_COLOR, linewidth=1.6)
        rank = study.observed_percentile[metric]
        # Max drawdown is negative, so a HIGH percentile means SHALLOWER than
        # most paths; ">= N% of paths" would read exactly backwards there.
        note = (
            f"shallower than {rank:.0%} of paths"
            if metric == "max_drawdown"
            else f">= {rank:.0%} of paths"
        )
        axis.set_title(
            f"{title}\nobserved {_format_value(observed, kind)} ({note})",
            fontsize=8.5,
        )
        axis.set_ylabel("paths", fontsize=8)
        _style(axis)
    figure.subplots_adjust(left=0.06, right=0.96, bottom=0.10, top=0.82, wspace=0.28)
    pdf.savefig(figure)
    plt.close(figure)


def _summary_page(pdf: PdfPages, study: MonteCarloStudy) -> None:
    figure = plt.figure(figsize=_PAGE)
    figure.text(0.06, 0.95, "Monte Carlo summary", fontsize=13, fontweight="bold", va="top")
    grid = figure.add_gridspec(
        2,
        1,
        left=0.06,
        right=0.94,
        top=0.86,
        bottom=0.30,
        hspace=0.45,
        height_ratios=(len(_METRIC_PRESENTATION) + 1, 3),
    )

    percentile_rows = []
    for metric, title, kind in _METRIC_PRESENTATION:
        row = study.metric_percentiles[metric]
        percentile_rows.append(
            (
                title,
                _format_value(row["p05"], kind),
                _format_value(row["p25"], kind),
                _format_value(row["p50"], kind),
                _format_value(row["p75"], kind),
                _format_value(row["p95"], kind),
                _format_value(getattr(study.observed, metric), kind),
                f"{study.observed_percentile[metric]:.0%}",
            )
        )
    table_axis = figure.add_subplot(grid[0, 0])
    _draw_table(
        table_axis,
        columns=("metric", "p05", "p25", "p50", "p75", "p95", "observed", "obs percentile"),
        rows=percentile_rows,
        numeric_columns=(1, 2, 3, 4, 5, 6, 7),
        font_size=7.5,
    )

    probability_rows = [
        (
            "resampled paths ending below initial funding",
            f"{study.probabilities['negative_total_return']:.1%}",
        ),
        (
            "resampled paths with a drawdown worse than 20%",
            f"{study.probabilities['max_drawdown_worse_than_20pct']:.1%}",
        ),
    ]
    probability_axis = figure.add_subplot(grid[1, 0])
    _draw_table(
        probability_axis,
        columns=("exceedance probability (within the resampled worlds)", "value"),
        rows=probability_rows,
        numeric_columns=(1,),
        col_widths=(0.7, 0.3),
        font_size=7.5,
    )

    figure.text(
        0.06,
        0.22,
        "\n".join(
            (
                f"paths {study.paths}   base seed {study.seed}   mean block "
                f"{study.mean_block_sessions} sessions   funding {study.initial_cash:,.0f}",
                "",
                study.caveat,
            )
        ),
        fontsize=7.2,
        va="top",
        color="#333333",
        wrap=True,
    )
    pdf.savefig(figure)
    plt.close(figure)


def render_montecarlo_pdf(path: Path, study: MonteCarloStudy) -> None:
    """Write the three-page Monte Carlo report to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(path) as pdf:
        _fan_page(pdf, study)
        _histogram_page(pdf, study)
        _summary_page(pdf, study)
        info = pdf.infodict()
        info["Title"] = f"AMAT Monte Carlo study -- {study.strategy}"
        info["Subject"] = (
            f"{study.paths} stationary-bootstrap paths, base seed {study.seed}, "
            f"mean block {study.mean_block_sessions} sessions"
        )
