"""Frozen history revisions require a numerical corporate-action explanation."""

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from amat.data.alpaca import DailyBar, DividendEvent, SplitEvent
from amat.data.snapshot import write_snapshot
from autoresearch.genetic.reconciliation import reconciliation_report


@pytest.fixture
def history(tmp_path: Path):
    days = [dt.date(2026, 1, n) for n in (5, 6, 7, 8, 9)]
    raw = [DailyBar("SPY", day, 100.0, 101.0, 99.0, 100.0, 10.0, 100.0, 1) for day in days]
    path, _ = write_snapshot(
        tmp_path,
        "old",
        raw_bars=raw[:3],
        adjusted_bars=raw[:3],
        window_bars=[],
        dividends=[],
        splits=[],
        provider="test",
        feed="sip",
        as_of=dt.datetime(2026, 1, 7, 22, tzinfo=dt.UTC),
        execution_window_start=dt.time(9, 30),
        execution_window_end=dt.time(10),
        exchange_timezone="America/New_York",
    )
    dividend = DividendEvent("SPY", days[3], 1.0)
    adjusted = [replace(bar, close=99.0) if i < 3 else bar for i, bar in enumerate(raw)]
    return path, raw, adjusted, dividend, days[-1]


def test_cash_factor_explains_history(history):
    path, raw, adjusted, dividend, through = history
    result = reconciliation_report(path, raw, adjusted, [dividend], [], through)
    assert result["status"] == "passed"
    assert result["assets"][0]["expected_factor"] == 0.99
    assert result["assets"][0]["adjusted_changed_rows"] == 3


@pytest.mark.parametrize("field,value", [("close", 100.1), ("open", 100.1), ("volume", 11.0)])
def test_raw_revisions_refused(history, field, value):
    path, raw, adjusted, dividend, through = history
    raw[0] = replace(raw[0], **{field: value})
    result = reconciliation_report(path, raw, adjusted, [dividend], [], through)
    assert result["status"] == "refused"
    assert any(issue["code"] == "raw_history_revised" for issue in result["issues"])


@pytest.mark.parametrize("kind", ["gap", "duplicate", "unexplained", "split", "old_action"])
def test_unexplained_or_incomplete_refused(history, kind):
    path, raw, adjusted, dividend, through = history
    dividends, splits = [dividend], []
    if kind == "gap":
        raw.pop(1)
    elif kind == "duplicate":
        adjusted.append(adjusted[0])
    elif kind == "unexplained":
        adjusted[0] = replace(adjusted[0], close=98.5)
    elif kind == "split":
        splits = [SplitEvent("SPY", dividend.ex_date, 2.0)]
    else:
        dividends.append(DividendEvent("SPY", raw[0].session_date, 0.5))
    assert (
        reconciliation_report(path, raw, adjusted, dividends, splits, through)["status"]
        == "refused"
    )


def test_rounding_accounts_for_both_frozen_and_new_prices(history):
    path, raw, _, dividend, through = history
    # True old close can be 100.0049, stored 100.00; after the factor it is
    # 99.9988997, also stored 100.00. Error vs rounded old * factor is .006.
    dividend = replace(dividend, rate=0.006)
    result = reconciliation_report(path, raw, raw, [dividend], [], through)
    assert result["status"] == "passed"
    assert result["assets"][0]["maximum_absolute_error"] > 0.005


def test_no_action_does_not_excuse_small_revision(history):
    path, raw, _, _, through = history
    adjusted = list(raw)
    adjusted[0] = replace(adjusted[0], close=100.001)
    assert reconciliation_report(path, raw, adjusted, [], [], through)["status"] == "refused"


def test_multiple_new_cash_factors_compound(history):
    path, raw, _, dividend, through = history
    second = DividendEvent("SPY", through, 2.0)
    adjusted = [replace(bar, close=97.02, low=97.0) if i < 3 else bar for i, bar in enumerate(raw)]
    result = reconciliation_report(path, raw, adjusted, [dividend, second], [], through)
    assert result["status"] == "passed"
    assert result["assets"][0]["expected_factor"] == pytest.approx(0.99 * 0.98)


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), -1.0, 101.0])
def test_invalid_dividend_refused(history, rate):
    path, raw, adjusted, dividend, through = history
    assert (
        reconciliation_report(path, raw, adjusted, [replace(dividend, rate=rate)], [], through)[
            "status"
        ]
        == "refused"
    )


def test_unknown_universe_refused(history):
    path, raw, adjusted, dividend, through = history
    result = reconciliation_report(path, raw, adjusted, [dividend], [], through, symbols=["NOPE"])
    assert result["status"] == "refused"
    assert result["issues"] == [{"code": "invalid_universe"}]


def test_exact_action_repeat_collapses_but_conflicting_rate_refuses(history):
    path, raw, adjusted, dividend, through = history
    assert (
        reconciliation_report(path, raw, adjusted, [dividend, dividend], [], through)["status"]
        == "passed"
    )
    result = reconciliation_report(
        path, raw, adjusted, [dividend, replace(dividend, rate=0.9)], [], through
    )
    assert result["status"] == "refused"
    assert any(issue["code"] == "conflicting_new_actions" for issue in result["issues"])
