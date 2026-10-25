# Native intraday evidence contract

P9 adds separately typed vendor aggregates and a comparison audit. These are
research inputs, not replacements for the strict minute-based campaign. The
existing expression, calendar alignment, missing-minute policy and suspension
remain unchanged.

## Receipts and comparison

`native-intraday-data/1` records explicit SIP/all-adjusted provenance, the requested
15m or 1h timeframe, captured native timestamps, UTC collection time, date range,
ordered universe and a payload checksum. The receipt preserves extended-hours
bars; it does not assert exchange-session alignment. Missing bars stay absent.
Receipt validation checks complete bar duration by collection time, duplicate
keys, timestamp/date coverage, finite OHLCV, and immutable output creation. Native
receipts cannot be loaded by the minute-receipt loader.

The audit independently constructs closed clock-aligned intervals wholly within
the XNYS regular session. It compares each native OHLCV with first open, maximum
high, minimum low, last close and summed volume of available minutes on that
same half-open interval. It reports complete and sparse constituent coverage
separately, missing counterparts, per-field exact counts/errors, and bounded
violation examples. It never forward-fills or changes the strict aggregator.

Declared absolute tolerances are 0.0100000001 for prices (two separately
cent-rounded observations) and 0.000001 for numerical volume error. Exact counts
are also retained; P9's real comparison was exact for every compared field, so
these tolerances were not needed to accept any discrepancy.

An hourly clock interval typically covers 10:00–11:00 through 15:00–16:00 ET.
The 09:00–10:00 interval crosses the regular-session open and is excluded.
The existing strict hourly grid covers 09:30–10:30 through 14:30–15:30 instead.
These series must not be relabelled or substituted for each other. Early closes
and DST use the exchange schedule. A 15m receipt additionally reports whether
all four native components are present for each existing session-open hourly
interval; this is coverage evidence, not a new inference implementation.

Both APIs share the same provider. Agreement does not independently verify the
underlying market record, identify every missing minute's cause, or establish
profitability. The provider describes higher intervals as aggregation of emitted
minute bars; minutes with no price-updating trades can be absent. See the
[Alpaca aggregation documentation](https://docs.alpaca.markets/us/docs/market-data-faq).

## Commands

Run from `amat`. Choose unused output paths for immutable receipts and audits.

```sh
uv run --locked autoresearch patterns shadow-fetch-native \
  --symbols SPY QQQ IWM EFA EEM IEF GLD SLV DBC USO UUP \
  --start 2026-06-01 --end 2026-09-28 --timeframe 15m \
  --output data/research/my-native/15m.json

uv run --locked autoresearch patterns shadow-compare-native \
  --native-data data/research/my-native/15m.json \
  --intraday-data data/research/genetic-patterns-p8-2026-09-29/minutes.json \
  --intraday-calibration-end 2026-09-11 \
  --as-of 2026-09-28T20:00:00+00:00 \
  --output outputs/my-native/15m-comparison.json
```

Use `--timeframe 1h` for a separate native hourly receipt. Comparisons require
identical ordered universe, date coverage, provider, feed and adjustment fields.
Timestamp cadence must match the clock grid; unexpected alignment is rejected.
The audit scope is the captured range and effective cutoff, not a claim of
freshness beyond source coverage. Outputs include both source-file hashes.

See [p9-verification.md](p9-verification.md) for the real experiment and next
integration boundary.
