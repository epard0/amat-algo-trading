"""Daily reporting: build, persist, prune, deliver."""

from __future__ import annotations

from executor.report.daily import (
    DailyReport,
    PerformanceStats,
    build_daily_report,
    compute_stats,
    prune_old_reports,
    write_report,
)
from executor.report.email import send_report

__all__ = [
    "DailyReport",
    "PerformanceStats",
    "build_daily_report",
    "compute_stats",
    "prune_old_reports",
    "send_report",
    "write_report",
]
