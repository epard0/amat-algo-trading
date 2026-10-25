"""Global strategy registry is explicit, read-only and does not expose stored payloads."""

import hashlib
import json
import sqlite3

import pytest

from autoresearch.improvements.campaign_audit import inventory_global_strategy_ledger


def test_global_registry_preserves_identities_metrics_and_payload_hashes(tmp_path):
    path = tmp_path / "best-strategies.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE strategies(session TEXT,candidate TEXT,comparison_group TEXT,
          annual_return REAL,score REAL,eligible INTEGER,payload TEXT);
        CREATE TABLE return_references(id TEXT,payload TEXT);
    """)
    payload = '{"private_provider_response":"DO_NOT_DISCLOSE"}'
    db.execute(
        "INSERT INTO strategies VALUES(?,?,?,?,?,?,?)",
        ("run1", "candidate2", "group", 0.5, float("inf"), 0, payload),
    )
    db.execute("INSERT INTO return_references VALUES(?,?)", ("baseline", payload))
    db.commit()
    db.close()
    before = path.read_bytes()
    result = inventory_global_strategy_ledger(path)
    assert result["status"] == "requires_contract_replay"
    assert result["strategies"][0]["candidate"] == "candidate2"
    assert result["strategies"][0]["annual_return_text"] == "0.5"
    assert result["strategies"][0]["payload_sha256"] == hashlib.sha256(payload.encode()).hexdigest()
    assert result["return_references"][0]["id"] == "baseline"
    assert "DO_NOT_DISCLOSE" not in json.dumps(result, allow_nan=False)
    assert path.read_bytes() == before
    assert (
        inventory_global_strategy_ledger(path)["logical_snapshot_sha256"]
        == (result["logical_snapshot_sha256"])
    )


def test_missing_registry_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        inventory_global_strategy_ledger(path)
    assert not path.exists()
