"""A minimal PDF 1.7 writer, hand-rolled for the same reason the Prometheus
exposition and the dashboard charts are.

The bot ships with a pinned, deliberately short dependency list, and an
undeclared import has already broken this repo once. reportlab would be one
more thing to install on a trading host to produce a document nothing on that
host reads, so the format is written directly instead. It is less work than it
sounds: the base-14 fonts need no embedding, the graphics operators are a dozen
of PostScript's, and zlib is in the standard library.

Two decisions are worth knowing before editing.

**The coordinate system is top-down.** PDF's own origin is the bottom-left
corner with y increasing upwards, which is the wrong way round for laying out a
document that flows downward and breaks onto a new page at the bottom. Every
public method here takes y measured *downwards from the top edge*, and the flip
happens at the single point where operators are emitted. ``rect`` takes its top
edge; ``text`` takes its baseline; angles run counter-clockwise as seen on the
printed page.

**Text is WinAnsi.** The base-14 fonts are single-byte, so strings are encoded
to CP1252 and anything outside it becomes "?". That covers the em dash, the
bullet and the middle dot this report actually uses; it does not cover CJK, and
adding a font that does means embedding one.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

# --------------------------------------------------------------- font metrics --

# Advance widths in 1/1000 em for WinAnsiEncoding codes 32..255, transcribed
# from Adobe's own Helvetica.afm and Helvetica-Bold.afm. They are here rather
# than read at run time because the AFM files are not installed on a trading
# host, and a text_width() that guessed would put every right-aligned number in
# a slightly different place.
_HELVETICA_WIDTHS: tuple[int, ...] = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333,
    278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278,
    584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
    500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
    667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
    278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
    278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584, 0, 556, 0, 222,
    556, 333, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0, 0,
    222, 222, 333, 333, 350, 556, 1000, 333, 1000, 500, 333, 944, 0, 500,
    667, 278, 333, 556, 556, 556, 556, 260, 556, 333, 737, 370, 556, 584,
    333, 737, 333, 400, 584, 333, 333, 333, 556, 537, 278, 333, 333, 365,
    556, 834, 834, 834, 611, 667, 667, 667, 667, 667, 667, 1000, 722, 667,
    667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778,
    584, 778, 722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556,
    556, 889, 500, 556, 556, 556, 556, 278, 278, 278, 278, 556, 556, 556,
    556, 556, 556, 556, 584, 611, 556, 556, 556, 556, 500, 556, 500,
)

_HELVETICA_BOLD_WIDTHS: tuple[int, ...] = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333,
    278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333,
    584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278,
    556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
    667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556,
    333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556,
    333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584, 0, 556, 0, 278,
    556, 500, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0, 0,
    278, 278, 500, 500, 350, 556, 1000, 333, 1000, 556, 333, 944, 0, 500,
    667, 278, 333, 556, 556, 556, 556, 280, 556, 333, 737, 370, 556, 584,
    333, 737, 333, 400, 584, 333, 333, 333, 611, 556, 278, 333, 333, 365,
    556, 834, 834, 834, 611, 722, 722, 722, 722, 722, 722, 1000, 722, 667,
    667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778,
    584, 778, 722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556,
    556, 889, 556, 556, 556, 556, 556, 278, 278, 278, 278, 611, 611, 611,
    611, 611, 611, 611, 584, 611, 611, 611, 611, 611, 556, 611, 556,
)

# Courier is a fixed-pitch design: every glyph, including the space, advances
# 600/1000 em. That is the whole point of using it for numbers.
_COURIER_WIDTH = 600

HELVETICA = "Helvetica"
HELVETICA_BOLD = "Helvetica-Bold"
COURIER = "Courier"
COURIER_BOLD = "Courier-Bold"

_FONT_RESOURCE = {
    HELVETICA: "F1",
    HELVETICA_BOLD: "F2",
    COURIER: "F3",
    COURIER_BOLD: "F4",
}

_FONT_WIDTHS: dict[str, tuple[int, ...] | None] = {
    HELVETICA: _HELVETICA_WIDTHS,
    HELVETICA_BOLD: _HELVETICA_BOLD_WIDTHS,
    COURIER: None,
    COURIER_BOLD: None,
}


def encode_text(text: str) -> bytes:
    """Down to the single byte range the base-14 fonts can actually show."""
    return text.encode("cp1252", "replace")


def text_width(
    text: str,
    font: str = HELVETICA,
    size: float = 10.0,
    *,
    char_spacing: float = 0.0,
) -> float:
    """Advance width in points.

    The trailing character's spacing is excluded: PDF's Tc adds it after the
    last glyph too, but it is whitespace, not ink, and including it would nudge
    every right-aligned tracked label a fraction off its rule.
    """
    if font not in _FONT_WIDTHS:
        raise KeyError(f"unknown base-14 font: {font}")
    data = encode_text(text)
    widths = _FONT_WIDTHS[font]
    if widths is None:
        total = _COURIER_WIDTH * len(data)
    else:
        total = sum(widths[b - 32] if 32 <= b <= 255 else 0 for b in data)
    return total * size / 1000.0 + char_spacing * max(len(data) - 1, 0)


# ------------------------------------------------------------------ helpers --


def _num(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-", "-0"} else text


def _escape(text: str) -> bytes:
    data = encode_text(text)
    for old, new in ((b"\\", b"\\\\"), (b"(", b"\\("), (b")", b"\\)")):
        data = data.replace(old, new)
    return data


def _rgb(colour: str) -> tuple[float, float, float]:
    value = colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f"colour must be a #RRGGBB hex string, got {colour!r}")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _fill_op(colour: str) -> str:
    r, g, b = _rgb(colour)
    return f"{_num(r)} {_num(g)} {_num(b)} rg"


def _stroke_op(colour: str) -> str:
    r, g, b = _rgb(colour)
    return f"{_num(r)} {_num(g)} {_num(b)} RG"


# ------------------------------------------------------------------- tables --


@dataclass(frozen=True)
class Column:
    """One table column. ``width`` is in points and is not negotiable."""

    heading: str
    width: float
    align: str = "left"
    mono: bool = False
    bold: bool = False


@dataclass(frozen=True)
class Cell:
    """A table cell that needs its own colour or weight."""

    text: str
    colour: str | None = None
    bold: bool | None = None


@dataclass(frozen=True)
class TableStyle:
    """Everything a table needs to look like the storefront's .kx-kv."""

    body_font: str = HELVETICA
    body_bold_font: str = HELVETICA_BOLD
    mono_font: str = COURIER
    mono_bold_font: str = COURIER_BOLD
    body_size: float = 9.0
    body_colour: str = "#404654"
    header_font: str = HELVETICA_BOLD
    header_size: float = 7.5
    header_colour: str = "#69748C"
    header_tracking: float = 0.9
    header_rule_colour: str = "#D7E0DB"
    rule_colour: str = "#EBEFED"
    rule_width: float = 1.0
    pad_v: float = 8.8
    pad_h: float = 5.6


