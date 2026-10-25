"""Durable, rate-limited operational alerts with best-effort delivery."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from email.message import EmailMessage
from typing import TYPE_CHECKING

from executor.mailgun import send_mailgun
from executor.report.email import send_message
from executor.safety import atomic_json
from executor.telegram import send_telegram

if TYPE_CHECKING:
    from executor.config import ExecutorSettings

LOGGER = logging.getLogger("executor.alerts")
_REPEAT_SECONDS = 3600


def notify(settings: ExecutorSettings, kind: str, detail: str) -> bool:
    """At most one attempt per identical alert/hour, including across restarts."""
    if settings.mailgun is None and settings.smtp is None and settings.telegram is None:
        return False
    identity = hashlib.sha256(f"{kind}:{detail}".encode()).hexdigest()
    path = settings.state_root / "alerts" / f"{kind}.json"
    now = dt.datetime.now(dt.UTC)
    try:
        if path.exists():
            previous = json.loads(path.read_text())
            age = (now - dt.datetime.fromisoformat(previous["attempted_at"])).total_seconds()
            if previous["identity"] == identity and age < _REPEAT_SECONDS:
                return False
        record = {"identity": identity, "attempted_at": now.isoformat(), "status": "pending"}
        atomic_json(path, record)
        subject = f"[AMAT PAPER] {kind}"
        body = (
            f"{detail}\n\nObserved: {now.isoformat()}\nReview executor health, status and journal."
        )
        if settings.telegram is not None:
            accepted, result = send_telegram(settings.telegram, subject, body)
        elif settings.mailgun is not None:
            accepted, result = send_mailgun(settings.mailgun, subject, body)
        else:
            assert settings.smtp is not None  # noqa: S101 - narrowed above
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = settings.smtp.sender
            message["To"] = ", ".join(settings.smtp.recipients)
            message.set_content(body)
            accepted, result = send_message(message, settings.smtp)
        atomic_json(
            path, {**record, "status": "accepted" if accepted else "failed", "detail": result}
        )
        if not accepted:
            LOGGER.warning("alert delivery failed: %s", result)
        return accepted
    except (OSError, ValueError, KeyError, TypeError):
        LOGGER.exception("alert could not be persisted or sent")
        return False
