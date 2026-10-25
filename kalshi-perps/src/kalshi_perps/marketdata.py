"""Read-only Kalshi market snapshots, native candles and funding; no strategy dependency."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

from .domain import HOUR, Instrument, book, dec


class Feed:
    """Read-only REST snapshots. No fabricated ticks or synthetic warm-up candles."""

    def __init__(self, transport, instruments, *, include_leverage=False):
        self.transport, self.instruments = transport, instruments
        self.include_leverage = include_leverage

    def snapshot(self, max_age_ms):
        # A mark may age while its separate book request is in flight. Recollect
        # the entire snapshot once; each attempt must satisfy the same limits.
        first_error = None
        for attempt in range(2):
            try:
                return self._snapshot(max_age_ms)
            except ValueError as exc:
                if attempt:
                    raise first_error from exc
                if str(exc) not in {
                    "stale liquidation mark",
                    "stale liquidation mark at snapshot completion",
                    "stale order book at snapshot completion",
                    "market snapshot exceeded freshness limit",
                }:
                    raise
                first_error = exc

    def _snapshot(self, max_age_ms):
        started = self.transport.clock()
        quotes = {}
        risk_parameters = (
            self.transport.request("GET", "/risk_parameters") if self.include_leverage else None
        )

        def fetch(item):
            ticker, instrument = item
            row = self.transport.request("GET", "/markets/" + quote(ticker, safe=""))["market"]

            if Instrument.parse(row).identity() != instrument.identity():
                raise ValueError("market metadata changed; revalidate registry before resuming")
            mark = row["liquidation_mark_price"]
            if not 0 <= self.transport.clock() - mark["ts_ms"] <= max_age_ms:
                raise ValueError("stale liquidation mark")
            levels = self.transport.request(
                "GET", "/markets/" + quote(ticker, safe="") + "/orderbook", params={"depth": 20}
            )["orderbook"]
            normalized = book(levels, instrument)
            result = dict(
                **{side: [[str(p), str(q)] for p, q in rows] for side, rows in normalized.items()},
                mark=mark["price"],
                mark_ts_ms=mark["ts_ms"],
                book_received_ms=self.transport.clock(),
            )
            if self.include_leverage:
                result["leverage"] = dict(
                    long=row["long_leverage_estimates"],
                    short=row["short_leverage_estimates"],
                    initial_margin_multiplier=str(
                        risk_parameters["initial_margin_multiplier"][ticker]
                    ),
                    liquidation_threshold=str(
                        risk_parameters["liquidation_margin_ratio_threshold"]
                    ),
                )
            return ticker, result

        # Thread-safe HTTP client; transport serializes request starts, not responses.
        # All reads remain paced and no stale partial snapshot is published.
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(self.instruments)))) as pool:
            quotes = dict(pool.map(fetch, self.instruments.items()))
        now = self.transport.clock()
        if now - started > max_age_ms:
            raise ValueError("market snapshot exceeded freshness limit")
        for result in quotes.values():
            if not 0 <= now - result["mark_ts_ms"] <= max_age_ms:
                raise ValueError("stale liquidation mark at snapshot completion")
            if not 0 <= now - result["book_received_ms"] <= max_age_ms:
                raise ValueError("stale order book at snapshot completion")
        return {
            "event_id": f"poll-{now}",
            "timestamp_ms": now,
            "quotes": quotes,
            "collection_duration_ms": now - started,
        }

    def candles(self, boundary, previous=None):
        result = {}
        for ticker in self.instruments:
            records = list((previous or {}).get(ticker, []))
            first = records[-1]["timestamp_ms"] + HOUR if records else boundary - 36 * 24 * HOUR
            # Bounded weekly chunks avoid a burst of dozens of warm-up requests.
            for start in range(first, boundary, 7 * 24 * HOUR):
                stop = min(start + 7 * 24 * HOUR, boundary)
                rows = self.transport.request(
                    "GET",
                    f"/markets/{ticker}/candlesticks",
                    params={
                        "start_ts": start // 1000,
                        "end_ts": stop // 1000,
                        "period_interval": 60,
                    },
                )["candlesticks"]
                for row in rows:
                    raw_end = int(row["end_period_ts"])
                    if raw_end % 3600 == 0:
                        end = raw_end * 1000
                    elif raw_end % 3600 == 3599:
                        end = (raw_end + 1) * 1000
                    else:
                        raise ValueError("unexpected hourly candle boundary")
                    if not start <= end - HOUR < stop:
                        continue
                    p = row["price"]
                    if any(p[k] is None for k in ("open", "high", "low", "close")):
                        # Missing trades block contiguous strategy input rather than manufacturing prices.
                        continue
                    records.append(
                        dict(
                            timestamp_ms=end - HOUR,
                            **{k: p[k] for k in ("open", "high", "low", "close")},
                            tradable=int(dec(row["volume"]) > 0),
                        )
                    )
            result[ticker] = sorted(records, key=lambda r: r["timestamp_ms"])
        return result

    def funding(self, start_ms, end_ms):
        result = []
        for ticker in self.instruments:
            rows = self.transport.request(
                "GET",
                "/funding_rates/historical",
                params={
                    "ticker": ticker,
                    "start_ts": start_ms // 1000,
                    "end_ts": end_ms // 1000,
                },
            )["funding_rates"]
            for row in rows:
                if row["market_ticker"] != ticker:
                    raise ValueError("funding response instrument mismatch")
                when = int(
                    datetime.fromisoformat(row["funding_time"].replace("Z", "+00:00")).timestamp()
                    * 1000
                )
                if start_ms < when <= end_ms:
                    result.append(
                        {
                            "event_id": f"native:{ticker}:{when}",
                            "ticker": ticker,
                            "timestamp_ms": when,
                            "rate": str(row["funding_rate"]),
                            "mark": row["mark_price"],
                        }
                    )
        return sorted(result, key=lambda r: (r["timestamp_ms"], r["ticker"]))
