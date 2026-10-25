"""SSH diagnostics with verified host identity and private local configuration."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import stat
import threading
import time
import tomllib
import uuid
from pathlib import Path

from executor.remote import RemoteError, SSHRunner


def config_path(storage: Path) -> Path:
    return Path(os.getenv("TERMINAL_REMOTE_CONFIG", str(storage / "remote.toml"))).expanduser()


def load_config(path: Path) -> dict:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise RemoteError(
                "config_permissions",
                "SSH configuration must be a regular private file (chmod 600).",
            )
        if info.st_uid != os.getuid() or info.st_size > 65536:
            raise RemoteError(
                "config_permissions",
                "SSH configuration must be owned by the current user and at most 64 KiB.",
            )
        config = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise RemoteError(
            "config_missing", "Create the local SSH configuration from config/remote.example.toml."
        ) from None
    except (OSError, tomllib.TOMLDecodeError):
        raise RemoteError(
            "config_invalid",
            "SSH configuration could not be parsed; check its TOML syntax and permissions.",
        ) from None
    required = ("host", "port", "username", "key_path", "known_hosts")
    missing = [name for name in required if name not in config]
    if missing:
        raise RemoteError(
            "config_invalid",
            "Private SSH configuration is missing required fields: " + ", ".join(missing),
        )
    defaults = dict(
        container="amat-executor",
        state_root="/var/lib/amat-executor",
        docker_command=["docker"],
        connect_timeout=10,
        command_timeout=20,
        max_output_bytes=131072,
    )
    config = defaults | config
    for field in ["host", "username", "container"]:
        if not isinstance(config[field], str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,253}", config[field]
        ):
            raise RemoteError("config_invalid", "Invalid SSH host, user or container name.")
    for field, lower, upper in [
        ("port", 1, 65535),
        ("connect_timeout", 1, 30),
        ("command_timeout", 1, 60),
        ("max_output_bytes", 1024, 1048576),
    ]:
        if type(config[field]) is not int or not lower <= config[field] <= upper:
            raise RemoteError("config_invalid", f"Invalid {field}.")
    if not isinstance(config.get("key_passphrase", ""), str):
        raise RemoteError("config_invalid", "key_passphrase must be a string.")
    if (
        not isinstance(config["state_root"], str)
        or not config["state_root"].startswith("/")
        or "\x00" in config["state_root"]
    ):
        raise RemoteError("config_invalid", "Remote state_root must be an absolute path.")
    if (
        not isinstance(config["docker_command"], list)
        or not 1 <= len(config["docker_command"]) <= 5
        or not all(isinstance(v, str) and v and "\x00" not in v for v in config["docker_command"])
    ):
        raise RemoteError("config_invalid", "docker_command must be a nonempty argv list.")
    custom = config.get("commands", {})
    if (
        not isinstance(custom, dict)
        or len(custom) > 20
        or any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", k)
            or not isinstance(v, str)
            or not v.strip()
            or len(v) > 4096
            or "\x00" in v
            for k, v in custom.items()
        )
    ):
        raise RemoteError("config_invalid", "Invalid named diagnostic command registry.")
    for name in ["key_path", "known_hosts"]:
        if (
            not isinstance(config[name], str)
            or not config[name].strip()
            or any(char in config[name] for char in ("\x00", "\n", "\r"))
        ):
            raise RemoteError("config_invalid", f"{name} must be a nonempty local path.")
        config[name] = str(Path(config[name]).expanduser().resolve())
    return config


def commands(config):
    docker = config["docker_command"]
    container = config["container"]
    host_code = "import json,os,shutil,datetime; m=dict(line.split(':',1) for line in open('/proc/meminfo')); d=shutil.disk_usage('/'); print(json.dumps({'host':os.uname().nodename,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'load_1_5_15':os.getloadavg(),'memory':m,'disk_total_bytes':d.total,'disk_free_bytes':d.free,'uptime_seconds':float(open('/proc/uptime').read().split()[0])}))"
    performance_code = (
        "import json,pathlib,collections; r=pathlib.Path("
        + repr(config["state_root"])
        + "); p=sorted((r/'reports').glob('*.json')); report=json.loads(p[-1].read_text()) if p else {}; e=r/'equity_curve.ndjson'; tail=[];\nif e.exists():\n with e.open() as f:\n  tail=[json.loads(x) for x in collections.deque(f,maxlen=120) if x.strip()]\nprint(json.dumps({'source':'remote persisted executor evidence','report_session':report.get('session'),'report_generated_at':report.get('generated_at'),'stats':report.get('stats'),'warnings':report.get('warnings',[]),'equity_observations':tail}))"
    )
    activity_code = (
        "import pathlib,collections; p=pathlib.Path("
        + repr(config["state_root"])
        + ")/'journal.ndjson'; "
        "print(''.join(collections.deque(p.open(), maxlen=100)) if p.exists() else 'No persisted executor activity')"
    )
    specs = {
        "host": ["python3", "-c", host_code],
        "container": docker
        + [
            "inspect",
            "--format",
            '{"running":{{.State.Running}},"status":{{json .State.Status}},"restart_count":{{.RestartCount}},"image":{{json .Config.Image}},"state":{{json .State}}}',
            container,
        ],
        "resources": docker + ["stats", "--no-stream", "--format", "{{json .}}", container],
        "health": docker + ["exec", container, "executor", "health"],
        "readiness": docker + ["exec", container, "executor", "health", "--ready"],
        "status": docker + ["exec", container, "executor", "status", "--json"],
        "performance": docker + ["exec", container, "python", "-c", performance_code],
        "logs": docker + ["exec", container, "python", "-c", activity_code],
        "health_summary": [
            "timeout",
            "30",
            "python3",
            "-I",
            f"/home/{config['username']}/.local/bin/amat-vps-control",
            "health",
        ],
    }
    result = {name: shlex.join(argv) for name, argv in specs.items()}
    for name, command in config.get("commands", {}).items():
        if name in result:
            raise RemoteError("config_invalid", "Custom commands cannot replace built-in checks.")
        result[name] = command
    return result


def safe_output(value, config):
    from server.app import redact

    value = redact(value)
    if isinstance(value, str):
        value = re.sub(
            r'(?i)(["\']?(?:secret|password|token|api[_-]?key|account_number)["\']?\s*[:=]\s*)("[^"\n]*"|\'[^\'\n]*\'|[^\s,;]+)',
            r'\1"[redacted]"',
            value,
        )
    secret = config.get("key_passphrase", "")
    if isinstance(value, str) and secret:
        value = value.replace(secret, "[redacted]")
    if isinstance(value, dict):
        value = {k: safe_output(v, config) for k, v in value.items()}
    if isinstance(value, list):
        value = [safe_output(v, config) for v in value]
    return value


class Runner(SSHRunner):
    def execute(self, client, name):
        command = commands(self.config).get(name)
        if command is None:
            raise RemoteError("unknown_command", "Unknown named remote command.")
        return self.execute_command(client, command, name)

    def result(self, name, began, stdout, stderr, code, status, truncated):
        out = safe_output(stdout.decode(errors="replace"), self.config)
        err = safe_output(stderr.decode(errors="replace"), self.config)
        try:
            parsed = safe_output(json.loads(out), self.config)
        except ValueError:
            parsed = None
        if parsed is not None:
            out = json.dumps(parsed, ensure_ascii=False)
        if status == "passed" and isinstance(parsed, dict):
            if name == "container" and (
                parsed.get("running", parsed.get("Running")) is False
                or parsed.get("state", parsed).get("Health", {}).get("Status") == "unhealthy"
            ):
                status = "needs_attention"
            if name == "health" and parsed.get("healthy") is not True:
                status = "needs_attention"
            if name == "readiness" and parsed.get("ready_for_orders") is not True:
                status = "needs_attention"
            if (
                name == "performance"
                and parsed.get("stats") is None
                and not parsed.get("equity_observations")
            ):
                status = "needs_attention"
        if (
            status == "passed"
            and name in {"health", "readiness", "container", "host", "resources", "performance"}
            and not isinstance(parsed, dict)
        ):
            status = "needs_attention"
        return {
            "command": name,
            "status": status,
            "exit_code": code,
            "stdout": out,
            "stderr": err,
            "data": parsed,
            "truncated": truncated,
            "duration_seconds": round(time.monotonic() - began, 3),
            "observed_at": dt.datetime.now(dt.UTC).isoformat(),
        }


class RemoteMonitor:
    def __init__(self, service):
        self.service = service
        self.path = config_path(service.storage)
        self.lock = threading.Lock()
        with service.lock:
            row = service.db.execute(
                "SELECT data FROM observations WHERE name='remote.executor'"
            ).fetchone()
            if row:
                prior = json.loads(row["data"])
                if prior.get("status") == "running":
                    prior.update(
                        status="interrupted",
                        reason="API restarted during diagnostics; run a fresh check.",
                        finished_at=dt.datetime.now(dt.UTC).isoformat(),
                    )
                    self.save(prior)

    def describe(self):
        try:
            config = load_config(self.path)
            names = list(commands(config))
            state = {
                "configured": True,
                "host": config["host"],
                "port": config["port"],
                "username": config["username"],
                "container": config["container"],
                "commands": names,
                "config_path": str(self.path),
                "passphrase_configured": bool(config.get("key_passphrase")),
            }
        except RemoteError as exc:
            state = {
                "configured": False,
                "config_path": str(self.path),
                "error_code": exc.code,
                "reason": str(exc),
                "commands": [],
            }
        with self.service.lock:
            row = self.service.db.execute(
                "SELECT data FROM observations WHERE name=?", ("remote.executor",)
            ).fetchone()
        state["last_run"] = json.loads(row["data"]) if row else None
        with self.service.lock:
            completed = self.service.db.execute(
                "SELECT data FROM observations WHERE name='remote.executor.completed'"
            ).fetchone()
        state["last_completed_run"] = json.loads(completed["data"]) if completed else None
        state["running"] = self.lock.locked()
        return state

    def start(self, selected):
        config = load_config(self.path)
        registry = commands(config)
        names = selected or [
            "host",
            "container",
            "resources",
            "health",
            "readiness",
            "status",
            "performance",
            "logs",
        ]
        if (
            len(names) > 8
            or len(names) != len(set(names))
            or any(name not in registry for name in names)
        ):
            raise RemoteError("unknown_command", "Select up to eight distinct registered checks.")
        if not self.lock.acquire(blocking=False):
            raise RemoteError("busy", "A remote diagnostic run is already active.")
        run = {
            "id": str(uuid.uuid4()),
            "status": "running",
            "started_at": dt.datetime.now(dt.UTC).isoformat(),
            "host": config["host"],
            "port": config["port"],
            "username": config["username"],
            "results": [],
        }
        self.save(run)
        self.service.event(
            "remote.check.started",
            {"run_id": run["id"], "host": config["host"], "commands": names},
            run["id"],
        )
        threading.Thread(target=self.run, args=(config, names, run), daemon=True).start()
        return run

    def save(self, run):
        with self.service.lock:
            self.service.db.execute(
                "INSERT OR REPLACE INTO observations VALUES (?,?)",
                ("remote.executor", json.dumps(run)),
            )
            self.service.db.commit()

    def run(self, config, names, run):
        client = None
        try:
            runner = Runner(config)
            client = runner.connect()
            for name in names:
                self.service.event(
                    "remote.command.started", {"run_id": run["id"], "command": name}, run["id"]
                )
                result = runner.execute(client, name)
                run["results"].append(result)
                self.save(run)
                self.service.event(
                    "remote.command.finished",
                    {
                        "run_id": run["id"],
                        "command": name,
                        "status": result["status"],
                        "exit_code": result["exit_code"],
                    },
                    run["id"],
                )
            run["status"] = (
                "passed"
                if all(r["status"] == "passed" and not r["truncated"] for r in run["results"])
                else "needs_attention"
            )
        except RemoteError as exc:
            run.update(status="failed", error_code=exc.code, reason=str(exc))
        except Exception:
            run.update(
                status="failed",
                error_code="runner_failed",
                reason="Remote diagnostic runner failed; no automatic retry.",
            )
        finally:
            if client is not None:
                client.close()
            run["finished_at"] = dt.datetime.now(dt.UTC).isoformat()
            self.save(run)
            if run["status"] in {"passed", "needs_attention"}:
                with self.service.lock:
                    self.service.db.execute(
                        "INSERT OR REPLACE INTO observations VALUES (?,?)",
                        ("remote.executor.completed", json.dumps(run)),
                    )
                    self.service.db.commit()
            self.service.event(
                "remote.check.finished", {"run_id": run["id"], "status": run["status"]}, run["id"]
            )
            self.lock.release()


def main():
    parser = argparse.ArgumentParser(description="Run named executor diagnostics over SSH.")
    parser.add_argument(
        "--config", type=Path, default=config_path(Path(__file__).resolve().parents[1] / ".runtime")
    )
    parser.add_argument(
        "--check", action="append", help="Registered check name; repeat for multiple checks"
    )
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        registry = commands(config)
        if args.list:
            print(json.dumps({"host": config["host"], "commands": list(registry)}, indent=2))
            return 0
        names = args.check or list(registry)[:8]
        if len(names) > 8 or len(set(names)) != len(names):
            raise RemoteError("invalid_checks", "Select at most eight distinct checks.")
        if any(name not in registry for name in names):
            raise RemoteError("unknown_command", "Unknown named command.")
        runner = Runner(config)
        client = runner.connect()
        try:
            results = [runner.execute(client, name) for name in names]
        finally:
            client.close()
        print(json.dumps({"host": config["host"], "results": results}, indent=2))
        return 0 if all(r["status"] == "passed" and not r["truncated"] for r in results) else 1
    except RemoteError as exc:
        print(json.dumps({"status": "failed", "error_code": exc.code, "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
