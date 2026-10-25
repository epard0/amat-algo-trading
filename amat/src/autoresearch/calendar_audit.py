"""Independent US-equity session checks, separate from the price-data calendar."""

from __future__ import annotations

import datetime as dt
import importlib.metadata
from typing import Any

import exchange_calendars as xcals  # type: ignore[import-untyped]

from autoresearch.validation_contract import calendar_check


def audit_sessions(sessions: list[dt.date], *, start: dt.date, end: dt.date) -> dict[str, Any]:
    if start >= end:
        raise ValueError("calendar audit requires an ordered interval")
    calendar = xcals.get_calendar("XNYS", start=str(start), end=str(end))
    schedule = calendar.schedule
    expected = [day.date().isoformat() for day in schedule.index]
    calendar_check([day.isoformat() for day in sessions], expected)
    return {
        "schema": "independent-calendar-audit/1",
        "status": "matched",
        "calendar": "XNYS",
        "source": "exchange_calendars",
        "source_version": importlib.metadata.version("exchange-calendars"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": [
            {
                "session": day.date().isoformat(),
                "open": row["open"].isoformat(),
                "close": row["close"].isoformat(),
            }
            for day, row in schedule.iterrows()
        ],
        "scope": "US-equity market sessions; not symbol coverage or point-in-time prices",
        "authority": "independent library schedule; not exchange certification",
    }
