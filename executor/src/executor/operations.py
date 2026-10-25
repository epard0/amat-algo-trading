"""Observed deployment lifecycle and durable operational incidents.

This module has no broker client and cannot approve research or submit orders.
The configured allowlist is observed, not authored here. SQLite is authoritative;
JSON is a rebuildable read-only projection for status and downstream review.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from typing import Any

from executor.batch import _effective, load_batch
from executor.batch import digest as batch_digest
from executor.config import ExecutorSettings
from executor.errors import ExecutorError
from executor.observation_window import observe_window
from executor.plan_source import parse_plan
from executor.submit import client_order_id_for

_FRESH_SECONDS = 720
_STREAK_REQUIRED = 3
_STREAK_SECONDS = 480
_AGED_ORDER_SECONDS = 72 * 3600
_MAX_REVIEW_FLAGS = 100
_SCHEMA = """
CREATE TABLE IF NOT EXISTS deployments (
 config_sha256 TEXT PRIMARY KEY, state TEXT NOT NULL, details TEXT NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
 path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intents (
 client_order_id TEXT PRIMARY KEY, config_sha256 TEXT NOT NULL,
 plan_sha256 TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
 session TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL,
 status TEXT NOT NULL, streak INTEGER NOT NULL, first_failure TEXT,
 last_observed TEXT NOT NULL, opened_at TEXT, resolved_at TEXT,
 details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, kind TEXT NOT NULL,
 subject TEXT NOT NULL, previous_state TEXT, state TEXT NOT NULL
);
"""


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _read_fresh(path: Path, now: dt.datetime) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
        stamp = dt.datetime.fromisoformat(value["observed_at"])
        if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= _FRESH_SECONDS:
            return None
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _index_plans(db: sqlite3.Connection, directory: Path) -> list[str]:
    """Retain attribution beyond plan-file retention; reject altered archived plans."""
    problems = []
    for path in sorted(directory.glob("*.json")):
        try:
            body = path.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            prior = db.execute("SELECT sha256 FROM plans WHERE path=?", (path.name,)).fetchone()
            if prior:
                if prior[0] != digest:
                    problems.append(path.name)
                continue
            plan = parse_plan(json.loads(body))
            entries = [
                (
                    client_order_id_for(plan, intent),
                    plan.config_sha256,
                    digest,
                    intent.symbol,
                    intent.side,
                    plan.earliest_execution_session.isoformat(),
                )
                for intent in plan.intents
            ]
            for entry in entries:
                known = db.execute(
                    "SELECT config_sha256,plan_sha256,symbol,side,session FROM intents "
                    "WHERE client_order_id=?",
                    (entry[0],),
                ).fetchone()
                if known and tuple(known) != entry[1:]:
                    raise ValueError("ambiguous intent identity")
            db.executemany("INSERT OR IGNORE INTO intents VALUES(?,?,?,?,?,?)", entries)
            db.execute("INSERT INTO plans VALUES(?,?,?)", (path.name, digest, body.decode()))
        except (OSError, ValueError, TypeError, KeyError, ArithmeticError, ExecutorError):
            problems.append(path.name)
    return problems


def _incident(
    db: sqlite3.Connection,
    *,
    kind: str,
    subject: str,
    failing: bool | None,
    now: dt.datetime,
    details: dict[str, Any],
    persistent: bool = False,
) -> None:
    """Consecutive distinct observations, elapsed-time floor, and no clear on missing data."""
    key = hashlib.sha256(f"{kind}:{subject}".encode()).hexdigest()[:24]
    previous = db.execute("SELECT * FROM incidents WHERE id=?", (key,)).fetchone()
    if not previous and failing is not True:
        return
    stamp = now.isoformat()
    if previous and dt.datetime.fromisoformat(previous["last_observed"]) >= now:
        return
    old = previous["status"] if previous else "clear"
    streak = previous["streak"] if previous else 0
    first = previous["first_failure"] if previous else None
    opened = previous["opened_at"] if previous else None
    resolved = previous["resolved_at"] if previous else None
    if failing is None:
        streak, first = 0, None
        state = "unconfirmed" if old in {"open", "unconfirmed"} else "clear"
    elif failing:
        streak += 1
        first = first or stamp
        mature = not persistent or (
            streak >= _STREAK_REQUIRED
            and (now - dt.datetime.fromisoformat(first)).total_seconds() >= _STREAK_SECONDS
        )
        state = "open" if mature or old in {"open", "unconfirmed"} else "pending"
        if state == "open" and old not in {"open", "unconfirmed"}:
            opened, resolved = stamp, None
    else:
        streak, first = 0, None
        state = "resolved" if opened else "clear"
        if old != state:
            resolved = stamp
    db.execute(
        "INSERT OR REPLACE INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            key,
            kind,
            subject,
            state,
            streak,
            first,
            stamp,
            opened,
            resolved,
            json.dumps(details, sort_keys=True),
        ),
    )
    if state != old:
        db.execute(
            "INSERT INTO events(observed_at,kind,subject,previous_state,state) VALUES(?,?,?,?,?)",
            (stamp, kind, subject, old, state),
        )


def _index_batches(db: sqlite3.Connection, directory: Path) -> list[str]:
    """Attribute scaled order IDs only through a matching retained original plan."""
    problems = []
    for path in sorted(directory.glob("*.json")):
        try:
            state = load_batch(path)
            archived = db.execute(
                "SELECT sha256,payload FROM plans WHERE path=?", (path.name,)
            ).fetchone()
            if archived is None:
                raise ValueError("batch original plan is unavailable")
            plan = parse_plan(json.loads(archived[1]))
            if state["plan_sha256"] != batch_digest(plan.raw):
                raise ValueError("batch original plan changed")
            for row in state["intents"]:
                if not row.get("order"):
                    continue
                original = next(
                    i for i in plan.intents if i.symbol == row["symbol"] and i.side == row["side"]
                )
                if Decimal(row["original_authorized_quantity"]) != original.quantity:
                    raise ValueError("batch original quantity changed")
                actual = _effective(plan, row)
                identity = client_order_id_for(plan, actual)
                if identity != row["order"]["client_order_id"]:
                    raise ValueError("batch client identity mismatch")
                entry = (
                    identity,
                    plan.config_sha256,
                    archived[0],
                    row["symbol"],
                    row["side"],
                    plan.earliest_execution_session.isoformat(),
                )
                known = db.execute(
                    "SELECT config_sha256,plan_sha256,symbol,side,session "
                    "FROM intents WHERE client_order_id=?",
                    (identity,),
                ).fetchone()
                if known and tuple(known) != entry[1:]:
                    raise ValueError("ambiguous batch attribution")
                db.execute("INSERT OR IGNORE INTO intents VALUES(?,?,?,?,?,?)", entry)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            ArithmeticError,
            ExecutorError,
            StopIteration,
        ):
            problems.append("batch:" + path.name)
    return problems


def _orders(
    db: sqlite3.Connection, registry: dict[str, Any] | None, now: dt.datetime
) -> tuple[set[str], dict[str, int]]:
    """Attribute only exact recorded client IDs plus symbol/side, never ticker alone."""
    active_configs: set[str] = set()
    counts = {"matched": 0, "unattributed_outstanding": 0, "identity_mismatch": 0}
    rows = registry.get("orders", {}) if registry else {}
    observed_subjects: set[str] = set()
    for key, row in rows.items():
        observed_subjects.add(key)
        fresh = _row_available(row, now)
        known = db.execute(
            "SELECT * FROM intents WHERE client_order_id=?", (row.get("client_order_id"),)
        ).fetchone()
        matched = known is not None and (known["symbol"], known["side"]) == (
            row.get("symbol"),
            row.get("side"),
        )
        outstanding = not row.get("terminal", False)
        if matched:
            counts["matched"] += 1
            if fresh and Decimal(row["filled_quantity"]) > 0:
                active_configs.add(known["config_sha256"])
        elif outstanding:
            counts["unattributed_outstanding"] += 1
        mismatch = known is not None and not matched
        counts["identity_mismatch"] += int(mismatch)
        data = {"symbol": row.get("symbol"), "broker_status": row.get("broker_status")}
        for kind, failing in (
            ("unattributed_order", outstanding and not matched),
            ("order_identity_mismatch", mismatch),
        ):
            _incident(
                db,
                kind=kind,
                subject=key,
                failing=failing if fresh else None,
                now=now,
                details=data,
            )
        # Age of submission, not age of the cancellation request.
        try:
            submitted = dt.datetime.fromisoformat(row["submitted_at"])
            aged = outstanding and (now - submitted).total_seconds() > _AGED_ORDER_SECONDS
        except (KeyError, TypeError, ValueError):
            aged = None
        _incident(
            db,
            kind="aged_outstanding_order",
            subject=key,
            failing=aged if fresh else None,
            now=now,
            details=data,
        )
    for previous in db.execute(
        "SELECT kind,subject FROM incidents WHERE kind IN "
        "('unattributed_order','order_identity_mismatch','aged_outstanding_order')"
    ).fetchall():
        if previous["subject"] not in observed_subjects:
            _incident(
                db,
                kind=previous["kind"],
                subject=previous["subject"],
                failing=None,
                now=now,
                details={"reason": "order_observation_missing"},
            )
    return active_configs, counts


def _row_available(row: dict[str, Any], now: dt.datetime) -> bool:
    try:
        stamp = dt.datetime.fromisoformat(row["observed_at"])
        return (
            row["registry_status"] != "observation_unavailable"
            and stamp.tzinfo is not None
            and 0 <= (now - stamp).total_seconds() <= _FRESH_SECONDS
        )
    except (ValueError, TypeError, KeyError):
        return False


def refresh_operations(settings: ExecutorSettings, now: dt.datetime) -> dict[str, Any]:
    """Observe the pinned deployment and publish bounded review flags on every daemon tick."""
    root = settings.state_root
    root.mkdir(parents=True, exist_ok=True)
    if now.tzinfo is None:
        raise ValueError("operations require an aware observation time")
    orders = _read_fresh(root / "order-registry.json", now)
    portfolio = _read_fresh(root / "portfolio-monitor.json", now)
    if orders is not None and (
        not isinstance(orders.get("orders"), dict)
        or not all(isinstance(row, dict) for row in orders["orders"].values())
    ):
        orders = None
    if portfolio is not None and portfolio.get("status") not in {
        "baseline_recorded",
        "reconciled",
        "discrepancy",
        "observation_unavailable",
    }:
        portfolio = None
    config = hashlib.sha256(settings.strategy_config.read_bytes()).hexdigest()
    with closing(sqlite3.connect(root / "deployment-registry.sqlite3", timeout=10)) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError("unsupported deployment registry version")
        db.executescript(_SCHEMA)
        db.execute("PRAGMA user_version=1")
        db.execute("BEGIN IMMEDIATE")
        problems = _index_plans(db, settings.plans_dir)
        problems.extend(_index_batches(db, settings.state_root / "batches"))
        for kind, failing in (
            ("order_observation_unavailable", orders is None),
            (
                "portfolio_observation_unavailable",
                portfolio is None or portfolio.get("status") == "observation_unavailable",
            ),
            ("plan_evidence_invalid", bool(problems)),
        ):
            _incident(
                db,
                kind=kind,
                subject="account",
                failing=failing,
                now=now,
                details={"invalid_plan_files": problems} if problems else {},
            )
        _incident(
            db,
            kind="portfolio_discrepancy",
            subject="account",
            now=dt.datetime.fromisoformat(portfolio["observed_at"]) if portfolio else now,
            persistent=True,
            failing=(portfolio["status"] == "discrepancy")
            if portfolio and portfolio.get("status") in {"reconciled", "discrepancy"}
            else None,
            details={
                k: portfolio[k]
                for k in ("cash_difference", "position_differences")
                if portfolio and k in portfolio
            },
        )
        active_configs, ownership = _orders(db, orders, now)
        authorized = config in settings.limits.authorized_config_sha256
        _incident(
            db,
            kind="configuration_not_authorized",
            subject=config,
            failing=not authorized,
            now=now,
            details={},
        )
        active = db.execute(
            "SELECT * FROM incidents WHERE status IN ('open','unconfirmed') ORDER BY opened_at,id"
        ).fetchall()
        prior = db.execute("SELECT * FROM deployments WHERE config_sha256=?", (config,)).fetchone()
        if prior and dt.datetime.fromisoformat(prior["last_seen"]) > now:
            raise ValueError("deployment clock moved backwards")
        observed_fill = config in active_configs or bool(
            prior and json.loads(prior["details"]).get("matched_fill_observed")
        )
        authorized_state = (
            "live_authorized" if settings.trading_mode == "live" else "paper_approved"
        )
        state = (
            "unapproved"
            if not authorized
            else ("watch" if active else "active" if observed_fill else authorized_state)
        )
        deployment = {
            "config_sha256": config,
            "trading_mode": settings.trading_mode,
            "state": state,
            "authorized": authorized,
            "approval_source": "configured_digest_allowlist",
            "research_validation": "not_imported",
            "matched_fill_observed": observed_fill,
            "submission_mode": "disabled"
            if settings.limits.disabled
            or (settings.trading_mode == "live" and not settings.live_trading_enabled)
            else "dry_run"
            if settings.dry_run
            else "enabled",
            "execution_authority": "none",
            "monitoring_mode": "shadow",
        }
        stamp = now.isoformat()
        db.execute(
            "INSERT OR REPLACE INTO deployments VALUES(?,?,?,?,?)",
            (
                config,
                state,
                json.dumps(deployment, sort_keys=True),
                prior["first_seen"] if prior else stamp,
                stamp,
            ),
        )
        if prior is None or prior["state"] != state:
            db.execute(
                "INSERT INTO events(observed_at,kind,subject,previous_state,state) "
                "VALUES(?,?,?,?,?)",
                (stamp, "deployment", config, prior["state"] if prior else None, state),
            )
        window = observe_window(
            db,
            config=config,
            now=now,
            market_open=bool(orders and orders.get("market_is_open")),
            healthy=not active
            and portfolio is not None
            and portfolio.get("status") == "reconciled",
        )
        db.commit()
        flags = [
            {
                "id": r["id"],
                "kind": r["kind"],
                "status": r["status"],
                "opened_at": r["opened_at"],
                "details": json.loads(r["details"]),
                "category": "operational",
                "execution_authority": "none",
            }
            for r in active[:_MAX_REVIEW_FLAGS]
        ]
        result = {
            "schema": "operations/1",
            "status": "observed",
            "observed_at": stamp,
            "deployment": deployment,
            "ownership": ownership,
            "observation_window": window,
            "active_incident_count": len(active),
            "flags": flags,
            "omitted_flag_count": max(0, len(active) - len(flags)),
            "statistical_monitoring": "not_ready_no_frozen_performance_baseline",
        }
        _write(root / "operations.json", result)
        return result


def observe_operations(settings: ExecutorSettings, now: dt.datetime) -> dict[str, Any]:
    """A failed observation cannot leave a fresh, apparently successful projection."""
    try:
        return refresh_operations(settings, now)
    except (
        ExecutorError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ArithmeticError,
        sqlite3.Error,
    ) as exc:
        result = {
            "schema": "operations/1",
            "status": "observation_unavailable",
            "observed_at": now.isoformat(),
            "error_type": type(exc).__name__,
            "execution_authority": "none",
        }
        _write(settings.state_root / "operations.json", result)
        return result
