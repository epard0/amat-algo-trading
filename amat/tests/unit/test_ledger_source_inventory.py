"""Legacy source maps preserve rejected candidates and never emit private payload text."""

import hashlib
import json
import sqlite3

import pytest

from autoresearch.improvements.ledger_source_inventory import inventory_session


def fixture(root):
    source = root / "runtime/evaluator.py"
    source.parent.mkdir(parents=True)
    source.write_text("# retained evaluator\n")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    program = {
        "schema_version": 1,
        "baseline": {},
        "frozen_files": {"runtime/evaluator.py": digest},
        "measured_start": "2018-01-02",
        "chain": "PRIVATE_PROVIDER_TEXT",
    }
    path = root / "program.json"
    path.write_text(json.dumps(program))
    path.with_suffix(".sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    database = root / "research.sqlite3"
    db = sqlite3.connect(database)
    db.execute("create table experiments(id integer,candidate text,status text,result text)")
    result = {
        "fit_count": 3,
        "entry_model_fit_count": 2,
        "folds": [{}, {}],
        "eligible": False,
        "diagnostics": "PRIVATE_PROVIDER_TEXT",
    }
    db.execute(
        "insert into experiments values(1,?,?,?)", ("a" * 64, "rejected", json.dumps(result))
    )
    db.execute("insert into experiments values(2,?,?,?)", ("b" * 64, "queued", None))
    db.commit()
    db.close()
    candidates = root / "candidates"
    candidates.mkdir()
    (candidates / ("a" * 64 + ".json")).write_text(
        json.dumps({"config": {}, "features": [], "rationale": "PRIVATE_PROVIDER_TEXT"})
    )
    return database, source


def test_source_map_is_read_only_preserves_rejections_and_redacts_payload(tmp_path):
    db, source = fixture(tmp_path)
    before = db.read_bytes()
    result = inventory_session(db)
    assert db.read_bytes() == before
    assert result["status"] == "requires_contract_review"
    assert result["program_seal_status"] == "verified_raw_sha256"
    assert len(result["experiments"]) == 2
    assert result["experiments"][0]["status"] == "rejected"
    assert result["experiments"][0]["fit_count"] == 3
    assert result["experiments"][0]["entry_model_fit_count"] == 2
    assert result["experiments"][0]["fold_count"] == 2
    assert "PRIVATE_PROVIDER_TEXT" not in json.dumps(result)
    assert result["source_file_checks"]["runtime/evaluator.py"]["status"] == "verified"
    source.write_text("# changed evaluator\n")
    assert (
        inventory_session(db)["source_file_checks"]["runtime/evaluator.py"]["status"]
        == "missing_or_changed"
    )


def test_inventory_does_not_create_missing_ledger(tmp_path):
    (tmp_path / "program.json").write_text("{}")
    database = tmp_path / "research.sqlite3"
    with pytest.raises(FileNotFoundError):
        inventory_session(database)
    assert not database.exists()


def test_static_contract_inspection_does_not_execute_source(tmp_path):
    from autoresearch.improvements.ledger_source_inventory import static_native_contract

    sentinel = tmp_path / "executed"
    evaluator = tmp_path / "evaluator.py"
    evaluator.write_text(f"""open({str(sentinel)!r},'w').write('must not run')
def run(self):
    return run_backtest(strategy, panel, universe, decision_overlay=overlay,
        signal_override=scores, one_way_bps=cost,
        max_entry_window_participation=0.01 if schema == 'v7' else None)
""")
    runner = tmp_path / "runner.py"
    runner.write_text(
        "def run_backtest(strategy,panel,universe,*,execution_start=None,"
        "initial_cash=100000,max_entry_window_participation=None): pass\n"
    )
    result = static_native_contract(evaluator, runner)
    assert not sentinel.exists()
    call = result["calls"][0]
    assert call["function"] == "run"
    assert call["opening_cap_expression"]["kind"] == "IfExp"
    assert not call["explicit_execution_start"]
    assert not call["explicit_whole_share_buys"]
    assert call["uses_decision_overlay"] and call["uses_signal_override"]
    assert result["runner_defaults"]["execution_start"]["value"] is None
    assert not result["runner_defaults"]["whole_share_buys"]["present_in_signature"]
    assert "must not run" not in json.dumps(result)


def test_static_contract_preserves_explicit_policy_and_unknown_unpack(tmp_path):
    from autoresearch.improvements.ledger_source_inventory import static_native_contract

    evaluator = tmp_path / "evaluator.py"
    evaluator.write_text(
        "def run():\n return run_backtest(s,p,u,execution_start=start,"
        "whole_share_buys=True,**kwargs)\n"
    )
    runner = tmp_path / "runner.py"
    runner.write_text("def run_backtest(s,p,u,*,whole_share_buys=False): pass\n")
    result = static_native_contract(evaluator, runner)
    assert result["calls"][0]["explicit_execution_start"]
    assert result["calls"][0]["explicit_whole_share_buys"]
    assert result["calls"][0]["has_keyword_unpack"]
    assert result["runner_defaults"]["whole_share_buys"] == {
        "kind": "literal",
        "value": False,
        "present_in_signature": True,
    }
