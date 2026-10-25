import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from amat.data.synthetic import generate_panel
from autoresearch.genetic import shadow_refresh as refresh
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.replay import attach_pattern, load_incumbent
from autoresearch.genetic.shadow_artifact import freeze_shadow_artifact

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(monkeypatch, tmp_path):
    base = load_incumbent(ROOT / "configs/strategy/amat_v13_return_target.toml")
    panel = generate_panel(base.config.strategy.to_universe(), sessions=550)
    panel = replace(
        panel,
        adjusted_opens=panel.adjusted_closes * 0.999,
        adjusted_highs=panel.adjusted_closes * 1.01,
        adjusted_lows=panel.adjusted_closes * 0.99,
    )
    pattern = parse_pattern(
        json.loads((ROOT / "docs/genetic-patterns/symbolic.example.json").read_text())
    )
    candidate = attach_pattern(base, pattern)
    artifact_path = tmp_path / "previous.json"
    envelope = freeze_shadow_artifact(
        candidate, panel, provenance={"old": True}, output=artifact_path
    )
    manifest = SimpleNamespace(
        first_session=panel.sessions[0],
        last_session=panel.sessions[-1],
        provider="alpaca",
        feed="sip",
        execution_window_start=dt.time(9, 30),
        execution_window_end=dt.time(9, 35),
        exchange_timezone="America/New_York",
        model_dump=lambda **_: {"snapshot_id": "old"},
    )
    monkeypatch.setattr(refresh, "load_panel", lambda *_, **__: (panel, manifest))
    monkeypatch.setattr(refresh, "audit_sessions", lambda *_, **__: {"status": "matched"})
    monkeypatch.setattr(refresh, "_windows", lambda *_, **__: [])
    monkeypatch.setattr(
        refresh, "reconciliation_report", lambda *_, **__: {"status": "passed", "issues": []}
    )
    writes = []

    def write(root, snapshot_id, **kwargs):
        writes.append(kwargs)
        return root / "data/snapshots" / snapshot_id, manifest

    monkeypatch.setattr(refresh, "write_snapshot", write)
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    end = panel.sessions[-1] + dt.timedelta(days=4)
    for kind in ["raw", "all", "actions"]:
        payload = {
            "provider": "alpaca",
            "feed": "sip",
            "symbols": list(panel.symbols),
            "start": panel.sessions[0].isoformat(),
            "end": end.isoformat(),
            "received_at": dt.datetime.combine(
                end + dt.timedelta(days=1), dt.time(), dt.UTC
            ).isoformat(),
            "kind": kind,
            "records": []
            if kind == "actions"
            else [
                {
                    "symbol": "SPY",
                    "session_date": panel.sessions[0].isoformat(),
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 100.0,
                    "vwap": 100.0,
                    "trade_count": 10,
                }
            ],
        }
        (receipts / f"{kind}.json").write_text(
            json.dumps({"payload": payload, "payload_sha256": digest_json(payload)})
        )
    kwargs = {
        "previous_artifact": artifact_path,
        "old_snapshot": tmp_path / "old",
        "receipts": receipts,
        "root": tmp_path,
        "snapshot_id": "fresh",
        "baseline_end": end,
        "output": tmp_path / "refresh",
    }
    return kwargs, envelope, panel, writes


def test_refresh_preserves_formula_calibration_and_removes_approval(setup):
    kwargs, previous, _, writes = setup
    report = refresh.refresh_shadow(**kwargs)
    artifact = json.loads((kwargs["output"] / "artifact.json").read_text())
    assert artifact["payload"]["candidate"] == previous["payload"]["candidate"]
    assert artifact["payload"]["calibration"] == previous["payload"]["calibration"]
    assert report["provenance"]["historical_validation_approved"] is False
    assert report["promotion_authority"] == "none"
    assert len(writes) == 1


