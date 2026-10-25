"""Optional Telegram notifications, bounded and independent of order execution."""

from __future__ import annotations

import http.client
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from executor.mailgun import _NoRedirect


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    token: str = field(repr=False)
    chat_id: str

    @classmethod
    def from_environment(cls) -> TelegramSettings | None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        # An incomplete optional notifier must not prevent the daemon starting.
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?\d+", chat):
            return None
        return cls(token, chat)


def send_telegram(settings: TelegramSettings, subject: str, body: str) -> tuple[bool, str]:
    """One bounded request, plain text, no redirects or automatic resend on ambiguity."""
    text = f"{subject}\n\n{body}"
    # UTF-16 bound also handles astral characters safely within Telegram's limit.
    if len(text.encode("utf-16-le")) > 7800:  # noqa: PLR2004
        text = text.encode("utf-16-le")[:7600].decode("utf-16-le", errors="ignore")
        text += "\n\n[Report shortened; full report retained on executor.]"
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{settings.token}/sendMessage",
        data=json.dumps({"chat_id": settings.chat_id, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=5) as response:
            payload = json.loads(response.read(65536))
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return False, "Telegram did not accept message"
        result = payload.get("result")
        if not isinstance(result, dict) or not result.get("message_id"):
            return False, "Telegram acknowledgment unavailable"
    except urllib.error.HTTPError as exc:
        return False, f"Telegram HTTP {exc.code}; not retried"
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # Exception text can contain the token-bearing request URL.
        return False, f"Telegram {type(exc).__name__}; acceptance unknown; not retried"
    return True, "accepted by Telegram"
