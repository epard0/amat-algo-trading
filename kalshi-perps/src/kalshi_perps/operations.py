"""Paper operational diagnostics and durable Telegram alerts; no trading authority."""

import hashlib
import json
import os
import time
import traceback
import urllib.request
from pathlib import Path

# Known validation text only. Never serialize exception URLs or arbitrary payloads.
_REASONS = {
    message: message.replace(";", "").replace(" ", "_")
    for message in (
        "stale snapshot before execution",
        "stale liquidation mark",
        "stale liquidation mark at snapshot completion",
        "stale order book at snapshot completion",
        "market snapshot exceeded freshness limit",
        "market metadata changed; revalidate registry before resuming",
        "crossed book",
        "two-sided executable book required",
        "invalid book level",
        "duplicate book price",
        "completed provider history changed; cache not overwritten",
        "invalid external OHLC",
        "conflicting provider candle",
        "invalid mark",
        "funding identity conflict",
        "invalid native funding economics",
        "funding response instrument mismatch",
        "snapshot time must increase",
        "primary strategy candle grids differ",
    )
}


def diagnostic(exc, stage):
    return dict(
        error_type=type(exc).__name__,
        error_stage=stage,
        error_reason=_REASONS.get(str(exc), "unclassified_" + type(exc).__name__),
        error_frames=[
            dict(file=Path(f.filename).name, function=f.name, line=f.lineno)
            for f in traceback.extract_tb(exc.__traceback__)[-5:]
        ],
    )


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _notify(root, kind, detail, *, now=None, send=None):
    """Persist before sending; one attempt per identical alert/hour across restarts."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return False
    now = time.time() if now is None else now
    identity = hashlib.sha256((kind + ":" + detail).encode()).hexdigest()
    path = Path(root) / "alerts" / (kind + ".json")
    if path.exists():
        previous = json.loads(path.read_text())
        if previous["identity"] == identity and 0 <= now - previous["attempted_at"] < 3600:
            return False
    record = dict(identity=identity, attempted_at=now, status="pending")
    _save(path, record)
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=json.dumps(
            dict(chat_id=chat, text="[KALSHI PERPS PAPER] " + kind + "\n\n" + detail)
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        if send is None:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=5) as response:
                payload = json.loads(response.read(65536))
        else:
            payload = send(request)
        accepted = payload.get("ok") is True and bool(payload.get("result", {}).get("message_id"))
        result = "accepted by Telegram" if accepted else "acknowledgment unavailable"
    except Exception as exc:
        accepted, result = False, type(exc).__name__  # exception text may include token
    _save(path, dict(record, status="accepted" if accepted else "failed", detail=result))
    return accepted


def report_alert(root, report):
    """Alert on blocked/halted observations and recovery, without changing the account."""
    try:
        path = Path(root) / "alerts" / "observation.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        state = report["state"]
        if state != "running":
            notify(
                root,
                "data-or-account-blocked",
                f"State: {state}; stage: {report.get('error_stage')}; reason: {report.get('error_reason')}. "
                "No strategy orders are authorized by this alert. Inspect the stored status.",
            )
        elif previous.get("state") not in (None, "running"):
            notify(
                root,
                "recovered",
                "Fresh paper observations resumed after state: " + previous["state"],
            )
        _save(path, dict(state=state, updated_at=report["updated_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass  # Notifications must never stop accounting or grant execution authority.


def notify(root, kind, detail, **kwargs):
    try:
        return _notify(root, kind, detail, **kwargs)
    except (OSError, ValueError, KeyError, TypeError):
        return False
