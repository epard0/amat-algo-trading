"""Single-writer offline registry with immutable source blobs and review history."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from portfolio_team.contracts import Assessment, Bundle, Decision
from portfolio_team.evidence import EvidenceError, sha256

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    hash TEXT PRIMARY KEY, content BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS bundles (
    hash TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bindings (
    alias TEXT PRIMARY KEY, bundle_hash TEXT REFERENCES bundles(hash), error TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY, bundle_hash TEXT NOT NULL REFERENCES bundles(hash),
    status TEXT NOT NULL, decision TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
    review_id TEXT NOT NULL REFERENCES reviews(id), role TEXT NOT NULL,
    attempt INTEGER NOT NULL, status TEXT NOT NULL, response TEXT, error TEXT,
    PRIMARY KEY(review_id, role, attempt)
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_calls (
    review_id TEXT NOT NULL, role TEXT NOT NULL, attempt INTEGER NOT NULL,
    day TEXT NOT NULL, request_hash TEXT NOT NULL REFERENCES artifacts(hash),
    response_hash TEXT REFERENCES artifacts(hash),
    reserved_input INTEGER NOT NULL, reserved_output INTEGER NOT NULL,
    charged_input INTEGER NOT NULL, charged_output INTEGER NOT NULL,
    actual_input INTEGER, actual_output INTEGER, elapsed_seconds REAL, error TEXT,
    PRIMARY KEY(review_id,role,attempt),
    FOREIGN KEY(review_id,role,attempt) REFERENCES attempts(review_id,role,attempt)
);
"""