# -------------------------------------------------------------------- canvas --


class PdfCanvas:
    """A paginated drawing surface that serialises to a single PDF file.

    ``y`` is the running cursor, measured downwards from the top edge, and is
    the only piece of mutable layout state. Callers advance it; ``ensure_space``
    turns it into a page break when the next block will not fit.
    """

    def __init__(
        self,
        *,
        width: float = 612.0,
        height: float = 792.0,
        margin: float = 54.0,
        bottom_margin: float | None = None,
        title: str = "",
        author: str = "",
        subject: str = "",
        creator: str = "KX15",
        background: str | None = None,
        on_new_page: Callable[["PdfCanvas"], None] | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.margin = margin
        self.bottom_margin = margin if bottom_margin is None else bottom_margin
        self.title = title
        self.author = author
        self.subject = subject
        self.creator = creator
        self.background = background
        self.on_new_page = on_new_page
        self.y = margin
        self._pages: list[list[str]] = []
        self._current = -1
        self._alpha_names: dict[str, str] = {}
        self._footers_drawn = False
        self.footer: Callable[["PdfCanvas", int, int], None] | None = None

    # -- pages ---------------------------------------------------------------

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def content_width(self) -> float:
        return self.width - 2 * self.margin

    @property
    def bottom(self) -> float:
        return self.height - self.bottom_margin

    def new_page(self) -> None:
        self._pages.append([])
        self._current = len(self._pages) - 1
        self.y = self.margin
        if self.background:
            self.rect(0, 0, self.width, self.height, fill=self.background)
        if self.on_new_page is not None:
            self.on_new_page(self)

    def ensure_space(self, needed: float) -> bool:
        """Break to a new page if ``needed`` points will not fit. True if broken."""
        if self._current < 0:
            self.new_page()
            return True
        if self.y + needed > self.bottom:
            self.new_page()
            return True
        return False

    def _emit(self, *ops: str) -> None:
        if self._current < 0:
            self.new_page()
        self._pages[self._current].extend(ops)

    def _alpha_gs(self, alpha: float) -> str:
        key = f"{alpha:.3f}"
        name = self._alpha_names.get(key)
        if name is None:
            name = f"GA{len(self._alpha_names)}"
            self._alpha_names[key] = name
        return name

    def _open(self, alpha: float | None) -> None:
        self._emit("q")
        if alpha is not None and alpha < 1.0:
            self._emit(f"/{self._alpha_gs(alpha)} gs")

    def _close(self) -> None:
        self._emit("Q")

    def _ty(self, y: float) -> float:
        return self.height - y

    # -- text ----------------------------------------------------------------

    def text_width(
        self,
        text: str,
        font: str = HELVETICA,
        size: float = 10.0,
        *,
        char_spacing: float = 0.0,
    ) -> float:
        return text_width(text, font, size, char_spacing=char_spacing)

    def text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        font: str = HELVETICA,
        size: float = 10.0,
        colour: str = "#000000",
        char_spacing: float = 0.0,
        alpha: float | None = None,
    ) -> float:
        """Draw one line with its baseline at ``y``. Returns the advance width."""
        if font not in _FONT_RESOURCE:
            raise KeyError(f"unknown base-14 font: {font}")
        if not text:
            return 0.0
        self._open(alpha)
        self._emit("BT")
        if char_spacing:
            self._emit(f"{_num(char_spacing)} Tc")
        self._emit(
            f"/{_FONT_RESOURCE[font]} {_num(size)} Tf",
            _fill_op(colour),
            f"{_num(x)} {_num(self._ty(y))} Td",
        )
        self._emit(f"({_escape(text).decode('latin-1')}) Tj")
        self._emit("ET")
        self._close()
        return self.text_width(text, font, size, char_spacing=char_spacing)

    def text_right(
        self,
        x: float,
        y: float,
        text: str,
        *,
        font: str = HELVETICA,
        size: float = 10.0,
        colour: str = "#000000",
        char_spacing: float = 0.0,
        alpha: float | None = None,
    ) -> float:
        """Draw with the string's right edge at ``x``."""
        width = self.text_width(text, font, size, char_spacing=char_spacing)
        return self.text(
            x - width,
            y,
            text,
            font=font,
            size=size,
            colour=colour,
            char_spacing=char_spacing,
            alpha=alpha,
        )

    def text_centre(
        self,
        x: float,
        y: float,
        text: str,
        *,
        font: str = HELVETICA,
        size: float = 10.0,
        colour: str = "#000000",
        char_spacing: float = 0.0,
        alpha: float | None = None,
    ) -> float:
        """Draw with the string centred on ``x``."""
        width = self.text_width(text, font, size, char_spacing=char_spacing)
        return self.text(
            x - width / 2.0,
            y,
            text,
            font=font,
            size=size,
            colour=colour,
            char_spacing=char_spacing,
            alpha=alpha,
        )

    def wrap(
        self,
        text: str,
        max_width: float,
        font: str = HELVETICA,
        size: float = 10.0,
    ) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and self.text_width(candidate, font, size) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def paragraph(
        self,
        x: float,
        text: str,
        max_width: float,
        *,
        font: str = HELVETICA,
        size: float = 9.5,
        colour: str = "#404654",
        leading: float = 1.45,
    ) -> None:
        """Flow prose from the cursor, breaking pages between lines."""
        step = size * leading
        for line in self.wrap(text, max_width, font, size):
            self.ensure_space(step)
            self.text(x, self.y + size, line, font=font, size=size, colour=colour)
            self.y += step

    # -- shapes --------------------------------------------------------------

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        line_width: float = 1.0,
        radius: float = 0.0,
        alpha: float | None = None,
    ) -> None:
        """``y`` is the top edge; the rectangle extends downwards."""
        if fill is None and stroke is None:
            return
        self._open(alpha)
        if fill:
            self._emit(_fill_op(fill))
        if stroke:
            self._emit(_stroke_op(stroke), f"{_num(line_width)} w")
        if radius <= 0:
            self._emit(
                f"{_num(x)} {_num(self._ty(y + height))} "
                f"{_num(width)} {_num(height)} re"
            )
        else:
            self._rounded_path(x, y, width, height, radius)
        self._emit(self._paint_op(fill, stroke))
        self._close()

    def _rounded_path(
        self, x: float, y: float, width: float, height: float, radius: float
    ) -> None:
        r = min(radius, width / 2.0, height / 2.0)
        k = r * 0.5522847498307936
        left, right = x, x + width
        top, bottom = self._ty(y), self._ty(y + height)
        self._emit(
            f"{_num(left + r)} {_num(bottom)} m",
            f"{_num(right - r)} {_num(bottom)} l",
            f"{_num(right - r + k)} {_num(bottom)} {_num(right)} {_num(bottom + r - k)} "
            f"{_num(right)} {_num(bottom + r)} c",
            f"{_num(right)} {_num(top - r)} l",
            f"{_num(right)} {_num(top - r + k)} {_num(right - r + k)} {_num(top)} "
            f"{_num(right - r)} {_num(top)} c",
            f"{_num(left + r)} {_num(top)} l",
            f"{_num(left + r - k)} {_num(top)} {_num(left)} {_num(top - r + k)} "
            f"{_num(left)} {_num(top - r)} c",
            f"{_num(left)} {_num(bottom + r)} l",
            f"{_num(left)} {_num(bottom + r - k)} {_num(left + r - k)} {_num(bottom)} "
            f"{_num(left + r)} {_num(bottom)} c",
            "h",
        )

    @staticmethod
    def _paint_op(fill: str | None, stroke: str | None) -> str:
        if fill and stroke:
            return "B"
        return "f" if fill else "S"

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        colour: str = "#000000",
        line_width: float = 1.0,
        cap: int = 0,
        dash: Sequence[float] | None = None,
        alpha: float | None = None,
    ) -> None:
        self._open(alpha)
        self._emit(_stroke_op(colour), f"{_num(line_width)} w", f"{cap} J")
        if dash:
            pattern = " ".join(_num(v) for v in dash)
            self._emit(f"[{pattern}] 0 d")
        self._emit(
            f"{_num(x1)} {_num(self._ty(y1))} m",
            f"{_num(x2)} {_num(self._ty(y2))} l",
            "S",
        )
        self._close()

    def polyline(
        self,
        points: Sequence[tuple[float, float]],
        *,
        stroke: str | None = None,
        fill: str | None = None,
        line_width: float = 1.0,
        close: bool = False,
        cap: int = 1,
        join: int = 1,
        alpha: float | None = None,
    ) -> None:
        if len(points) < 2 or (stroke is None and fill is None):
            return
        self._open(alpha)
        if fill:
            self._emit(_fill_op(fill))
        if stroke:
            self._emit(_stroke_op(stroke), f"{_num(line_width)} w")
        self._emit(f"{cap} J", f"{join} j")
        head, *rest = points
        self._emit(f"{_num(head[0])} {_num(self._ty(head[1]))} m")
        for px, py in rest:
            self._emit(f"{_num(px)} {_num(self._ty(py))} l")
        if close or fill:
            self._emit("h")
        self._emit(self._paint_op(fill, stroke))
        self._close()

    def bezier(
        self,
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
        *,
        colour: str = "#000000",
        line_width: float = 1.0,
        cap: int = 1,
        alpha: float | None = None,
    ) -> None:
        self._open(alpha)
        self._emit(_stroke_op(colour), f"{_num(line_width)} w", f"{cap} J")
        self._emit(
            f"{_num(p0[0])} {_num(self._ty(p0[1]))} m",
            f"{_num(p1[0])} {_num(self._ty(p1[1]))} "
            f"{_num(p2[0])} {_num(self._ty(p2[1]))} "
            f"{_num(p3[0])} {_num(self._ty(p3[1]))} c",
            "S",
        )
        self._close()

    def circle_arc(
        self,
        cx: float,
        cy: float,
        radius: float,
        start_deg: float,
        end_deg: float,
        *,
        stroke: str | None = None,
        fill: str | None = None,
        line_width: float = 1.0,
        cap: int = 1,
        close: bool = False,
        alpha: float | None = None,
    ) -> None:
        """Arc from ``start_deg`` to ``end_deg``.

        0 degrees is due east and the angle increases counter-clockwise as seen
        on the printed page, which is the direction a reader means when they say
        "counter-clockwise" while looking at it.
        """
        if stroke is None and fill is None:
            return
        # PDF space puts y upwards, so mirroring the centre is enough to make
        # the maths below the ordinary textbook parametrisation.
        cy_pdf = self._ty(cy)
        a0 = math.radians(start_deg)
        a1 = math.radians(end_deg)
        span = a1 - a0
        if span == 0:
            return
        segments = max(1, math.ceil(abs(span) / (math.pi / 2)))
        step = span / segments

        def point(angle: float) -> tuple[float, float]:
            return (cx + radius * math.cos(angle), cy_pdf + radius * math.sin(angle))

        self._open(alpha)
        if fill:
            self._emit(_fill_op(fill))
        if stroke:
            self._emit(_stroke_op(stroke), f"{_num(line_width)} w")
        self._emit(f"{cap} J")
        sx, sy = point(a0)
        self._emit(f"{_num(sx)} {_num(sy)} m")
        k = 4.0 / 3.0 * math.tan(step / 4.0)
        for index in range(segments):
            s = a0 + step * index
            e = s + step
            x0, y0 = point(s)
            x3, y3 = point(e)
            c1 = (x0 - k * radius * math.sin(s), y0 + k * radius * math.cos(s))
            c2 = (x3 + k * radius * math.sin(e), y3 - k * radius * math.cos(e))
            self._emit(
                f"{_num(c1[0])} {_num(c1[1])} {_num(c2[0])} {_num(c2[1])} "
                f"{_num(x3)} {_num(y3)} c"
            )
        if close or fill:
            self._emit("h")
        self._emit(self._paint_op(fill, stroke))
        self._close()

    def circle(
        self,
        cx: float,
        cy: float,
        radius: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        line_width: float = 1.0,
        alpha: float | None = None,
    ) -> None:
        self.circle_arc(
            cx,
            cy,
            radius,
            0.0,
            360.0,
            fill=fill,
            stroke=stroke,
            line_width=line_width,
            close=True,
            alpha=alpha,
        )

    def set_alpha(self, alpha: float) -> None:
        """Set constant alpha for everything drawn until the next page.

        Prefer the per-call ``alpha=`` argument: it brackets itself in q/Q and
        cannot leak into the next thing drawn.
        """
        self._emit(f"/{self._alpha_gs(alpha)} gs")

    # -- tables --------------------------------------------------------------

    def table(
        self,
        x: float,
        columns: Sequence[Column],
        rows: Iterable[Sequence[str | Cell]],
        *,
        style: TableStyle | None = None,
        show_header: bool = True,
    ) -> None:
        """Draw a table from the cursor down, repeating the header after a break.

        No zebra striping and no header fill: a single hairline under each row
        is the storefront's convention, and it is the one that survives being
        printed on a monochrome office laser.
        """
        style = style or TableStyle()
        total = sum(c.width for c in columns)
        row_height = style.pad_v * 2 + style.body_size

        def draw_header() -> None:
            header_height = style.pad_v * 2 + style.header_size
            self.ensure_space(header_height + row_height)
            baseline = self.y + style.pad_v + style.header_size * 0.8
            offset = x
            for column in columns:
                self._aligned(
                    column,
                    offset,
                    baseline,
                    column.heading.upper(),
                    font=style.header_font,
                    size=style.header_size,
                    colour=style.header_colour,
                    pad_h=style.pad_h,
                    char_spacing=style.header_tracking,
                )
                offset += column.width
            self.y += header_height
            self.line(
                x,
                self.y,
                x + total,
                self.y,
                colour=style.header_rule_colour,
                line_width=style.rule_width,
            )

        if show_header:
            draw_header()

        for row in rows:
            if self.ensure_space(row_height) and show_header:
                draw_header()
            baseline = self.y + style.pad_v + style.body_size * 0.8
            offset = x
            for column, raw in zip(columns, row):
                cell = raw if isinstance(raw, Cell) else Cell(str(raw))
                bold = column.bold if cell.bold is None else cell.bold
                if column.mono:
                    font = style.mono_bold_font if bold else style.mono_font
                else:
                    font = style.body_bold_font if bold else style.body_font
                self._aligned(
                    column,
                    offset,
                    baseline,
                    cell.text,
                    font=font,
                    size=style.body_size,
                    colour=cell.colour or style.body_colour,
                    pad_h=style.pad_h,
                )
                offset += column.width
            self.y += row_height
            self.line(
                x,
                self.y,
                x + total,
                self.y,
                colour=style.rule_colour,
                line_width=style.rule_width,
            )

    def _aligned(
        self,
        column: Column,
        offset: float,
        baseline: float,
        text: str,
        *,
        font: str,
        size: float,
        colour: str,
        pad_h: float,
        char_spacing: float = 0.0,
    ) -> None:
        if column.align == "right":
            self.text_right(
                offset + column.width - pad_h,
                baseline,
                text,
                font=font,
                size=size,
                colour=colour,
                char_spacing=char_spacing,
            )
        elif column.align == "centre":
            self.text_centre(
                offset + column.width / 2.0,
                baseline,
                text,
                font=font,
                size=size,
                colour=colour,
                char_spacing=char_spacing,
            )
        else:
            self.text(
                offset + pad_h,
                baseline,
                text,
                font=font,
                size=size,
                colour=colour,
                char_spacing=char_spacing,
            )

    # -- serialisation -------------------------------------------------------

    def save(self, path: Path | str) -> Path:
        if not self._pages:
            self.new_page()
        if self.footer is not None and not self._footers_drawn:
            # Once only: a second save() would otherwise stamp a second footer
            # over the first, at the same coordinates, in the same ink.
            self._footers_drawn = True
            total = len(self._pages)
            saved = self._current
            for index in range(total):
                self._current = index
                self.footer(self, index + 1, total)
            self._current = saved

        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog_id = add(b"")  # 1
        pages_id = add(b"")  # 2

        font_ids = {name: add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /" + name.encode("ascii")
            + b" /Encoding /WinAnsiEncoding >>"
        ) for name in _FONT_RESOURCE}

        gs_ids: dict[str, int] = {}
        for value, name in self._alpha_names.items():
            body = f"<< /Type /ExtGState /ca {value} /CA {value} >>".encode("ascii")
            gs_ids[name] = add(body)

        font_entries = " ".join(
            f"/{_FONT_RESOURCE[name]} {font_ids[name]} 0 R" for name in _FONT_RESOURCE
        )
        gs_entries = " ".join(f"/{name} {oid} 0 R" for name, oid in gs_ids.items())
        resources = f"<< /Font << {font_entries} >>"
        if gs_entries:
            resources += f" /ExtGState << {gs_entries} >>"
        resources += " /ProcSet [/PDF /Text] >>"

        page_ids: list[int] = []
        for ops in self._pages:
            stream = zlib.compress("\n".join(ops).encode("latin-1"), 9)
            content_id = add(
                b"<< /Length "
                + str(len(stream)).encode("ascii")
                + b" /Filter /FlateDecode >>\nstream\n"
                + stream
                + b"\nendstream"
            )
            page_ids.append(
                add(
                    f"<< /Type /Page /Parent {pages_id} 0 R "
                    f"/MediaBox [0 0 {_num(self.width)} {_num(self.height)}] "
                    f"/Resources {resources} /Contents {content_id} 0 R >>".encode(
                        "latin-1"
                    )
                )
            )

        info_id = add(
            b"<< /Title (" + _escape(self.title)
            + b") /Author (" + _escape(self.author)
            + b") /Subject (" + _escape(self.subject)
            + b") /Creator (" + _escape(self.creator)
            + b") /Producer (" + _escape(self.creator) + b") >>"
        )

        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objects[pages_id - 1] = (
            f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("latin-1")
        )
        objects[catalog_id - 1] = (
            f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")
        )

        # The second line is a comment of high bytes. It is what makes a naive
        # transfer agent treat the file as binary instead of mangling newlines.
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = []
        for index, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode("ascii")
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R "
            f"/Info {info_id} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
        ).encode("ascii")

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(out))
        return target


