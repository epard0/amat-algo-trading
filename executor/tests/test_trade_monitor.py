"""Local replay protects attribution, cumulative quantities and outcome maturity."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from tests.test_plan_source import _plan_payload
from tests.test_report import _account
from tests.test_report import _order as broker_order

from executor.cli import main
from executor.plan_source import parse_plan
from executor.report.daily import build_daily_report, prune_old_reports, write_report
from executor.report.trade_monitor import capture_report, rebuild_monitor
from executor.submit import client_order_id_for


def _plan(*, later=False):
    payload = _plan_payload()
    payload["intents"][0]["quantity"] = "10"
    payload["intents"][0]["reference_price"] = "100"
    payload["intents"][0]["notional"] = "1000"
    dates = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17"]
    raw = [100, 100, 45, 50, 55, 60]
    adjusted = [50, 50, 45, 50, 55, 60]
    if later:
        payload.update(as_of_session=dates[-1], earliest_execution_session="2026-08-18", intents=[])
    history = [
        {"session": date, "prices": {"SPY": {"raw_close": r, "adjusted_close": a}}}
        for date, r, a in zip(dates, raw, adjusted, strict=True)
    ]
    payload["monitoring"] = {
        "schema": "trade-context/1",
        "as_of_session": payload["as_of_session"],
        "price_history": history if later else history[:1],
        "decision_diagnostics": {
            "trend_scores": {"SPY": 0.7},
            "score_basis": "core trend score; not a probability",
            "binding_constraints": ["symbol_cap"],
            "forecast_portfolio_volatility_post": 0.08,
        },
    }
    return parse_plan(payload)


def _order(plan, **changes):
    payload = {
        "symbol": "SPY",
        "side": "buy",
        "quantity": "10",
        "filled_quantity": "10",
        "filled_avg_price": "100",
        "status": "filled",
        "client_order_id": client_order_id_for(plan, plan.intents[0]),
        "submitted_at": "2026-08-11T13:31:00+00:00",
        "filled_at": "2026-08-11T13:31:02+00:00",
    }
    payload.update(changes)
    return payload


def _report(*orders, later=False, submission=None):
    return SimpleNamespace(
        session=dt.date(2026, 8, 18 if later else 11),
        generated_at=dt.datetime(2026, 8, 18 if later else 11, 21, tzinfo=dt.UTC),
        orders=orders,
        submission=submission or {},
    )


def test_replay_is_idempotent_and_partial_cumulative_fills_are_not_added(tmp_path):
    plan = _plan()
    first = capture_report(tmp_path, _report(_order(plan, filled_quantity="4")), plan)
    assert first["events"][0]["filled_quantity"] == 4
    assert first["summary"]["alert_counts"]["partial_fill"] == 1
    final = capture_report(tmp_path, _report(_order(plan)), plan)
    replay = rebuild_monitor(tmp_path)
    assert final == replay
    assert final["summary"]["events"] == 1
    assert final["events"][0]["filled_quantity"] == 10
    assert final["events"][0]["alerts"] == []
    assert len(list((tmp_path / "trade-observations").glob("*.json"))) == 1


def test_actual_sessions_and_single_snapshot_adjustment_basis_after_sale(tmp_path):
    plan = _plan()
    first = capture_report(tmp_path, _report(_order(plan)), plan)
    assert first["events"][0]["outcomes"]["1"]["status"] == "pending"
    result = capture_report(tmp_path, _report(later=True), _plan(later=True))
    event = result["events"][0]
    assert event["outcomes"]["5"]["signed_markout_bps"] == pytest.approx(2000)
    assert event["outcomes"]["5"]["adverse_close_bps"] == pytest.approx(1000)
    assert event["outcomes"]["20"]["status"] == "pending"
    assert event["forecasts"] == []
    assert event["signal_score"] == 0.7
    assert event["decision_constraints"] == ["symbol_cap"]
    assert result["summary"]["cohorts"][0]["sample_status"] == "insufficient_sample"
    assert event["outcomes"]["5"]["end_session"] == "2026-08-17"


def test_matching_requires_order_identity_even_when_symbol_side_and_quantity_match(tmp_path):
    plan = _plan()
    payload = capture_report(
        tmp_path, _report(_order(plan, client_order_id="amat-unrelated")), plan
    )
    assert payload["summary"]["events"] == 2
    unrelated = next(row for row in payload["events"] if row["model_id"] == "unattributed")
    assert unrelated["instruction_status"] == "unmatched_order"
    original = next(row for row in payload["events"] if row is not unrelated)
    assert original["filled_quantity"] == 0


def test_recorded_quantity_adjustment_does_not_become_false_overfill(tmp_path):
    plan = _plan()
    order = _order(plan, quantity="8", filled_quantity="8")
    submission = {
        "outcomes": [
            {
                "client_order_id": order["client_order_id"],
                "submitted_quantity": "8",
                "detail": "rounded",
            }
        ]
    }
    payload = capture_report(tmp_path, _report(order, submission=submission), plan)
    row = payload["events"][0]
    assert row["instruction_status"] == "matched_with_recorded_adjustment"
    assert row["intended_quantity"] == 10
    assert row["submitted_quantity"] == row["filled_quantity"] == 8
    assert row["alerts"] == []
    later = capture_report(tmp_path, _report(order, later=True), _plan(later=True))
    assert later["events"][0]["instruction_status"] == "matched_with_recorded_adjustment"
    assert later["events"][0]["submitted_quantity"] == 8


def test_new_mismatched_order_evidence_invalidates_prior_matured_outcomes(tmp_path):
    plan = _plan()
    capture_report(tmp_path, _report(_order(plan)), plan)
    mature = capture_report(tmp_path, _report(later=True), _plan(later=True))
    assert mature["events"][0]["outcomes"]["5"]["status"] == "matured"
    changed = capture_report(
        tmp_path, _report(_order(plan, side="sell"), later=True), _plan(later=True)
    )
    assert changed["events"][0]["outcomes"]["5"]["status"] == "execution_mismatch"


@pytest.mark.parametrize(
    ("change", "alert"),
    [
        ({"filled_quantity": "11"}, "overfill"),
        ({"side": "sell"}, "symbol_or_side_mismatch"),
        ({"submitted_at": "2026-08-11T14:00:00+00:00"}, "outside_planned_window"),
        ({"quantity": "9"}, "submitted_quantity_mismatch"),
    ],
)
def test_execution_deviations_are_identified(tmp_path, change, alert):
    plan = _plan()
    result = capture_report(tmp_path, _report(_order(plan, **change)), plan)
    assert alert in result["events"][0]["alerts"]


def test_late_snapshot_uses_original_model_identity(tmp_path):
    plan = _plan()
    capture_report(tmp_path, _report(_order(plan, filled_quantity="4")), plan)
    result = capture_report(tmp_path, _report(_order(plan), later=True), _plan(later=True))
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["model_id"] == plan.config_sha256
    assert event["decision_session"] == "2026-08-10"
    assert event["execution_session"] == "2026-08-11"
    assert event["filled_quantity"] == 10


def test_failed_refresh_does_not_erase_previous_fills(tmp_path):
    plan = _plan()
    capture_report(tmp_path, _report(_order(plan)), plan)
    result = capture_report(tmp_path, _report(), plan, orders_available=False)
    assert result["events"][0]["filled_quantity"] == 10
    assert result["events"][0]["execution_status"] == "order_data_unavailable"


def test_future_or_duplicate_price_frames_fail_before_overwriting_history(tmp_path):
    plan = _plan()
    capture_report(tmp_path, _report(_order(plan)), plan)
    original = (tmp_path / "trade-observations/2026-08-11.json").read_bytes()
    bad = _plan()
    bad.raw["monitoring"]["price_history"] *= 2
    with pytest.raises(ValueError, match="inconsistent or future"):
        capture_report(tmp_path, _report(_order(plan)), bad)
    assert (tmp_path / "trade-observations/2026-08-11.json").read_bytes() == original


def test_legacy_plan_missing_context_and_old_report_retention_are_explicit(tmp_path):
    plan = _plan()
    plan.raw.pop("monitoring")
    capture_report(tmp_path, _report(_order(plan)), plan)
    reports = tmp_path / "reports"
    reports.mkdir()
    for day in (10, 11):
        (reports / f"2026-08-{day}.json").write_text("{}")
    prune_old_reports((reports,), retain_days=1)
    result = rebuild_monitor(tmp_path)
    assert result["summary"]["events"] == 1
    assert result["events"][0]["outcomes"]["5"]["status"] == "missing_history"
    assert (tmp_path / "trade-observations/2026-08-11.json").is_file()


def test_offline_cli_needs_no_credentials_or_network(tmp_path, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    assert main(["trades", "--state-root", str(tmp_path)]) == 0
    payload = json.loads((tmp_path / "trade-monitor.json").read_text())
    assert payload["summary"]["events"] == 0


def test_daily_report_carries_the_durable_monitor_summary_and_timestamps(tmp_path):
    plan = _plan()
    order = broker_order(
        client_order_id=client_order_id_for(plan, plan.intents[0]),
        submitted_at="2026-08-11T13:31:00+00:00",
    )
    report = build_daily_report(
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 21, tzinfo=dt.UTC),
        account=_account(),
        positions=(),
        equity_curve=[],
        submission=None,
        plan=plan,
        orders=(order,),
    )
    payload = capture_report(tmp_path, report, plan)
    report = replace(report, trade_monitor=payload["summary"])
    markdown, saved = write_report(report, tmp_path / "reports")
    assert "Trade performance monitor" in markdown.read_text()
    assert json.loads(saved.read_text())["trade_monitor"]["events"] == 1
    assert report.orders[0]["submitted_at"] == order.submitted_at
