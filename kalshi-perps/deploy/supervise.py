"""Supervise the native runner with a bounded progress watchdog and graceful stop."""

import os
import signal
import subprocess
import sys
import time

from health import health

from kalshi_perps.api import create_strategy
from kalshi_perps.config import Config
from kalshi_perps.operations import notify

config = Config.load("configs/paper.toml")
if config.mode != "paper" or config.raw.get("execution_enabled") is not False:
    raise SystemExit("Paper deployment requires mode=paper and execution_enabled=false")
for key in ("KALSHI_PERPS_KEY_ID", "KALSHI_PERPS_PRIVATE_KEY_PATH"):
    if os.environ.get(key):
        raise SystemExit("Venue signing credentials must not enter the paper deployment")
create_strategy(config)  # Verify exact source hashes before starting a process.
child = subprocess.Popen(
    [sys.executable, "-m", "kalshi_perps.cli", "run", "--config", str(config.path)]
)
stopping = False


def stop(_signum, _frame):
    global stopping
    stopping = True
    child.terminate()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
last_progress = time.monotonic()
while child.poll() is None:
    if stopping:
        try:
            child.wait(timeout=45)
        except subprocess.TimeoutExpired:
            child.kill()
        break
    if health().get("responsive", False):
        last_progress = time.monotonic()
    if time.monotonic() - last_progress > 600:
        notify(
            config.database.parent,
            "watchdog-stalled",
            "No durable runner heartbeat for ten minutes; restarting the paper process.",
        )
        print("Paper progress watchdog expired; exiting for Docker recovery", flush=True)
        child.terminate()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
        raise SystemExit(1)
    time.sleep(5)
if not stopping and child.returncode:
    notify(
        config.database.parent,
        "runner-exited",
        f"Paper runner exited with code {child.returncode}; Docker recovery will restart it.",
    )
raise SystemExit(0 if stopping else child.returncode or 0)
