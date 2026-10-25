"""Observed external perpetual signals with durable provenance and strict hourly quality."""

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .domain import HOUR, canonical, dec, digest
from .strategy import HistoryUnavailable

SYMBOL_MAP = {
    "KXBTCPERP": "PF_XBTUSD",
    "KXETHPERP": "PF_ETHUSD",
    "KXDOGEPERP": "PF_DOGEUSD",
    "KXSOLPERP": "PF_SOLUSD",
    "KXNEARPERP": "PF_NEARUSD",
    "KXUNIPERP": "PF_UNIUSD",
}
HOST = "https://futures.kraken.com"


def validate_rows(rows, start, stop):
    expected = start
    missing = 0
    for row in rows:
        t = row["timestamp_ms"]
        if type(t) is not int or t % HOUR or not start <= t < stop:
            raise ValueError("invalid external candle timestamp")
        o, hi, lo, close = (dec(row[k]) for k in ("open", "high", "low", "close"))
        if lo <= 0 or lo > min(o, close) or hi < max(o, close):
            raise ValueError("invalid external OHLC")
        if row["tradable"] not in (0, 1):
            raise ValueError("invalid external volume flag")
        if t != expected:
            missing += 1
        expected += HOUR
    if expected != stop or missing:
        raise HistoryUnavailable(
            "external hourly coverage incomplete: missing or unordered intervals"
        )


