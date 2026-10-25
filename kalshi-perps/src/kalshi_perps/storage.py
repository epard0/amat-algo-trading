"""Atomic event deduplication and state transitions, with account identity binding."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .domain import canonical, digest


class Store:
    def __init__(self, path, identity, initial):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT, body TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, hash TEXT, body TEXT, result TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, hash TEXT, payload TEXT, status TEXT, response TEXT)"
        )
        with self.transaction():
            row = self.db.execute("SELECT identity FROM state WHERE id=1").fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO state VALUES(1,?,?)", (canonical(identity), canonical(initial))
                )
            elif row[0] != canonical(identity):
                raise ValueError("database account/mode/policy identity mismatch")

    @contextmanager
    def transaction(self):
        nested = self.db.in_transaction
        if nested:
            import uuid

            savepoint = "s" + uuid.uuid4().hex
            self.db.execute(f"SAVEPOINT {savepoint}")
        else:
            self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
        except BaseException:
            if nested:
                self.db.execute(f"ROLLBACK TO {savepoint}")
                self.db.execute(f"RELEASE {savepoint}")
            else:
                self.db.execute("ROLLBACK")
            raise

    def state(self):
        return json.loads(self.db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])

    def save(self, value):
        self.db.execute("UPDATE state SET body=? WHERE id=1", (canonical(value),))

    def event(self, event_id, payload, transition):
        with self.transaction():
            old = self.db.execute(
                "SELECT hash,result FROM events WHERE id=?", (event_id,)
            ).fetchone()
            if old:
                if old[0] != digest(payload):
                    raise ValueError("event identity reused with different payload")
                return json.loads(old[1])
            state = self.state()
            result = transition(state)
            self.save(state)
            self.db.execute(
                "INSERT INTO events VALUES(?,?,?,?)",
                (event_id, digest(payload), canonical(payload), canonical(result)),
            )
            return result

    def close(self):
        self.db.close()
