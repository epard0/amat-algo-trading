"""Projection PDF: forward outcome distributions from a run's own returns.

Two pages, drawn from a finished
:class:`~amat.validation.projection.ProjectionStudy` without recomputing it:

1. the forward wealth fan -- percentile bands of resampled equity from the
   funding level out to the longest horizon, with the horizon marks drawn;
2. the horizon tables -- one percentile row per metric per horizon, the
   exceedance probabilities, and the study's caveat verbatim.

Sessions, not dates, on the fan's x-axis: a projection has no calendar --
pretending one (say, stamping future trading days) would dress resampled
draws as a schedule of real sessions.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.ticker import FuncFormatter

from amat.reporting.pdf import _PAGE, _draw_table, _style
from amat.validation.projection import ProjectionStudy

__all__ = ["render_projection_pdf"]

_BAND_OUTER = "#c7d8ec"
_BAND_INNER = "#8fb4dc"
_MEDIAN_COLOR = "#2f6db3"
_FUNDING_COLOR = "#555555"


def _fan_page(pdf: PdfPages, study: ProjectionStudy) -> None:
    figure, axis = plt.subplots(figsize=_PAGE)
    sessions = range(1, len(study.wealth_bands["p50"]) + 1)
    axis.fill_between(
        sessions,
        study.wealth_bands["p05"],
        study.wealth_bands["p95"],
        color=_BAND_OUTER,
        label="resampled 5th-95th percentile",
    )
    axis.fill_between(
        sessions,
        study.wealth_bands["p25"],
        study.wealth_bands["p75"],
        color=_BAND_INNER,
        label="resampled 25th-75th percentile",
    )
    axis.plot(
        sessions,
        study.wealth_bands["p50"],
        color=_MEDIAN_COLOR,
        linestyle="--",
        linewidth=1.2,
        label="resampled median",
    )
    axis.axhline(study.funding, color=_FUNDING_COLOR, linewidth=0.8, linestyle=":")
    for horizon in study.horizons:
        axis.axvline(horizon.sessions, color="#999999", linewidth=0.6, linestyle=":")
        axis.annotate(
            horizon.label,
            xy=(horizon.sessions, study.funding),
            xytext=(horizon.sessions, study.funding * 0.985),
            fontsize=7,
            ha="center",
            color="#666666",
        )
    axis.set_title(
        f"Forward wealth fan -- {study.strategy}, {study.paths} resampled paths "
        f"(mean block {study.mean_block_sessions} sessions, seed {study.seed}, "
        f"funding {study.funding:,.0f})"
    )
    axis.set_xlabel("projected session (no calendar: resampled draws, not scheduled days)")
    axis.set_ylabel("projected equity (account currency)")
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:,.0f}"))
    _style(axis)
    axis.legend(loc="upper left", fontsize=7)
    figure.text(0.06, 0.02, study.caveat, fontsize=6.5, color="#555555", wrap=True)
    pdf.savefig(figure)
    plt.close(figure)


def _horizon_rows(study: ProjectionStudy) -> list[list[str]]:
    rows: list[list[str]] = []
    for horizon in study.horizons:
        rows.extend(
            [
                [
                    horizon.label,
                    "terminal equity",
                    *(
                        f"{horizon.terminal_equity[key]:,.0f}"
                        for key in ("p05", "p25", "p50", "p75", "p95")
                    ),
                ],
                [
                    "",
                    "total return",
                    *(
                        f"{horizon.total_return[key]:+.2%}"
                        for key in ("p05", "p25", "p50", "p75", "p95")
                    ),
                ],
                [
                    "",
                    "annualized return",
                    *(
                        f"{horizon.annualized_return[key]:+.2%}"
                        for key in ("p05", "p25", "p50", "p75", "p95")
                    ),
                ],
                [
                    "",
                    "max drawdown",
                    *(
                        f"{horizon.max_drawdown[key]:+.2%}"
                        for key in ("p05", "p25", "p50", "p75", "p95")
                    ),
                ],
            ]
        )
    return rows


def _summary_page(pdf: PdfPages, study: ProjectionStudy) -> None:
    figure = plt.figure(figsize=_PAGE)
    figure.suptitle("Projection summary", x=0.06, y=0.94, ha="left", fontweight="bold")

    table_axis = figure.add_axes((0.06, 0.34, 0.88, 0.52))
    _draw_table(
        table_axis,
        columns=["horizon", "metric", "p05", "p25", "p50", "p75", "p95"],
        rows=_horizon_rows(study),
        numeric_columns=(2, 3, 4, 5, 6),
        col_widths=(0.08, 0.22, 0.14, 0.14, 0.14, 0.14, 0.14),
    )

    probability_axis = figure.add_axes((0.06, 0.16, 0.88, 0.14))
    _draw_table(
        probability_axis,
        columns=[
            "exceedance probability (within the resampled worlds)",
            *[horizon.label for horizon in study.horizons],
        ],
        rows=[
            [
                "paths ending below the starting funding",
                *(f"{h.probability_below_funding:.1%}" for h in study.horizons),
            ],
            [
                "paths with a drawdown worse than 20%",
                *(f"{h.probability_drawdown_worse_20pct:.1%}" for h in study.horizons),
            ],
        ],
        numeric_columns=tuple(range(1, len(study.horizons) + 1)),
    )

    figure.text(
        0.06,
        0.10,
        f"paths {study.paths}   seed {study.seed}   mean block "
        f"{study.mean_block_sessions} sessions   funding {study.funding:,.0f}   "
        f"source {study.source_sessions} sessions "
        f"({study.source_annualized_return:+.2%} ann, "
        f"{study.source_annualized_volatility:.2%} vol)",
        fontsize=7,
    )
    figure.text(0.06, 0.04, study.caveat, fontsize=6.5, color="#555555", wrap=True)
    pdf.savefig(figure)
    plt.close(figure)


def render_projection_pdf(path: Path, study: ProjectionStudy) -> None:
    """Write the two-page projection PDF to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(path) as pdf:
        _fan_page(pdf, study)
        _summary_page(pdf, study)
