"""Read-only access to the bot's durable state.

Everything in this package observes; nothing here writes. The ledger is opened
through a ``mode=ro`` URI so a bug in the dashboard can never corrupt the
canonical record of real money, and the monitoring server is expected to run as
its own process for the same reason — if it dies, trading does not.

SQLite in WAL mode allows a reader concurrent with the bot's writer, so these
functions are safe to call while a live session is running.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator


def _dec(value: Any) -> Decimal | None:
    """Money stays Decimal. Unparseable values become None, never 0.0.

    A silent zero would read as "this trade cost nothing" in every downstream
    sum; None forces the caller to decide.
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class LedgerTrade:
    """One settled or open position, as the ledger records it."""

    trade_id: str
    mode: str
    environment: str
    asset: str
    ticker: str
    outcome: str
    status: str
    market_result: str | None
    placed_at: datetime | None
    market_open_time: datetime | None
    market_close_time: datetime | None
    settled_at: datetime | None
    contracts: Decimal | None
    entry_price: Decimal | None
    entry_cost: Decimal | None
    fees_paid: Decimal | None
    total_debit: Decimal | None
    gross_payout: Decimal | None
    pnl: Decimal | None
    target_price: Decimal | None
    model_probability: Decimal | None
    model_edge: Decimal | None
    metadata: dict[str, Any]

    @property
    def regime(self) -> str:
        return str(self.metadata.get("regime") or "UNKNOWN")

    @property
    def decision_id(self) -> str | None:
        value = self.metadata.get("decision_id")
        return str(value) if value else None

    @property
    def is_settled(self) -> bool:
        return self.status in {"WON", "LOST", "VOID"}


def read_trades(
    ledger_path: Path,
    *,
    mode: str | None = None,
    environment: str | None = None,
) -> list[LedgerTrade]:
    """Load every trade, newest last. Returns [] if the ledger does not exist."""
    if not Path(ledger_path).exists():
        return []

    query = "SELECT * FROM trades"
    clauses: list[str] = []
    params: list[Any] = []
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    if environment:
        clauses.append("environment = ?")
        params.append(environment)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY placed_at ASC"

    uri = f"file:{Path(ledger_path).resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(query, params).fetchall()
    except sqlite3.DatabaseError:
        # A half-written or foreign database is a monitoring problem, not a
        # trading one. Report nothing rather than raising into the server.
        return []
    finally:
        connection.close()

    trades: list[LedgerTrade] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        trades.append(
            LedgerTrade(
                trade_id=row["trade_id"],
                mode=row["mode"],
                environment=row["environment"],
                asset=row["asset"],
                ticker=row["ticker"],
                outcome=row["outcome"],
                status=row["status"],
                market_result=row["market_result"],
                placed_at=_dt(row["placed_at"]),
                market_open_time=_dt(row["market_open_time"]),
                market_close_time=_dt(row["market_close_time"]),
                settled_at=_dt(row["settled_at"]),
                contracts=_dec(row["contracts"]),
                entry_price=_dec(row["entry_price"]),
                entry_cost=_dec(row["entry_cost"]),
                fees_paid=_dec(row["fees_paid"]),
                total_debit=_dec(row["total_debit"]),
                gross_payout=_dec(row["gross_payout"]),
                pnl=_dec(row["pnl"]),
                target_price=_dec(row["target_price"]),
                model_probability=_dec(row["model_probability"]),
                model_edge=_dec(row["model_edge"]),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
    return trades


def iter_decisions(decisions_path: Path) -> Iterator[dict[str, Any]]:
    """Stream decision records, skipping corrupt lines.

    The decision log is append-only and written by a live process, so the final
    line can be a partial write. One bad line must not cost the whole file.
    """
    path = Path(decisions_path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def read_decisions(
    decisions_path: Path,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Load decision records. ``limit`` keeps the most recent N."""
    records = list(iter_decisions(decisions_path))
    if limit is not None and len(records) > limit:
        return records[-limit:]
    return records


class DecisionTail:
    """Incremental reader for an append-only decision log.

    ``read_decisions`` re-parses the whole file. The dashboard refreshes every
    few seconds, so with a day of records that is the entire log parsed from
    byte zero, several times a minute, for as long as a browser is open. This
    keeps a byte offset and parses only what was appended since the last call.

    Two things force a full re-read, and both are detected rather than assumed:
    the file shrinking (the log rotates by truncating in place) and the inode
    changing (it was replaced). Anything else is an append.
    """

    def __init__(self, path: Path, *, limit: int | None = None) -> None:
        self.path = Path(path)
        self.limit = limit
        self._offset = 0
        self._identity: tuple[int, int] | None = None
        self._records: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._offset = 0
        self._identity = None
        self._records = []

    def load(self) -> list[dict[str, Any]]:
        try:
            stat = self.path.stat()
        except OSError:
            self.reset()
            return []

        identity = (stat.st_dev, stat.st_ino)
        if identity != self._identity or stat.st_size < self._offset:
            # Replaced, or truncated by rotation. Either way the offset means
            # nothing now and the records behind it are gone.
            self._offset = 0
            self._identity = identity
            self._records = []
        elif stat.st_size == self._offset:
            return list(self._records)

        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
        except OSError:
            return list(self._records)

        # A live writer can be mid-record. Consume only up to the last complete
        # line and leave the remainder for the next call, so a torn tail is read
        # once it is whole rather than discarded as corrupt.
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return list(self._records)
        self._offset += cut + 1
        for raw in chunk[: cut + 1].decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                self._records.append(record)
        if self.limit is not None and len(self._records) > self.limit:
            del self._records[: len(self._records) - self.limit]
        return list(self._records)
