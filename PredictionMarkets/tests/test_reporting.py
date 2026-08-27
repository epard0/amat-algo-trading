"""Tests for the PDF performance report.

Nothing here touches the network or the real ledger. Trades are hand-built so
every expected figure can be worked out on paper, and the PDFs are written to
tmp_path and parsed back out of their own bytes rather than handed to a viewer.

The structural assertions matter more than they look: a PDF with a wrong xref
offset opens fine in one reader and silently fails in another, and the failure
is a blank page rather than an error.
"""

from __future__ import annotations

import re
import zlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from monitoring.sources import LedgerTrade
from reporting import pdf, theme
from reporting.report import (
    build_report,
    format_counter,
    next_report_number,
    report_filename,
    slice_window,
    window_start,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def make_trade(
    trade_id: str = "t1",
    *,
    status: str = "WON",
    pnl: str | None = "1.00",
    asset: str = "BTC",
    mode: str = "paper",
    debit: str = "5.00",
    fees: str = "0.10",
    probability: str = "0.70",
    minutes_ago: int = 30,
) -> LedgerTrade:
    settled = NOW - timedelta(minutes=minutes_ago)
    return LedgerTrade(
        trade_id=trade_id,
        mode=mode,
        environment="production",
        asset=asset,
        ticker=f"KX{asset}15M-{trade_id}",
        outcome="yes",
        status=status,
        market_result="yes" if status == "WON" else "no",
        placed_at=settled - timedelta(minutes=10),
        market_open_time=settled - timedelta(minutes=15),
        market_close_time=settled,
        settled_at=settled,
        contracts=Decimal("10"),
        entry_price=Decimal("0.50"),
        entry_cost=Decimal(debit),
        fees_paid=Decimal(fees),
        total_debit=Decimal(debit),
        gross_payout=Decimal("10") if status == "WON" else Decimal("0"),
        pnl=None if pnl is None else Decimal(pnl),
        target_price=Decimal("100000"),
        model_probability=Decimal(probability),
        model_edge=Decimal("0.10"),
        metadata={"regime": "TREND"},
    )


def write_ledger(path: Path, trades: list[LedgerTrade], *, seed: str = "100") -> Path:
    """A minimal ledger with the columns monitoring.sources reads."""
    import sqlite3

    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE ledger_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)
        """
    )
    connection.execute(
        "INSERT INTO ledger_metadata VALUES ('paper_starting_balance', ?)", (seed,)
    )
    connection.execute(
        """
        CREATE TABLE trades (
            trade_id TEXT PRIMARY KEY, mode TEXT, environment TEXT, asset TEXT,
            ticker TEXT, outcome TEXT, status TEXT, market_result TEXT,
            placed_at TEXT, market_open_time TEXT, market_close_time TEXT,
            settled_at TEXT, contracts TEXT, entry_price TEXT, entry_cost TEXT,
            fees_paid TEXT, total_debit TEXT, gross_payout TEXT, pnl TEXT,
            target_price TEXT, model_probability TEXT, model_edge TEXT,
            metadata_json TEXT
        )
        """
    )
    for trade in trades:
        connection.execute(
            "INSERT INTO trades VALUES (" + ",".join("?" * 23) + ")",
            (
                trade.trade_id,
                trade.mode,
                trade.environment,
                trade.asset,
                trade.ticker,
                trade.outcome,
                trade.status,
                trade.market_result,
                trade.placed_at.isoformat() if trade.placed_at else None,
                trade.market_open_time.isoformat() if trade.market_open_time else None,
                trade.market_close_time.isoformat()
                if trade.market_close_time
                else None,
                trade.settled_at.isoformat() if trade.settled_at else None,
                str(trade.contracts),
                str(trade.entry_price),
                str(trade.entry_cost),
                str(trade.fees_paid),
                str(trade.total_debit),
                str(trade.gross_payout),
                None if trade.pnl is None else str(trade.pnl),
                str(trade.target_price),
                str(trade.model_probability),
                str(trade.model_edge),
                "{}",
            ),
        )
    connection.commit()
    connection.close()
    return path


# ------------------------------------------------------------ file naming --


def test_counter_starts_at_one_in_an_empty_directory(tmp_path: Path) -> None:
    assert next_report_number(tmp_path) == 1
    assert report_filename(1, datetime(2026, 8, 11).date()) == (
        "kx15-report-0001-08-11-26.pdf"
    )


def test_counter_takes_one_past_the_highest_present(tmp_path: Path) -> None:
    for name in ("kx15-report-0001-01-01-26.pdf", "kx15-report-0007-01-02-26.pdf"):
        (tmp_path / name).touch()
    # Not a report, and must not be mistaken for one.
    (tmp_path / "notes.txt").touch()
    assert next_report_number(tmp_path) == 8


def test_counter_widens_past_9999(tmp_path: Path) -> None:
    (tmp_path / "kx15-report-9999-01-01-26.pdf").touch()
    number = next_report_number(tmp_path)
    assert number == 10000
    assert format_counter(number) == "10000"
    assert report_filename(number, datetime(2026, 8, 11).date()) == (
        "kx15-report-10000-08-11-26.pdf"
    )
    # And keeps widening, rather than wrapping back into the four-digit space.
    (tmp_path / report_filename(number, datetime(2026, 8, 11).date())).touch()
    assert next_report_number(tmp_path) == 10001
    assert format_counter(99999 + 1) == "100000"


def test_counter_reads_a_widened_name_back(tmp_path: Path) -> None:
    (tmp_path / "kx15-report-123456-08-11-26.pdf").touch()
    assert next_report_number(tmp_path) == 123457


def test_counter_below_four_digits_stays_padded() -> None:
    assert format_counter(1) == "0001"
    assert format_counter(42) == "0042"
    assert format_counter(9999) == "9999"


# ------------------------------------------------------------- text metrics --


def test_text_width_matches_the_afm_tables() -> None:
    # K=722 X=667 1=556 5=556 in Helvetica-Bold, so 2501/1000 em.
    assert pdf.text_width("KX15", pdf.HELVETICA_BOLD, 30.0) == 2501 * 30.0 / 1000.0
    # Helvetica: space=278, A=667, W=944, i=222.
    assert pdf.text_width(" ", pdf.HELVETICA, 10.0) == 2.78
    assert pdf.text_width("A", pdf.HELVETICA, 10.0) == 6.67
    assert pdf.text_width("W", pdf.HELVETICA, 10.0) == 9.44
    assert pdf.text_width("i", pdf.HELVETICA, 10.0) == 2.22


def test_helvetica_is_proportional_and_courier_is_not() -> None:
    assert pdf.text_width("iiii", pdf.HELVETICA, 10.0) < pdf.text_width(
        "WWWW", pdf.HELVETICA, 10.0
    )
    assert pdf.text_width("iiii", pdf.COURIER, 10.0) == pdf.text_width(
        "WWWW", pdf.COURIER, 10.0
    )
    assert pdf.text_width("123456", pdf.COURIER, 10.0) == 6 * 6.0


def test_text_width_scales_linearly_with_size() -> None:
    small = pdf.text_width("Performance report", pdf.HELVETICA_BOLD, 10.0)
    assert pdf.text_width("Performance report", pdf.HELVETICA_BOLD, 20.0) == small * 2


def test_char_spacing_excludes_the_trailing_gap() -> None:
    plain = pdf.text_width("ABCD", pdf.HELVETICA_BOLD, 8.0)
    tracked = pdf.text_width("ABCD", pdf.HELVETICA_BOLD, 8.0, char_spacing=1.4)
    assert tracked == plain + 1.4 * 3


def test_non_winansi_characters_do_not_break_measurement() -> None:
    # The em dash is in WinAnsi and has a real width; a CJK glyph is not and
    # falls back to "?" rather than raising in the middle of a report.
    assert pdf.text_width(theme.EM_DASH, pdf.HELVETICA, 10.0) == 10.0
    assert pdf.text_width("中", pdf.HELVETICA, 10.0) == pdf.text_width(
        "?", pdf.HELVETICA, 10.0
    )


def test_right_alignment_lands_on_the_requested_edge(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas()
    canvas.new_page()
    width = canvas.text_right(500.0, 100.0, "$1,234.56", font=pdf.COURIER, size=9.0)
    stream = _page_streams(canvas.save(tmp_path / "align.pdf"))[0]
    placement = re.search(r"([-\d.]+) ([-\d.]+) Td", stream)
    assert placement is not None
    assert abs(float(placement.group(1)) + width - 500.0) < 1e-6


# ------------------------------------------------------ structural validity --


def _page_streams(path: Path) -> list[str]:
    """Every page content stream, inflated and decoded back out of WinAnsi.

    Decoding as cp1252 rather than latin-1 is what lets a test assert on an em
    dash as an em dash instead of on the byte 0x97.
    """
    data = path.read_bytes()
    streams = []
    for match in re.finditer(rb"/FlateDecode >>\nstream\n(.*?)\nendstream", data, re.S):
        streams.append(zlib.decompress(match.group(1)).decode("cp1252", "replace"))
    return streams


def _xref_offsets(data: bytes) -> list[int]:
    start = int(re.search(rb"startxref\n(\d+)", data).group(1))  # type: ignore[union-attr]
    table = data[start:]
    assert table.startswith(b"xref\n")
    return [
        int(row.split()[0])
        for row in table.split(b"\n")[2:]
        if re.match(rb"^\d{10} \d{5} n\s*$", row)
    ]


def test_pdf_header_xref_and_trailer_are_consistent(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas(title="Structure")
    canvas.new_page()
    canvas.text(72.0, 72.0, "hello", font=pdf.HELVETICA, size=12.0, colour="#118843")
    canvas.new_page()
    canvas.rect(72.0, 72.0, 100.0, 40.0, fill="#F9FBFA", stroke="#DFE6E1", radius=16.0)
    path = canvas.save(tmp_path / "structure.pdf")
    data = path.read_bytes()

    assert data.startswith(b"%PDF-1.7\n")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in data
    assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") == 2

    offsets = _xref_offsets(data)
    size = int(re.search(rb"/Size (\d+)", data).group(1))  # type: ignore[union-attr]
    assert len(offsets) == size - 1
    # Every offset must land exactly on its own "N 0 obj" header, which is the
    # thing a reader uses to find the object at all.
    for index, offset in enumerate(offsets, start=1):
        assert data[offset:].startswith(f"{index} 0 obj".encode("ascii"))


def test_content_streams_are_flate_compressed(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas()
    canvas.new_page()
    canvas.text(72.0, 72.0, "compress me" * 40, size=9.0)
    path = canvas.save(tmp_path / "flate.pdf")
    assert b"/Filter /FlateDecode" in path.read_bytes()
    assert "compress me" in _page_streams(path)[0]


def test_parentheses_and_backslashes_are_escaped(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas()
    canvas.new_page()
    canvas.text(72.0, 72.0, r"a (b) \c", size=10.0)
    stream = _page_streams(canvas.save(tmp_path / "escape.pdf"))[0]
    assert r"(a \(b\) \\c) Tj" in stream


def test_automatic_page_breaks_run_the_new_page_hook(tmp_path: Path) -> None:
    seen: list[int] = []

    canvas = pdf.PdfCanvas(on_new_page=lambda c: seen.append(c.page_count))
    canvas.new_page()
    for _ in range(200):
        canvas.ensure_space(20.0)
        canvas.text(72.0, canvas.y + 10.0, "row", size=9.0)
        canvas.y += 20.0
    path = canvas.save(tmp_path / "flow.pdf")
    assert canvas.page_count > 1
    assert seen == list(range(1, canvas.page_count + 1))
    assert len(_page_streams(path)) == canvas.page_count


def test_footer_runs_once_per_page_with_the_final_count(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas()
    canvas.footer = lambda c, page, total: c.text(
        72.0, 700.0, f"Page {page} of {total}", size=8.0
    )
    canvas.new_page()
    canvas.new_page()
    canvas.new_page()
    streams = _page_streams(canvas.save(tmp_path / "footer.pdf"))
    assert len(streams) == 3
    for index, stream in enumerate(streams, start=1):
        assert f"(Page {index} of 3) Tj" in stream


def test_alpha_registers_one_extgstate_per_value(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas()
    canvas.new_page()
    canvas.line(0.0, 0.0, 10.0, 10.0, colour="#16A34A", alpha=0.5)
    canvas.line(0.0, 0.0, 10.0, 10.0, colour="#16A34A", alpha=0.5)
    canvas.line(0.0, 0.0, 10.0, 10.0, colour="#16A34A", alpha=0.9)
    data = canvas.save(tmp_path / "alpha.pdf").read_bytes()
    assert data.count(b"/Type /ExtGState") == 2
    assert b"/ca 0.500" in data and b"/CA 0.900" in data


def test_full_opacity_needs_no_extgstate(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas()
    canvas.new_page()
    canvas.line(0.0, 0.0, 10.0, 10.0, colour="#16A34A", alpha=1.0)
    assert b"/Type /ExtGState" not in canvas.save(tmp_path / "opaque.pdf").read_bytes()


def test_the_logo_keeps_its_opacity_ladder(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas()
    canvas.new_page()
    width, height = pdf.draw_logo(canvas, 54.0, 22.0, scale=0.5)
    assert (width, height) == (153.0, 32.0)
    data = canvas.save(tmp_path / "logo.pdf").read_bytes()
    # 0.9 dial and tagline, 0.5 wicks, 0.45 and 0.75 bodies. The third body is
    # fully opaque and must not have acquired a state of its own.
    for value in (b"/ca 0.900", b"/ca 0.500", b"/ca 0.450", b"/ca 0.750"):
        assert value in data
    assert b"/ca 1.000" not in data


def test_the_dial_leaves_the_top_right_quadrant_open(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas(height=64.0, width=306.0)
    canvas.new_page()
    pdf.draw_logo(canvas, 0.0, 0.0, scale=1.0, with_wordmark=False)
    stream = _page_streams(canvas.save(tmp_path / "dial.pdf"))[0]
    start = re.search(r"([\d.]+) ([\d.]+) m", stream)
    assert start is not None
    # Twelve o'clock in page coordinates is (32, 7) from the top, which is
    # y = 64 - 7 = 57 in the PDF's own upward axis.
    assert abs(float(start.group(1)) - 32.0) < 0.01
    assert abs(float(start.group(2)) - 57.0) < 0.01
    # Three o'clock, and nothing beyond it: the gap is the fifteen minutes.
    assert "57 32 c" in stream


def test_table_repeats_its_header_after_a_break(tmp_path: Path) -> None:
    canvas = pdf.PdfCanvas()
    canvas.new_page()
    columns = (
        pdf.Column("Asset", 200.0),
        pdf.Column("Net P&L", 200.0, align="right", mono=True),
    )
    rows = [[f"BTC{i}", "$1.00"] for i in range(80)]
    canvas.table(72.0, columns, rows)
    streams = _page_streams(canvas.save(tmp_path / "table.pdf"))
    assert len(streams) > 1
    assert all("(NET P&L) Tj" in stream for stream in streams)


# ------------------------------------------------------- money and colours --


def test_money_puts_the_minus_outside_the_dollar_sign() -> None:
    assert theme.money(Decimal("-18.13")) == "-$18.13"
    assert theme.money(Decimal("25.33")) == "$25.33"
    assert theme.money(Decimal("0")) == "$0.00"
    assert theme.money(None) == theme.EM_DASH
    assert theme.money(Decimal("1234.5")) == "$1,234.50"


def test_the_leading_plus_is_the_call_sites_choice() -> None:
    assert theme.money(Decimal("3.62"), signed=True) == "+$3.62"
    assert theme.money(Decimal("-3.62"), signed=True) == "-$3.62"
    # Zero is a level, not a gain, and never wears a plus.
    assert theme.money(Decimal("0"), signed=True) == "$0.00"


def test_colour_is_reserved_for_money_that_moved() -> None:
    assert theme.pnl_colour(Decimal("0.01")) == theme.GREEN
    assert theme.pnl_colour(Decimal("-0.01")) == theme.LOSS
    assert theme.pnl_colour(Decimal("0")) == theme.BODY
    assert theme.pnl_colour(None) == theme.BODY


def test_the_palette_uses_the_storefronts_own_values() -> None:
    assert theme.GREEN == "#118843"
    assert theme.LOGO_GREEN == "#16A34A"
    assert theme.LOSS == "#B8331E"
    assert theme.HAIRLINE == "#EBEFED"


def test_percent_and_number_handle_absence() -> None:
    assert theme.percent(Decimal("0.731")) == "73.1%"
    assert theme.percent(Decimal("0.0566"), digits=2, signed=True) == "+5.66%"
    assert theme.percent(None) == theme.EM_DASH
    assert theme.number(None) == theme.EM_DASH
    assert theme.number(Decimal("1234")) == "1,234"


# -------------------------------------------------------------- the windows --


def test_window_start_bounds() -> None:
    assert window_start("all", NOW) is None
    assert window_start("day", NOW) == datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert window_start("hour", NOW) == NOW - timedelta(hours=1)


def test_slicing_keys_on_settlement_not_placement() -> None:
    # Placed before the hour boundary, settled inside it: the balance moved
    # inside the window, so the trade belongs to it.
    trade = make_trade("straddle", minutes_ago=30)
    assert slice_window([trade], window_start("hour", NOW)) == [trade]
    assert slice_window([make_trade("old", minutes_ago=180)], window_start("hour", NOW)) == []


# ------------------------------------------------------------- the report --


def test_report_over_a_real_shaped_ledger(tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "ledger.db",
        [
            make_trade("a", status="WON", pnl="4.00", minutes_ago=30),
            make_trade("b", status="LOST", pnl="-5.00", asset="ETH", minutes_ago=90),
            make_trade("c", status="WON", pnl="2.00", asset="SOL", minutes_ago=4000),
        ],
    )
    path = build_report(
        ledger_path=ledger, output_dir=tmp_path / "out", mode="paper", now=NOW
    )
    assert path.name == "kx15-report-0001-08-11-26.pdf"
    data = path.read_bytes()
    assert data.startswith(b"%PDF-1.7")

    text = "\n".join(_page_streams(path))
    assert "(Performance report) Tj" in text
    assert "(PAPER) Tj" in text
    assert "(All-time performance) Tj" in text
    assert "(The last hour) Tj" in text
    # Seed 100 plus a net +1.00, with nothing open.
    assert "($101.00) Tj" in text
    assert "(+$1.00) Tj" in text

    # A second call claims the next number without any state file.
    second = build_report(
        ledger_path=ledger, output_dir=tmp_path / "out", mode="paper", now=NOW
    )
    assert second.name == "kx15-report-0002-08-11-26.pdf"


def test_live_report_shows_no_balance_and_wears_the_live_mark(tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "ledger.db",
        [make_trade("a", status="WON", pnl="4.00", mode="live", minutes_ago=30)],
    )
    path = build_report(
        ledger_path=ledger, output_dir=tmp_path / "out", mode="live", now=NOW
    )
    text = "\n".join(_page_streams(path))
    assert "(LIVE \xb7 REAL MONEY) Tj" in text
    assert "(BALANCE) Tj" in text
    # Live cash is not in the ledger, so the tile is an em dash rather than a
    # stale figure dressed up as current.
    assert f"({theme.EM_DASH}) Tj" in text


def test_an_empty_ledger_reports_in_words_without_raising(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "empty.db", [])
    path = build_report(
        ledger_path=ledger, output_dir=tmp_path / "out", mode="paper", now=NOW
    )
    assert path.exists()
    text = "\n".join(_page_streams(path))
    assert "no curve to draw" in text
    assert "nothing to measure" in text
    assert "nothing to break down by asset" in text
    # No division by zero anywhere: the win rate is withheld, not printed as 0%.
    assert "(0.0%) Tj" not in text


def test_a_missing_ledger_still_produces_a_report(tmp_path: Path) -> None:
    path = build_report(
        ledger_path=tmp_path / "nope.db",
        output_dir=tmp_path / "out",
        mode="paper",
        now=NOW,
    )
    assert path.exists() and path.read_bytes().startswith(b"%PDF")


def test_a_single_window_can_be_asked_for(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "ledger.db", [make_trade("a")])
    path = build_report(
        ledger_path=ledger,
        output_dir=tmp_path / "out",
        mode="paper",
        windows=("hour",),
        now=NOW,
    )
    text = "\n".join(_page_streams(path))
    assert "(The last hour) Tj" in text
    assert "(All-time performance) Tj" not in text


def test_explicit_output_path_is_honoured(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "ledger.db", [make_trade("a")])
    target = tmp_path / "elsewhere" / "named.pdf"
    assert build_report(
        ledger_path=ledger, output_path=target, mode="paper", now=NOW
    ) == target
    assert target.exists()


def test_bad_arguments_are_refused(tmp_path: Path) -> None:
    for kwargs in (
        {"mode": "demo", "output_dir": tmp_path},
        {"windows": ("week",), "output_dir": tmp_path},
        {},
    ):
        try:
            build_report(ledger_path=tmp_path / "x.db", now=NOW, **kwargs)  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")
