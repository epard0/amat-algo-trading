"""Local daemon progress checks, independent of broker credential availability."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from executor.errors import ConfigurationError
from executor.safety import require_reconciled_portfolio, submission_interlock

if TYPE_CHECKING:
    from executor.config import ExecutorSettings

MAX_PROGRESS_AGE_SECONDS = 1800


def health_status(root: Path, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Missing/corrupt/future/stale evidence is unhealthy, never fabricated success."""
    now = now or dt.datetime.now(dt.UTC)
    try:
        payload = json.loads((root / "heartbeat.json").read_text())
        last = dt.datetime.fromisoformat(payload["last_success_at"])
        age = (now - last).total_seconds()
        healthy = 0 <= age <= MAX_PROGRESS_AGE_SECONDS and payload["status"] == "running"
        result = {"healthy": healthy, "status": payload["status"], "progress_age_seconds": age}
    except (OSError, ValueError, KeyError, TypeError):
        result = {"healthy": False, "status": "missing_or_invalid_progress"}
    try:
        result["submission_interlock"] = submission_interlock(root)
    except (OSError, ConfigurationError):
        result["submission_interlock"] = "state unavailable or corrupt"
        result["healthy"] = False
    return result


def readiness_status(settings: ExecutorSettings) -> dict[str, Any]:
    """A deployment check, never an action that enables trading."""
    result = health_status(settings.state_root)
    issues = []
    if not result["healthy"]:
        issues.append("daemon progress is unhealthy")
    if result["submission_interlock"]:
        issues.append(result["submission_interlock"])
    if settings.dry_run:
        issues.append("DRY_RUN is enabled")
    if settings.limits.disabled:
        issues.append("EXECUTOR_DISABLED is enabled")
    if settings.trading_mode == "live" and not settings.live_trading_enabled:
        issues.append("LIVE_TRADING_ENABLED is false")
    warnings = []
    if settings.smtp is None and settings.mailgun is None and settings.telegram is None:
        warnings.append("notifications are not configured")
    digest = hashlib.sha256(settings.strategy_config.read_bytes()).hexdigest()
    if digest not in settings.limits.authorized_config_sha256:
        issues.append("strategy digest is not authorized")
    try:
        require_reconciled_portfolio(settings.state_root, dt.datetime.now(dt.UTC))
    except ConfigurationError as exc:
        issues.append(str(exc))
    result.update(
        {
            "trading_mode": settings.trading_mode,
            "dry_run": settings.dry_run,
            "executor_disabled": settings.limits.disabled,
            "live_trading_enabled": settings.live_trading_enabled,
            "ready_for_orders": not issues,
            "order_readiness_issues": issues,
            "notification_warnings": warnings,
            "telegram_configured": settings.telegram is not None,
            "smtp_configured": settings.smtp is not None,
            "mailgun_configured": settings.mailgun is not None,
            "execution_caps": {
                "session_notional": str(settings.limits.max_session_notional or "unlimited"),
                "symbol_notional": str(settings.limits.max_symbol_notional or "unlimited"),
                "orders_per_session": settings.limits.max_orders_per_session or "unlimited",
            },
            "scope": "local checks; validate VPS reboot, restore and off-host alerts separately",
        }
    )
    return result
