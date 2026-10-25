#!/usr/bin/env python3
"""Start local API and WebUI together; stop children on exit."""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8765)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument(
        "--production", action="store_true", help="Serve the built web/dist from the API process"
    )
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.api_port, args.web_port)):
        parser.error("ports must be between 1 and 65535")
    if args.production and not (ROOT / "web/dist/index.html").is_file():
        parser.error("Build the WebUI first: cd frontend/web && npm run build")
    if not args.api_only and not args.production and not (ROOT / "web/node_modules").is_dir():
        parser.error("Install WebUI dependencies first: cd frontend/web && npm ci")
    children = []

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        api_env = dict(os.environ)
        api_env.setdefault(
            "TERMINAL_ALLOWED_ORIGINS",
            ",".join(
                f"http://{host}:{port}"
                for host in ("127.0.0.1", "localhost")
                for port in (args.api_port, args.web_port)
            ),
        )
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "server.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                    "--timeout-graceful-shutdown",
                    "5",
                ],
                cwd=ROOT,
                env=api_env,
                start_new_session=os.name != "nt",
            )
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if children[0].poll() is not None:
                return children[0].returncode or 1
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{args.api_port}/api/v1/health/live", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except (OSError, ValueError):
                time.sleep(0.2)
        else:
            print("API did not become live within 30 seconds", file=sys.stderr)
            return 1
        if args.production:
            print(f"AMAT Terminal: http://127.0.0.1:{args.api_port}", flush=True)
        elif not args.api_only:
            npm = shutil.which("npm")
            if npm is None:
                raise RuntimeError("npm is missing")
            env = dict(os.environ, AMAT_API_URL=f"http://127.0.0.1:{args.api_port}")
            children.append(
                subprocess.Popen(
                    [
                        npm,
                        "run",
                        "dev",
                        "--",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(args.web_port),
                        "--strictPort",
                    ],
                    cwd=ROOT / "web",
                    env=env,
                    start_new_session=os.name != "nt",
                )
            )
            print(f"AMAT Terminal: http://127.0.0.1:{args.web_port}", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.3)
        return next((child.returncode for child in children if child.returncode), 0)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                if os.name != "nt":
                    os.killpg(child.pid, signal.SIGTERM)
                else:
                    child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
