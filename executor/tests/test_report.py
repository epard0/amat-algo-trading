"""Performance arithmetic, the honesty guards, and the retention window."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from tests.test_plan_source import _plan_payload

from executor.broker import Account, Order
from executor.journal import Journal
from executor.plan_source import parse_plan
from executor.report.daily import (
    _MEANINGFUL_SAMPLE,
    build_daily_report,
    compute_stats,
    prune_old_reports,
    write_report,
)
from executor.report.metrics import compute_execution_quality
from executor.submit import client_order_id_for


def _curve(*equities: str) -> list[dict[str, object]]:
    start = dt.date(2026, 8, 3)
    return [
        {"session": (start + dt.timedelta(days=index)).isoformat(), "equity": equity}
        for index, equity in enumerate(equities)
    ]


# -- statistics -------------------------------------------------------------


def test_empty_curve_produces_zeros_not_an_error() -> None:
    stats = compute_stats([])
    assert stats.sessions == 0
    assert stats.total_return == 0.0
    assert not stats.is_meaningful


def test_total_return_and_day_return() -> None:
    stats = compute_stats(_curve("100000", "101000", "102010"))

    assert stats.sessions == 3
    assert stats.total_return == pytest.approx(0.0201, abs=1e-6)
    assert stats.day_return == pytest.approx(0.01, abs=1e-9)
    assert stats.positive_days == 2


def test_max_drawdown_is_measured_from_the_running_peak() -> None:
    stats = compute_stats(_curve("100000", "110000", "99000", "105000"))
    assert stats.max_drawdown == pytest.approx(-0.10, abs=1e-9)


def test_a_monotonic_curve_has_no_drawdown() -> None:
    stats = compute_stats(_curve("100", "101", "102"))
    assert stats.max_drawdown == 0.0


def test_dispersion_needs_two_returns() -> None:
    stats = compute_stats(_curve("100000", "101000"))
    assert stats.annualized_volatility is None
    assert stats.sharpe_ratio is None


def test_a_flat_curve_has_zero_volatility_and_no_divide_by_zero() -> None:
    stats = compute_stats(_curve("100000", "100000", "100000"))
    assert stats.annualized_volatility == 0.0
    assert stats.sharpe_ratio == 0.0


def test_extended_observation_gap_suppresses_annualized_metrics() -> None:
    curve = [
        {"session": "2026-08-27", "equity": "100000"},
        {"session": "2026-09-08", "equity": "101000"},
    ]
    stats = compute_stats(curve)

    assert stats.day_return == pytest.approx(0.01)
    assert stats.latest_interval_calendar_days == 12
    assert stats.has_extended_gap
    assert stats.annualized_return is None
    assert stats.annualized_volatility is None
    assert stats.historical_var_95 is None


def test_downside_and_tail_metrics_are_reported_on_a_daily_curve() -> None:
    stats = compute_stats(_curve("100", "102", "100", "103", "99"))

    assert stats.annualized_downside_deviation is not None
    assert stats.sortino_ratio is not None
    assert stats.historical_var_95 is not None
    assert stats.historical_cvar_95 is not None
    assert stats.ulcer_index > 0
    assert stats.negative_days == 2
    assert stats.gain_to_pain_ratio is not None


def test_equity_sessions_must_be_strictly_increasing() -> None:
    curve = [
        {"session": "2026-08-03", "equity": "100"},
        {"session": "2026-08-03", "equity": "101"},
    ]
    with pytest.raises(ValueError, match="strictly increasing"):
        compute_stats(curve)


def test_short_samples_are_labelled_not_meaningful() -> None:
    """Guardrail 2, applied where it erodes fastest: a daily email."""
    assert not compute_stats(_curve(*["100000"] * (_MEANINGFUL_SAMPLE - 1))).is_meaningful
    assert compute_stats(_curve(*["100000"] * _MEANINGFUL_SAMPLE)).is_meaningful


# -- rendering --------------------------------------------------------------


def _account() -> Account:
    return Account.from_payload(
        {
            "account_number": "PA123",
            "status": "ACTIVE",
            "equity": "101000",
            "last_equity": "100000",
            "cash": "40000",
            "buying_power": "80000",
            "trading_blocked": False,
            "transfers_blocked": False,
            "account_blocked": False,
            "pattern_day_trader": False,
        }
    )


def test_a_short_sample_report_carries_the_caveat() -> None:
    report = build_daily_report(
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 20, 15, tzinfo=dt.UTC),
        account=_account(),
        positions=(),
        equity_curve=_curve("100000", "101000"),
        submission=None,
        plan=None,
        orders=(),
    )
    markdown = report.to_markdown()

    assert "Not a meaningful sample" in markdown
    assert "optimistic bound on live behaviour" in markdown
    assert "+1.00%" in markdown


def test_the_subject_line_flags_a_day_that_needs_attention() -> None:
    report = build_daily_report(
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 20, 15, tzinfo=dt.UTC),
        account=_account(),
        positions=(),
        equity_curve=_curve("100000", "101000"),
        submission=None,
        plan=None,
        orders=(),
        extra_warnings=("the broker rejected two orders",),
    )

    assert "[ATTENTION]" in report.subject
    assert "2026-08-11" in report.subject
    assert "⚠ Attention" in report.to_markdown()


def test_a_blocked_account_warns_even_without_an_explicit_warning() -> None:
    blocked = Account.from_payload(
        {
            "account_number": "PA123",
            "status": "ACTIVE",
            "equity": "101000",
            "last_equity": "100000",
            "cash": "40000",
            "buying_power": "0",
            "trading_blocked": True,
            "transfers_blocked": False,
            "account_blocked": False,
            "pattern_day_trader": False,
        }
    )
    report = build_daily_report(
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 20, 15, tzinfo=dt.UTC),
        account=blocked,
        positions=(),
        equity_curve=_curve("100000", "101000"),
        submission=None,
        plan=None,
        orders=(),
    )
    assert any("not accepting orders" in warning for warning in report.warnings)


def test_write_report_emits_both_forms(tmp_path: Path) -> None:
    report = build_daily_report(
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 20, 15, tzinfo=dt.UTC),
        account=_account(),
        positions=(),
        equity_curve=_curve("100000", "101000"),
        submission=None,
        plan=None,
        orders=(),
    )
    markdown_path, json_path = write_report(report, tmp_path)

    assert markdown_path.name == "2026-08-11.md"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["stats"]["sessions"] == 2
    assert not payload["execution"]["reconciliation_complete"]


# -- execution reconciliation ------------------------------------------------


def _order(**overrides: object) -> Order:
    payload = {
        "id": "order-1",
        "client_order_id": client_order_id_for(
            parse_plan(_plan_payload()), parse_plan(_plan_payload()).intents[0]
        ),
        "symbol": "SPY",
        "side": "buy",
        "qty": "15.9684",
        "filled_qty": "15.9684",
        "filled_avg_price": "780.00",
        "status": "filled",
        "submitted_at": "2026-08-11T13:30:00Z",
    }
    payload.update(overrides)
    return Order.from_payload(payload)


def test_execution_quality_reconciles_fill_and_adverse_markout() -> None:
    plan = parse_plan(_plan_payload())
    quality = compute_execution_quality(plan, (_order(),))

    expected_bps = (780.0 / 772.99 - 1.0) * 10_000.0
    assert quality.reconciliation_complete
    assert quality.filled_order_count == 1
    assert quality.reference_notional_fill_rate == pytest.approx(1.0, rel=1e-4)
    assert quality.decision_to_fill_slippage_bps == pytest.approx(expected_bps)


def test_execution_quality_flags_an_unmatched_plan_intent() -> None:
    quality = compute_execution_quality(parse_plan(_plan_payload()), ())

    assert not quality.reconciliation_complete
    assert quality.unmatched_intent_count == 1
    assert quality.reference_notional_fill_rate == 0.0


def test_same_symbol_side_from_another_plan_does_not_reconcile() -> None:
    quality = compute_execution_quality(
        parse_plan(_plan_payload()), (_order(client_order_id="unrelated-SPY-buy"),)
    )
    assert not quality.reconciliation_complete
    assert quality.unmatched_intent_count == 1
    assert quality.unmatched_order_count == 1


def test_missing_plan_or_failed_order_fetch_cannot_reconcile() -> None:
    assert not compute_execution_quality(None, ()).reconciliation_complete
    empty = parse_plan(_plan_payload(intents=[]))
    assert compute_execution_quality(empty, ()).reconciliation_complete
    assert not compute_execution_quality(empty, (), orders_available=False).reconciliation_complete


def test_execution_quality_flags_unmatched_and_overfilled_orders() -> None:
    plan = parse_plan(_plan_payload())
    overfill = _order(filled_qty="16.0000")
    unrelated = _order(
        id="order-2",
        client_order_id="amat-2026-08-10-QQQ-buy-test",
        symbol="QQQ",
        qty="1",
        filled_qty="1",
    )

    quality = compute_execution_quality(plan, (overfill, unrelated))

    assert not quality.reconciliation_complete
    assert quality.overfilled_order_count == 1
    assert quality.unmatched_order_count == 1


def test_report_warns_when_broker_reconciliation_is_incomplete() -> None:
    report = build_daily_report(
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 20, 15, tzinfo=dt.UTC),
        account=_account(),
        positions=(),
        equity_curve=_curve("100000", "101000"),
        submission=None,
        plan=parse_plan(_plan_payload()),
        orders=(),
    )

    assert any("reconciliation is incomplete" in warning for warning in report.warnings)
    assert "Execution quality" in report.to_markdown()


# -- retention --------------------------------------------------------------


def _seed(directory: Path, days: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    start = dt.date(2026, 7, 1)
    for index in range(days):
        stamp = (start + dt.timedelta(days=index)).isoformat()
        (directory / f"{stamp}.md").write_text("report", encoding="utf-8")
        (directory / f"{stamp}.json").write_text("{}", encoding="utf-8")


def test_retention_keeps_exactly_one_week_of_days(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _seed(reports, days=30)

    removed = prune_old_reports((reports,), retain_days=7)

    remaining = sorted({path.stem for path in reports.iterdir()})
    assert len(remaining) == 7
    assert remaining[-1] == "2026-07-30"
    assert remaining[0] == "2026-07-24"
    assert len(removed) == 46  # 23 days x 2 files


def test_retention_keeps_a_days_markdown_and_json_together(tmp_path: Path) -> None:
    """A half-pruned day would render a report with no machine-readable pair."""
    reports = tmp_path / "reports"
    _seed(reports, days=10)

    prune_old_reports((reports,), retain_days=3)

    stems = [path.stem for path in reports.iterdir()]
    for stem in set(stems):
        assert stems.count(stem) == 2


def test_retention_never_deletes_a_file_it_cannot_explain(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _seed(reports, days=10)
    (reports / "README.md").write_text("do not delete me", encoding="utf-8")
    (reports / "notes.txt").write_text("nor me", encoding="utf-8")

    prune_old_reports((reports,), retain_days=2)

    assert (reports / "README.md").is_file()
    assert (reports / "notes.txt").is_file()


def test_retention_is_a_no_op_below_the_window(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _seed(reports, days=3)

    assert prune_old_reports((reports,), retain_days=7) == ()
    assert len(list(reports.iterdir())) == 6


def test_retention_tolerates_a_missing_directory(tmp_path: Path) -> None:
    assert prune_old_reports((tmp_path / "nope",), retain_days=7) == ()


def test_retention_rejects_a_zero_window() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        prune_old_reports((), retain_days=0)


def test_the_equity_curve_is_never_pruned(tmp_path: Path) -> None:
    """It is what every statistic is computed from, and it is tiny."""
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity_curve.ndjson")
    for index in range(40):
        journal.record_equity(
            session=dt.date(2026, 7, 1) + dt.timedelta(days=index),
            equity=Decimal("100000"),
            cash=Decimal("50000"),
            gross_exposure=0.5,
            positions={},
        )

    prune_old_reports((tmp_path,), retain_days=7)

    assert len(journal.read_equity_curve()) == 40


def test_one_equity_observation_per_session(tmp_path: Path) -> None:
    """A restart after the report ran must not append a duplicate point."""
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity_curve.ndjson")
    session = dt.date(2026, 8, 11)

    assert not journal.has_equity_for(session)
    journal.record_equity(
        session=session,
        equity=Decimal("100000"),
        cash=Decimal("0"),
        gross_exposure=1.0,
        positions={},
    )
    assert journal.has_equity_for(session)
