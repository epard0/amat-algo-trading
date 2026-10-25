"""Observed operational readiness, never a synthetic performance track record."""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

_REQUIRED_SESSIONS = 20
_MIN_SECONDS = 1800
_MAX_GAP = 720


def observe_window(
    db: sqlite3.Connection, *, config: str, now: dt.datetime, market_open: bool, healthy: bool
) -> dict[str, Any]:
    db.execute(
        "CREATE TABLE IF NOT EXISTS observation_sessions(config TEXT, session TEXT, "
        "last_seen TEXT, seconds REAL, failed INTEGER, PRIMARY KEY(config,session))"
    )
    session = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if market_open:
        old = db.execute(
            "SELECT last_seen,seconds,failed FROM observation_sessions "
            "WHERE config=? AND session=?",
            (config, session),
        ).fetchone()
        elapsed = (now - dt.datetime.fromisoformat(old[0])).total_seconds() if old else 0
        if old is None or elapsed > 0:
            seconds = float(old[1]) if old else 0
            failed = bool(old[2]) if old else False
            seconds += elapsed if healthy and 0 < elapsed <= _MAX_GAP and not failed else 0
            failed = failed or not healthy or elapsed > _MAX_GAP
            db.execute(
                "INSERT OR REPLACE INTO observation_sessions VALUES(?,?,?,?,?)",
                (config, session, now.isoformat(), seconds, int(failed)),
            )
    completed = db.execute(
        "SELECT COUNT(*) FROM observation_sessions WHERE config=? AND session<? "
        "AND failed=0 AND seconds>=?",
        (config, session, _MIN_SECONDS),
    ).fetchone()[0]
    return {
        "schema": "operational-observation/1",
        "required_sessions": _REQUIRED_SESSIONS,
        "completed_sessions": completed,
        "status": "observed" if completed >= _REQUIRED_SESSIONS else "collecting",
        "minimum_observed_seconds_per_session": _MIN_SECONDS,
        "scope": "operational_reliability_only",
        "execution_authority": "none",
    }