def test_reconciliation_refuses_without_writes(setup, monkeypatch):
    kwargs, _, _, writes = setup
    monkeypatch.setattr(
        refresh,
        "reconciliation_report",
        lambda *_, **__: {"status": "refused", "issues": ["revision"]},
    )
    with pytest.raises(ValueError, match="reconciliation refused"):
        refresh.refresh_shadow(**kwargs)
    assert not writes
    assert not kwargs["output"].exists()


def test_original_calibration_change_refuses(setup):
    kwargs, _, panel, writes = setup
    panel.adjusted_opens[10, 0] *= 1.001
    with pytest.raises(ValueError, match="original calibration panel"):
        refresh.refresh_shadow(**kwargs)
    assert not writes


def test_receipt_checksum_refuses(setup):
    kwargs, _, _, writes = setup
    path = kwargs["receipts"] / "raw.json"
    envelope = json.loads(path.read_text())
    envelope["payload"]["records"][0]["close"] = 999
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="checksum"):
        refresh.refresh_shadow(**kwargs)
    assert not writes


def test_changed_registry_requires_explicit_formula_review(setup):
    kwargs, _, _, writes = setup
    envelope = json.loads(kwargs["previous_artifact"].read_text())
    envelope["payload"]["candidate"]["genetic_pattern"]["registry_sha256"] = "a" * 64
    envelope["payload"]["candidate_sha256"] = digest_json(envelope["payload"]["candidate"])
    envelope["payload_sha256"] = digest_json(envelope["payload"])
    kwargs["previous_artifact"].write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="registry binding mismatch"):
        refresh.refresh_shadow(**kwargs)
    assert not writes


def test_immutable_output_and_snapshot_name(setup):
    kwargs, _, _, writes = setup
    with pytest.raises(ValueError, match="path component"):
        refresh.refresh_shadow(**{**kwargs, "snapshot_id": "../bad"})
    kwargs["output"].mkdir()
    with pytest.raises(FileExistsError):
        refresh.refresh_shadow(**kwargs)
    assert not writes


def _supplement_file(receipts, date, source_file="issuer.json"):
    import hashlib

    source = receipts / source_file
    source.write_text('{"dividend": 0.5}')
    payload = {
        "schema_version": "shadow-action-supplements/1",
        "received_at": dt.datetime.now(dt.UTC).isoformat(),
        "records": [
            {
                "symbol": "SPY",
                "ex_date": date.isoformat(),
                "rate": 0.5,
                "source_file": source_file,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_url": "https://issuer.example/dividends",
                "source_record": {"dividend": 0.5},
            }
        ],
    }
    (receipts / "action_supplements.json").write_text(
        json.dumps({"payload": payload, "payload_sha256": digest_json(payload)})
    )


def test_explicit_new_dividend_supplement_retains_separate_authority(setup):
    kwargs, _, _, writes = setup
    _supplement_file(kwargs["receipts"], kwargs["baseline_end"])
    result = refresh.refresh_shadow(**kwargs)
    assert writes[0]["dividends"][0].rate == 0.5
    assert "not an Alpaca" in result["provenance"]["action_supplement"]["authority"]


@pytest.mark.parametrize("mode", ["history", "escape", "tamper"])
def test_invalid_supplement_refuses_before_write(setup, mode):
    kwargs, _, panel, writes = setup
    date = panel.sessions[-1] if mode == "history" else kwargs["baseline_end"]
    _supplement_file(
        kwargs["receipts"], date, "../issuer.json" if mode == "escape" else "issuer.json"
    )
    if mode == "tamper":
        (kwargs["receipts"] / "issuer.json").write_text("changed")
    with pytest.raises(ValueError, match="action supplement"):
        refresh.refresh_shadow(**kwargs)
    assert not writes


def test_future_receipt_clock_refuses_before_writes(setup):
    kwargs, _, _, writes = setup
    path = kwargs["receipts"] / "raw.json"
    envelope = json.loads(path.read_text())
    envelope["payload"]["received_at"] = (
        dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    ).isoformat()
    envelope["payload_sha256"] = digest_json(envelope["payload"])
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="receipt coverage"):
        refresh.refresh_shadow(**kwargs)
    assert not writes
