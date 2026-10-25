"""Observable lifecycle without trading authority or fabricated validation."""

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest
from tests.test_daemon import _settings
from tests.test_plan_source import _plan_payload

from executor.operations import observe_operations, refresh_operations
from executor.plan_source import parse_plan
from executor.submit import client_order_id_for

NOW = dt.datetime(2026, 9, 21, 12, tzinfo=dt.UTC)


def setup(tmp_path):
    settings = _settings(tmp_path)
    settings.ensure_directories()
    snapshots(settings, NOW)
    return settings


def snapshots(settings, now, *, orders=None, portfolio="reconciled"):
    (settings.state_root / "order-registry.json").write_text(
        json.dumps(
            {
                "observed_at": now.isoformat(),
                "market_is_open": False,
                "orders": orders or {},
            }
        )
    )
    (settings.state_root / "portfolio-monitor.json").write_text(
        json.dumps(
            {
                "observed_at": now.isoformat(),
                "status": portfolio,
                "cash_difference": "10" if portfolio == "discrepancy" else "0",
                "position_differences": {},
            }
        )
    )


def order(now=NOW, *, client_id="outside", filled="0", terminal=False, status="pending_cancel"):
    return {
        "client_order_id": client_id,
        "symbol": "SPY",
        "side": "buy",
        "filled_quantity": filled,
        "quantity": "15.9684",
        "terminal": terminal,
        "broker_status": status,
        "registry_status": "pending_market_closed" if not terminal else status,
        "observed_at": now.isoformat(),
        "submitted_at": now.isoformat(),
    }


def plan_file(settings, **changes):
    config = hashlib.sha256(settings.strategy_config.read_bytes()).hexdigest()
    payload = _plan_payload(config_sha256=config, **changes)
    path = settings.plans_dir / "2026-09-21.json"
    path.write_text(json.dumps(payload))
    return path, parse_plan(payload)


def events(settings):
    with sqlite3.connect(settings.state_root / "deployment-registry.sqlite3") as db:
        return db.execute("SELECT kind,previous_state,state FROM events ORDER BY id").fetchall()


def test_authorization_is_not_a_validation_or_fill_claim(tmp_path):
    settings = setup(tmp_path)
    result = refresh_operations(settings, NOW)
    assert result["deployment"]["state"] == "paper_approved"
    assert result["deployment"]["research_validation"] == "not_imported"
    assert result["deployment"]["matched_fill_observed"] is False
    assert result["deployment"]["execution_authority"] == "none"
    assert result["active_incident_count"] == 0
    assert result["statistical_monitoring"] == "not_ready_no_frozen_performance_baseline"


def test_exact_plan_attribution_and_retained_evidence_after_pruning(tmp_path):
    settings = setup(tmp_path)
    path, plan = plan_file(settings)
    original = path.read_bytes()
    client_id = client_order_id_for(plan, plan.intents[0])
    snapshots(settings, NOW, orders={"one": order(client_id=client_id, filled="1")})
    result = refresh_operations(settings, NOW)
    assert result["ownership"]["matched"] == 1
    assert result["deployment"]["state"] == "active"
    path.unlink()
    later = NOW + dt.timedelta(minutes=4)
    snapshots(settings, later, orders={"one": order(later, client_id=client_id, filled="1")})
    assert refresh_operations(settings, later)["deployment"]["state"] == "active"
    with sqlite3.connect(settings.state_root / "deployment-registry.sqlite3") as db:
        retained = db.execute("SELECT payload FROM plans").fetchone()[0].encode()
    assert retained == original


def test_symbol_alone_never_proves_ownership_and_repeats_are_deduplicated(tmp_path):
    settings = setup(tmp_path)
    plan_file(settings)
    snapshots(settings, NOW, orders={"one": order(filled="1")})
    first = refresh_operations(settings, NOW)
    before = events(settings)
    refresh_operations(settings, NOW)
    assert events(settings) == before
    assert first["deployment"]["state"] == "watch"
    assert first["deployment"]["matched_fill_observed"] is False
    assert first["flags"][0]["kind"] == "unattributed_order"


def test_terminal_confirmation_resolves_but_missing_order_does_not(tmp_path):
    settings = setup(tmp_path)
    snapshots(settings, NOW, orders={"one": order()})
    refresh_operations(settings, NOW)
    later = NOW + dt.timedelta(minutes=4)
    snapshots(settings, later)
    result = refresh_operations(settings, later)
    assert result["flags"][0]["status"] == "unconfirmed"
    later += dt.timedelta(minutes=4)
    snapshots(settings, later, orders={"one": order(later, terminal=True, status="canceled")})
    assert refresh_operations(settings, later)["active_incident_count"] == 0


def test_discrepancy_requires_three_distinct_observations_and_resets(tmp_path):
    settings = setup(tmp_path)
    for minutes in (0, 4, 8):
        now = NOW + dt.timedelta(minutes=minutes)
        snapshots(settings, now, portfolio="discrepancy")
        result = refresh_operations(settings, now)
        assert result["active_incident_count"] == (1 if minutes == 8 else 0)
    now = NOW + dt.timedelta(minutes=12)
    snapshots(settings, now)
    assert refresh_operations(settings, now)["active_incident_count"] == 0
    now += dt.timedelta(minutes=4)
    snapshots(settings, now, portfolio="discrepancy")
    assert refresh_operations(settings, now)["active_incident_count"] == 0


