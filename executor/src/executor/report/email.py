"""Mail the daily report.

Delivery is best-effort *by design*. A mail server that is down, rate-limiting,
or misconfigured must never stop the trading loop or crash the daemon -- the
report is already durable on disk before this module is called, so a failure
here costs a notification, not a record. Failures are recorded in the journal
and surfaced in the following day's report rather than raised.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from executor.config import SmtpSettings
from executor.report.daily import DailyReport

__all__ = ["send_report"]

_TIMEOUT_SECONDS = 30.0

#: Implicit TLS. Every other port is treated as submission with STARTTLS.
_IMPLICIT_TLS_PORT = 465


def _build_message(report: DailyReport, settings: SmtpSettings) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = report.subject
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message.set_content(report.to_markdown())
    return message


def send_report(report: DailyReport, settings: SmtpSettings) -> tuple[bool, str]:
    """Send the report. Returns ``(delivered, detail)`` and never raises.

    Args:
        report: The already-persisted daily report.
        settings: Resolved SMTP settings.

    Returns:
        ``(True, "")`` on success, or ``(False, reason)`` with a reason safe to
        write into a report -- the password is never part of an exception
        message this function propagates.
    """
    return send_message(_build_message(report, settings), settings)


def send_message(message: EmailMessage, settings: SmtpSettings) -> tuple[bool, str]:
    """Shared SMTP transport for persisted reports and operational notifications."""
    context = ssl.create_default_context()

    try:
        if settings.port == _IMPLICIT_TLS_PORT:
            with smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=_TIMEOUT_SECONDS, context=context
            ) as server:
                if settings.username:
                    server.login(settings.username, settings.password)
                server.send_message(message)
        else:
            with smtplib.SMTP(settings.host, settings.port, timeout=_TIMEOUT_SECONDS) as server:
                server.ehlo()
                if settings.use_starttls:
                    server.starttls(context=context)
                    server.ehlo()
                if settings.username:
                    server.login(settings.username, settings.password)
                server.send_message(message)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        # The exception text can contain the server's response but not the
        # credential, since smtplib does not echo it. Truncated regardless.
        return False, f"{type(exc).__name__}: {str(exc)[:300]}"

    return True, ""
