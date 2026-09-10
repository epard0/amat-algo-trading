"""Daily reporting: build, persist, prune, deliver."""

from __future__ import annotations

from executor.report.daily import (
    DailyReport,
    build_daily_report,
    prune_old_reports,
    write_report,
)
from executor.report.email import send_report
from executor.report.metrics import (
    ExecutionQuality,
    PerformanceStats,
    compute_execution_quality,
    compute_stats,
)

__all__ = [
    "DailyReport",
    "ExecutionQuality",
    "PerformanceStats",
    "build_daily_report",
    "compute_execution_quality",
    "compute_stats",
    "prune_old_reports",
    "send_report",
    "write_report",
]
