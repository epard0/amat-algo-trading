"""Local API-owned remote diagnostics. Never acquires execution authority."""

from __future__ import annotations

import datetime as dt
import random
import threading
import time


def issues_for(observation):
    issues = []
    if observation.get("transport") != "connected":
        return ["Remote SSH observations unavailable"]
    infra = observation.get("infrastructure", {})
    host = infra.get("host", {})
    available, total = host.get("memory_available_bytes"), host.get("memory_total_bytes")
    if available is not None and total and available < max(96 * 1024**2, total * 0.1):
        issues.append("Host memory headroom is low; optional diagnostics deferred")
    if (
        host.get("swap_total_bytes")
        and host.get("swap_used_bytes", 0) / host["swap_total_bytes"] >= 0.75
    ):
        issues.append("Host swap usage exceeds 75% of available swap")
    if (
        host.get("disk_total_bytes")
        and host.get("disk_free_bytes", 0) / host["disk_total_bytes"] < 0.1
    ):
        issues.append("Host disk space is low")
    if host.get("inodes_total") and host.get("inodes_free", 0) / host["inodes_total"] < 0.05:
        issues.append("Host inode availability is low")
    for name, container in infra.get("containers", {}).items():
        if not container.get("running"):
            issues.append(
                f"{name} is {container.get('status', 'unobserved')}; operator stop is preserved"
            )
        if container.get("oom_killed"):
            issues.append(f"{name} reports an out-of-memory termination")
        if container.get("health") == "unhealthy":
            issues.append(f"{name} native container health needs attention")
        try:
            if float(container.get("resources", {}).get("MemPerc", "0").rstrip("%")) >= 85:
                issues.append(f"{name} memory usage exceeds 85% of its limit")
        except (ValueError, AttributeError):
            pass
    for field in ("unexpected_containers", "unexpected_listeners", "unexpected_services"):
        if infra.get(field):
            issues.append(f"{field.replace('_', ' ').capitalize()}: {', '.join(infra[field])}")
    for field in ("listener_observation_available", "service_observation_available"):
        if infra.get(field) is False:
            issues.append(f"{field.replace('_', ' ').capitalize()}: no")
    if infra.get("unexpected_ui_processes"):
        issues.append("Unexpected web UI or API process is running on the VPS")
    for kind, backup in infra.get("backups", {}).items():
        if backup.get("status") != "fresh":
            issues.append(f"{kind} backup is {backup.get('status', 'unobserved')}")
    if infra.get("paper_monitor", {}).get("stale"):
        issues.append("Existing paper-session collector observation is stale")
    for target in ("executor", "perps"):
        engine = observation.get(target, {})
        if engine.get("health", {}).get("healthy") is False:
            issues.append(f"{target} native progress or data health needs attention")
        if target == "executor" and engine.get("readiness", {}).get("ready_for_orders") is False:
            issues.append("Executor readiness has blocking conditions")
    return sorted(set([*issues, *infra.get("issues", [])]))


def optional_work_allowed(observation):
    if observation.get("transport") != "connected":
        return False
    host = observation.get("infrastructure", {}).get("host", {})
    available, total = host.get("memory_available_bytes"), host.get("memory_total_bytes")
    cpu = host.get("cpu_count")
    load = host.get("load_1_5_15", [])
    return bool(
        available is not None
        and total
        and available >= max(128 * 1024**2, total * 0.15)
        and cpu
        and load
        and load[0] <= cpu * 1.5
        and (
            not host.get("swap_total_bytes")
            or host.get("swap_used_bytes", 0) / host["swap_total_bytes"] < 0.75
        )
    )


class RemoteHealthScheduler:
    def __init__(
        self,
        service,
        engines,
        diagnostics,
        *,
        interval=60,
        heavy_interval=300,
        clock=time.monotonic,
        jitter=lambda: random.uniform(0, 5),
    ):
        self.service, self.engines, self.diagnostics = service, engines, diagnostics
        self.interval, self.heavy_interval = interval, heavy_interval
        self.clock, self.jitter = clock, jitter
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.tick_lock = threading.Lock()
        self.failures = 0
        self.next_due = 0.0
        self.heavy_due = 0.0
        self.last_attempt = None
        self.issues = None
        self.heavy_status = "waiting"

    def start(self):
        from server.vps_actions import enabled

        if not enabled() or (self.thread and self.thread.is_alive()):
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="remote-health", daemon=True)
        self.thread.start()

    def stop(self):
        # No new SSH work starts after the stop flag; an already bounded probe may finish.
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=1)

    def request_refresh(self):
        """Wake the sole collector; repeated manual requests remain single-flight."""
        if self.stop_event.is_set() or not self.thread or not self.thread.is_alive():
            return {
                **self.describe(),
                "accepted": False,
                "reason": "Automatic remote checks are paused; run the local API with TERMINAL_PERPS_TARGET=remote.",
            }
        self.next_due = min(self.next_due, self.clock())
        self.wake_event.set()
        return {**self.describe(), "accepted": True}

    def _run(self):
        while not self.stop_event.is_set():
            self.tick()
            delay = max(0.1, self.next_due - self.clock())
            self.wake_event.wait(delay)
            self.wake_event.clear()

    def tick(self):
        now = self.clock()
        if self.stop_event.is_set() or now < self.next_due or not self.tick_lock.acquire(False):
            return
        try:
            self.last_attempt = dt.datetime.now(dt.UTC).isoformat()
            observation = self.engines.refresh()
            self.failures = self.failures + 1 if observation.get("transport") != "connected" else 0
            self.next_due = (
                self.clock() + min(300, self.interval * 2 ** min(self.failures, 3)) + self.jitter()
            )
            issues = issues_for(observation)
            if issues != self.issues:
                if not self.stop_event.is_set():
                    self.service.event(
                        "remote.health.changed",
                        {"issues": issues, "recovered": self.issues is not None and not issues},
                    )
                self.issues = issues
            if self.stop_event.is_set():
                return
            if now >= self.heavy_due:
                if optional_work_allowed(observation):
                    try:
                        self.diagnostics.start(["status", "performance", "logs"])
                        self.heavy_due = now + self.heavy_interval
                        self.heavy_status = "scheduled"
                    except ValueError:
                        self.heavy_status = "another diagnostic run is active"
                else:
                    self.heavy_status = "deferred until host headroom is sufficient"
        except Exception:
            # A transient collector/DB failure must not silently kill scheduling.
            self.failures += 1
            self.next_due = self.clock() + min(300, self.interval * 2 ** min(self.failures, 3))
            self.issues = ["Remote health collector failed; a bounded retry is scheduled"]
        finally:
            self.tick_lock.release()

    def describe(self):
        return dict(
            scheduler=dict(
                running=bool(
                    self.thread and self.thread.is_alive() and not self.stop_event.is_set()
                ),
                lifecycle="local API",
                interval_seconds=self.interval,
                heavy_interval_seconds=self.heavy_interval,
                failures=self.failures,
                next_check_in_seconds=max(0, round(self.next_due - self.clock())),
                last_attempt_at=self.last_attempt,
                heavy_status=self.heavy_status,
            ),
            observation=self.engines.describe(),
            issues=self.issues or [],
        )
