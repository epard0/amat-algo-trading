#!/usr/bin/env python3
"""Fetch the authoritative settlement for every market the bot ever evaluated.

The decision log records 558 evaluations that computed a full prediction, and
only 46 of them became trades. The other ~512 are the counterfactuals — a gate
refused, and nobody ever asked whether refusing was right. They are the majority
of the signal available for tuning a gate, and they are unusable without a label.

Kalshi knows. `get_market` returns the settled result for any closed market, it
is a public GET, and one call per ticker is cheap. This caches them so the
dataset build is offline and repeatable.

    uv run python research/settlements.py               # fill gaps
    uv run python research/settlements.py --refresh     # re-ask about everything

Read-only. No key is required for market data, `validate_live` is never called,
and there is no order path in this file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CACHE = PROJECT_ROOT / "data" / "settlements.jsonl"


def tickers_from_decisions(path: Path) -> set[str]:
    """Every ticker that appears in the decision log, traded or not."""
    found: set[str] = set()
    if not path.is_file():
        return found
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a torn final line is expected on a live writer
            ticker = record.get("ticker")
            if ticker:
                found.add(ticker)
    return found


def tickers_from_ledger(path: Path) -> set[str]:
    import sqlite3

    if not path.is_file():
        return set()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        queries = []
        if "trades" in tables:
            queries.append("SELECT ticker FROM trades")
        # Signal evaluations include every fully classified refusal, not just
        # the tiny subset that became a trade. Those counterfactual labels are
        # what allow gate/threshold analysis without survivorship bias.
        if "signal_evaluations" in tables:
            queries.append("SELECT ticker FROM signal_evaluations")
        if not queries:
            return set()
        return {
            row[0]
            for row in connection.execute(" UNION ".join(queries))
            if row[0]
        }
    finally:
        connection.close()


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    cached: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return cached
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("ticker"):
                cached[record["ticker"]] = record
    return cached


async def fetch(tickers: Iterable[str], concurrency: int = 4) -> list[dict[str, Any]]:
    """Ask Kalshi for each market's final state.

    Unauthenticated: market data is public, and a report is not a trade. A
    ticker that cannot be read yields a row with ``result: None`` rather than
    aborting, so one delisted market does not cost the whole build.
    """
    from config import KalshiSettings, load_environment
    from execution.kalshi_client import AsyncKalshiTradingClient

    # Without this the settings resolve to the DEMO exchange and every label
    # below describes a different venue than the one the bot traded. It did:
    # the first run of this script cached 135 demo settlements as if they were
    # production truth.
    load_environment(PROJECT_ROOT)
    settings = KalshiSettings.from_env()
    client = AsyncKalshiTradingClient(settings, authenticated=False)
    gate = asyncio.Semaphore(concurrency)
    out: list[dict[str, Any]] = []

    async def one(ticker: str) -> None:
        async with gate:
            try:
                status = await client.get_market_status(ticker)
                out.append(
                    {
                        "ticker": ticker,
                        "status": status.status,
                        "result": status.result,
                        "settlement_value": (
                            str(status.settlement_value)
                            if getattr(status, "settlement_value", None) is not None
                            else None
                        ),
                        "source": "kalshi",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - one bad ticker is not fatal
                out.append(
                    {"ticker": ticker, "status": None, "result": None,
                     "error": f"{type(exc).__name__}: {exc}", "source": "kalshi"}
                )

    try:
        await asyncio.gather(*(one(t) for t in tickers))
    finally:
        await client.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--decisions", type=Path, default=PROJECT_ROOT / "decisions.jsonl")
    parser.add_argument("--ledger", type=Path, default=PROJECT_ROOT / "trade_ledger.db")
    parser.add_argument("--out", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--refresh", action="store_true",
                        help="re-ask about tickers already cached")
    parser.add_argument("--limit", type=int, default=0, help="cap the number fetched")
    args = parser.parse_args(argv)

    wanted = tickers_from_decisions(args.decisions) | tickers_from_ledger(args.ledger)
    cached = load_cache(args.out)
    # A market that has not settled yet is worth re-asking about; one that has
    # is final and never needs another call.
    todo = sorted(
        t for t in wanted
        if args.refresh or cached.get(t, {}).get("result") not in ("yes", "no", "void")
    )
    if args.limit:
        todo = todo[: args.limit]

    print(f"tickers seen: {len(wanted)}   cached settled: "
          f"{sum(1 for v in cached.values() if v.get('result') in ('yes','no','void'))}")
    print(f"to fetch: {len(todo)}")
    if not todo:
        return 0

    fetched = asyncio.run(fetch(todo))
    for record in fetched:
        cached[record["ticker"]] = record

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for ticker in sorted(cached):
            handle.write(json.dumps(cached[ticker], sort_keys=True) + "\n")

    settled = sum(1 for v in cached.values() if v.get("result") in ("yes", "no", "void"))
    errors = sum(1 for v in cached.values() if v.get("error"))
    print(f"wrote {args.out}: {len(cached)} rows, {settled} settled, {errors} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
