"""Mailgun transactional mail over HTTPS; credentials never enter research workers."""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from executor.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class MailgunSettings:
    """Use a domain sending key, rather than an account administration key."""

    domain: str
    api_key: str = field(repr=False)
    region: str
    sender: str
    recipients: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> MailgunSettings | None:
        domain = os.environ.get("MAILGUN_DOMAIN", "").strip()
        key = os.environ.get("MAILGUN_API_KEY", "").strip()
        # A host may carry a generic Mailgun key for unrelated applications.
        # Only selecting a sending domain opts this executor into delivery.
        if not domain:
            return None
        region = os.environ.get("MAILGUN_REGION", "US").strip().upper()
        sender = os.environ.get("REPORT_SENDER", "").strip()
        recipients = tuple(
            item.strip()
            for item in os.environ.get("REPORT_RECIPIENTS", "").split(",")
            if item.strip()
        )
        if (
            not key
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain)
            or region not in {"US", "EU"}
            or not sender
            or not recipients
        ):
            raise ConfigurationError(
                "Mailgun requires MAILGUN_DOMAIN, MAILGUN_API_KEY, US/EU MAILGUN_REGION, "
                "REPORT_SENDER and REPORT_RECIPIENTS"
            )
        if any("\n" in value or "\r" in value for value in (sender, *recipients)):
            raise ConfigurationError("mail addresses cannot contain line breaks")
        return cls(domain, key, region, sender, recipients)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        """Do not forward an API credential to a redirected destination."""
        return


def send_mailgun(settings: MailgunSettings, subject: str, text: str) -> tuple[bool, str]:
    """Single bounded attempt. True means accepted by Mailgun, not inbox delivery."""
    host = "api.eu.mailgun.net" if settings.region == "EU" else "api.mailgun.net"
    boundary = f"amat-{uuid.uuid4().hex}"
    parts = []
    fields = [
        ("from", settings.sender),
        *(("to", item) for item in settings.recipients),
        ("subject", subject),
        ("text", text),
        ("o:tracking", "no"),
    ]
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        )
    body = ("".join(parts) + f"--{boundary}--\r\n").encode()
    authorization = base64.b64encode(f"api:{settings.api_key}".encode()).decode()
    request = urllib.request.Request(
        f"https://{host}/v3/{settings.domain}/messages",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {authorization}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=15) as response:
            payload = json.loads(response.read(65536))
        if not isinstance(payload, dict) or not payload.get("id"):
            return False, "Mailgun acknowledgment unavailable; not retried"
    except urllib.error.HTTPError as exc:
        return False, f"Mailgun HTTP {exc.code}; not retried"
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return False, f"Mailgun {type(exc).__name__}; acceptance unknown; not retried"
    return True, "accepted by Mailgun; inbox delivery not independently verified"
