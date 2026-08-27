# The mark

<img src="../assets/logo-wordmark.svg" alt="KX15" width="300">

## Files

| File | Use |
| --- | --- |
| `assets/logo.svg` | The mark. Anywhere above about 24px |
| `assets/favicon.svg` | The 16px cut. Browser tabs, small chrome |
| `assets/logo-wordmark.svg` | Horizontal lockup for READMEs and email headers |

`storefront/web/favicon.svg` and `monitoring/webui/favicon.svg` are copies of
`assets/favicon.svg`; a test fails if they drift, because nobody notices a
favicon slowly going stale.

## What it is

A dial with its first quarter missing, holding three candles. The gap is the
fifteen minutes the bot trades, and it is the only asymmetry in an otherwise
regular shape — which is what the eye keeps at small sizes. A closed ring would
read as a generic circle.

The three candles close higher each time, so the mark reads as a price chart at
a glance. Below roughly 20px the wicks vanish and the bodies survive as a
rising step; below that, use `favicon.svg`, which drops to a single candle and
a heavier ring.

## Colour

One green, `#16a34a`. Depth comes from opacity rather than a second colour,
because the mark ships through `<img>` tags in places that cannot theme it — a
README, a favicon, an email — and any second colour would have to work on both
a white page and a near-black one.

Green is both the brand colour and the profit colour here, which means **red is
reserved for losses and errors**. Nothing decorative is ever red.

## Theming

Inline the SVG when the surrounding page has a theme. The nav marks in the
storefront and the dashboard are inlined and take `currentColor`, so the
dashboard's live-mode rule recolours the mark without a second asset.

`logo-wordmark.svg` carries a `prefers-color-scheme` block for the wordmark
text, which works even inside an `<img>` because a standalone SVG evaluates its
own media queries. It follows the *operating system* scheme, not the background
it is placed on, so a light card inside a dark-scheme page will render the name
light-on-light. If that matters, inline it.

## Geometry

64-unit grid, 4-unit strokes, so edges land on whole device pixels at 16, 32 and
64. Clear space is 12 units — half the dial's radius.

The wordmark is live text in a system font stack rather than outlines: the file
stays editable and tiny at the cost of a few pixels of difference across
platforms. Convert the text to paths if exact letterforms ever matter.
