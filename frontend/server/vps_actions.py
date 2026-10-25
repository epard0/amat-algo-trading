"""Bounded remote engine controls and an explicit remote-only observation cache."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import sys
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from server.remote import Runner, config_path, load_config, safe_output

REMOTE_IDS = {
    "perps.start": "perps.start",
    "perps.stop": "perps.stop",
    "perps.restart": "perps.restart",
    "perps.status": "perps.status",
    **{
        f"vps.executor.{verb}": f"executor.{verb}"
        for verb in ("start", "stop", "restart", "status")
    },
}


def enabled():
    return os.getenv("TERMINAL_PERPS_TARGET", "local") == "remote"


def capabilities(root):
    if not enabled():
        return []
    return [
        dict(
            id=id_,
            capability=id_,
            target="remote",
            supported=True,
            reason=None,
            effects=[
                f"Runs {operation} on the configured VPS; affects the remote paper container",
                "Stop preserves positions and durable state; start/restart resumes the native engine",
            ],
            inputs={},
        )
        for id_, operation in REMOTE_IDS.items()
    ]


def command(service, action):
    if not enabled() or action.capability not in REMOTE_IDS:
        return None
    if action.inputs:
        raise HTTPException(422, "Remote container actions accept no inputs")
    cap = next(c for c in capabilities(service.root) if c["id"] == action.capability)
    load_config(
        config_path(service.storage)
    )  # fail before issuing a preview if config is unavailable
    return (
        [
            sys.executable,
            "-m",
            "server.vps_actions",
            "--config",
            str(config_path(service.storage)),
            "--operation",
            REMOTE_IDS[action.capability],
        ],
        Path(__file__).resolve().parents[1],
        cap,
    )


def invoke(path, operation):
    if operation not in {"overview", *REMOTE_IDS.values()}:
        raise ValueError("Unregistered VPS operation")
    config = load_config(Path(path))
    config["command_timeout"] = 60
    # The program and operation names are fixed by this adapter, not browser shell text.
    remote_program = f"/home/{config['username']}/.local/bin/amat-vps-control"
    result = Runner(config).run(shlex.join([remote_program, operation]))
    result = safe_output(result, config)
    if result["exit_code"] != 0 or result["truncated"]:
        raise RuntimeError(result.get("stderr") or f"Remote operation {result['status']}")
    data = json.loads(result["stdout"])
    if operation in ("overview", "perps.status"):
        perps = data.get("perps", data)
        if perps.get("status") == "observed" and (
            perps.get("mode") != "paper" or perps.get("live_enabled") is not False
        ):
            raise ValueError("Remote observation did not establish paper mode")
    return data


class RemoteEngines:
    def __init__(self, service):
        self.service = service
        self.lock = threading.Lock()
        self.collect_lock = threading.Lock()
        self.last_attempt = 0.0
        self.cached = None
        with service.lock:
            row = service.db.execute(
                "SELECT data FROM observations WHERE name='remote.engines'"
            ).fetchone()
        if row:
            self.cached = json.loads(row["data"])

    def describe(self):
        """Cache-only UI reads never initiate SSH or amplify request traffic."""
        with self.lock:
            value = dict(self.cached or {"transport": "unobserved", "status": "waiting"})
        stamp = value.get("refreshed_at")
        try:
            age = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(stamp)).total_seconds()
        except (TypeError, ValueError):
            age = None
        value["age_seconds"] = round(age, 1) if age is not None else None
        value["stale"] = age is None or not 0 <= age <= 150 or value.get("transport") != "connected"
        return value

    def refresh(self):
        """One in-flight named, read-only probe shared by scheduler and UI."""
        if not self.collect_lock.acquire(blocking=False):
            return self.describe_unlocked()
        try:
            self.last_attempt = time.monotonic()
            config = load_config(config_path(self.service.storage))
            config["command_timeout"] = 35
            runner = Runner(config)
            client = runner.connect()
            try:
                result = runner.execute(client, "health_summary")
            finally:
                client.close()
            if result["exit_code"] != 0 or result["truncated"]:
                raise RuntimeError("Remote health helper unavailable or diagnostic timed out")
            value = result.get("data")
            if (
                not isinstance(value, dict)
                or value.get("schema") != "remote-health/1"
                or value.get("execution_authority") != "none"
            ):
                raise ValueError("Remote health contract is unavailable")
            perps = value.get("perps", {})
            if perps.get("status") == "observed" and (
                perps.get("mode") != "paper" or perps.get("live_enabled") is not False
            ):
                raise ValueError("Remote observation did not establish paper mode")
            if perps.get("status") in {"stopped", "unavailable"}:
                # A health probe never starts a helper container to read a stopped
                # ledger. Preserve dated native measurements without promoting them
                # to current process health or order readiness.
                prior = (self.cached or {}).get("perps", {})
                retained = {
                    key: prior[key]
                    for key in (
                        "accounts",
                        "positions",
                        "observed_at",
                        "leverage_policy",
                        "mode",
                        "live_enabled",
                        "withdrawals_enabled",
                        "source",
                    )
                    if key in prior
                }
                value["perps"] = {**retained, **perps, "stale": True}
            value.update(transport="connected", refreshed_at=dt.datetime.now(dt.UTC).isoformat())
            self.cached = value
            with self.service.lock, self.service.db:
                self.service.db.execute(
                    "INSERT OR REPLACE INTO observations(name,data) VALUES (?,?)",
                    ("remote.engines", json.dumps(value)),
                )
        except (OSError, ValueError, RuntimeError) as exc:
            self.cached = {
                **(self.cached or {}),
                "transport": "unavailable",
                "reason": str(exc),
                "attempted_at": dt.datetime.now(dt.UTC).isoformat(),
                **{
                    name: {
                        "source": f"remote VPS · {name}",
                        **((self.cached or {}).get(name) or {}),
                        "stale": True,
                        "status": "unavailable",
                        "reason": str(exc),
                    }
                    for name in ("executor", "executor_live", "perps")
                },
            }
        finally:
            self.collect_lock.release()
        return self.describe()

    def describe_unlocked(self):
        # A busy collector never forces UI threads to wait on SSH.
        return {**(self.cached or {}), "collecting": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--operation", required=True, choices=sorted(set(REMOTE_IDS.values())))
    args = parser.parse_args()
    try:
        print(json.dumps(invoke(args.config, args.operation)))
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
