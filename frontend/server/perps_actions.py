"""Registered paper-only Kalshi commands; execution retains the native strategy gates."""

import os
import tomllib
from pathlib import Path

from fastapi import HTTPException

IDS = {
    "perps.status",
    "perps.observe",
    "perps.stop",
    "perps.start_comparison",
    "perps.stop_comparison",
}
SERVICE = "kalshi-perps-paper-comparison.service"


def capabilities(root):
    from server.vps_actions import capabilities as remote_capabilities
    from server.vps_actions import enabled

    if enabled():
        return remote_capabilities(root)
    root = Path(root)
    ready = (root / "kalshi-perps/.venv/bin/python").is_file()
    strategies = [
        p.stem
        for p in sorted((root / "kalshi-perps/configs/strategies").glob("*.toml"))
        if not p.is_symlink()
    ]
    result = []
    for id_ in sorted(IDS):
        service = id_.endswith("comparison")
        installed = any(
            p.is_file()
            for p in (
                Path.home() / ".config/systemd/user" / SERVICE,
                Path(f"/run/user/{os.getuid()}/systemd/transient") / SERVICE,
            )
        )
        supported = ready and (installed if service else True)
        result.append(
            dict(
                id=id_,
                capability=id_,
                supported=supported,
                reason=None
                if supported
                else "Requires the Kalshi runtime and installed paper comparison user service",
                effects=[
                    {
                        "perps.status": "Reads persisted paper accounts; no market request",
                        "perps.observe": "One bounded paper observation using public market data; native research gates apply",
                        "perps.stop": "Requests a native paper stop; optional flatten attempts reduce-only simulated closes",
                        "perps.start_comparison": "Starts the installed supervised paper comparison service; independent of the browser",
                        "perps.stop_comparison": "Stops the comparison service; positions remain and can be inspected",
                    }[id_]
                ],
                inputs={}
                if service
                else {
                    "strategy": {
                        "type": "select",
                        "options": ["all", *strategies],
                        "default": "all",
                    },
                    **(
                        {"flatten": {"type": "boolean", "default": False}}
                        if id_ == "perps.stop"
                        else {}
                    ),
                },
            )
        )
    return result


def command(service, action):
    from server.vps_actions import command as remote_command
    from server.vps_actions import enabled

    if enabled():
        return remote_command(service, action)
    if action.capability not in IDS:
        return None
    root = Path(service.root)
    cap = next(c for c in capabilities(root) if c["id"] == action.capability)
    if not cap["supported"]:
        raise HTTPException(422, cap["reason"])
    # Never accept an arbitrary config, mode, service name, account path, or CLI flag.
    config = root / "kalshi-perps/configs/paper.toml"
    if tomllib.loads(config.read_text()).get("mode") != "paper":
        raise HTTPException(422, "Dashboard Kalshi controls are paper-only")
    if action.capability.endswith("comparison"):
        if action.inputs:
            raise HTTPException(422, "Comparison service control accepts no inputs")
        operation = "start" if action.capability == "perps.start_comparison" else "stop"
        return ["/usr/bin/systemctl", "--user", operation, SERVICE], root, cap
    allowed = {"strategy", "flatten"} if action.capability == "perps.stop" else {"strategy"}
    if set(action.inputs) - allowed:
        raise HTTPException(422, "Unknown Kalshi input fields")
    strategy = action.inputs.get("strategy", "all")
    if strategy not in cap["inputs"]["strategy"]["options"]:
        raise HTTPException(422, "Unknown registered strategy")
    operation = {"perps.status": "status", "perps.observe": "run", "perps.stop": "stop"}[
        action.capability
    ]
    argv = [
        str(root / "kalshi-perps/.venv/bin/python"),
        "-m",
        "kalshi_perps.cli",
        operation,
        "--config",
        str(config),
    ]
    argv += ["--all"] if strategy == "all" else ["--strategy", strategy]
    if operation == "run":
        argv += ["--once"]
    if operation == "stop":
        flatten = action.inputs.get("flatten", False)
        if type(flatten) is not bool:
            raise HTTPException(422, "flatten must be a boolean")
        if flatten:
            argv += ["--flatten"]
    return argv, root / "kalshi-perps", cap


def observation(root):
    """Read only the native current comparison report; no subprocess, broker or DB writes."""
    import datetime as dt
    import json

    root = Path(root) / "kalshi-perps/runtime"

    def read(path):
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or path.stat().st_size > 4_000_000
        ):
            raise ValueError("Unregistered or oversized comparison report")
        return json.loads(path.read_text())

    try:
        pointer = read(root / "comparison-current.json")
        report = read(Path(pointer["directory"]) / "comparison.json")
        if report.get("mode") != "paper":
            raise ValueError("Paper report required")
        accounts = []
        for name, entry in report.get("accounts", {}).items():
            metrics = entry.get("metrics", {})
            accounts.append(
                {
                    "strategy": name,
                    "state": entry.get("state"),
                    **{
                        k: metrics.get(k)
                        for k in (
                            "equity",
                            "net_pnl",
                            "net_return",
                            "max_drawdown",
                            "fees",
                            "funding",
                            "effective_leverage",
                            "closed_trades",
                        )
                    },
                }
            )
        return dict(
            status="observed",
            source="native local paper comparison",
            observed_at=report.get("updated_at"),
            refreshed_at=dt.datetime.now(dt.UTC).isoformat(),
            state=report.get("state"),
            campaign=report.get("campaign"),
            leverage_policy=report.get("leverage_policy"),
            accounts=accounts,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return dict(
            status="unavailable",
            source="native local paper comparison",
            accounts=[],
            reason="No readable registered paper comparison report",
        )
