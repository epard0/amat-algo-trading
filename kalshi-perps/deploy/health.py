"""Read durable native progress; process liveness alone is not health."""

import datetime as dt
import json
import time
from pathlib import Path


def health():
    try:
        report = json.loads(Path("/var/lib/kalshi-perps/adx-forward/status.json").read_text())
        age = time.time() - dt.datetime.fromisoformat(report["updated_at"]).timestamp()
        responsive = (
            0 <= age < 180 and report["mode"] == "paper" and report["live_enabled"] is False
        )
        return dict(
            healthy=responsive and report["state"] == "running",
            responsive=responsive,
            state=report["state"],
            mode=report["mode"],
            progress_age_seconds=round(age, 1),
            updated_at=report["updated_at"],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return dict(healthy=False, responsive=False, state="unobserved")


if __name__ == "__main__":
    result = health()
    print(json.dumps(result))
    raise SystemExit(0 if result["healthy"] else 1)
