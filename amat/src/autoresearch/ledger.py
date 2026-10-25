"""Single-writer durable experiment, cache and model-budget ledger."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import sqlite3
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from llmrouter.chain import Target
from llmrouter.contracts import ChatRequest, ChatResponse


class BudgetPauseError(ValueError):
    pass


class Ledger:
    def __init__(self, root: Path, model_limit: int) -> None:
        self.root, self.model_limit = root, model_limit
        self.call_started: dict[int, float] = {}
        self.lock = (root / "writer.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise ValueError("another strategy-development process owns this workspace") from None
        self.db = sqlite3.connect(root / "research.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS experiments(
              id INTEGER PRIMARY KEY, candidate TEXT NOT NULL, parent TEXT NOT NULL,
              proposal TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS cache(candidate TEXT PRIMARY KEY, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls(
              id INTEGER PRIMARY KEY, day TEXT NOT NULL,
              provider TEXT NOT NULL, model TEXT NOT NULL,
              request TEXT NOT NULL, response TEXT, charged INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL,
              kind TEXT NOT NULL, payload TEXT NOT NULL);
        """)
        # An interrupted worker is not silently rerun or mistaken for a cache hit.
        with self.db:
            self.db.execute(
                "UPDATE experiments SET status='crash',error='interrupted' WHERE status='running'"
            )
            retained = self.db.execute(
                "SELECT id,candidate FROM experiments WHERE status IN ('baseline','keep') "
                "AND result IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if retained:
                # Recover a crash between the durable outcome and metadata update.
                self.db.execute(
                    "INSERT OR REPLACE INTO state VALUES('incumbent',?)",
                    (json.dumps(retained["candidate"]),),
                )
                later = self.db.execute(
                    "SELECT proposal FROM experiments WHERE id>? AND parent!=''",
                    (retained["id"],),
                ).fetchall()
                stall = sum(json.loads(row["proposal"]).get("stage") != "screen" for row in later)
                self.db.execute(
                    "INSERT OR REPLACE INTO state VALUES('stall',?)", (json.dumps(stall),)
                )

        previous = self.get("progress", {})
        if previous and previous["phase"] != "finished":
            self.event("phase_interrupted", **previous)
            self.put("progress", {})

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.db.close()
        self.lock.close()

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, json.dumps(value)))

    def history(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM experiments ORDER BY id")]

    def event(self, kind: str, **payload: Any) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO events(recorded_at,kind,payload) VALUES(?,?,?)",
                (dt.datetime.now(dt.UTC).isoformat(), kind, json.dumps(payload)),
            )

    def phase(self, name: str, experiment: int | None = None) -> None:
        now = dt.datetime.now(dt.UTC)
        previous = self.get("progress", {})
        if previous and previous["phase"] != "finished":
            elapsed = max(
                0.0, (now - dt.datetime.fromisoformat(previous["started_at"])).total_seconds()
            )
            self.event("phase_finished", **previous, elapsed_seconds=elapsed)
        progress = {"phase": name, "experiment": experiment, "started_at": now.isoformat()}
        self.put("progress", progress)
        self.event("phase_started", **progress)

    def reserve(self, target: Target, request: ChatRequest) -> int:
        day = dt.datetime.now(dt.UTC).date().isoformat()
        reservation = len(request.canonical().encode()) + request.max_output_tokens + 2048
        retained = request.canonical()
        if request.persistence == "metadata_only":
            retained = json.dumps(
                {
                    "persistence": "metadata_only",
                    "request_sha256": request.digest(),
                    "schema_name": request.schema_name,
                    "task_class": request.task_class,
                    "max_output_tokens": request.max_output_tokens,
                    "timeout_seconds": request.timeout_seconds,
                },
                sort_keys=True,
            )
        with self.db:
            used = self.db.execute(
                "SELECT COALESCE(SUM(charged),0) FROM calls WHERE day=?", (day,)
            ).fetchone()[0]
            if used + reservation > self.model_limit:
                raise BudgetPauseError("daily model budget exhausted; retry after UTC rollover")
            cursor = self.db.execute(
                "INSERT INTO calls(day,provider,model,request,charged) VALUES(?,?,?,?,?)",
                (day, target.provider, target.model, retained, reservation),
            )
        call_id = int(cursor.lastrowid or 0)
        self.call_started[call_id] = time.monotonic()
        self.event(
            "model_call_started",
            call_id=call_id,
            provider=target.provider,
            model=target.model,
            purpose=request.schema_name,
            task_class=request.task_class,
            phase=self.get("progress", {}).get("phase"),
            experiment=self.get("progress", {}).get("experiment"),
        )
        return call_id

    def settle(self, call_id: int, response: ChatResponse) -> None:
        row = self.db.execute("SELECT request FROM calls WHERE id=?", (call_id,)).fetchone()
        if row is None:
            raise ValueError("unknown model reservation")
        # Explicit request rejection means generation never started. Unknown
        # transport/timeouts and interrupted reservations remain charged.
        if (
            response.error_code
            in {
                "schema_not_supported",
                "provider_http_400",
                "provider_http_401",
                "provider_http_403",
                "provider_http_404",
                "provider_http_422",
                "provider_http_429",
            }
            and response.input_tokens is None
            and response.output_tokens is None
            and not response.text
            and not response.ok
        ):
            response = response.model_copy(update={"input_tokens": 0, "output_tokens": 0})
        retained = response.canonical()
        if json.loads(row[0]).get("persistence") == "metadata_only":
            retained = json.dumps(
                {
                    "response_sha256": response.digest(),
                    "provider": response.provider,
                    "model": response.model,
                    "served_model": response.served_model,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "error_class": response.error_class,
                    "elapsed_seconds": response.elapsed_seconds,
                },
                sort_keys=True,
            )
        with self.db:
            self.db.execute(
                "UPDATE calls SET response=?, charged=CASE WHEN ? IS NOT NULL "
                "AND ? IS NOT NULL THEN ? ELSE charged END WHERE id=?",
                (
                    retained,
                    response.input_tokens,
                    response.output_tokens,
                    (response.input_tokens or 0) + (response.output_tokens or 0),
                    call_id,
                ),
            )
        started = self.call_started.pop(call_id, None)
        self.event(
            "model_call_finished",
            call_id=call_id,
            provider=response.provider,
            model=response.model,
            served_model=response.served_model,
            status=response.error_class,
            error_code=response.error_code,
            elapsed_seconds=time.monotonic() - started if started is not None else None,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