# --------------------------------------------------------------------- mark --

# The wordmark, transcribed from assets/logo-wordmark.svg. Drawn rather than
# embedded because a bitmap at this size looks soft on a 600dpi laser and a
# 306x64 SVG cannot be pasted into a PDF without a converter.
LOGO_VIEWBOX = (306.0, 64.0)
_LOGO_GREEN = "#16A34A"
_LOGO_INK = "#0B1A12"


@dataclass(frozen=True)
class _Candle:
    x: float
    wick_top: float
    wick_bottom: float
    body_top: float
    body_bottom: float
    body_alpha: float


# The opacity ladder is the whole depth model: one green, three weights of it,
# rising left to right like the prices they stand for. Flattening it to a single
# opacity makes the mark read as a bar chart drawn by accident.
_CANDLES: tuple[_Candle, ...] = (
    _Candle(21.0, 27.0, 41.0, 33.5, 38.5, 0.45),
    _Candle(32.0, 21.0, 46.0, 27.0, 42.0, 0.75),
    _Candle(43.0, 15.0, 37.0, 20.0, 32.0, 1.0),
)


def draw_logo(
    canvas: PdfCanvas,
    x: float,
    y: float,
    *,
    scale: float = 1.0,
    with_wordmark: bool = True,
) -> tuple[float, float]:
    """Draw the KX15 mark with its top-left corner at (x, y).

    Returns the (width, height) consumed. ``with_wordmark=False`` draws the dial
    and candles alone, which is the 64x64 logo.
    """

    def px(vx: float) -> float:
        return x + vx * scale

    def py(vy: float) -> float:
        return y + vy * scale

    # A 270-degree dial open at the top right. The missing quadrant is the
    # fifteen minutes, and it is the only asymmetry in the shape, so it is what
    # the eye keeps at small sizes - closing it would produce a generic ring.
    canvas.circle_arc(
        px(32.0),
        py(32.0),
        25.0 * scale,
        90.0,
        360.0,
        stroke=_LOGO_GREEN,
        line_width=5.0 * scale,
        cap=1,
        alpha=0.9,
    )
    for candle in _CANDLES:
        canvas.line(
            px(candle.x),
            py(candle.wick_top),
            px(candle.x),
            py(candle.wick_bottom),
            colour=_LOGO_GREEN,
            line_width=3.5 * scale,
            cap=1,
            alpha=0.5,
        )
    for candle in _CANDLES:
        canvas.line(
            px(candle.x),
            py(candle.body_top),
            px(candle.x),
            py(candle.body_bottom),
            colour=_LOGO_GREEN,
            line_width=9.0 * scale,
            cap=1,
            alpha=candle.body_alpha,
        )

    if not with_wordmark:
        return (64.0 * scale, 64.0 * scale)

    canvas.text(
        px(76.0),
        py(42.0),
        "KX15",
        font=HELVETICA_BOLD,
        size=30.0 * scale,
        colour=_LOGO_INK,
    )
    canvas.text(
        px(172.0),
        py(41.0),
        "15-MIN CRYPTO",
        font=HELVETICA_BOLD,
        size=13.0 * scale,
        colour=_LOGO_GREEN,
        char_spacing=1.4 * scale,
        alpha=0.9,
    )
    return (LOGO_VIEWBOX[0] * scale, LOGO_VIEWBOX[1] * scale)


__all__ = [
    "COURIER",
    "COURIER_BOLD",
    "HELVETICA",
    "HELVETICA_BOLD",
    "Cell",
    "Column",
    "PdfCanvas",
    "TableStyle",
    "draw_logo",
    "encode_text",
    "text_width",
]