class KrakenPerpetualProvider:
    """Public trade candles, UTC opening timestamps in milliseconds. No keys."""

    name = "kraken-perpetual-trade/1"

    def __init__(self, client=None, sleeper=time.sleep):
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.sleep = sleeper

    def candles(self, symbol, start, stop):
        cursor = start
        result = {}
        receipts = []
        while cursor < stop:
            url = f"{HOST}/api/charts/v1/trade/{symbol}/1h"
            for attempt in range(4):
                try:
                    response = self.client.get(
                        url, params={"from": cursor // 1000, "to": stop // 1000, "count": 500}
                    )
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                        self.sleep(2**attempt)
                        continue
                    response.raise_for_status()
                    body = response.json()
                    break
                except httpx.TransportError:
                    if attempt == 3:
                        raise
                    self.sleep(2**attempt)
            rows = body["candles"]
            receipts.append(
                dict(
                    url=url,
                    start_ms=cursor,
                    stop_ms=stop,
                    response_sha256=digest(body),
                    rows=len(rows),
                    retrieved_at=datetime.now(UTC).isoformat(),
                )
            )
            observed = []
            for row in rows:
                t = row["time"]
                if type(t) is not int or t % HOUR:
                    raise ValueError(
                        "provider timestamp must be an hourly millisecond opening time"
                    )
                if not cursor <= t < stop:
                    continue  # Drop the open candle and overlapping/out-of-range observations.
                if dec(row["volume"]) < 0:
                    raise ValueError("negative candle volume")
                record = dict(
                    timestamp_ms=t,
                    **{k: str(dec(row[k])) for k in ("open", "high", "low", "close")},
                    tradable=int(dec(row["volume"]) > 0),
                )
                if t in result and result[t] != record:
                    raise ValueError("conflicting provider candle")
                result[t] = record
                observed.append(t)
            if not observed:
                break
            next_cursor = max(observed) + HOUR
            if next_cursor <= cursor:
                raise ValueError("external history pagination made no progress")
            cursor = next_cursor
            if not body.get("more_candles", False):
                break
            self.sleep(0.2)
        rows = [result[t] for t in sorted(result)]
        validate_rows(rows, start, stop)
        return rows, receipts


class SignalHistory:
    """A provider series, never a splice of different venue prices.

    Underlying USD prices are normalized once to each Kalshi contract's units.
    Execution and funding continue to use actual Kalshi prices and event marks.
    """

    def __init__(self, path, instruments, *, days=90, provider=None):
        if type(days) is not int or days < 32 or days > 3650:
            raise ValueError("history_days must be 32..3650")
        self.instruments, self.days = instruments, days
        self.provider = provider or KrakenPerpetualProvider()
        if any(t not in SYMBOL_MAP for t in instruments):
            raise ValueError("no verified external perpetual symbol mapping")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS candles (provider TEXT, symbol TEXT, ts INTEGER, body TEXT, PRIMARY KEY(provider,symbol,ts))"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts (hash TEXT PRIMARY KEY, body TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS anchors (key TEXT PRIMARY KEY, start INTEGER)")

    def load(self, boundary):
        all_rows, report = {}, {}
        for ticker, instrument in self.instruments.items():
            symbol = SYMBOL_MAP[ticker]
            key = f"{self.provider.name}:{symbol}:{self.days}"
            anchor = self.db.execute("SELECT start FROM anchors WHERE key=?", (key,)).fetchone()
            start = anchor[0] if anchor else boundary - self.days * 24 * HOUR
            # Locate the first hole using timestamp-only iteration. Keep the fixed
            # seed anchor; a rolling window would change recursive indicators.
            expected = start
            for (stamp,) in self.db.execute(
                "SELECT ts FROM candles WHERE provider=? AND symbol=? AND ts>=? AND ts<? ORDER BY ts",
                (self.provider.name, symbol, start, boundary),
            ):
                if stamp != expected:
                    break
                expected += HOUR
            missing = expected < boundary
            receipts = []
            if missing:
                # Fetch an overlapping completed hour to detect changed provider history.
                fetch_from = max(start, expected - HOUR)
                incoming, receipts = self.provider.candles(symbol, fetch_from, boundary)
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    for row in incoming:
                        old = None
                        stored = self.db.execute(
                            "SELECT body FROM candles WHERE provider=? AND symbol=? AND ts=?",
                            (self.provider.name, symbol, row["timestamp_ms"]),
                        ).fetchone()
                        if stored is not None:
                            old = json.loads(stored[0])
                        if old is not None and old != row:
                            raise ValueError(
                                "completed provider history changed; cache not overwritten"
                            )
                        self.db.execute(
                            "INSERT OR IGNORE INTO candles VALUES(?,?,?,?)",
                            (self.provider.name, symbol, row["timestamp_ms"], canonical(row)),
                        )
                    for receipt in receipts:
                        self.db.execute(
                            "INSERT OR IGNORE INTO receipts VALUES(?,?)",
                            (digest(receipt), canonical(receipt)),
                        )
                    self.db.execute("INSERT OR IGNORE INTO anchors VALUES(?,?)", (key, start))
                    self.db.execute("COMMIT")
                except BaseException:
                    self.db.execute("ROLLBACK")
                    raise
            # Retain only the exact engine input, without a second raw history
            # dictionary/list. SQL traversal and validation use constant scratch.
            normalized = []
            checksum = hashlib.sha256(b"[")
            for (body,) in self.db.execute(
                "SELECT body FROM candles WHERE provider=? AND symbol=? AND ts>=? AND ts<? ORDER BY ts",
                (self.provider.name, symbol, start, boundary),
            ):
                row = json.loads(body)
                record = {
                    **row,
                    **{
                        k: str(dec(row[k]) * instrument.contract_size)
                        for k in ("open", "high", "low", "close")
                    },
                }
                if normalized:
                    checksum.update(b",")
                checksum.update(canonical(record).encode())
                normalized.append(record)
            checksum.update(b"]")
            validate_rows(normalized, start, boundary)
            all_rows[ticker] = normalized
            report[ticker] = dict(
                provider=self.provider.name,
                symbol=symbol,
                interval="1h",
                first_ms=start,
                completed_through_ms=boundary,
                rows=len(normalized),
                missing_intervals=0,
                price_unit="usd_per_contract",
                underlying_contract_size=str(instrument.contract_size),
                sha256=checksum.hexdigest(),
                new_receipts=len(receipts),
                proxy=True,
            )
        return all_rows, report

    def close(self):
        self.db.close()
