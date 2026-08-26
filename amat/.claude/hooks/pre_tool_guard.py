from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hooklib import (
    emit_pretool,
    is_protected_path,
    read_event,
    relative_project_path,
    scan_secrets,
)

_DESTRUCTIVE_COMMANDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("recursive forced deletion", re.compile(r"(?i)(?:^|[;&|]\s*)rm\s+[^\n]*-[^\n]*r[^\n]*f")),
    ("hard git reset", re.compile(r"(?i)\bgit\s+reset\s+--hard\b")),
    ("forced git clean", re.compile(r"(?i)\bgit\s+clean\s+-[^\s]*f")),
    ("force push", re.compile(r"(?i)\bgit\s+push\b[^\n]*(?:--force|-f\b)")),
    ("privileged command", re.compile(r"(?i)(?:^|[;&|]\s*)sudo\b")),
    ("filesystem formatter", re.compile(r"(?i)(?:^|[;&|]\s*)mkfs(?:\.|\s)")),
    ("raw block-device write", re.compile(r"(?i)(?:^|[;&|]\s*)dd\s+[^\n]*\bof=/dev/")),
    ("world-writable permission", re.compile(r"(?i)\bchmod\s+(?:-R\s+)?777\b")),
)

_ORDER_ENDPOINT_RE = re.compile(
    r"(?i)(?:api|paper-api)\.alpaca\.markets[^\s\"']*/(?:v\d+/)?orders\b"
)
_BROKER_ORDER_CODE_RE = re.compile(
    r"(?i)(?:\bfrom\s+alpaca\.trading\b|\bimport\s+alpaca\.trading\b|"
    r"\bTradingClient\s*\(|\.submit_order\s*\(|/(?:v\d+/)?orders\b)"
)
_GENERIC_ORDER_CODE_RE = re.compile(r"(?i)(?:\.place_order\s*\(|\bdef\s+place_order\s*\()")
_LIVE_ENABLE_RE = re.compile(
    r"(?i)(?:AMAT_LIVE_TRADING_ENABLED\s*=\s*(?:1|true)|AMAT_EXECUTION_MODE\s*=\s*[\"']?live)"
)
_INERT_PATH_ROOTS = {"tests", "docs", ".claude", "examples"}
_SIMULATION_MARKERS = {"backtest", "simulated", "simulation", "simulator", "paper-model"}


def content_from_input(tool_input: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("content", "new_string", "new_source", "source"):
        value = tool_input.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
    return "\n".join(parts)


def target_path(tool_input: dict[str, Any]) -> Path | None:
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return relative_project_path(value)
    return None


def is_inert_or_simulation_path(rel: Path) -> bool:
    lowered = tuple(part.lower() for part in rel.parts)
    if lowered and lowered[0] in _INERT_PATH_ROOTS:
        return True
    joined = "/".join(lowered)
    return any(marker in joined for marker in _SIMULATION_MARKERS)


def guard_bash(command: str) -> None:
    for label, pattern in _DESTRUCTIVE_COMMANDS:
        if pattern.search(command):
            emit_pretool("deny", f"Blocked {label}. Use a reversible, narrowly scoped alternative.")
            return

    if _ORDER_ENDPOINT_RE.search(command) or _BROKER_ORDER_CODE_RE.search(command) or _LIVE_ENABLE_RE.search(command):
        emit_pretool(
            "deny",
            "Broker order submission, trading-client use, or live-mode activation is prohibited in the AMAT backtesting repository.",
        )
        return

    if re.search(r"(?i)(?:^|[;&|]\s*)(?:curl|wget)\b", command):
        emit_pretool(
            "deny",
            "Direct curl/wget is blocked. Use WebFetch/WebSearch for research or an audited Python data client for a declared public-data provider.",
        )
        return

    if re.search(r"(?i)\b(?:alpaca|broker)\b[^\n]*\b(?:buy|sell|order|trade)\b", command):
        emit_pretool(
            "ask",
            "This command resembles broker/trading execution. Confirm it is a local backtest-only operation with no account mutation.",
        )


def guard_order_code(rel: Path, content: str) -> None:
    contains_endpoint = bool(_ORDER_ENDPOINT_RE.search(content))
    contains_broker_order = bool(_BROKER_ORDER_CODE_RE.search(content))
    contains_generic_order = bool(_GENERIC_ORDER_CODE_RE.search(content))
    if not (contains_endpoint or contains_broker_order or contains_generic_order):
        return

    if not is_inert_or_simulation_path(rel):
        emit_pretool(
            "deny",
            "Broker/order-submission code is forbidden outside inert tests, documentation, or explicitly named simulation/backtest modules.",
            additional_context="Use historical market-data clients only. Keep execution abstractions explicitly simulated and network-free.",
        )
        return

    emit_pretool(
        "ask",
        "The proposed write contains order-like code in an inert/simulation path. Confirm it cannot instantiate a broker trading client, call a remote order endpoint, or mutate an account.",
        additional_context="Name simulator types explicitly, inject a network-disabled transport, and add a test proving no broker SDK or network call occurs.",
    )


def main() -> None:
    event = read_event()
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return

    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            guard_bash(command)
        return

    if tool_name not in {"Edit", "Write", "NotebookEdit"}:
        return

    rel = target_path(tool_input)
    if rel is None:
        emit_pretool("deny", "Write target is outside the project root or cannot be resolved safely.")
        return
    if is_protected_path(rel):
        emit_pretool(
            "deny",
            f"Protected evidence or credential path may not be edited: {rel.as_posix()}",
        )
        return

    content = content_from_input(tool_input)
    findings = scan_secrets(content)
    if findings:
        emit_pretool(
            "deny",
            "Potential secret material in proposed write: " + ", ".join(findings),
        )
        return

    if _LIVE_ENABLE_RE.search(content):
        emit_pretool(
            "deny",
            "The proposed write enables live trading, which violates the backtest-only contract.",
        )
        return

    guard_order_code(rel, content)


if __name__ == "__main__":
    main()
