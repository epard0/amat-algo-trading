import datetime as dt

import pytest

from autoresearch.calendar_audit import audit_sessions


def test_nyse_holiday_and_short_session():
    days = [dt.date(2024, 11, 27), dt.date(2024, 11, 29)]
    evidence = audit_sessions(days, start=days[0], end=days[-1])
    assert evidence["status"] == "matched"
    assert evidence["sessions"][-1]["close"].startswith("2024-11-29T18:00")


def test_missing_and_extra_sessions_fail():
    first, last = dt.date(2024, 11, 27), dt.date(2024, 11, 29)
    with pytest.raises(ValueError, match="sessions"):
        audit_sessions([first], start=first, end=last)
    with pytest.raises(ValueError, match="sessions"):
        audit_sessions([first, dt.date(2024, 11, 28), last], start=first, end=last)


def test_special_closure_is_not_inferred_from_weekdays():
    days = [dt.date(2018, 12, 4), dt.date(2018, 12, 6)]
    assert audit_sessions(days, start=days[0], end=days[-1])["status"] == "matched"
