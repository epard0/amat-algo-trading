"""Executable schema constraints for research lineage and resource accounting."""
import sqlite3
from pathlib import Path

import pytest

DDL = Path(__file__).resolve().parents[2] / "schemas/funded_research_v1.sql"


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.executescript(DDL.read_text())
    connection.execute(
        "INSERT INTO artifact VALUES(?,?,?,?,?,?,?,?)",
        ("a", "a" * 64, "urn:test", "application/json", 2, "internal", 1, "{}"),
    )
    connection.execute(
        "INSERT INTO campaign VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("c", "MNQ research", 1, "a", 50_000_000_000, 3_000_000_000, 30,
         "research", 10, "{}", "test"),
    )
    yield connection
    connection.close()


def test_schema_installs_and_enables_foreign_keys(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] >= 40


def test_campaign_amounts_and_immutability(db):
    assert db.execute("SELECT target_profit_usd_micros FROM campaign").fetchone()[0] == 3_000_000_000
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE campaign SET target_profit_usd_micros=300 WHERE id='c'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM campaign")


def test_artifact_evidence_cannot_be_replaced(db):
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE artifact SET sha256=? WHERE id='a'", ("b" * 64,))


def test_unknown_usage_retains_reservation_and_blocks_more_work(db):
    db.execute("INSERT INTO resource_budget VALUES('b','c','tokens',100,1,1000)")
    db.execute("INSERT INTO budget_reservation VALUES('r','b','task1',80,2)")
    db.execute("INSERT INTO budget_settlement VALUES('r',NULL,'unknown',3)")
    assert db.execute("SELECT charged_amount FROM budget_usage WHERE id='b'").fetchone()[0] == 80
    with pytest.raises(sqlite3.IntegrityError, match="budget exhausted"):
        db.execute("INSERT INTO budget_reservation VALUES('r2','b','task2',21,4)")


def test_released_usage_and_duplicate_settlement(db):
    db.execute("INSERT INTO resource_budget VALUES('b','c','tokens',100,1,1000)")
    db.execute("INSERT INTO budget_reservation VALUES('r','b','task1',100,2)")
    db.execute("INSERT INTO budget_settlement VALUES('r',0,'released',3)")
    assert db.execute("SELECT charged_amount FROM budget_usage WHERE id='b'").fetchone()[0] == 0
    db.execute("INSERT INTO budget_reservation VALUES('r2','b','task2',100,4)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO budget_settlement VALUES('r',0,'released',5)")


def test_foreign_key_and_execution_scope_validation(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO resource_budget VALUES('b','missing','tokens',100,1,1000)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO campaign VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("live", "invalid", 1, "a", 1, 1, 30, "live", 10, "{}", "test"))


def test_invalid_json_is_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO artifact VALUES(?,?,?,?,?,?,?,?)",
                   ("bad", "b"*64, "urn:bad", "application/json", 1, "internal", 1, "not-json"))


def test_task_projection_keeps_append_only_history(db):
    db.execute("INSERT INTO iteration VALUES('i','c',0,NULL,NULL,NULL,'a',1)")
    db.execute("INSERT INTO work_item VALUES('w','i','simulate','a','unique',1,10,NULL,1)")
    db.execute("INSERT INTO task_event VALUES('e1','w',0,'queued',NULL,1,NULL,'{}')")
    db.execute("INSERT INTO task_event VALUES('e2','w',1,'leased','worker',2,10,'{}')")
    assert db.execute("SELECT event FROM latest_task_state").fetchone()[0] == "leased"
    assert db.execute("SELECT count(*) FROM task_event").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE task_event SET event='succeeded' WHERE id='e2'")
