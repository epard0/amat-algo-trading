import json

import pytest

from autoresearch.genetic.shadow_ledger import read_shadow_ledger, record_shadow_observation


def observation(day="2026-09-08", state="observing"):
    return {
        "schema_version": "genetic-shadow-observation/1",
        "artifact_sha256": "a" * 64,
        "panel_sha256": "b" * 64,
        "as_of": day,
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "recommended_monitor_state": state,
        "alerts": [],
        "latency_ms": 1.0,
        "assets": [
            {
                "baseline_score": 0.5,
                "reported_score": 0.5 if state == "suspended" else 0.7,
                "bounded_contribution": 0.0 if state == "suspended" else 0.2,
            }
        ],
    }


def test_idempotent_timing_and_changed_session_refusal(tmp_path):
    path = tmp_path / "ledger.json"
    report = observation()
    first = record_shadow_observation(path, report)
    before = path.read_bytes()
    report["latency_ms"] = 100
    assert record_shadow_observation(path, report) == first
    assert path.read_bytes() == before
    report["panel_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="same-session"):
        record_shadow_observation(path, report)
    assert path.read_bytes() == before


def test_suspension_latches_neutralizes_under_lock_and_retries_idempotently(tmp_path):
    path = tmp_path / "ledger.json"
    record_shadow_observation(path, observation(state="suspended"))
    next_report = observation("2026-09-09")
    record = record_shadow_observation(path, next_report)
    assert next_report["recommended_monitor_state"] == "observing"
    assert record["effective_monitor_state"] == "suspended"
    assert record["report"]["assets"][0]["bounded_contribution"] == 0
    assert record_shadow_observation(path, next_report) == record
    assert len(read_shadow_ledger(path)) == 2


@pytest.mark.parametrize("change", ["truncate", "body", "chain"])
def test_corrupted_or_truncated_ledger_refuses_append(tmp_path, change):
    path = tmp_path / "ledger.json"
    record_shadow_observation(path, observation())
    record_shadow_observation(path, observation("2026-09-09"))
    payload = json.loads(path.read_text())
    if change == "truncate":
        payload["records"].pop()
    elif change == "body":
        payload["records"][0]["report"]["panel_sha256"] = "c" * 64
    else:
        payload["records"][1]["previous_sha256"] = "0" * 64
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    with pytest.raises(ValueError, match=r"mismatch|integrity"):
        record_shadow_observation(path, observation("2026-09-10"))
    assert path.read_bytes() == before


def test_artifact_switch_rewind_authority_and_order_fields_refused(tmp_path):
    path = tmp_path / "ledger.json"
    record_shadow_observation(path, observation())
    cases = [
        {**observation("2026-09-09"), "artifact_sha256": "c" * 64},
        observation("2026-09-07"),
        {**observation("2026-09-09"), "execution_authority": "paper"},
        {**observation("2026-09-09"), "details": {"order_intents": []}},
        {**observation("2026-09-09"), "as_of": None},
        {**observation("2026-09-09"), "recommended_monitor_state": "suspended"},
    ]
    for report in cases:
        with pytest.raises(ValueError):
            record_shadow_observation(path, report)
    assert len(read_shadow_ledger(path)) == 1
