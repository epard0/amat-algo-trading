"""The storefront's visual identity, expressed as numbers a PDF can use.

Every hex below was resolved from ``storefront/web/theme.css`` and
``storefront/emails/license.html`` rather than re-picked by eye, so a report
printed on paper and the sales page seen in a browser are recognisably the same
product. The light palette is the canonical one: a PDF has no colour scheme to
follow, and paper is white.

Colour discipline is inherited verbatim from ``theme.css``: green is both the
brand and the profit colour, which is exactly why red is reserved for losses and
errors. Nothing decorative may be either. That rule is what makes a glance at a
page trustworthy - if something is green it is money the bot made, not a section
divider somebody liked the look of.
"""

from __future__ import annotations

from decimal import Decimal

# ------------------------------------------------------------------ palette --

PAGE_BG = "#FFFFFF"
SURFACE = "#F9FBFA"
EMAIL_TONE = "#F4F7F5"
CARD_BORDER = "#DFE6E1"
HAIRLINE = "#EBEFED"
BORDER_STRONG = "#D7E0DB"

BODY = "#404654"
MUTED = "#69748C"
HEADING = "#2E333D"
TITLE = "#1F2229"

# The UI green and the logo green are genuinely different values and both are
# load-bearing: the mark ships as a fixed asset at #16A34A, while the interface
# derives everything from hsl(145 78% 30%). Using one for the other would make
# the logo look faded beside its own headings.
GREEN = "#118843"
LOGO_GREEN = "#16A34A"
LOSS = "#B8331E"

WARNING_TEXT = "#92610A"
WARNING_BG = "#FDF7E7"
WARNING_BORDER = "#E2C98A"

TERMINAL_BG = "#09110D"
TERMINAL_FG = "#E8EEEB"

WORDMARK_INK = "#0B1A12"

# --------------------------------------------------------------- typography --

FONT_BODY = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
# Every number in a table is Courier. Proportional digits in Helvetica are
# tabular, but the separators are not, so a column of "$1,234.56" against
# "-$18.13" drifts; a monospaced face makes the decimal points line up without
# measuring anything.
FONT_MONO = "Courier"
FONT_MONO_BOLD = "Courier-Bold"

SIZE_TITLE = 26.0
SIZE_SECTION = 14.0
SIZE_SUBHEAD = 10.5
SIZE_BODY = 9.5
SIZE_TABLE = 9.0
SIZE_LABEL = 7.5
SIZE_FOOTER = 7.5

LABEL_TRACKING = 0.9
LEADING = 1.45

# ------------------------------------------------------------------- layout --

# Letter portrait rather than A4: the bot's operator, its exchange and its
# accountant are all American, and a report printed on the wrong stock loses a
# quarter-inch off the bottom rule.
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
MARGIN = 54.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

CARD_RADIUS = 16.0
CARD_PAD = 16.0
BORDER_WIDTH = 1.0

# From .kx-kv: .55rem vertical / .35rem horizontal at a 16px root.
ROW_PAD_V = 8.8
CELL_PAD_H = 5.6

SECTION_GAP = 20.0
BLOCK_GAP = 11.0

# ------------------------------------------------------------------- money ---

EM_DASH = "—"


def money(value: Decimal | None, *, signed: bool = False) -> str:
    """Render money the way the dashboard does: the minus outside the sign.

    "-$18.13" reads as a debt; "$-18.13" reads as a typo. ``signed`` adds the
    leading "+" for gains, which is only wanted where a figure is a change
    rather than a level.
    """
    if value is None:
        return EM_DASH
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,.2f}"


def percent(value: Decimal | None, *, digits: int = 1, signed: bool = False) -> str:
    if value is None:
        return EM_DASH
    scaled = value * Decimal(100)
    sign = "+" if signed and scaled > 0 else ""
    return f"{sign}{scaled:.{digits}f}%"


def number(value: Decimal | None, *, digits: int = 0) -> str:
    if value is None:
        return EM_DASH
    return f"{value:,.{digits}f}"


def pnl_colour(value: Decimal | None) -> str:
    """The colour a P&L figure is allowed to be.

    Zero is deliberately not green. A flat book has made nothing, and painting
    it the profit colour is the one lie this palette could tell without anybody
    noticing.
    """
    if value is None or value == 0:
        return BODY
    return GREEN if value > 0 else LOSS
