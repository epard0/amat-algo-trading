#!/usr/bin/env python3
"""Fixed remote operations used by the dashboard. No shell or arbitrary target inputs."""

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CONTAINERS = {"perps": "kalshi-perps", "executor": "amat-executor"}
EXPECTED_CONTAINERS = (*CONTAINERS.values(), "kalshi-perps-trade-alerts")
SUPPORTING_CONTAINER = re.compile(r"amat-backup-(?:state|perps)-[0-9]+(?:-cleanup)?\Z")


def call(argv, timeout=50, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(
            f"{argv[0]} command failed (exit {result.returncode}): {result.stderr[-1000:]}"
        )
    return result


def inspect(target):
    result = call(["docker", "inspect", CONTAINERS[target]], check=False)
    if result.returncode:
        return None
    return json.loads(result.stdout)[0]


def container_view(info):
    if not info:
        return dict(running=False, status="not_deployed")
    state = info["State"]
    return dict(
        running=state["Running"],
        status=state["Status"],
        restart_count=info["RestartCount"],
        health=state.get("Health", {}).get("Status"),
        started_at=state["StartedAt"],
        image=info["Config"]["Image"],
        image_id=info["Image"],
        memory_limit_bytes=info["HostConfig"]["Memory"],
        restart_policy=info["HostConfig"]["RestartPolicy"]["Name"],
        oom_killed=state.get("OOMKilled", False),
        memory_swap_limit_bytes=info["HostConfig"].get("MemorySwap"),
        cpu_limit=info["HostConfig"].get("NanoCpus", 0) / 1_000_000_000,
        pids_limit=info["HostConfig"].get("PidsLimit"),
    )


def validate(target, info):
    if not info:
        raise ValueError("Container is not deployed")
    if target == "perps":
        if info["Config"].get("Labels", {}).get("amat.mode") != "paper":
            raise ValueError("Only the registered paper image can be controlled")
        env = dict(x.split("=", 1) for x in info["Config"].get("Env", []) if "=" in x)
        if any(env.get(k) for k in ("KALSHI_PERPS_KEY_ID", "KALSHI_PERPS_PRIVATE_KEY_PATH")):
            raise ValueError("Signing credentials detected in paper container")
    else:
        env = dict(x.split("=", 1) for x in info.get("Config", {}).get("Env", []) if "=" in x)
        if env.get("TRADING_MODE", "paper") != "paper":
            raise ValueError("Paper lifecycle controls cannot operate a live executor")
        # Verify the installed broker implementation before every remote lifecycle action.
        code = "from executor.config import PAPER_TRADING_HOST; assert PAPER_TRADING_HOST == 'paper-api.alpaca.markets'; print('paper')"
        if info["State"]["Running"]:
            call(["docker", "exec", CONTAINERS[target], "python", "-c", code])
        else:
            call(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    info["Image"],
                    "-c",
                    code,
                ]
            )


def read_perps(info, timeout=15):
    output = dict(
        status="unavailable",
        source="remote VPS · Kalshi paper",
        accounts=[],
        deployment=container_view(info),
        observed_at=None,
    )
    if not info:
        return output
    validate("perps", info)
    code = "import json,pathlib; p=pathlib.Path('/var/lib/kalshi-perps/adx-forward/status.json'); print(p.read_text() if p.exists() else '{}')"
    if info["State"]["Running"]:
        argv = ["docker", "exec", "kalshi-perps", "python", "-c", code]
    else:
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--volumes-from",
            "kalshi-perps:ro",
            "--entrypoint",
            "/opt/perps-venv/bin/python",
            info["Image"],
            "-c",
            code,
        ]
    native = json.loads(call(argv, timeout=timeout).stdout)
    if not native:
        return output
    if native.get("mode") != "paper" or native.get("live_enabled") is not False:
        raise ValueError("Remote status must prove paper-only mode")
    metrics = native.get("metrics", {})
    fields = (
        "equity",
        "net_pnl",
        "net_return",
        "max_drawdown",
        "fees",
        "funding",
        "effective_leverage",
        "closed_trades",
        "fills",
        "take_profit_actions",
        "profitable_take_profit_actions",
    )
    state = native.get("state") if info["State"]["Running"] else "stopped"
    account = dict(strategy=native["strategy"], state=state, **{k: metrics.get(k) for k in fields})
    output.update(
        status="observed",
        state=state,
        observed_at=native["updated_at"],
        mode="paper",
        live_enabled=False,
        accounts=[account],
        leverage_policy=native.get("leverage_mode"),
        positions=metrics.get("positions", {}),
        error_type=native.get("error_type"),
        withdrawals_enabled=False,
    )
    return output