def test_same_evidence_cannot_supply_three_confirmations(tmp_path):
    settings = setup(tmp_path)
    snapshots(settings, NOW, portfolio="discrepancy")
    for minutes in (0, 4, 8):
        assert (
            refresh_operations(settings, NOW + dt.timedelta(minutes=minutes))[
                "active_incident_count"
            ]
            == 0
        )


def test_unknown_breaks_pending_streak_and_does_not_clear_confirmed_incident(tmp_path):
    settings = setup(tmp_path)
    for minutes, status in (
        (0, "discrepancy"),
        (4, "observation_unavailable"),
        (8, "discrepancy"),
        (12, "discrepancy"),
        (16, "discrepancy"),
        (20, "observation_unavailable"),
    ):
        now = NOW + dt.timedelta(minutes=minutes)
        snapshots(settings, now, portfolio=status)
        result = refresh_operations(settings, now)
        flags = {f["kind"]: f["status"] for f in result["flags"]}
        if minutes < 16:
            assert "portfolio_discrepancy" not in flags
        elif minutes == 16:
            assert flags["portfolio_discrepancy"] == "open"
        else:
            assert flags["portfolio_discrepancy"] == "unconfirmed"


def test_changed_plan_is_flagged_without_overwriting_its_evidence(tmp_path):
    settings = setup(tmp_path)
    path, _ = plan_file(settings)
    original = path.read_bytes()
    refresh_operations(settings, NOW)
    path.write_text("{}")
    result = refresh_operations(settings, NOW + dt.timedelta(minutes=4))
    assert any(f["kind"] == "plan_evidence_invalid" for f in result["flags"])
    with sqlite3.connect(settings.state_root / "deployment-registry.sqlite3") as db:
        assert db.execute("SELECT payload FROM plans").fetchone()[0].encode() == original


def test_stale_inputs_are_explicit_and_cannot_clear_existing_flags(tmp_path):
    settings = setup(tmp_path)
    snapshots(settings, NOW, orders={"one": order()})
    refresh_operations(settings, NOW)
    result = refresh_operations(settings, NOW + dt.timedelta(minutes=13))
    flags = {f["kind"]: f["status"] for f in result["flags"]}
    assert flags["order_observation_unavailable"] == "open"
    assert flags["portfolio_observation_unavailable"] == "open"
    assert flags["unattributed_order"] == "unconfirmed"


def test_configuration_not_in_allowlist_cannot_become_approved(tmp_path):
    settings = setup(tmp_path)
    settings = replace(
        settings, limits=replace(settings.limits, authorized_config_sha256=frozenset())
    )
    result = refresh_operations(settings, NOW)
    assert result["deployment"]["state"] == "unapproved"
    assert result["deployment"]["authorized"] is False


def test_closed_market_pending_order_is_not_itself_an_incident(tmp_path):
    settings = setup(tmp_path)
    _, plan = plan_file(settings)
    client_id = client_order_id_for(plan, plan.intents[0])
    snapshots(settings, NOW, orders={"one": order(client_id=client_id)})
    assert refresh_operations(settings, NOW)["active_incident_count"] == 0


def test_aged_order_uses_submission_age_without_claiming_cancel_age(tmp_path):
    settings = setup(tmp_path)
    row = order()
    row["submitted_at"] = (NOW - dt.timedelta(days=5)).isoformat()
    snapshots(settings, NOW, orders={"one": row})
    assert {f["kind"] for f in refresh_operations(settings, NOW)["flags"]} == {
        "aged_outstanding_order",
        "unattributed_order",
    }


@pytest.mark.parametrize("corruption", ["[]", "{broken"])
def test_bad_input_has_unavailable_projection_and_no_execution(tmp_path, corruption):
    settings = setup(tmp_path)
    (settings.state_root / "order-registry.json").write_text(corruption)
    result = observe_operations(settings, NOW)
    assert result["status"] == "observed"
    assert any(f["kind"] == "order_observation_unavailable" for f in result["flags"])


def test_unknown_database_schema_fails_closed_without_destroying_it(tmp_path):
    settings = setup(tmp_path)
    path = settings.state_root / "deployment-registry.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    result = observe_operations(settings, NOW)
    assert result["status"] == "observation_unavailable"
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999


def test_review_projection_is_bounded_without_hiding_total(tmp_path):
    settings = setup(tmp_path)
    snapshots(settings, NOW, orders={str(i): order(client_id=str(i)) for i in range(105)})
    result = refresh_operations(settings, NOW)
    assert result["active_incident_count"] == 105
    assert len(result["flags"]) == 100
    assert result["omitted_flag_count"] == 5


def test_staged_scaled_order_retains_original_strategy_attribution(tmp_path):
    from tests.test_batch import Broker, advance, plan

    settings = setup(tmp_path)
    original = plan()
    (settings.plans_dir / "2026-08-11.json").write_text(json.dumps(original.raw))
    broker = Broker()
    advance(settings.state_root, broker, original)
    broker.fill(broker.posts[0]["client_order_id"], 10)
    advance(settings.state_root, broker, original)
    identity = broker.posts[1]["client_order_id"]
    assert identity != client_order_id_for(original, original.intents[1])
    observed = order(client_id=identity)
    observed.update(symbol="QQQ", side="buy")
    snapshots(settings, NOW, orders={"scaled": observed})
    refresh_operations(settings, NOW)
    db = sqlite3.connect(settings.state_root / "deployment-registry.sqlite3")
    try:
        row = db.execute(
            "SELECT config_sha256,symbol,side FROM intents WHERE client_order_id=?", (identity,)
        ).fetchone()
        assert row == (original.config_sha256, "QQQ", "buy")
    finally:
        db.close()
