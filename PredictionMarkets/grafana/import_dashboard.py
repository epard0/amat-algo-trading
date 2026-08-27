#!/usr/bin/env python3
"""Push grafana/dashboard.json into a running Grafana.

Only needed when Grafana was started outside `docker compose` — the compose
stack provisions the same file automatically through
``grafana/provisioning/dashboards/dashboards.yml``.

Uses urllib rather than ``requests``: this repo already carries one undeclared
import that breaks a clean install, and an ops script is not worth a second.
The dashboard path is resolved from ``__file__`` so the script works from any
working directory.

    python grafana/import_dashboard.py
    python grafana/import_dashboard.py --url http://localhost:3000 --token glsa_xxx
    GRAFANA_TOKEN=glsa_xxx python grafana/import_dashboard.py
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DASHBOARD_PATH = Path(__file__).resolve().parent / "dashboard.json"


def request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    basic: tuple[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[int, Any]:
    """Perform one JSON request, returning (status, parsed_body).

    Never raises for HTTP errors: the caller needs the status code and the body
    to explain what went wrong, which a traceback would hide.
    """
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    elif basic:
        raw = base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {raw}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode()
            try:
                return response.status, json.loads(text)
            except json.JSONDecodeError:
                return response.status, text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(text)
        except json.JSONDecodeError:
            return exc.code, text
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


def load_dashboard(path: Path) -> dict[str, Any]:
    """Read the dashboard, accepting either the bare object or an API envelope.

    The file is stored bare so Grafana's file provisioner can mount it
    directly, while the HTTP API wants it wrapped. Tolerating both means the
    two delivery paths cannot drift apart.
    """
    if not path.is_file():
        raise SystemExit(f"dashboard not found: {path}")
    data = json.loads(path.read_text())
    dashboard = data.get("dashboard", data)
    if "panels" not in dashboard:
        raise SystemExit(f"{path} does not look like a Grafana dashboard")
    # An id minted by another Grafana instance is rejected as a conflict.
    dashboard.pop("id", None)
    return dashboard


def resolve_folder(base: str, title: str, auth: dict[str, Any]) -> str | None:
    status, folders = request(f"{base}/api/folders", **auth)
    if status == 200 and isinstance(folders, list):
        for folder in folders:
            if folder.get("title") == title:
                return folder.get("uid")
    status, created = request(
        f"{base}/api/folders", method="POST", payload={"title": title}, **auth
    )
    if status in (200, 201) and isinstance(created, dict):
        print(f"created folder '{title}'")
        return created.get("uid")
    print(f"could not create folder ({status}); importing to General", file=sys.stderr)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the bot dashboard into Grafana")
    parser.add_argument("--url", default=os.environ.get("GRAFANA_URL", "http://localhost:3000"))
    parser.add_argument("--token", default=os.environ.get("GRAFANA_TOKEN"),
                        help="service account token (preferred over basic auth)")
    parser.add_argument("--user", default=os.environ.get("GRAFANA_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("GRAFANA_PASSWORD", "admin"))
    parser.add_argument("--folder", default="Trading")
    parser.add_argument("--dashboard", type=Path, default=DASHBOARD_PATH)
    args = parser.parse_args(argv)

    base = args.url.rstrip("/")
    auth: dict[str, Any] = (
        {"token": args.token} if args.token else {"basic": (args.user, args.password)}
    )

    status, health = request(f"{base}/api/health", **auth)
    if status != 200:
        print(f"cannot reach Grafana at {base}: {health}", file=sys.stderr)
        print("is it running?  docker compose up -d grafana", file=sys.stderr)
        return 1
    version = health.get("version", "?") if isinstance(health, dict) else "?"
    print(f"Grafana {version} reachable at {base}")

    dashboard = load_dashboard(args.dashboard)
    folder_uid = resolve_folder(base, args.folder, auth)

    payload: dict[str, Any] = {
        "dashboard": dashboard,
        "overwrite": True,
        "message": "imported by grafana/import_dashboard.py",
    }
    if folder_uid:
        payload["folderUid"] = folder_uid

    status, result = request(
        f"{base}/api/dashboards/db", method="POST", payload=payload, **auth
    )
    if status in (200, 201) and isinstance(result, dict):
        print(f"imported '{dashboard['title']}' -> {base}{result.get('url', '')}")
        print(f"  {len(dashboard['panels'])} panels, status {result.get('status', 'ok')}")
        print("\nIf panels read 'No data', check the scrape target first:")
        print("  curl -s localhost:8787/metrics | head")
        return 0

    print(f"import failed ({status}): {result}", file=sys.stderr)
    if status == 401:
        print("authentication rejected — pass --token, or correct --user/--password",
              file=sys.stderr)
    elif status == 412:
        print("a dashboard with this uid exists and overwrite was refused", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