def read_executor(info, timeout=50):
    result = dict(source="remote VPS · Alpaca paper", deployment=container_view(info))
    if info and info["State"]["Running"]:
        code = "from executor.config import PAPER_TRADING_HOST; import os,json; assert PAPER_TRADING_HOST == 'paper-api.alpaca.markets'; assert os.environ.get('TRADING_MODE','paper') == 'paper'; print(json.dumps({'mode':'paper','broker_host':PAPER_TRADING_HOST,'dry_run':os.environ.get('DRY_RUN'),'executor_disabled':os.environ.get('EXECUTOR_DISABLED')}))"
        result["configuration"] = json.loads(
            call(["docker", "exec", "amat-executor", "python", "-c", code], timeout=timeout).stdout
        )
        raw = call(
            ["docker", "exec", "amat-executor", "executor", "health", "--ready"],
            timeout=timeout,
            check=False,
        )
        result["readiness"] = (
            json.loads(raw.stdout) if raw.stdout.strip().startswith("{") else {"healthy": False}
        )
    return result


def inspect_live():
    raw = call(["docker", "inspect", "amat-executor-live"], check=False)
    if raw.returncode:
        # A missing container is an expected, explicit observation.
        if "No such" in raw.stderr:
            return None
        raise RuntimeError("Unable to inspect live container")
    return json.loads(raw.stdout)[0]


def read_live_executor(info):
    result = dict(
        source="remote VPS · Alpaca live",
        mode="live",
        deployment=container_view(info),
        observed_at=dt.datetime.now(dt.UTC).isoformat(),
    )
    if info and info["State"]["Running"]:
        raw = call(
            ["docker", "exec", "amat-executor-live", "executor", "health", "--ready"],
            check=False,
        )
        ready = json.loads(raw.stdout)
        if ready.get("trading_mode") != "live":
            raise ValueError("Live observation did not establish live mode")
        result["readiness"] = ready
        status = call(
            ["docker", "exec", "amat-executor-live", "executor", "status", "--json"],
            check=False,
        )
        if status.returncode == 0:
            result["operations"] = json.loads(status.stdout)
    return result


