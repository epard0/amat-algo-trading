"""The PDF performance report: what the ledger says, laid out to be read.

This module is an observer. It opens the ledger read-only through
``monitoring.sources`` and computes nothing it can borrow from
``monitoring.performance_tracker``, so the numbers a customer sees on paper are
the same numbers the dashboard shows and there is no second implementation to
drift. It never touches the network, never reads the decision log - 188 MB of
it - and has no order path.

Three windows are reported because they answer different questions. All-time is
the only one with enough settled trades to mean anything statistically; today
is what an operator checks before bed; the last hour is what they check when
something looks wrong. A window with no trades in it says so in a sentence
rather than printing a table of zeroes, because a zero win rate over no trades
is not a fact about the model.

Live balances are deliberately absent. Real cash lives at Kalshi behind an
authenticated call, and a report that quietly showed the last known figure
would be wrong in exactly the situation - a bot that stopped - where it matters
most. Paper balances are in the ledger and are shown.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from monitoring.performance_tracker import (
    Summary,
    brier_score,
    current_streak,
    edge_realisation,
    equity_curve,
    group_summaries,
    longest_losing_streak,
    max_drawdown,
    summarize,
)
from monitoring.sources import LedgerTrade, read_trades
from reporting import pdf, theme

ZERO = Decimal("0")

REPORT_PREFIX = "kx15-report-"
# The counter, then the generation date. The spec writes that date MM/DD/YY;
# a slash is a path separator on every platform this runs on, so it is written
# with hyphens and the meaning is unchanged.
_COUNTER_RE = re.compile(rf"^{re.escape(REPORT_PREFIX)}(\d+)-")

WINDOW_KEYS = ("all", "day", "hour")
WINDOW_TITLES = {
    "all": "All-time performance",
    "day": "Today",
    "hour": "The last hour",
}
WINDOW_BLURBS = {
    "all": "Every trade the ledger holds for this book.",
    "day": "Trades settled since 00:00 UTC today.",
    "hour": "Trades settled in the sixty minutes to now.",
}


# ------------------------------------------------------------- file naming --


def next_report_number(directory: Path) -> int:
    """One past the highest counter already on disk.

    Derived from the directory rather than a state file: a state file can be
    deleted, restored from a backup, or disagree with the folder it names, and
    the folder is the thing a person actually looks at.
    """
    highest = 0
    directory = Path(directory)
    if directory.exists():
        for entry in directory.glob(f"{REPORT_PREFIX}*"):
            match = _COUNTER_RE.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def format_counter(number: int) -> str:
    """Zero-padded to four digits, widening on its own past 9999."""
    return f"{number:04d}"


def report_filename(number: int, when: date) -> str:
    return f"{REPORT_PREFIX}{format_counter(number)}-{when:%m-%d-%y}.pdf"


# ----------------------------------------------------------------- windows --


def _stamp(trade: LedgerTrade) -> datetime | None:
    """When this trade counts as having happened.

    Settlement, not placement: P&L moves the balance when the market resolves,
    and a position opened at 23:58 that settles at 00:03 belongs to the day it
    paid out. ``equity_curve`` orders on the same principle.
    """
    return trade.settled_at or trade.market_close_time or trade.placed_at


def window_start(key: str, now: datetime) -> datetime | None:
    if key == "all":
        return None
    if key == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "hour":
        return now - timedelta(hours=1)
    raise ValueError(f"unknown window: {key}")


def slice_window(
    trades: Sequence[LedgerTrade], start: datetime | None
) -> list[LedgerTrade]:
    if start is None:
        return list(trades)
    kept = []
    for trade in trades:
        stamp = _stamp(trade)
        if stamp is not None and stamp >= start:
            kept.append(trade)
    return kept


# --------------------------------------------------------------- portfolio --


def read_paper_starting_balance(ledger_path: Path) -> Decimal | None:
    """The seed balance the ledger was created with, or None.

    Opened ``mode=ro`` for the same reason everything else in the read path is:
    a reporting bug must not be able to reach the canonical record of real
    money. ``monitoring.sources`` has no reader for this table, and adding one
    there would put a report-shaped concern in the dashboard's module.
    """
    path = Path(ledger_path)
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(
            "SELECT value FROM ledger_metadata WHERE key = 'paper_starting_balance'"
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    try:
        return Decimal(str(row[0]))
    except ArithmeticError:
        return None


@dataclass(frozen=True)
class Portfolio:
    """Capital state for one book, as far as the ledger can honestly say."""

    mode: str
    environment: str | None
    summary: Summary
    starting_balance: Decimal | None
    balance: Decimal | None
    available: Decimal | None
    exposure: Decimal
    drawdown: Decimal
    best: LedgerTrade | None
    worst: LedgerTrade | None
    streak: tuple[str, int]
    longest_losing: int


def build_portfolio(
    trades: Sequence[LedgerTrade],
    *,
    mode: str,
    environment: str | None,
    ledger_path: Path,
) -> Portfolio:
    summary = summarize(trades)
    exposure = sum(
        (t.total_debit or ZERO for t in trades if t.status == "OPEN"), ZERO
    )
    starting = read_paper_starting_balance(ledger_path) if mode == "paper" else None
    balance = starting + summary.net_pnl if starting is not None else None
    available = balance - exposure if balance is not None else None
    settled = [t for t in trades if t.is_settled and t.pnl is not None]
    return Portfolio(
        mode=mode,
        environment=environment,
        summary=summary,
        starting_balance=starting,
        balance=balance,
        available=available,
        exposure=exposure,
        drawdown=max_drawdown(equity_curve(trades)),
        best=max(settled, key=lambda t: t.pnl or ZERO, default=None),
        worst=min(settled, key=lambda t: t.pnl or ZERO, default=None),
        streak=current_streak(trades),
        longest_losing=longest_losing_streak(trades),
    )


# ------------------------------------------------------------------ drawing --

_HEADER_RULE_Y = 68.0
_FOOTER_RULE_Y = theme.PAGE_HEIGHT - 46.0
_LOGO_SCALE = 150.0 / pdf.LOGO_VIEWBOX[0]


def _table_style(size: float = theme.SIZE_TABLE) -> pdf.TableStyle:
    return pdf.TableStyle(
        body_font=theme.FONT_BODY,
        body_bold_font=theme.FONT_BOLD,
        mono_font=theme.FONT_MONO,
        mono_bold_font=theme.FONT_MONO_BOLD,
        body_size=size,
        body_colour=theme.BODY,
        header_font=theme.FONT_BOLD,
        header_size=theme.SIZE_LABEL,
        header_colour=theme.MUTED,
        header_tracking=theme.LABEL_TRACKING,
        header_rule_colour=theme.BORDER_STRONG,
        rule_colour=theme.HAIRLINE,
        rule_width=theme.BORDER_WIDTH,
        pad_v=theme.ROW_PAD_V,
        pad_h=theme.CELL_PAD_H,
    )


def _section(canvas: pdf.PdfCanvas, title: str, blurb: str | None = None) -> None:
    """A heading and its one-line explanation, never split from what follows."""
    canvas.y += theme.SECTION_GAP
    # A heading stranded at the foot of a page is worse than a short page, so
    # enough of the block to be worth turning to has to fit as well.
    canvas.ensure_space(96.0)
    canvas.text(
        theme.MARGIN,
        canvas.y + theme.SIZE_SECTION,
        title,
        font=theme.FONT_BOLD,
        size=theme.SIZE_SECTION,
        colour=theme.HEADING,
    )
    canvas.y += theme.SIZE_SECTION + 5.0
    if blurb:
        canvas.paragraph(
            theme.MARGIN,
            blurb,
            theme.CONTENT_WIDTH,
            font=theme.FONT_BODY,
            size=theme.SIZE_BODY,
            colour=theme.MUTED,
            leading=theme.LEADING,
        )
        canvas.y += 4.0


def _pill(
    canvas: pdf.PdfCanvas,
    x: float,
    y: float,
    text: str,
    *,
    background: str,
    border: str,
    ink: str,
) -> float:
    size = theme.SIZE_LABEL
    width = canvas.text_width(text, theme.FONT_BOLD, size, char_spacing=theme.LABEL_TRACKING)
    box = width + 16.0
    canvas.rect(x, y, box, 17.0, fill=background, stroke=border, radius=8.5)
    canvas.text(
        x + 8.0,
        y + 11.8,
        text,
        font=theme.FONT_BOLD,
        size=size,
        colour=ink,
        char_spacing=theme.LABEL_TRACKING,
    )
    return box


def _tile(
    canvas: pdf.PdfCanvas,
    x: float,
    y: float,
    label: str,
    value: str,
    *,
    colour: str,
) -> None:
    canvas.text(
        x,
        y + theme.SIZE_LABEL,
        label.upper(),
        font=theme.FONT_BOLD,
        size=theme.SIZE_LABEL,
        colour=theme.MUTED,
        char_spacing=theme.LABEL_TRACKING,
    )
    canvas.text(
        x,
        y + theme.SIZE_LABEL + 15.0,
        value,
        font=theme.FONT_MONO_BOLD,
        size=12.0,
        colour=colour,
    )


def _draw_portfolio_card(canvas: pdf.PdfCanvas, portfolio: Portfolio) -> None:
    summary = portfolio.summary
    unavailable = theme.EM_DASH
    tiles: list[tuple[str, str, str]] = [
        (
            "Balance",
            theme.money(portfolio.balance) if portfolio.balance is not None else unavailable,
            theme.BODY,
        ),
        (
            "Available funds",
            theme.money(portfolio.available) if portfolio.available is not None else unavailable,
            theme.BODY,
        ),
        ("Exposure", theme.money(portfolio.exposure), theme.BODY),
        ("Total trades", theme.number(Decimal(summary.trades)), theme.BODY),
        (
            "Win rate",
            theme.percent(summary.win_rate) if summary.wins + summary.losses else unavailable,
            theme.BODY,
        ),
        ("Net P&L", theme.money(summary.net_pnl, signed=True), theme.pnl_colour(summary.net_pnl)),
        (
            "ROI on stake",
            theme.percent(summary.roi, digits=2, signed=True) if summary.total_staked else unavailable,
            theme.pnl_colour(summary.roi),
        ),
        ("Max drawdown", theme.money(portfolio.drawdown), theme.pnl_colour(portfolio.drawdown)),
        (
            "Avg per trade",
            theme.money(summary.average_pnl, signed=True) if summary.settled else unavailable,
            theme.pnl_colour(summary.average_pnl if summary.settled else None),
        ),
        (
            "Best trade",
            theme.money(portfolio.best.pnl, signed=True) if portfolio.best else unavailable,
            theme.pnl_colour(portfolio.best.pnl if portfolio.best else None),
        ),
        (
            "Worst trade",
            theme.money(portfolio.worst.pnl, signed=True) if portfolio.worst else unavailable,
            theme.pnl_colour(portfolio.worst.pnl if portfolio.worst else None),
        ),
        ("Fees paid", theme.money(summary.fees_paid), theme.BODY),
    ]

    columns = 4
    rows = (len(tiles) + columns - 1) // columns
    tile_height = 40.0
    card_height = theme.CARD_PAD * 2 + rows * tile_height - 6.0
    canvas.ensure_space(card_height)
    top = canvas.y
    canvas.rect(
        theme.MARGIN,
        top,
        theme.CONTENT_WIDTH,
        card_height,
        fill=theme.SURFACE,
        stroke=theme.CARD_BORDER,
        radius=theme.CARD_RADIUS,
    )
    inner = theme.CONTENT_WIDTH - theme.CARD_PAD * 2
    step = inner / columns
    for index, (label, value, colour) in enumerate(tiles):
        row, column = divmod(index, columns)
        _tile(
            canvas,
            theme.MARGIN + theme.CARD_PAD + column * step,
            top + theme.CARD_PAD + row * tile_height,
            label,
            value,
            colour=colour,
        )
    canvas.y = top + card_height


def _draw_equity_chart(
    canvas: pdf.PdfCanvas,
    trades: Sequence[LedgerTrade],
    *,
    height: float = 190.0,
) -> None:
    points = equity_curve(trades)
    canvas.ensure_space(height + 26.0)
    top = canvas.y
    canvas.rect(
        theme.MARGIN,
        top,
        theme.CONTENT_WIDTH,
        height,
        fill=theme.PAGE_BG,
        stroke=theme.CARD_BORDER,
        radius=theme.CARD_RADIUS,
    )

    if not points:
        canvas.text_centre(
            theme.MARGIN + theme.CONTENT_WIDTH / 2.0,
            top + height / 2.0,
            "No settled trades yet, so there is no curve to draw.",
            font=theme.FONT_BODY,
            size=theme.SIZE_BODY,
            colour=theme.MUTED,
        )
        canvas.y = top + height
        return

    pad_left, pad_right, pad_top, pad_bottom = 52.0, 18.0, 18.0, 26.0
    plot_x = theme.MARGIN + pad_left
    plot_w = theme.CONTENT_WIDTH - pad_left - pad_right
    plot_y = top + pad_top
    plot_h = height - pad_top - pad_bottom

    # The curve starts at zero: the first trade's P&L is a move away from the
    # balance the session opened with, not the balance itself.
    values = [ZERO] + [p.cumulative for p in points]
    low = min(min(values), ZERO)
    high = max(max(values), ZERO)
    span = high - low
    if span == 0:
        low, high, span = low - Decimal(1), high + Decimal(1), Decimal(2)
    pad_value = span / Decimal(12)
    low -= pad_value
    high += pad_value
    span = high - low

    def to_y(value: Decimal) -> float:
        return plot_y + plot_h * float((high - value) / span)

    def to_x(index: int) -> float:
        last = max(len(values) - 1, 1)
        return plot_x + plot_w * (index / last)

    for step in range(5):
        value = high - span * Decimal(step) / Decimal(4)
        gy = to_y(value)
        canvas.line(
            plot_x,
            gy,
            plot_x + plot_w,
            gy,
            colour=theme.HAIRLINE,
            line_width=theme.BORDER_WIDTH,
        )
        canvas.text_right(
            plot_x - 6.0,
            gy + 2.6,
            theme.money(value),
            font=theme.FONT_MONO,
            size=7.0,
            colour=theme.MUTED,
        )

    if low < ZERO < high:
        zero_y = to_y(ZERO)
        canvas.line(
            plot_x,
            zero_y,
            plot_x + plot_w,
            zero_y,
            colour=theme.BORDER_STRONG,
            line_width=theme.BORDER_WIDTH,
        )

    final = values[-1]
    canvas.polyline(
        [(to_x(i), to_y(v)) for i, v in enumerate(values)],
        stroke=theme.pnl_colour(final) if final != ZERO else theme.MUTED,
        line_width=1.6,
    )
    # Markers carry the outcome of the individual trade, which is money, so the
    # money colours are the right ones here and nowhere decorative.
    for index, point in enumerate(points, start=1):
        canvas.circle(
            to_x(index),
            to_y(point.cumulative),
            1.9,
            fill=theme.pnl_colour(point.pnl),
        )

    first_stamp = points[0].timestamp
    last_stamp = points[-1].timestamp
    canvas.text(
        plot_x,
        plot_y + plot_h + 14.0,
        first_stamp.strftime("%d %b %Y"),
        font=theme.FONT_BODY,
        size=7.5,
        colour=theme.MUTED,
    )
    canvas.text_right(
        plot_x + plot_w,
        plot_y + plot_h + 14.0,
        last_stamp.strftime("%d %b %Y"),
        font=theme.FONT_BODY,
        size=7.5,
        colour=theme.MUTED,
    )
    canvas.text_centre(
        plot_x + plot_w / 2.0,
        plot_y + plot_h + 14.0,
        f"{len(points)} settled trades, in settlement order",
        font=theme.FONT_BODY,
        size=7.5,
        colour=theme.MUTED,
    )
    canvas.y = top + height


def _window_rows(summary: Summary, drawdown: Decimal) -> list[list[pdf.Cell]]:
    decided = summary.wins + summary.losses
    pairs: list[tuple[str, pdf.Cell]] = [
        ("Trades placed", pdf.Cell(theme.number(Decimal(summary.trades)))),
        ("Settled", pdf.Cell(theme.number(Decimal(summary.settled)))),
        ("Wins", pdf.Cell(theme.number(Decimal(summary.wins)))),
        ("Losses", pdf.Cell(theme.number(Decimal(summary.losses)))),
        (
            "Win rate",
            pdf.Cell(theme.percent(summary.win_rate) if decided else theme.EM_DASH),
        ),
        ("Voids", pdf.Cell(theme.number(Decimal(summary.voids)))),
        (
            "Net P&L",
            pdf.Cell(
                theme.money(summary.net_pnl, signed=True),
                colour=theme.pnl_colour(summary.net_pnl),
            ),
        ),
        (
            "ROI on stake",
            pdf.Cell(
                theme.percent(summary.roi, digits=2, signed=True)
                if summary.total_staked
                else theme.EM_DASH,
                colour=theme.pnl_colour(summary.roi if summary.total_staked else None),
            ),
        ),
        ("Staked", pdf.Cell(theme.money(summary.total_staked))),
        ("Fees paid", pdf.Cell(theme.money(summary.fees_paid))),
        (
            "Average per trade",
            pdf.Cell(
                theme.money(summary.average_pnl, signed=True)
                if summary.settled
                else theme.EM_DASH,
                colour=theme.pnl_colour(summary.average_pnl if summary.settled else None),
            ),
        ),
        (
            "Max drawdown",
            pdf.Cell(theme.money(drawdown), colour=theme.pnl_colour(drawdown)),
        ),
    ]
    rows: list[list[pdf.Cell]] = []
    for index in range(0, len(pairs), 2):
        left_label, left_value = pairs[index]
        right_label, right_value = pairs[index + 1]
        rows.append(
            [
                pdf.Cell(left_label),
                left_value,
                pdf.Cell(""),
                pdf.Cell(right_label),
                right_value,
            ]
        )
    return rows


def _draw_window(
    canvas: pdf.PdfCanvas, key: str, trades: Sequence[LedgerTrade]
) -> None:
    _section(canvas, WINDOW_TITLES[key], WINDOW_BLURBS[key])
    if not trades:
        canvas.paragraph(
            theme.MARGIN,
            "No trades fall in this window, so there is nothing to measure. "
            "A win rate over zero trades is not a fact about the model, and it "
            "is not printed as one.",
            theme.CONTENT_WIDTH,
            font=theme.FONT_BODY,
            size=theme.SIZE_BODY,
            colour=theme.BODY,
            leading=theme.LEADING,
        )
        return

    # An empty middle column, because a right-aligned figure hard against the
    # next row's label reads as one number followed by a word.
    gutter = 20.0
    half = (theme.CONTENT_WIDTH - gutter) / 2.0
    columns = (
        pdf.Column("Metric", half * 0.58),
        pdf.Column("Value", half * 0.42, align="right", mono=True, bold=True),
        pdf.Column("", gutter),
        pdf.Column("Metric", half * 0.58),
        pdf.Column("Value", half * 0.42, align="right", mono=True, bold=True),
    )
    drawdown = max_drawdown(equity_curve(trades))
    canvas.table(
        theme.MARGIN,
        columns,
        _window_rows(summarize(trades), drawdown),
        style=_table_style(),
        show_header=False,
    )


def _draw_asset_table(canvas: pdf.PdfCanvas, trades: Sequence[LedgerTrade]) -> None:
    _section(
        canvas,
        "Per-asset breakdown",
        "All-time, split by the underlying the market settles on.",
    )
    groups = group_summaries(trades, "asset")
    if not groups:
        canvas.paragraph(
            theme.MARGIN,
            "The ledger holds no trades for this book, so there is nothing to "
            "break down by asset.",
            theme.CONTENT_WIDTH,
            font=theme.FONT_BODY,
            size=theme.SIZE_BODY,
            colour=theme.BODY,
        )
        return

    width = theme.CONTENT_WIDTH
    columns = (
        pdf.Column("Asset", width * 0.16, bold=True),
        pdf.Column("Trades", width * 0.11, align="right", mono=True),
        pdf.Column("Settled", width * 0.11, align="right", mono=True),
        pdf.Column("Win rate", width * 0.14, align="right", mono=True),
        pdf.Column("Staked", width * 0.16, align="right", mono=True),
        pdf.Column("Fees", width * 0.14, align="right", mono=True),
        pdf.Column("Net P&L", width * 0.18, align="right", mono=True, bold=True),
    )
    rows: list[list[str | pdf.Cell]] = []
    for asset, summary in sorted(
        groups.items(), key=lambda item: item[1].net_pnl, reverse=True
    ):
        decided = summary.wins + summary.losses
        rows.append(
            [
                asset,
                theme.number(Decimal(summary.trades)),
                theme.number(Decimal(summary.settled)),
                theme.percent(summary.win_rate) if decided else theme.EM_DASH,
                theme.money(summary.total_staked),
                theme.money(summary.fees_paid),
                pdf.Cell(
                    theme.money(summary.net_pnl, signed=True),
                    colour=theme.pnl_colour(summary.net_pnl),
                ),
            ]
        )
    canvas.table(theme.MARGIN, columns, rows, style=_table_style())


def _draw_model_note(canvas: pdf.PdfCanvas, trades: Sequence[LedgerTrade]) -> None:
    _section(
        canvas,
        "Model quality",
        "Whether the probabilities the bot priced on were worth believing.",
    )
    brier = brier_score(trades)
    realisation = edge_realisation(trades)
    claimed = realisation["claimed_mean_edge"]
    realised = realisation["realised_roi"]
    if brier is None:
        canvas.paragraph(
            theme.MARGIN,
            "No settled trade carries a recorded probability, so the forecast "
            "cannot be scored yet.",
            theme.CONTENT_WIDTH,
            font=theme.FONT_BODY,
            size=theme.SIZE_BODY,
            colour=theme.BODY,
        )
        return

    width = theme.CONTENT_WIDTH
    columns = (
        pdf.Column("Measure", width * 0.34),
        pdf.Column("Value", width * 0.16, align="right", mono=True, bold=True),
        pdf.Column("What it means", width * 0.50),
    )
    rows: list[list[str | pdf.Cell]] = [
        [
            "Brier score",
            f"{brier:.4f}",
            "Mean squared forecast error. 0.2500 is what always saying 50% scores.",
        ],
        [
            "Mean claimed edge",
            theme.percent(claimed, digits=2) if claimed is not None else theme.EM_DASH,
            "What the model asserted at order time, averaged over settled trades.",
        ],
        [
            "Realised return",
            pdf.Cell(
                theme.percent(realised, digits=2, signed=True)
                if realised is not None
                else theme.EM_DASH,
                colour=theme.pnl_colour(realised),
            ),
            "What the book actually paid, per dollar staked.",
        ],
    ]
    canvas.table(theme.MARGIN, columns, rows, style=_table_style())
    canvas.y += 4.0
    canvas.paragraph(
        theme.MARGIN,
        "A large claimed edge beside a smaller realised one is the signature of "
        "a model priced against the wrong reference. That gap is what the "
        "divergence gate exists to catch, and it is the number to watch as the "
        "sample grows.",
        theme.CONTENT_WIDTH,
        font=theme.FONT_BODY,
        size=theme.SIZE_BODY,
        colour=theme.MUTED,
        leading=theme.LEADING,
    )


# -------------------------------------------------------------- the report --


def build_report(
    *,
    ledger_path: Path,
    output_dir: Path | None = None,
    output_path: Path | None = None,
    mode: str = "paper",
    environment: str | None = None,
    windows: Sequence[str] = WINDOW_KEYS,
    now: datetime | None = None,
) -> Path:
    """Render one report and return the path it was written to.

    Either ``output_path`` (an exact file) or ``output_dir`` (where the next
    numbered filename is claimed) must be given.
    """
    if mode not in {"paper", "live"}:
        raise ValueError("mode must be 'paper' or 'live'")
    unknown = [key for key in windows if key not in WINDOW_TITLES]
    if unknown:
        raise ValueError(f"unknown window(s): {', '.join(unknown)}")
    if output_path is None and output_dir is None:
        raise ValueError("build_report needs output_path or output_dir")

    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ledger_path = Path(ledger_path)
    trades = read_trades(ledger_path, mode=mode, environment=environment)
    portfolio = build_portfolio(
        trades, mode=mode, environment=environment, ledger_path=ledger_path
    )

    if output_path is None:
        directory = Path(output_dir)  # type: ignore[arg-type]
        directory.mkdir(parents=True, exist_ok=True)
        number = next_report_number(directory)
        target = directory / report_filename(number, generated.date())
    else:
        target = Path(output_path)
        number = next_report_number(target.parent) - 1

    reference = f"{REPORT_PREFIX}{format_counter(max(number, 1))}"
    canvas = pdf.PdfCanvas(
        width=theme.PAGE_WIDTH,
        height=theme.PAGE_HEIGHT,
        margin=theme.MARGIN,
        bottom_margin=62.0,
        title=f"KX15 performance report {format_counter(max(number, 1))}",
        author="KX15",
        subject=f"{mode} book, generated {generated:%Y-%m-%d %H:%M UTC}",
        background=theme.PAGE_BG,
    )

    def header(target_canvas: pdf.PdfCanvas) -> None:
        pdf.draw_logo(target_canvas, theme.MARGIN, 22.0, scale=_LOGO_SCALE)
        target_canvas.text_right(
            theme.PAGE_WIDTH - theme.MARGIN,
            40.0,
            "PERFORMANCE REPORT",
            font=theme.FONT_BOLD,
            size=theme.SIZE_LABEL,
            colour=theme.MUTED,
            char_spacing=theme.LABEL_TRACKING,
        )
        target_canvas.text_right(
            theme.PAGE_WIDTH - theme.MARGIN,
            53.0,
            reference,
            font=theme.FONT_MONO,
            size=theme.SIZE_LABEL,
            colour=theme.MUTED,
        )
        target_canvas.line(
            theme.MARGIN,
            _HEADER_RULE_Y,
            theme.PAGE_WIDTH - theme.MARGIN,
            _HEADER_RULE_Y,
            colour=theme.BORDER_STRONG,
            line_width=theme.BORDER_WIDTH,
        )
        target_canvas.y = _HEADER_RULE_Y + 22.0

    def footer(target_canvas: pdf.PdfCanvas, page: int, total: int) -> None:
        target_canvas.line(
            theme.MARGIN,
            _FOOTER_RULE_Y,
            theme.PAGE_WIDTH - theme.MARGIN,
            _FOOTER_RULE_Y,
            colour=theme.HAIRLINE,
            line_width=theme.BORDER_WIDTH,
        )
        book = f"{mode} book"
        if environment:
            book += f" \xb7 {environment}"
        target_canvas.text(
            theme.MARGIN,
            _FOOTER_RULE_Y + 13.0,
            f"KX15 \xb7 {book} \xb7 generated {generated:%Y-%m-%d %H:%M UTC}",
            font=theme.FONT_BODY,
            size=theme.SIZE_FOOTER,
            colour=theme.MUTED,
        )
        target_canvas.text_right(
            theme.PAGE_WIDTH - theme.MARGIN,
            _FOOTER_RULE_Y + 13.0,
            f"Page {page} of {total}",
            font=theme.FONT_BODY,
            size=theme.SIZE_FOOTER,
            colour=theme.MUTED,
        )

    canvas.on_new_page = header
    canvas.footer = footer
    canvas.new_page()

    canvas.text(
        theme.MARGIN,
        canvas.y + theme.SIZE_TITLE,
        "Performance report",
        font=theme.FONT_BOLD,
        size=theme.SIZE_TITLE,
        colour=theme.TITLE,
    )
    canvas.y += theme.SIZE_TITLE + 10.0

    if mode == "live":
        pill_width = _pill(
            canvas,
            theme.MARGIN,
            canvas.y,
            "LIVE \xb7 REAL MONEY",
            background=theme.WARNING_BG,
            border=theme.WARNING_BORDER,
            ink=theme.WARNING_TEXT,
        )
    else:
        pill_width = _pill(
            canvas,
            theme.MARGIN,
            canvas.y,
            "PAPER",
            background=theme.SURFACE,
            border=theme.BORDER_STRONG,
            ink=theme.MUTED,
        )
    canvas.text(
        theme.MARGIN + pill_width + 10.0,
        canvas.y + 12.0,
        f"{len(trades)} trades on record \xb7 ledger {ledger_path.name}",
        font=theme.FONT_BODY,
        size=theme.SIZE_BODY,
        colour=theme.MUTED,
    )
    canvas.y += 17.0 + theme.BLOCK_GAP

    _section(canvas, "Portfolio", _portfolio_blurb(portfolio))
    _draw_portfolio_card(canvas, portfolio)

    _section(
        canvas,
        "Equity curve",
        "Cumulative realised P&L, ordered by settlement.",
    )
    _draw_equity_chart(canvas, trades)

    for key in windows:
        _draw_window(canvas, key, slice_window(trades, window_start(key, generated)))

    _draw_asset_table(canvas, trades)
    _draw_model_note(canvas, trades)

    return canvas.save(target)


def _describe_streak(kind: str, length: int) -> str:
    if kind == "WON":
        return f"Currently {length} win{'' if length == 1 else 's'} in a row"
    if kind == "LOST":
        return f"Currently {length} loss{'' if length == 1 else 'es'} in a row"
    return "No settled trades yet"


def _portfolio_blurb(portfolio: Portfolio) -> str:
    tail = (
        f"{_describe_streak(*portfolio.streak)}; "
        f"longest losing run {portfolio.longest_losing}."
    )
    if portfolio.mode == "live":
        return (
            "Live cash is held at Kalshi and this report never opens a network "
            f"connection, so balance and available funds are not shown. {tail}"
        )
    if portfolio.starting_balance is None:
        return (
            "The ledger records no seed balance for this book, so only realised "
            f"P&L is shown. {tail}"
        )
    return (
        f"Paper balance is the ledger's own: the "
        f"{theme.money(portfolio.starting_balance)} seed plus realised P&L, less "
        f"anything still at risk. {tail}"
    )


# --------------------------------------------------------------------- CLI --


def _default_paths() -> tuple[Path, Path]:
    """(ledger, log directory) from the environment, with a usable fallback.

    A misconfigured ``.env`` should not stop somebody generating a report over
    a ledger they name explicitly on the command line.
    """
    root = Path(__file__).resolve().parent.parent
    try:
        from config import KalshiSettings, load_environment

        # The report names the ledger it read; without the environment that is
        # the default path rather than the operator's, so a configured install
        # would silently report on an empty ledger beside the real one.
        load_environment(root)
        settings = KalshiSettings.from_env(root)
        return (settings.ledger_path, settings.log_dir)
    except Exception:
        return (root / "trade_ledger.db", Path.home() / "kx15_Performance_Logs")


def main(argv: Sequence[str] | None = None) -> int:
    ledger_default, log_default = _default_paths()
    parser = argparse.ArgumentParser(
        prog="python -m reporting.report",
        description="Generate a KX15 PDF performance report from the trade ledger.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write to this exact path instead of the next numbered filename.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=log_default,
        help=f"Directory for numbered reports (default: {log_default}).",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ledger_default,
        help=f"SQLite trade ledger to read (default: {ledger_default}).",
    )
    parser.add_argument(
        "--mode",
        choices=("paper", "live"),
        default="paper",
        help="Which book to report on (default: paper).",
    )
    parser.add_argument(
        "--environment",
        default=None,
        help="Restrict to one exchange environment, e.g. production.",
    )
    parser.add_argument(
        "--window",
        choices=WINDOW_KEYS,
        default=None,
        help="Report a single window instead of all three.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the finished report in the default viewer.",
    )
    args = parser.parse_args(argv)

    path = build_report(
        ledger_path=args.ledger,
        output_dir=None if args.output else args.output_dir,
        output_path=args.output,
        mode=args.mode,
        environment=args.environment,
        windows=(args.window,) if args.window else WINDOW_KEYS,
    )
    print(path)
    if args.open:
        webbrowser.open(path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