class Store:
    """Hold an OS writer lock, never a SQL transaction, during inference work.

    This first implementation intentionally supports one CLI coordinator at a
    time. The lock is released by the OS on process death; bounded unfinished
    attempts can resume on the next invocation. Distributed leases are not claimed.
    """

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._lock = (root / "writer.lock").open("a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise EvidenceError("another pmteam process holds the registry lock") from None
        try:
            self.db = sqlite3.connect(root / "registry.sqlite3")
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=5000")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise EvidenceError("unsupported registry schema version")
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA user_version=2")
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            self._lock.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.db.close()
        self._lock.close()

    def event(self, kind: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO events(at,kind,payload) VALUES(?,?,?)",
            (
                dt.datetime.now(dt.UTC).isoformat(),
                kind,
                json.dumps(payload, sort_keys=True),
            ),
        )

    def import_bundle(self, bundle: Bundle, blobs: dict[str, bytes]) -> str:
        digest = bundle.digest()
        with self.db:
            for key, content in blobs.items():
                if sha256(content) != key:
                    raise EvidenceError("artifact content digest mismatch")
                existing = self.db.execute(
                    "SELECT content FROM artifacts WHERE hash=?", (key,)
                ).fetchone()
                if existing is not None and bytes(existing[0]) != content:
                    raise EvidenceError("stored artifact corruption")
                self.db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (key, content))
            existing = self.db.execute(
                "SELECT payload FROM bundles WHERE hash=?", (digest,)
            ).fetchone()
            if existing is not None and existing[0] != bundle.canonical():
                raise EvidenceError("stored bundle corruption")
            self.db.execute(
                "INSERT OR IGNORE INTO bundles VALUES(?,?)", (digest, bundle.canonical())
            )
            self.db.execute(
                "INSERT INTO bindings VALUES(?,?,NULL) ON CONFLICT(alias) "
                "DO UPDATE SET bundle_hash=excluded.bundle_hash,error=NULL",
                (bundle.comparison.id, digest),
            )
            self.event("bundle_imported", {"comparison": bundle.comparison.id, "hash": digest})
        return digest

    def quarantine(self, alias: str, error: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO bindings VALUES(?,NULL,?) ON CONFLICT(alias) "
                "DO UPDATE SET bundle_hash=NULL,error=excluded.error",
                (alias, error),
            )
            self.event("import_quarantined", {"comparison": alias, "error": error})

    def resolve(self, alias: str) -> Bundle:
        row = self.db.execute(
            "SELECT bundle_hash,error FROM bindings WHERE alias=?", (alias,)
        ).fetchone()
        if row is None:
            raise EvidenceError(f"unknown comparison: {alias}")
        if row["error"]:
            raise EvidenceError(f"comparison is quarantined: {row['error']}")
        return self.bundle(row["bundle_hash"])

    def bundle(self, digest: str) -> Bundle:
        row = self.db.execute("SELECT payload FROM bundles WHERE hash=?", (digest,)).fetchone()
        if row is None:
            raise EvidenceError("missing evidence bundle")
        bundle = Bundle.model_validate_json(row[0])
        if bundle.digest() != digest:
            raise EvidenceError("stored bundle digest mismatch")
        for run in (bundle.candidate, bundle.control):
            for key in (run.manifest_file_sha256, run.results_file_sha256, run.config_file_sha256):
                if key is not None:
                    self.artifact(key)
        return bundle

    def artifact(self, digest: str) -> bytes:
        row = self.db.execute("SELECT content FROM artifacts WHERE hash=?", (digest,)).fetchone()
        if row is None or sha256(bytes(row[0])) != digest:
            raise EvidenceError("stored source artifact missing or corrupted")
        return bytes(row[0])

    def record_artifact(self, content: bytes) -> str:
        digest = sha256(content)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (digest, content))
        self.artifact(digest)
        return digest

    def start(self, review_id: str, bundle_hash: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO reviews(id,bundle_hash,status) VALUES(?,?,'reviewing')",
                (review_id, bundle_hash),
            )
            self.db.execute(
                "UPDATE reviews SET status='reviewing',error=NULL WHERE id=? AND decision IS NULL",
                (review_id,),
            )
            self.event("review_started", {"review_id": review_id})

    def cached(self, review_id: str, role: str) -> Assessment | None:
        row = self.db.execute(
            "SELECT response FROM attempts WHERE review_id=? AND role=? AND status='completed' "
            "ORDER BY attempt DESC LIMIT 1",
            (review_id, role),
        ).fetchone()
        return Assessment.model_validate_json(row[0]) if row else None

    def begin_attempt(self, review_id: str, role: str, limit: int) -> int:
        row = self.db.execute(
            "SELECT COALESCE(MAX(attempt),0) FROM attempts WHERE review_id=? AND role=?",
            (review_id, role),
        ).fetchone()
        attempt = int(row[0]) + 1
        if attempt > limit:
            raise EvidenceError(f"attempt budget exhausted for {role}")
        with self.db:
            self.db.execute(
                "INSERT INTO attempts VALUES(?,?,?,'running',NULL,NULL)", (review_id, role, attempt)
            )
            self.event(
                "attempt_started", {"review_id": review_id, "role": role, "attempt": attempt}
            )
        return attempt

    def finish_attempt(
        self,
        review_id: str,
        role: str,
        attempt: int,
        assessment: Assessment | None,
        error: str | None,
    ) -> None:
        with self.db:
            self.db.execute(
                "UPDATE attempts SET status=?,response=?,error=? "
                "WHERE review_id=? AND role=? AND attempt=?",
                (
                    "completed" if assessment else "failed",
                    assessment.canonical() if assessment else None,
                    error,
                    review_id,
                    role,
                    attempt,
                ),
            )
            self.event(
                "attempt_finished",
                {"review_id": review_id, "role": role, "attempt": attempt, "error": error},
            )

    def fail(self, review_id: str, error: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE reviews SET status='failed',error=? WHERE id=? AND decision IS NULL",
                (error, review_id),
            )
            self.event("review_failed", {"review_id": review_id, "error": error})

    def complete(self, decision: Decision) -> None:
        with self.db:
            row = self.db.execute(
                "SELECT decision FROM reviews WHERE id=?", (decision.review_id,)
            ).fetchone()
            if row is None or (row[0] is not None and row[0] != decision.canonical()):
                raise EvidenceError("cannot overwrite a completed decision")
            self.db.execute(
                "UPDATE reviews SET status='completed',decision=?,error=NULL WHERE id=?",
                (decision.canonical(), decision.review_id),
            )
            self.event("review_completed", {"review_id": decision.review_id})

    def decision(self, review_id: str) -> Decision | None:
        row = self.db.execute("SELECT decision FROM reviews WHERE id=?", (review_id,)).fetchone()
        return Decision.model_validate_json(row[0]) if row and row[0] else None

    def status(self) -> dict[str, Any]:
        return {
            "comparisons": [
                dict(row) for row in self.db.execute("SELECT * FROM bindings ORDER BY alias")
            ],
            "reviews": [
                dict(row)
                for row in self.db.execute("SELECT id,status,error FROM reviews ORDER BY id")
            ],
            "model_usage": [
                dict(row)
                for row in self.db.execute(
                    "SELECT day,COUNT(*) AS calls,SUM(charged_input) AS charged_input,"
                    "SUM(charged_output) AS charged_output FROM model_calls "
                    "GROUP BY day ORDER BY day"
                )
            ],
        }

    def reserve_model_call(
        self,
        review_id: str,
        role: str,
        attempt: int,
        *,
        request_json: str,
        input_tokens: int,
        output_tokens: int,
        limits: tuple[int, int, int, int],
    ) -> None:
        """Reserve before dispatch; interrupted/unknown usage never counts as zero."""
        day = dt.datetime.now(dt.UTC).date().isoformat()
        request_hash = self.record_artifact(request_json.encode())
        with self.db:
            review = self.db.execute(
                "SELECT COALESCE(SUM(charged_input),0),COALESCE(SUM(charged_output),0) "
                "FROM model_calls WHERE review_id=?",
                (review_id,),
            ).fetchone()
            daily = self.db.execute(
                "SELECT COALESCE(SUM(charged_input),0),COALESCE(SUM(charged_output),0) "
                "FROM model_calls WHERE day=?",
                (day,),
            ).fetchone()
            proposed = (
                review[0] + input_tokens,
                review[1] + output_tokens,
                daily[0] + input_tokens,
                daily[1] + output_tokens,
            )
            if any(value > limit for value, limit in zip(proposed, limits, strict=True)):
                raise EvidenceError("model token reservation budget exhausted")
            self.db.execute(
                "INSERT INTO model_calls(review_id,role,attempt,day,request_hash,reserved_input,"
                "reserved_output,charged_input,charged_output) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    review_id,
                    role,
                    attempt,
                    day,
                    request_hash,
                    input_tokens,
                    output_tokens,
                    input_tokens,
                    output_tokens,
                ),
            )

    def finish_model_call(
        self,
        review_id: str,
        role: str,
        attempt: int,
        *,
        response_json: str,
        input_tokens: int | None,
        output_tokens: int | None,
        elapsed: float,
        error: str | None,
    ) -> None:
        response_hash = self.record_artifact(response_json.encode())
        with self.db:
            self.db.execute(
                "UPDATE model_calls SET response_hash=?,actual_input=?,actual_output=?,"
                "charged_input=COALESCE(?,reserved_input),"
                "charged_output=COALESCE(?,reserved_output),elapsed_seconds=?,error=? "
                "WHERE review_id=? AND role=? AND attempt=?",
                (
                    response_hash,
                    input_tokens,
                    output_tokens,
                    input_tokens,
                    output_tokens,
                    elapsed,
                    error,
                    review_id,
                    role,
                    attempt,
                ),
            )

    def model_calls(self, review_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM model_calls WHERE review_id=? ORDER BY role,attempt", (review_id,)
            )
        ]