def host_observation():
    """Read kernel counters only; no broker calls or optional maintenance."""
    memory = {
        k: int(v.split()[0]) * 1024
        for k, v in (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        if k in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    }
    disk = shutil.disk_usage("/")
    fs = os.statvfs("/")
    return dict(
        memory_total_bytes=memory.get("MemTotal"),
        memory_available_bytes=memory.get("MemAvailable"),
        swap_total_bytes=memory.get("SwapTotal"),
        swap_used_bytes=memory.get("SwapTotal", 0) - memory.get("SwapFree", 0),
        disk_total_bytes=disk.total,
        disk_free_bytes=disk.free,
        inodes_total=fs.f_files,
        inodes_free=fs.f_favail,
        load_1_5_15=list(os.getloadavg()),
        cpu_count=os.cpu_count(),
        uptime_seconds=float(Path("/proc/uptime").read_text().split()[0]),
        memory_pressure=(
            Path("/proc/pressure/memory").read_text()[:1024]
            if Path("/proc/pressure/memory").exists()
            else None
        ),
    )


def backup_observation(now):
    root = Path.home() / ".local/share/amat"
    result = {}
    for kind in ("state", "perps"):
        paths = sorted((root / "backups").glob(f"backup-{kind}-*.tar.gz"))
        latest = paths[-1] if paths else None
        age = now.timestamp() - latest.stat().st_mtime if latest else None
        result[kind] = dict(
            status=("missing" if age is None else "fresh" if 0 <= age <= 8 * 3600 else "stale"),
            age_seconds=round(age) if age is not None else None,
            bytes=latest.stat().st_size if latest else None,
        )
    health = root / "paper-monitor/health.json"
    monitor = (
        json.loads(health.read_text()) if health.is_file() and health.stat().st_size < 65536 else {}
    )
    stamp = monitor.get("last_success_at")
    age = (now - dt.datetime.fromisoformat(stamp)).total_seconds() if stamp else None
    return result, dict(
        status=monitor.get("status", "unobserved"),
        age_seconds=age,
        stale=age is None or not 0 <= age <= 180,
    )


def ui_process_observation():
    """Expose only matching process names/PIDs, never full command lines."""
    found = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            with (path / "cmdline").open("rb") as handle:
                argv = handle.read(8192).split(b"\0")
            if any(
                value in {b"uvicorn", b"vite", b"next", b"server.app:app"}
                or value.endswith(b"/uvicorn")
                for value in argv
            ):
                found.append(dict(pid=int(path.name), application="web UI or API"))
        except OSError:
            continue
    return found


def health_observation():
    """Versioned read-only snapshot for the local API scheduler."""
    now = dt.datetime.now(dt.UTC)
    errors = []
    # Return selected inspect fields, never environment/credential dictionaries.
    names = call(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=5).stdout.splitlines()
    running_names = call(["docker", "ps", "--format", "{{.Names}}"], timeout=5).stdout.splitlines()
    deployed = {}
    inspected = {}
    for name in EXPECTED_CONTAINERS:
        if name not in names:
            deployed[name] = dict(running=False, status="not_deployed")
            continue
        info = json.loads(call(["docker", "inspect", name], timeout=5).stdout)[0]
        inspected[name] = info
        deployed[name] = container_view(info)
    stats = call(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *EXPECTED_CONTAINERS],
        timeout=8,
        check=False,
    )
    for line in stats.stdout.splitlines():
        row = json.loads(line)
        if row.get("Name") in deployed:
            deployed[row["Name"]]["resources"] = {
                k: row.get(k) for k in ("MemUsage", "MemPerc", "CPUPerc", "PIDs", "BlockIO")
            }
    if stats.returncode:
        errors.append("Some container resource counters are unavailable")
    engines = {}
    for target, name in CONTAINERS.items():
        try:
            if deployed[name].get("running"):
                info = inspected[name]
                engines[target] = (
                    read_perps(info, timeout=5)
                    if target == "perps"
                    else read_executor(info, timeout=5)
                )
                if target == "perps":
                    probe = call(
                        ["docker", "exec", name, "python", "deploy/health.py"],
                        timeout=5,
                        check=False,
                    )
                    engines[target]["health"] = json.loads(probe.stdout)
                else:
                    # Native readiness already embeds the exact native health result.
                    engines[target]["health"] = {
                        key: value
                        for key, value in engines[target].get("readiness", {}).items()
                        if key
                        in {"healthy", "status", "progress_age_seconds", "submission_interlock"}
                    }
            else:
                engines[target] = dict(status="stopped", deployment=deployed[name])
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            engines[target] = dict(status="unavailable", deployment=deployed[name])
            errors.append(f"{name} native health unavailable")
    listeners = call(["ss", "-ltnH"], timeout=5, check=False)
    tcp = [line.split()[3] for line in listeners.stdout.splitlines() if len(line.split()) >= 4]
    unexpected_listeners = [x for x in tcp if x.rsplit(":", 1)[-1] not in {"53", "2222"}]
    units = call(
        [
            "systemctl",
            "--user",
            "list-units",
            "--type=service",
            "--state=running",
            "--no-legend",
            "--no-pager",
        ],
        timeout=5,
        check=False,
    )
    unexpected_services = [
        line.split()[0]
        for line in units.stdout.splitlines()
        if any(x in line.lower() for x in ("amat-terminal", "uvicorn", "vite", "frontend"))
    ]
    backups, monitor = backup_observation(now)
    return dict(
        schema="remote-health/1",
        observed_at=now.isoformat(),
        execution_authority="none",
        **engines,
        infrastructure=dict(
            host=host_observation(),
            containers=deployed,
            expected_containers=list(EXPECTED_CONTAINERS),
            unexpected_containers=[
                name
                for name in running_names
                if name not in EXPECTED_CONTAINERS and not SUPPORTING_CONTAINER.fullmatch(name)
            ],
            supporting_containers=[
                name for name in running_names if SUPPORTING_CONTAINER.fullmatch(name)
            ],
            inactive_containers=[name for name in names if name not in running_names],
            unexpected_listeners=unexpected_listeners,
            unexpected_services=unexpected_services,
            unexpected_ui_processes=ui_process_observation(),
            listener_observation_available=listeners.returncode == 0,
            service_observation_available=units.returncode == 0,
            backups=backups,
            paper_monitor=monitor,
            issues=errors,
        ),
    )


def execute(operation):
    if operation == "health":
        return health_observation()
    if operation == "overview":
        return dict(
            observed_at=dt.datetime.now(dt.UTC).isoformat(),
            perps=read_perps(inspect("perps")),
            executor=read_executor(inspect("executor")),
            executor_live=read_live_executor(inspect_live()),
        )
    target, verb = operation.split(".", 1)
    info = inspect(target)
    validate(target, info)
    if verb == "status":
        return read_perps(info) if target == "perps" else read_executor(info)
    if verb not in ("start", "stop", "restart"):
        raise ValueError("Unregistered operation")
    lock_path = Path.home() / ".local/share/amat" / f"control-{target}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another lifecycle action owns this remote engine") from exc
        if verb == "start":
            call(["docker", "start", CONTAINERS[target]])
        else:
            call(["docker", verb, "--time", "45", CONTAINERS[target]], timeout=55)

    return dict(
        operation=operation,
        observed_at=dt.datetime.now(dt.UTC).isoformat(),
        deployment=container_view(inspect(target)),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=["overview", "health"]
        + [f"{t}.{v}" for t in CONTAINERS for v in ("status", "start", "stop", "restart")],
    )
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.operation)))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps(dict(error=type(exc).__name__, message=str(exc))), file=sys.stderr)
        raise SystemExit(1)
