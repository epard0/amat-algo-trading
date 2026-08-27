"""Rebuild one-minute candles from Kraken's public trade tape.

The regime features are a pure function of forty-five one-minute candles. They
need no Kalshi market, no open position, and no settled outcome — which means
the entire training set for the regime mixture can be reconstructed from price
history alone, rather than waiting for the bot to observe it live.

That matters because the live decision log is a poor source. Rows arrive every
few seconds but each one re-reads the same forty-five-candle window, so eleven
days of it contained seventy-one independent observations. Ninety days of
history contains about 2,880 per asset, and arrives in an hour.

**Why not the OHLC endpoint.** `/0/public/OHLC` at `interval=1` returns 720
rows — twelve hours — and `since` does not page backwards past that window;
asking for thirty days ago returns the same twelve hours. Measured, not
assumed. `/0/public/Trades` does page, on a nanosecond cursor, a thousand
trades at a time, so candles are aggregated here rather than fetched.

Same venue as the bot prices off, deliberately. Two of the fourteen regime
features are raw volatility, and those are the ones whose scale would drift if
this were rebuilt from a different exchange's tape.

    uv run python3 research/backfill_candles.py --days 90
    uv run python3 research/backfill_candles.py --asset BTC --days 30
    uv run python3 research/backfill_candles.py --verify        # against OHLC

Trades are public data and this reads nothing else: no key is loaded, no
account is touched, and there is no order path in this file.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import KRAKEN_PAIR_BY_ASSET  # noqa: E402

BASE_URL = "https://api.kraken.com"
TRADES_PATH = "/0/public/Trades"
OHLC_PATH = "/0/public/OHLC"

# Kraken's public tier. One request a second is comfortably inside it and this
# is a background job, so there is no reason to push.
REQUEST_INTERVAL_SECONDS = 1.1
MAX_RETRIES = 5

DEFAULT_OUT = PROJECT_ROOT / "data" / "candles"


class BackfillError(RuntimeError):
    """The tape could not be read. Never raised into a trading path."""


@dataclass(frozen=True)
class Candle:
    """A finished one-minute bar. Money stays Decimal, as everywhere else."""

    minute: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "t": self.minute,
            "o": str(self.open),
            "h": str(self.high),
            "l": str(self.low),
            "c": str(self.close),
            "v": str(self.volume),
            "n": self.trades,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Candle":
        return Candle(
            minute=int(raw["t"]),
            open=Decimal(raw["o"]),
            high=Decimal(raw["h"]),
            low=Decimal(raw["l"]),
            close=Decimal(raw["c"]),
            volume=Decimal(raw["v"]),
            trades=int(raw["n"]),
        )


def _get(path: str, params: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    """One Kraken call, with backoff on the errors that are worth retrying."""
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"{BASE_URL}{path}?{query}"
    delay = 2.0
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if attempt == MAX_RETRIES - 1:
                raise BackfillError(f"{url}: {exc}") from exc
            time.sleep(delay)
            delay *= 2
            continue
        errors = payload.get("error") or []
        if errors:
            # Rate limiting is transient; a bad pair name is not.
            if any("Rate limit" in str(e) for e in errors) and attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise BackfillError(f"Kraken rejected {path}: {errors}")
        return payload["result"]
    raise BackfillError(f"{url}: exhausted retries")


def _result_rows(result: dict[str, Any]) -> tuple[list[Any], str]:
    """Kraken keys the payload by its own pair name, which is not the one asked
    for — XBTUSD comes back as XXBTZUSD. Take whichever key is not `last`."""
    keys = [key for key in result if key != "last"]
    if not keys:
        return ([], str(result.get("last", "")))
    return (result[keys[0]], str(result.get("last", "")))


# Emitted between pages when `count_pages` is set, so the caller can checkpoint
# on a page boundary without this function needing to know what a checkpoint is.
PAGE_BREAK = object()


def fetch_trades(
    pair: str, since_ns: int, *, pages: int | None = None, verbose: bool = True,
    count_pages: bool = False, stop_at: float | None = None,
) -> Iterator[Any]:
    """Stream (price, volume, epoch_seconds) forward from ``since_ns``.

    Terminates when Kraken stops advancing the cursor, which is how the tape
    signals that it has caught up with the present. A page that returns no
    trades ends it too; a cursor that repeats would otherwise loop forever.
    """
    cursor = str(since_ns)
    seen_cursors: set[str] = set()
    page = 0
    while True:
        result = _get(TRADES_PATH, {"pair": pair, "since": cursor})
        rows, last = _result_rows(result)
        if not rows:
            return
        reached = False
        for row in rows:
            # [price, volume, time, buy/sell, market/limit, misc, trade_id]
            moment = float(row[2])
            if stop_at is not None and moment >= stop_at:
                reached = True
                break
            yield (Decimal(str(row[0])), Decimal(str(row[1])), moment)
        page += 1
        if count_pages:
            yield PAGE_BREAK
        if verbose and page % 25 == 0:
            newest = datetime.fromtimestamp(float(rows[-1][2]), timezone.utc)
            print(f"    {pair}: {page} pages, at {newest:%Y-%m-%d %H:%M}", flush=True)
        if reached:
            if count_pages:
                yield PAGE_BREAK
            return
        if pages is not None and page >= pages:
            return
        if not last or last == cursor or last in seen_cursors:
            return
        seen_cursors.add(cursor)
        cursor = last
        time.sleep(REQUEST_INTERVAL_SECONDS)


def aggregate(
    trades: Iterator[tuple[Decimal, Decimal, float]]
) -> list[Candle]:
    """Fold a trade stream into one-minute bars.

    Kraken buckets a candle by the minute its trades fall in and stamps it with
    that minute's start, so the arithmetic here is a floor division and not a
    judgement call. `--verify` checks that claim against the exchange's own
    candles rather than trusting it.
    """
    buckets: dict[int, list[tuple[Decimal, Decimal]]] = defaultdict(list)
    for price, volume, moment in trades:
        buckets[int(moment) // 60 * 60].append((price, volume))

    candles: list[Candle] = []
    for minute in sorted(buckets):
        fills = buckets[minute]
        prices = [price for price, _ in fills]
        candles.append(
            Candle(
                minute=minute,
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                volume=sum((volume for _, volume in fills), Decimal("0")),
                trades=len(fills),
            )
        )
    return candles


def write_candles(path: Path, candles: Sequence[Candle]) -> int:
    """Merge into whatever is already there, newest wins, one bar per minute."""
    existing: dict[int, Candle] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                candle = Candle.from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            existing[candle.minute] = candle
    for candle in candles:
        existing[candle.minute] = candle

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for minute in sorted(existing):
            handle.write(json.dumps(existing[minute].as_dict(), separators=(",", ":")) + "\n")
    return len(existing)


def read_candles(path: Path) -> list[Candle]:
    if not path.is_file():
        return []
    out: list[Candle] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(Candle.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return out


def audit(candles: Sequence[Candle]) -> dict[str, Any]:
    """What is wrong with this series, stated rather than assumed.

    The loader forward-fills tradeless minutes, which is right for a thin pair
    but wrong if the gap is an outage: a six-hour hole becomes six hours of a
    flat line that the volatility features will read as dead calm. So gaps are
    counted and the largest is reported, and anything that cannot be a price
    is counted separately.
    """
    if not candles:
        return {"bars": 0}
    minutes = [c.minute for c in candles]
    span = (minutes[-1] - minutes[0]) // 60 + 1
    gaps = [(b - a) // 60 - 1 for a, b in zip(minutes, minutes[1:]) if b - a > 60]

    bad_price = sum(1 for c in candles if c.close <= 0 or c.high < c.low)
    inconsistent = sum(
        1 for c in candles
        if not (c.low <= c.open <= c.high and c.low <= c.close <= c.high)
    )
    # A one-minute move of more than 20% on a major pair is a bad print far more
    # often than it is a market event, and either way it deserves a look.
    jumps = 0
    for a, b in zip(candles, candles[1:]):
        if a.close > 0 and abs(b.close - a.close) / a.close > Decimal("0.2"):
            jumps += 1
    return {
        "bars": len(candles),
        "span_minutes": span,
        "coverage": len(candles) / span if span else 0.0,
        "gaps": len(gaps),
        "largest_gap_minutes": max(gaps) if gaps else 0,
        "minutes_missing": sum(gaps),
        "duplicate_minutes": len(minutes) - len(set(minutes)),
        "non_positive_or_inverted": bad_price,
        "ohlc_inconsistent": inconsistent,
        "jumps_over_20pct": jumps,
    }


def verify_against_ohlc(pair: str, candles: Sequence[Candle]) -> dict[str, Any]:
    """Diff the rebuilt bars against Kraken's own, over the window both cover.

    The whole method rests on aggregating a trade tape the same way the exchange
    does. That is an assumption until it is checked, and this is the check: any
    systematic disagreement in close price shows up here rather than as a
    quietly wrong feature vector three steps downstream.
    """
    result = _get(OHLC_PATH, {"pair": pair, "interval": 1})
    rows, _ = _result_rows(result)
    theirs = {int(row[0]): Decimal(str(row[4])) for row in rows}   # close
    ours = {candle.minute: candle.close for candle in candles}
    shared = sorted(set(theirs) & set(ours))
    # The final bar is the minute in progress on their side and a partial
    # aggregation on ours; comparing it measures the clock, not the method.
    shared = [minute for minute in shared if minute != max(theirs)]

    mismatches = [m for m in shared if theirs[m] != ours[m]]
    worst = Decimal("0")
    for minute in mismatches:
        gap = abs(theirs[minute] - ours[minute]) / theirs[minute]
        worst = max(worst, gap)
    return {
        "compared": len(shared),
        "mismatched": len(mismatches),
        "worst_relative_gap": float(worst),
        "exact_match_rate": (len(shared) - len(mismatches)) / len(shared) if shared else 0.0,
    }


def backfill(
    asset: str, *, days: float, out_dir: Path, verify: bool, verbose: bool = True,
    checkpoint_pages: int = 40, resume: bool = True,
    start: datetime | None = None,
) -> dict[str, Any]:
    pair = KRAKEN_PAIR_BY_ASSET[asset]
    path = out_dir / f"{asset}_1m.jsonl"
    since = (start if start is not None
             else datetime.now(timezone.utc) - timedelta(days=days))

    # Resume from the newest bar already stored. A deep fetch runs for hours;
    # losing it to a dropped connection and restarting from the beginning is
    # the failure this avoids, and it makes extending 30 days to 90 cheap.
    # Set when only a bounded slice is missing, so the fetch stops at the edge
    # of what is already stored instead of re-reading it.
    stop_at: float | None = None
    existing = read_candles(path) if resume else []
    if existing:
        oldest = datetime.fromtimestamp(existing[0].minute, timezone.utc)
        newest = datetime.fromtimestamp(existing[-1].minute, timezone.utc)
        # Only skip ahead when the store already reaches back to the requested
        # start. Resuming from the newest bar regardless looks right and is
        # badly wrong: a store holding two recent days, asked for thirty, would
        # fetch nothing and silently leave the twenty-eight-day hole in place.
        if oldest <= since < newest:
            print(f"  resuming from {newest:%Y-%m-%d %H:%M} "
                  f"({len(existing):,} bars already stored)")
            since = newest
        elif since < oldest:
            # Extending backwards. Fetch only [since, oldest) and stop there;
            # everything from `oldest` on is already on disk, and re-reading it
            # would cost hours for bars that would merge to themselves.
            print(f"  extending backwards: fetching {since:%Y-%m-%d} to "
                  f"{oldest:%Y-%m-%d} ({len(existing):,} bars already stored "
                  f"from {oldest:%Y-%m-%d})")
            stop_at = oldest.timestamp()
    since_ns = int(since.timestamp()) * 10**9
    print(f"\n{asset} ({pair}): from {since:%Y-%m-%d %H:%M} UTC")

    started = time.monotonic()
    # Flushed on page boundaries rather than at the end, so an interrupted
    # fetch keeps everything it had already read.
    candles: list[Candle] = []
    batch: list[tuple[Decimal, Decimal, float]] = []
    pages = 0
    for item in fetch_trades(pair, since_ns, verbose=verbose, count_pages=True,
                             stop_at=stop_at):
        if item is PAGE_BREAK:
            pages += 1
            if pages % checkpoint_pages == 0 and batch:
                chunk = aggregate(iter(batch))
                write_candles(path, chunk)
                candles.extend(chunk)
                batch = []
            continue
        batch.append(item)
    if batch:
        chunk = aggregate(iter(batch))
        write_candles(path, chunk)
        candles.extend(chunk)
    if not candles:
        if existing:
            candles = existing
        else:
            raise BackfillError(f"{asset}: no trades returned")

    # Reported over everything on disk, not just this run's increment. A resume
    # that adds one bar to eight hundred must not report a one-bar series.
    stored = read_candles(path)
    total = len(stored)
    first = datetime.fromtimestamp(stored[0].minute, timezone.utc)
    last = datetime.fromtimestamp(stored[-1].minute, timezone.utc)
    span_minutes = (stored[-1].minute - stored[0].minute) / 60 + 1
    summary: dict[str, Any] = {
        "asset": asset,
        "pair": pair,
        "fetched": len(candles),
        "on_disk": total,
        "first": first.isoformat(),
        "last": last.isoformat(),
        "coverage": total / span_minutes if span_minutes else 0.0,
        "independent_windows": int(span_minutes // 45),
        "seconds": round(time.monotonic() - started, 1),
    }
    print(
        f"  {total:,} candles (+{len(candles):,} new)  "
        f"{first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M}  "
        f"coverage {summary['coverage']:.0%}  "
        f"~{summary['independent_windows']:,} independent windows  "
        f"({summary['seconds']:.0f}s)"
    )
    summary["audit"] = audit(stored)
    quality = summary["audit"]
    flags = []
    if quality["duplicate_minutes"]:
        flags.append(f"{quality['duplicate_minutes']} duplicate minutes")
    if quality["non_positive_or_inverted"] or quality["ohlc_inconsistent"]:
        flags.append(f"{quality['non_positive_or_inverted'] + quality['ohlc_inconsistent']} malformed bars")
    if quality["jumps_over_20pct"]:
        flags.append(f"{quality['jumps_over_20pct']} one-minute moves over 20%")
    print(
        f"  audit: {quality['gaps']:,} gaps, largest {quality['largest_gap_minutes']:,} min, "
        f"{quality['minutes_missing']:,} minutes missing"
        + (f"  [{'; '.join(flags)}]" if flags else "  [no malformed bars]")
    )
    if verify:
        summary["verification"] = verify_against_ohlc(pair, stored)
        check = summary["verification"]
        verdict = "OK" if check["exact_match_rate"] >= 0.99 else "MISMATCH"
        print(
            f"  verify vs Kraken OHLC: {check['compared']} bars compared, "
            f"{check['exact_match_rate']:.1%} exact  [{verdict}]"
        )
    print(f"  -> {path}")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild one-minute candles from Kraken's public trade tape.",
    )
    parser.add_argument("--asset", choices=sorted(KRAKEN_PAIR_BY_ASSET),
                        help="one asset; default is all of them")
    parser.add_argument("--days", type=float, default=90.0)
    parser.add_argument("--since", type=str,
                        help="explicit start date, YYYY-MM-DD (overrides --days)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verify", action="store_true",
                        help="diff the rebuilt bars against Kraken's own OHLC")
    parser.add_argument("--no-verify", dest="verify", action="store_false")
    parser.add_argument("--no-resume", dest="resume", action="store_false",
                        help="ignore what is already on disk and refetch")
    parser.set_defaults(resume=True)
    parser.set_defaults(verify=True)
    args = parser.parse_args(argv)

    start = None
    if args.since:
        try:
            start = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            parser.error(f"--since must be YYYY-MM-DD, not {args.since!r}")

    assets = [args.asset] if args.asset else list(KRAKEN_PAIR_BY_ASSET)
    summaries = []
    failures = 0
    for asset in assets:
        try:
            summaries.append(backfill(asset, days=args.days, out_dir=args.out,
                                      verify=args.verify, resume=args.resume,
                                      start=start))
        except BackfillError as exc:
            print(f"  {asset}: FAILED — {exc}", file=sys.stderr)
            failures += 1

    if summaries:
        print(f"\n{'=' * 62}")
        windows = min(s["independent_windows"] for s in summaries)
        print(f"{len(summaries)} asset(s) written to {args.out}")
        print(f"smallest independent-window count: {windows:,}")
        if windows < 400:
            print("  below the fitter's --min-samples floor; fetch more days")
        else:
            print("  enough for a per-asset fit:")
            print("    uv run --extra research python3 research/fit_regime_model.py \\")
            print(f"        --candles {args.out} --per-asset")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
