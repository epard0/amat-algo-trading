"""Loopback terminal API. Reads registered evidence; executes bounded named commands."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from server.strategy_defaults import default_strategy

ROOT = Path(__file__).resolve().parents[2]


def now():
    return dt.datetime.now(dt.UTC).isoformat()


def redact(value):
    if isinstance(value, dict):
        return {
            k: (
                "[redacted]"
                if re.search(
                    r"secret|password|token|api.?key|private.?key|credential|account_number|authorization|environment",
                    k,
                    re.I,
                )
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if len(secret) >= 6 and re.search(
                r"secret|password|token|api.?key|trading_key", key, re.I
            ):
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"\bPA[A-Z0-9]{6,}\b", "[redacted-account]", value)
        value = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[redacted]", value)
        value = re.sub(
            r"(?i)((?:api[_ -]?key|secret|password|token)\s*[=:]\s*)[^\s,;]+",
            r"\1[redacted]",
            value,
        )
    return value


class CancelledError(Exception):
    pass


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class Execute(Action):
    preview_id: str
    idempotency_key: str = Field(min_length=8, max_length=128)


class RemoteChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[str] | None = Field(default=None, max_length=8)


class Service:
    def __init__(self, root=ROOT, state=None, storage=None, recover=True):
        self.root = Path(root).resolve()
        self.state = Path(
            state or os.getenv("TERMINAL_EXECUTOR_STATE", self.root / "executor/state")
        ).resolve()
        self.storage = Path(
            storage or os.getenv("TERMINAL_STORAGE", self.root / "frontend/.runtime")
        ).resolve()
        self.research_root = (
            Path(
                os.getenv(
                    "TERMINAL_RESEARCH_ROOT",
                    Path.home()
                    / ".local/share/amat/web-research"
                    / hashlib.sha256(str(self.root).encode()).hexdigest()[:12],
                )
            )
            .expanduser()
            .resolve()
        )
        self.storage.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.storage / "terminal.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.lock = threading.RLock()
        self.sessions = {}
        self.previews = {}
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, native_key TEXT UNIQUE, data TEXT); CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, idem TEXT UNIQUE, fingerprint TEXT, data TEXT); CREATE TABLE IF NOT EXISTS offsets (path TEXT PRIMARY KEY, inode INTEGER, offset INTEGER); CREATE TABLE IF NOT EXISTS observations (name TEXT PRIMARY KEY, data TEXT);"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS events_action_cursor ON events "
            "(json_extract(data, '$.action_id'), id)"
        )
        for row in self.db.execute("SELECT id,data FROM jobs").fetchall():
            data = json.loads(row["data"])
            from server.supervision import worker_active

            if (
                recover
                and data["status"] in ["queued", "started"]
                and not worker_active(self.storage, data["id"])
            ):
                data.update(
                    status="outcome_unknown",
                    finished_at=now(),
                    reason="API restarted; no automatic retry",
                )
                self.save_job(data)
                self.event("action.outcome_unknown", {"reason": data["reason"]}, data["id"])
        self.db.commit()

    def read(self, path):
        try:
            p = Path(path).resolve()
            if not p.is_relative_to(self.state) and not p.is_relative_to(self.root / "amat"):
                return None
            if p.stat().st_size > 8_000_000:
                return None
            return redact(json.loads(p.read_text()))
        except (OSError, ValueError):
            return None

    def event(
        self,
        kind,
        payload,
        action_id=None,
        native_key=None,
        occurred_at=None,
        origin="adapter",
        commit=True,
    ):
        data = {
            "schema_version": "terminal-event/1",
            "occurred_at": occurred_at or now(),
            "observed_at": now(),
            "kind": kind,
            "severity": "error" if kind.endswith(("failed", "outcome_unknown")) else "info",
            "source": "executor" if origin == "native" else "terminal",
            "causation_id": payload.get("causation_id") if isinstance(payload, dict) else None,
            "origin": origin,
            "action_id": action_id,
            "correlation_id": action_id,
            "deployment_id": "local",
            "payload": redact(payload),
        }
        with self.lock:
            self.db.execute(
                "INSERT OR IGNORE INTO events(native_key,data) VALUES (?,?)",
                (native_key, json.dumps(data)),
            )
            if commit:
                self.db.commit()

    def events(self, after=0, limit=200):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,data FROM events WHERE id>? ORDER BY id LIMIT ?",
                (after, limit),
            ).fetchall()
        return [
            dict(json.loads(r["data"]), event_id=str(r["id"]), cursor=str(r["id"])) for r in rows
        ]

    def ingest(self):
        for name in ["journal.1.ndjson", "journal.ndjson"]:
            path = self.state / name
            try:
                if path.is_symlink():
                    continue
                st = path.stat()
                with self.lock:
                    old = self.db.execute(
                        "SELECT inode,offset FROM offsets WHERE path=?", (name,)
                    ).fetchone()
                offset = (
                    old["offset"]
                    if old and old["inode"] == st.st_ino and old["offset"] <= st.st_size
                    else 0
                )
                with path.open("rb") as f:
                    f.seek(offset)
                    for _ in range(2000):
                        line = f.readline(262145)
                        if not line or not line.endswith(b"\n"):
                            break
                        end = f.tell()
                        try:
                            payload = json.loads(line)
                            key = hashlib.sha256(line).hexdigest()
                            self.event(
                                str(
                                    payload.get(
                                        "event",
                                        payload.get("kind", "executor.observation"),
                                    )
                                ),
                                payload,
                                native_key=key,
                                occurred_at=payload.get("at"),
                                origin="native",
                            )
                        except (ValueError, AttributeError):
                            self.event(
                                "source.corrupt_line",
                                {"file": name, "offset": offset},
                                native_key=f"corrupt:{st.st_ino}:{offset}",
                            )
                        offset = end
                with self.lock:
                    self.db.execute(
                        "INSERT OR REPLACE INTO offsets VALUES (?,?,?)",
                        (name, st.st_ino, offset),
                    )
                    self.db.commit()
            except OSError:
                pass

    def cleanup_transients(self):
        """Expire transient browser state without touching durable jobs/receipts."""
        cutoff = time.time()
        with self.lock:
            for mapping in (self.previews, self.sessions):
                for key in [key for key, value in mapping.items() if value["expires"] <= cutoff]:
                    del mapping[key]

    def revision(self):
        digest = hashlib.sha256()
        for p in [
            self.state / "heartbeat.json",
            self.state / "STOP_SUBMISSIONS",
            self.state / "order-registry.json",
            self.state / "portfolio-monitor.json",
            *sorted((self.root / "amat/configs/strategy").glob("*.toml")),
            *sorted((self.root / "amat/data/snapshots").glob("*/manifest.json")),
        ]:
            try:
                digest.update(str(p).encode())
                digest.update(p.read_bytes())
            except OSError:
                digest.update(b"missing")
        return digest.hexdigest()

    def capabilities(self):
        from server.catalog import decorate

        return [decorate(c) for c in self._capabilities()]

    def _capabilities(self):
        names = {
            "executor.status": ("executor", "status", True),
            "executor.health": ("executor", "health", True),
            "executor.trades": ("executor", "trades", True),
            "executor.check": ("executor", "check", False),
            "executor.account": ("executor", "account", False),
            "executor.readiness": ("executor", "health", False),
            "executor.reconcile_submissions": (
                "executor",
                "reconcile-submissions",
                False,
            ),
            "amat.status": ("amat", "status", True),
            "amat.active": ("amat", "active", True),
        }
        enabled = os.getenv("TERMINAL_ENABLE_BROKER_READS") == "1"
        extra = [
            {
                "id": k,
                "capability": k,
                "supported": (self.root / "amat/.venv/bin/python").exists(),
                "reason": None,
                "effects": ["Reads registered strategy configuration"]
                if k != "amat.backtest.synthetic"
                else ["Runs deterministic synthetic engine test; not market performance"],
                "inputs": {
                    "config": {
                        "type": "select",
                        "options": [
                            p.name
                            for p in sorted((self.root / "amat/configs/strategy").glob("*.toml"))
                        ],
                        "default": default_strategy(self.root),
                    },
                    **(
                        {
                            "sessions": {
                                "type": "integer",
                                "minimum": 300,
                                "maximum": 3000,
                                "default": 1200,
                            },
                            "seed": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 2147483647,
                                "default": 20260808,
                            },
                        }
                        if k == "amat.backtest.synthetic"
                        else {}
                    ),
                },
            }
            for k in [
                "amat.config.validate",
                "amat.config.show",
                "amat.config.universe",
                "amat.backtest.synthetic",
            ]
        ]
        controls_enabled = os.getenv("TERMINAL_ENABLE_EXECUTOR_ACTIONS") == "1"
        for operation in ("submit", "report"):
            extra.append(
                {
                    "id": "executor." + operation,
                    "capability": "executor." + operation,
                    "supported": controls_enabled
                    and (self.root / "executor/.venv/bin/python").exists(),
                    "reason": None
                    if controls_enabled
                    else "Requires TERMINAL_ENABLE_EXECUTOR_ACTIONS=1 and the configured paper executor environment",
                    "effects": [
                        "Runs existing paper submission preflight and may place paper orders; never retries uncertain orders"
                    ]
                    if operation == "submit"
                    else [
                        "Records equity, writes reports, may deliver configured notifications and apply report retention"
                    ],
                    "inputs": {
                        "session": {
                            "type": "string",
                            "default": "",
                            "description": "Optional exchange session date YYYY-MM-DD; empty uses the executor's exchange date",
                        }
                    },
                }
            )
        from server.control_actions import _snapshots

        research_snapshots = _snapshots(self.root)
        default_snapshot = next(
            (
                name
                for name in (
                    "alpaca-sip-core-2026-10-13-verified",
                    "alpaca-sip-2026-08-11",
                )
                if name in research_snapshots
            ),
            research_snapshots[0] if research_snapshots else "",
        )
        extra.append(
            {
                "id": "amat.research.launch",
                "capability": "amat.research.launch",
                "supported": (self.root / "amat/.venv/bin/python").exists()
                and bool(research_snapshots),
                "reason": None if research_snapshots else "A frozen research dataset is required",
                "effects": [
                    "Runs built-in proposals in an isolated workspace outside the project; no model calls or trades; may take up to 1 hour"
                ],
                "inputs": {
                    "name": {"type": "string", "default": "terminal-research"},
                    "program": {
                        "type": "select",
                        "options": ["standard", "training-2025"],
                        "default": "standard",
                    },
                    "robustness": {
                        "type": "select",
                        "options": ["historical", "extended"],
                        "default": "historical",
                    },
                    "snapshot": {
                        "type": "select",
                        "options": research_snapshots,
                        "default": default_snapshot,
                    },
                    "iterations": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3,
                        "default": 1,
                    },
                },
            }
        )
        from server.control_actions import capabilities as control_capabilities
        from server.engine_actions import capabilities as engine_capabilities
        from server.perps_actions import capabilities as perps_capabilities

        return (
            perps_capabilities(self.root)
            + engine_capabilities(self.root, self.storage)
            + extra
            + [
                {
                    "id": k,
                    "capability": k,
                    "supported": (self.root / v[0] / ".venv/bin/python").exists()
                    and (v[2] or enabled),
                    "reason": None
                    if v[2] or enabled
                    else "Requires TERMINAL_ENABLE_BROKER_READS=1 and configured executor environment",
                    "effects": ["Writes trade-monitor.json"]
                    if k == "executor.trades"
                    else ["Reads local evidence"]
                    if v[2]
                    else ["Contacts configured paper broker; may write reconciliation receipts"]
                    if k.endswith("reconcile_submissions")
                    else ["Contacts configured paper broker; check also builds an inert plan"],
                    "inputs": {},
                }
                for k, v in names.items()
            ]
            + control_capabilities(self.root, self.state)
        )

    def command(self, action):
        from server.perps_actions import command as perps_command

        perps = perps_command(self, action)
        if perps is not None:
            return perps
        from server.engine_actions import command as engine_command

        engine = engine_command(self, action)
        if engine is not None:
            return engine
        from server.control_actions import command as control_command

        control = control_command(self, action)
        if control is not None:
            return control
        cap = next((c for c in self.capabilities() if c["id"] == action.capability), None)
        if not cap or not cap["supported"]:
            raise HTTPException(422, "Capability unavailable")
        if action.capability in {"executor.submit", "executor.report"}:
            if set(action.inputs) - {"session"}:
                raise HTTPException(422, "Unknown executor input fields")
            session = action.inputs.get("session", "")
            if not isinstance(session, str):
                raise HTTPException(422, "Session must be an ISO date")
            if session:
                try:
                    if dt.date.fromisoformat(session).isoformat() != session:
                        raise ValueError()
                except ValueError:
                    raise HTTPException(422, "Session must be YYYY-MM-DD")
            command = [
                str(self.root / "executor/.venv/bin/python"),
                "-m",
                "executor.cli",
                action.capability.split(".")[-1],
            ]
            if session:
                command += ["--session", session]
            return command, self.root / "executor", cap
        if action.capability == "amat.research.launch":
            if set(action.inputs) - {"name", "snapshot", "iterations", "program", "robustness"}:
                raise HTTPException(422, "Unknown research input fields")
            name = action.inputs.get("name", "terminal-research")
            snapshot = action.inputs.get("snapshot", cap["inputs"]["snapshot"]["default"])
            iterations = action.inputs.get("iterations", 1)
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,47}", name):
                raise HTTPException(422, "Invalid research workspace name")
            snapshots = {
                p.parent.name: p.parent
                for p in (self.root / "amat/data/snapshots").glob("*/daily.parquet")
                if not p.is_symlink()
                and p.resolve().is_relative_to(self.root / "amat/data/snapshots")
            }
            if snapshot not in snapshots or type(iterations) is not int or not 1 <= iterations <= 3:
                raise HTTPException(422, "Invalid snapshot or iterations")
            program = action.inputs.get("program", "standard")
            robustness = action.inputs.get("robustness", "historical")
            if program not in ("standard", "training-2025") or robustness not in (
                "historical",
                "extended",
            ):
                raise HTTPException(422, "Invalid research program or robustness preset")
            if self.research_root.is_relative_to(self.root) or self.root.is_relative_to(
                self.research_root
            ):
                raise HTTPException(
                    422, "TERMINAL_RESEARCH_ROOT must be outside the project checkout"
                )
            workspace = self.research_root / name
            if not workspace.resolve().is_relative_to(self.research_root):
                raise HTTPException(422, "Research workspace escapes its configured root")
            if workspace.exists():
                raise HTTPException(409, "Research name already exists; choose a new name")
            cmd = [
                str(self.root / "amat/.venv/bin/python"),
                "-m",
                "autoresearch.cli",
                "start",
                str(iterations),
                "--source",
                str(self.root / "amat"),
                "--snapshot",
                str(snapshots[snapshot]),
                "--workspace",
                str(workspace),
                "--built-in",
                "--no-open",
                "--program",
                program,
                "--robustness",
                robustness,
                "--max-model-tokens-per-day",
                "256",
            ]
            return cmd, self.root / "amat", cap
        if action.capability in {
            "amat.config.validate",
            "amat.config.show",
            "amat.config.universe",
            "amat.backtest.synthetic",
        }:
            allowed = (
                {"config", "sessions", "seed"}
                if action.capability == "amat.backtest.synthetic"
                else {"config"}
            )
            if set(action.inputs) - allowed:
                raise HTTPException(422, "Unknown input fields")
            config = action.inputs.get(
                "config",
                default_strategy(self.root),
            )
            configs = {
                p.name: p
                for p in (self.root / "amat/configs/strategy").glob("*.toml")
                if not p.is_symlink()
            }
            if config not in configs:
                raise HTTPException(422, "Unknown registered configuration")
            cmd = [str(self.root / "amat/.venv/bin/python"), "-m", "amat.cli.main"]
            if action.capability == "amat.backtest.synthetic":
                sessions = action.inputs.get("sessions", 1200)
                seed = action.inputs.get("seed", 20260808)
                if (
                    type(sessions) is not int
                    or not 300 <= sessions <= 3000
                    or type(seed) is not int
                    or not 0 <= seed <= 2147483647
                ):
                    raise HTTPException(422, "Invalid sessions or seed bounds")
                cmd += [
                    "backtest-synthetic",
                    str(configs[config]),
                    "--sessions",
                    str(sessions),
                    "--seed",
                    str(seed),
                    "--json",
                ]
            else:
                cmd += [
                    {
                        "validate": "validate-config",
                        "show": "show-config",
                        "universe": "show-universe",
                    }[action.capability.split(".")[-1]],
                    str(configs[config]),
                ]
            return cmd, self.root / "amat", cap
        if action.inputs:
            raise HTTPException(422, "This capability accepts no input fields")
        project = "executor" if action.capability.startswith("executor.") else "amat"
        executable = self.root / project / ".venv/bin/python"
        if project == "executor":
            cmd = [
                str(executable),
                "-m",
                "executor.cli",
                (
                    "health"
                    if action.capability == "executor.readiness"
                    else action.capability.split(".")[1].replace("_", "-")
                ),
            ]
            if action.capability in [
                "executor.status",
                "executor.health",
                "executor.trades",
            ]:
                cmd += ["--state-root", str(self.state)]
            if action.capability == "executor.status":
                cmd += ["--json"]
        else:
            cmd = [
                str(executable),
                "-m",
                "autoresearch.cli",
                action.capability.split(".")[1],
                "--source",
                str(self.root / "amat"),
            ]
        if action.capability == "executor.readiness":
            cmd += ["--ready"]
        return cmd, self.root / project, cap

    def save_job(self, data, *, commit=True):
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)",
                (
                    data["id"],
                    data["idempotency_key"],
                    data["fingerprint"],
                    json.dumps(data),
                ),
            )
            if commit:
                self.db.commit()

    def save_job_event(self, job, payload):
        """Publish the job transition and matching activity event atomically."""
        with self.lock, self.db:
            self.save_job(job, commit=False)
            self.event("action." + job["status"], payload, job["id"], commit=False)

    def jobs(self):
        with self.lock:
            return [
                json.loads(r[0])
                for r in self.db.execute("SELECT data FROM jobs ORDER BY rowid DESC LIMIT 100")
            ]

    def live_job_output(self, job_id):
        """Recover bounded durable stdout even before the worker publishes its final job."""
        chunks, size, cursor = [], 0, 0
        with self.lock:
            rows = self.db.execute(
                "SELECT id,data FROM events WHERE json_extract(data,'$.action_id')=? "
                "AND json_extract(data,'$.kind')='action.output' ORDER BY id LIMIT 4097",
                (job_id,),
            )
            for index, row in enumerate(rows):
                text = json.loads(row["data"]).get("payload", {}).get("text", "")
                if index >= 4096 or size + len(text) > 128000:
                    return dict(
                        live_output="".join(chunks),
                        live_output_cursor=cursor,
                        live_output_truncated=True,
                    )
                chunks.append(text)
                size += len(text)
                cursor = row["id"]
        return dict(
            live_output="".join(chunks), live_output_cursor=cursor, live_output_truncated=False
        )

    def run(self, job, cmd, cwd):
        job.update(status="started", started_at=now())
        self.save_job_event(job, {"capability": job["capability"]})
        try:
            child_env = dict(os.environ)
            from server.control_actions import child_env as owned_env
            from server.control_actions import timeout_seconds

            override = owned_env(job["capability"], child_env)
            if override is not None:
                child_env = override
            elif job["capability"].startswith(("amat.", "perps.", "vps.")):
                child_env = {
                    key: value
                    for key, value in child_env.items()
                    if not re.search(
                        r"secret|token|api.?key|password|smtp|mailgun|telegram|alpaca",
                        key,
                        re.I,
                    )
                }
            else:
                child_env.update(STATE_ROOT=str(self.state), AMAT_ROOT=str(self.root / "amat"))
            # No shell, no user-supplied environment. Output is streamed and bounded on disk.
            with subprocess.Popen(
                cmd,
                cwd=cwd,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            ) as proc:
                timeout = timeout_seconds(job["capability"]) or (
                    3600 if job["capability"] in {"amat.research.launch", "amat.backtest"} else 120
                )
                job["pid"] = proc.pid
                self.save_job(job)
                cancel_marker = self.storage / "cancel" / job["id"]
                deadline = time.monotonic() + timeout
                import selectors
                import signal

                selector = selectors.DefaultSelector()
                selector.register(proc.stdout, selectors.EVENT_READ)
                total = 0
                output = []
                from server.streaming import OutputLines

                lines = OutputLines(redact)
                while proc.poll() is None or selector.get_map():
                    if cancel_marker.exists():
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait()
                        raise CancelledError("Cancelled by operator; partial outputs may remain")
                    if time.monotonic() > deadline:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait()
                        raise TimeoutError(
                            f"Command exceeded {timeout} seconds; effects may require inspection"
                        )
                    for key, _ in selector.select(0.2):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if total < 128000:
                            clean = lines.feed(chunk)
                            total += len(chunk)
                            if not clean:
                                continue
                            output.append(clean)
                            self.event(
                                "action.output",
                                {"text": clean, "stream": "stdout"},
                                job["id"],
                            )
                tail = lines.feed(b"", final=True)
                if tail:
                    output.append(tail)
                    self.event("action.output", {"text": tail, "stream": "stdout"}, job["id"])
                if total >= 128000:
                    self.event("action.output_truncated", {"limit_bytes": 128000}, job["id"])
                code = proc.wait()
            if job["capability"] in {
                "executor.account",
                "executor.readiness",
                "executor.status",
            }:
                try:
                    observation = json.loads("".join(output))
                    if isinstance(observation, dict) and (
                        code == 0 or job["capability"] == "executor.readiness"
                    ):
                        self.db.execute(
                            "INSERT OR REPLACE INTO observations VALUES (?,?)",
                            (
                                job["capability"],
                                json.dumps({"observed_at": now(), "value": redact(observation)}),
                            ),
                        )
                        self.db.commit()
                        self.event(
                            "observation.refreshed",
                            {"resource": job["capability"], "causation_id": job["id"]},
                            job["id"],
                        )
                except (ValueError, sqlite3.Error):
                    pass
            job.update(
                status="succeeded" if code == 0 else "failed",
                exit_code=code,
                output="".join(output),
                finished_at=now(),
            )
        except Exception as exc:
            job.update(
                status="cancelled"
                if isinstance(exc, CancelledError)
                else "outcome_unknown"
                if isinstance(exc, TimeoutError)
                else "failed",
                reason=redact(str(exc)),
                finished_at=now(),
            )
        self.save_job_event(
            job,
            {
                "capability": job["capability"],
                "exit_code": job.get("exit_code"),
                "reason": job.get("reason"),
                "note": "Command outcome does not establish broker fills",
            },
        )

    def artifacts(self):
        rows = []
        for base, patterns in [
            (self.state / "plans", ["*.json"]),
            (self.state / "reports", ["*.json", "*.md"]),
            (
                self.root / "amat/outputs",
                [
                    "*/summary.json",
                    "*/report.json",
                    "analysis/*/report-summary.json",
                    "news/assets.json",
                ],
            ),
            (
                self.storage / "engine-artifacts",
                ["*/plan.json", "*/report.pdf", "*/runs/*/manifest.json"],
            ),
            (
                self.research_root,
                ["*/program.json", "*/report.json", "*/report.md", "*/report.html"],
            ),
        ]:
            for pattern in patterns:
                for p in sorted(base.glob(pattern), reverse=True)[:100]:
                    if (
                        p.is_file()
                        and not p.is_symlink()
                        and p.resolve().is_relative_to(base.resolve())
                    ):
                        rows.append(
                            {
                                "id": hashlib.sha256(str(p).encode()).hexdigest()[:24],
                                "name": p.name,
                                "path": str(p.relative_to(self.root))
                                if p.is_relative_to(self.root)
                                else p.name,
                                "size_bytes": p.stat().st_size,
                                "modified_at": dt.datetime.fromtimestamp(
                                    p.stat().st_mtime, dt.UTC
                                ).isoformat(),
                                "kind": base.name,
                                "_path": str(p),
                            }
                        )
        return rows[:300]

    def metric_definitions(self):
        try:
            return json.loads((self.root / "frontend/contracts/metrics.json").read_text())
        except (OSError, ValueError):
            return None

    def dashboard(self):
        self.ingest()
        reports = sorted((self.state / "reports").glob("*.json"), reverse=True)
        report = self.read(reports[0]) if reports else None
        report = report or {}
        registry = self.read(self.state / "order-registry.json") or {}
        heartbeat = self.read(self.state / "heartbeat.json") or {}
        stamp = heartbeat.get("last_success_at")
        age = None
        try:
            age = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(stamp)).total_seconds()
        except (TypeError, ValueError):
            pass
        health = {
            "healthy": age is not None
            and 0 <= age <= 1800
            and heartbeat.get("status") == "running",
            "status": heartbeat.get("status", "unavailable"),
            "progress_age_seconds": age,
            "submission_interlock": "STOP_SUBMISSIONS present"
            if (self.state / "STOP_SUBMISSIONS").exists()
            else None,
            "ready_for_orders": None,
            "order_readiness_issues": [
                "Live readiness requires configured executor.health/check action"
            ],
            "observed_at": stamp,
        }
        from server.runtime import observe

        health["runtime"] = observe(self.storage, self.jobs())
        raworders = registry.get("orders", report.get("orders", []))
        orders = list(raworders.values()) if isinstance(raworders, dict) else raworders
        with self.lock:
            event_rows = self.db.execute(
                "SELECT id,data FROM events ORDER BY id DESC LIMIT 200"
            ).fetchall()
        events = [
            dict(json.loads(row["data"]), event_id=str(row["id"]), cursor=str(row["id"]))
            for row in reversed(event_rows)
        ]
        account = {
            "status": "historical" if report.get("account") else "unavailable",
            "source": "executor report",
            "observed_at": report.get("generated_at"),
            "account": report.get("account"),
            "positions": report.get("positions", []),
            "reason": "Latest archived observation; no live broker refresh has been performed",
        }
        with self.lock:
            observed = {
                row["name"]: json.loads(row["data"])
                for row in self.db.execute("SELECT name,data FROM observations")
            }
        refreshed = observed.get("executor.account")
        if refreshed and refreshed["observed_at"] > (account.get("observed_at") or ""):
            account = {
                "status": "observed",
                "source": "executor.account paper broker read",
                "observed_at": refreshed["observed_at"],
                "account": refreshed["value"].get("account"),
                "positions": refreshed["value"].get("positions", []),
                "reason": "Last successful broker observation; see timestamp for age",
            }
        readiness = observed.get("executor.readiness")
        if readiness:
            health["local_readiness"] = readiness["value"]
            health["local_readiness_observed_at"] = readiness["observed_at"]
            # A cached readiness result cannot establish present order permission.
            health["order_readiness_issues"] = readiness["value"].get(
                "order_readiness_issues", []
            ) + ["Cached local check; execution revalidates all gates"]
        return {
            "health": health,
            "account": account,
            "orders": {
                "items": orders,
                "observed_at": registry.get("observed_at", report.get("generated_at")),
                "source": "order-registry.json" if registry else "executor report",
            },
            "operations": self.read(self.state / "operations.json")
            or {"status": "unavailable", "reason": "No operations observation"},
            "portfolio": self.read(self.state / "portfolio-monitor.json")
            or {"status": "unavailable"},
            "metrics": {
                "definitions": self.metric_definitions(),
                "values": report.get("stats", {}),
                "source": "executor report",
                "observed_at": report.get("generated_at"),
                "sample_is_meaningful": report.get("stats", {}).get("sample_is_meaningful", False),
            },
            "plan": report.get("plan"),
            "runs": self.jobs(),
            "artifacts": [{k: v for k, v in a.items() if k != "_path"} for a in self.artifacts()],
            "events": events,
            "watermark": events[-1]["cursor"] if events else "0",
            "observed_at": now(),
        }


def create_app(service=None):
    svc = service or Service()
    from server.market import MarketData, MarketError

    market = MarketData(svc.root)
    from server.remote import RemoteError, RemoteMonitor

    remote = RemoteMonitor(svc)
    from server.vps_actions import RemoteEngines

    remote_engines = RemoteEngines(svc)
    from server.health_scheduler import RemoteHealthScheduler

    health_scheduler = RemoteHealthScheduler(svc, remote_engines, remote)
    from server.remote_paper import RemotePaperSession

    remote_paper = RemotePaperSession(svc)

    @asynccontextmanager
    async def lifespan(app):
        async def ingest_loop():
            while True:
                await asyncio.to_thread(svc.ingest)
                await asyncio.sleep(2)

        task = asyncio.create_task(ingest_loop())
        health_scheduler.start()
        try:
            yield
        finally:
            health_scheduler.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="AMAT Terminal", version="0.1.0", lifespan=lifespan)
    app.state.service = svc
    app.state.health_scheduler = health_scheduler

    @app.middleware("http")
    async def boundary(request, call_next):
        host = request.url.hostname
        if host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return JSONResponse({"detail": "Untrusted Host"}, 400)
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse

            parsed = urlparse(origin)
            if (
                parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.scheme not in {"http", "https"}
                or origin
                not in set(
                    os.getenv(
                        "TERMINAL_ALLOWED_ORIGINS",
                        "http://127.0.0.1:8765,http://localhost:8765,http://127.0.0.1:5173,http://localhost:5173",
                    ).split(",")
                )
            ):
                return JSONResponse({"detail": "Untrusted Origin"}, 403)
        if (
            request.url.path.startswith("/api/")
            and request.url.path not in {"/api/v1/session", "/api/v1/health/live"}
            and request.method not in {"OPTIONS"}
        ):
            session = svc.sessions.get(request.cookies.get("terminal_session", ""))
            if not session or session["expires"] < time.time():
                return JSONResponse({"detail": "Local session required"}, 401)
            if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), session["csrf"]
            ):
                return JSONResponse({"detail": "CSRF token required"}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        # Narrow development CORS, cookie-authenticated same-origin in production.
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        return response

    @app.options("/api/{path:path}")
    def options(path):
        return JSONResponse(
            {},
            headers={
                "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type,X-CSRF-Token",
            },
        )

    @app.get("/api/v1/session")
    def session(request: Request):
        svc.cleanup_transients()
        sid = request.cookies.get("terminal_session")
        entry = svc.sessions.get(sid or "")
        if not entry or entry["expires"] < time.time():
            sid = secrets.token_urlsafe(32)
            entry = {"csrf": secrets.token_urlsafe(32), "expires": time.time() + 43200}
            svc.sessions[sid] = entry
        response = JSONResponse({"csrf_token": entry["csrf"], "expires_at": entry["expires"]})
        response.set_cookie(
            "terminal_session", sid, httponly=True, samesite="strict", max_age=43200
        )
        return response

    @app.get("/api/v1/remote")
    def remote_status():
        return remote.describe()

    @app.post("/api/v1/remote/run", status_code=202)
    def remote_run(payload: RemoteChecks):
        try:
            return remote.start(payload.checks)
        except RemoteError as exc:
            raise HTTPException(
                409 if exc.code == "busy" else 422, {"code": exc.code, "message": str(exc)}
            ) from exc

    @app.get("/api/v1/health/live")
    def live():
        return {"status": "ok", "service": "terminal-api", "observed_at": now()}

    @app.get("/api/v1/health/ready")
    def ready():
        return {"ready": True, "scope": "API database; not trading readiness"}

    @app.get("/api/v1/workspace")
    def workspace():
        return {
            "deployment_id": "local",
            "roots": [
                {
                    "id": "executor",
                    "path": str(svc.state),
                    "role": "read-only evidence",
                },
                {"id": "amat", "path": str(svc.root / "amat"), "role": "engine"},
            ],
            "capabilities": svc.capabilities(),
            "mode": "local evidence",
            "observed_at": now(),
        }

    @app.get("/api/v1/capabilities")
    def capabilities():
        return {"items": svc.capabilities(), "discovery": discovery()}

    def discovery():
        try:
            return json.loads((svc.root / "frontend/contracts/cli-inventory.json").read_text())
        except (OSError, ValueError):
            return None

    @app.get("/api/v1/dashboard")
    def dashboard():
        return svc.dashboard()

    @app.get("/api/v1/deployments/{deployment}/{section}")
    def deployment(deployment: str, section: str):
        if deployment != "local" or section not in {
            "health",
            "account",
            "orders",
            "operations",
            "portfolio",
        }:
            raise HTTPException(404, "Unknown deployment resource")
        return svc.dashboard()[section]

    @app.get("/api/v1/analysis/performance")
    def analysis_performance(
        period: str = "walk_forward",
        strategy: str = "core_daily",
        cost: int = 2,
        study: str = "campaign",
    ):
        from server.analysis import overview

        try:
            return overview(svc.root, period=period, strategy=strategy, cost=cost, study_id=study)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/analysis/screen")
    def analysis_screen():
        from server.analysis import saved_screen

        return saved_screen(svc.root)

    @app.get("/api/v1/analysis/account-history")
    def analysis_account_history():
        from server.analysis import account_history

        return account_history(svc.state)

    @app.get("/api/v1/metrics")
    def metrics():
        return svc.dashboard()["metrics"]

    @app.get("/api/v1/jobs")
    @app.get("/api/v1/runs")
    def jobs():
        return {"items": svc.jobs()}

    @app.get("/api/v1/jobs/{job_id}")
    @app.get("/api/v1/actions/{job_id}")
    def job(job_id: str):
        result = next((j for j in svc.jobs() if j["id"] == job_id), None)
        if not result:
            raise HTTPException(404, "Job not found")
        if result["status"] in {"queued", "started"}:
            result.update(svc.live_job_output(job_id))
        return result

    @app.get("/api/v1/perps")
    def perps_observation():
        from server.perps_actions import observation
        from server.vps_actions import enabled

        return remote_engines.describe().get("perps", {}) if enabled() else observation(svc.root)

    @app.get("/api/v1/paper-session")
    def paper_session_observation(session: str | None = None):
        from server.vps_actions import enabled

        if enabled():
            try:
                return remote_paper.describe(session)
            except ValueError:
                raise HTTPException(422, "Invalid registered paper session") from None
        from server.paper_session import published

        return published(svc.root)

    @app.get("/api/v1/research-campaign")
    def research_campaign_observation():
        from server.research_campaign import describe

        return describe(svc.root)

    @app.get("/api/v1/remote/engines")
    def engines_observation():
        return remote_engines.describe()

    @app.get("/api/v1/remote/health")
    def remote_health():
        return health_scheduler.describe()

    @app.post("/api/v1/remote/health/refresh", status_code=202)
    def refresh_remote_health():
        return health_scheduler.request_refresh()

    @app.get("/api/v1/workflows")
    def workflows():
        from server.catalog import workflows as build

        return {"items": build(svc.capabilities())}

    @app.get("/api/v1/news")
    def news():
        from server.control_actions import news_overview

        return news_overview(svc.root)

    @app.post("/api/v1/jobs/{job_id}/cancel", status_code=202)
    def cancel(job_id: str):
        target = next((j for j in svc.jobs() if j["id"] == job_id), None)
        if not target:
            raise HTTPException(404, "Job not found")
        if target["status"] not in {"queued", "started"}:
            raise HTTPException(409, "Job is not running")
        # Order-affecting work is never interrupted from the browser: an aborted
        # submit leaves an uncertain broker outcome that only reconciliation resolves.
        if not target["capability"].startswith(("amat.", "news.")):
            raise HTTPException(422, "Only read-only research and news jobs can be cancelled")
        marker = svc.storage / "cancel"
        marker.mkdir(exist_ok=True, mode=0o700)
        (marker / job_id).touch()
        svc.event("action.cancel_requested", {"capability": target["capability"]}, job_id)
        return {"job_id": job_id, "status": "cancel_requested"}

    @app.post("/api/v1/actions/preview")
    def preview(action: Action, request: Request):
        svc.cleanup_transients()
        cmd, cwd, cap = svc.command(action)
        preview_id = str(uuid.uuid4())
        expires = time.time() + 120
        with svc.lock:
            if len(svc.previews) >= 2048:
                raise HTTPException(
                    429, "Preview capacity reached; wait for older previews to expire"
                )
            svc.previews[preview_id] = {
                "action": action.model_dump(),
                "revision": svc.revision(),
                "expires": expires,
                "session": request.cookies.get("terminal_session"),
            }
        return {
            "preview_id": preview_id,
            "capability": action.capability,
            "inputs": action.inputs,
            "expires_at": dt.datetime.fromtimestamp(expires, dt.UTC).isoformat(),
            "effects": cap["effects"],
            "command": [Path(cmd[0]).name, *cmd[1:]],
            "scope": "No command executed during preview",
        }

    @app.post("/api/v1/actions", status_code=202)
    def execute(action: Execute, request: Request):
        payload = {"capability": action.capability, "inputs": action.inputs}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with svc.lock:
            existing = svc.db.execute(
                "SELECT fingerprint,data FROM jobs WHERE idem=?",
                (action.idempotency_key,),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Idempotency key has different inputs")
                data = json.loads(existing["data"])
                return {
                    "action_id": data["id"],
                    "job_id": data["id"],
                    "status_url": "/api/v1/jobs/" + data["id"],
                }
            p = svc.previews.get(action.preview_id)
            if (
                not p
                or p["expires"] < time.time()
                or p["action"] != payload
                or p["revision"] != svc.revision()
                or p["session"] != request.cookies.get("terminal_session")
            ):
                raise HTTPException(409, "Preview expired, changed, or belongs to another session")
            active = [job for job in svc.jobs() if job["status"] in {"queued", "started"}]
            heavy = {
                "amat.research.launch",
                "amat.backtest.synthetic",
                "amat.backtest",
                "amat.walkforward",
                "amat.montecarlo",
            }
            if len(active) >= 4 or (
                action.capability in heavy and any(job["capability"] in heavy for job in active)
            ):
                raise HTTPException(409, "Worker capacity is busy; wait for active work to finish")
            cmd, cwd, _ = svc.command(action)
            data = {
                "id": str(uuid.uuid4()),
                "capability": action.capability,
                "inputs": action.inputs,
                "fingerprint": fingerprint,
                "idempotency_key": action.idempotency_key,
                "status": "queued",
                "requested_at": now(),
            }
            svc.save_job(data)
            svc.event("action.requested", payload, data["id"])
            from server.supervision import dispatch

            try:
                dispatch(svc, data)
            except Exception:
                data.update(
                    status="failed",
                    finished_at=now(),
                    reason="Worker dispatch failed; no automatic retry",
                )
                svc.save_job(data)
                svc.event("action.failed", {"reason": data["reason"]}, data["id"])
        return {
            "action_id": data["id"],
            "job_id": data["id"],
            "status_url": "/api/v1/jobs/" + data["id"],
        }

    @app.get("/api/v1/events/history")
    def history(after: int = 0, limit: int = 200):
        svc.ingest()
        items = svc.events(max(0, after), min(max(1, limit), 1000))
        return {"items": items, "cursor": items[-1]["cursor"] if items else str(after)}

    @app.get("/api/v1/events/stream")
    async def stream(request: Request, after: int = 0):
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422, "Invalid Last-Event-ID")

        async def emit():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = svc.events(cursor, 200)
                for row in rows:
                    cursor = int(row["cursor"])
                    yield f"id: {cursor}\ndata: {json.dumps(row)}\n\n"
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            emit(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/v1/artifacts")
    def artifacts():
        return {"items": [{k: v for k, v in a.items() if k != "_path"} for a in svc.artifacts()]}

    @app.get("/api/v1/artifacts/{artifact_id}/download")
    def artifact_download(artifact_id: str):
        artifact = next((a for a in svc.artifacts() if a["id"] == artifact_id), None)
        if not artifact:
            raise HTTPException(404, "Artifact not found")
        path = Path(artifact["_path"])
        if path.suffix == ".html":
            return FileResponse(
                path,
                media_type="text/html",
                filename="research-comparison.html",
                headers={
                    "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        if path.suffix == ".pdf":
            return FileResponse(path, media_type="application/pdf", filename=path.name)
        # Structured/text evidence is redacted before export.
        try:
            text = path.read_text()
            content = redact(json.loads(text)) if path.suffix == ".json" else redact(text)
        except (OSError, ValueError):
            raise HTTPException(422, "Artifact is not readable")
        return JSONResponse(
            content,
            headers={"Content-Disposition": 'attachment; filename="artifact.json"'},
        )

    @app.get("/api/v1/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        a = next((a for a in svc.artifacts() if a["id"] == artifact_id), None)
        if not a:
            raise HTTPException(404, "Artifact not found")
        p = Path(a["_path"])
        if p.stat().st_size > 2_000_000:
            raise HTTPException(413, "Artifact exceeds preview limit")
        if p.suffix == ".pdf":
            return {
                "artifact": {k: v for k, v in a.items() if k != "_path"},
                "content": "PDF report; use the authenticated download",
                "download_url": "/api/v1/artifacts/" + artifact_id + "/download",
            }
        if p.suffix == ".json":
            return {
                "artifact": {k: v for k, v in a.items() if k != "_path"},
                "content": redact(json.loads(p.read_text())),
            }
        return {
            "artifact": {k: v for k, v in a.items() if k != "_path"},
            "content": redact(p.read_text()),
        }

    @app.get("/api/v1/market/snapshots")
    def snapshots():
        return market.snapshots()

    @app.get("/api/v1/market/symbols")
    def symbols(snapshot: str | None = None):
        try:
            return market.symbols(snapshot)
        except MarketError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/market/bars")
    def bars(
        symbol: str = "SPY",
        snapshot: str | None = None,
        limit: int = 250,
        resolution: str = "1d",
        start: str | None = None,
        end: str | None = None,
    ):
        try:
            return market.bars(symbol, snapshot, limit, resolution, start, end)
        except MarketError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/market/quotes")
    def quotes(snapshot: str | None = None, symbols: str | None = None):
        try:
            return market.quotes(snapshot, symbols)
        except MarketError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/market/depth")
    def depth():
        return {
            "items": [],
            "status": "unavailable",
            "reason": "No entitled depth provider configured",
            "live": False,
        }

    web = svc.root / "frontend/web/dist"

    @app.get("/{path:path}")
    def static(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API endpoint not found")
        target = (web / path).resolve()
        if not target.is_relative_to(web.resolve()):
            raise HTTPException(404)
        if target.is_file():
            return FileResponse(target)
        if (web / "index.html").exists():
            return FileResponse(web / "index.html")
        raise HTTPException(404, "Build frontend/web to serve the terminal")

    return app


app = create_app()
