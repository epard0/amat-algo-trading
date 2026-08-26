from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd())).expanduser().resolve()
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private key material",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b")),
)
_ASSIGNMENT_RE = re.compile(
    r"(?im)\b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token|password|private[_-]?key)\b"
    r"\s*[:=]\s*([\"'])([^\"'\n]{8,})\1"
)
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:example|placeholder|changeme|change-me|redacted|masked|dummy|test|none|null|<.*>|\$\{.*\})$"
)


def read_event() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def emit_pretool(
    decision: str,
    reason: str,
    *,
    additional_context: str | None = None,
) -> None:
    output: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    if additional_context:
        output["hookSpecificOutput"]["additionalContext"] = additional_context
    print(json.dumps(output))


def emit_context(event_name: str, text: str, **extra: Any) -> None:
    specific: dict[str, Any] = {
        "hookEventName": event_name,
        "additionalContext": text,
    }
    specific.update(extra)
    print(json.dumps({"hookSpecificOutput": specific}))


def emit_block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))


def relative_project_path(value: str | os.PathLike[str]) -> Path | None:
    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(PROJECT_ROOT)
        return resolved.relative_to(PROJECT_ROOT)
    except (OSError, RuntimeError, ValueError):
        return None


def is_protected_path(rel: Path) -> bool:
    posix = rel.as_posix()
    protected_exact = {
        ".env",
        ".claude/settings.local.json",
    }
    protected_prefixes = (
        "secrets/",
        "credentials/",
        "data/raw/",
        "outputs/validated/",
    )
    if posix in protected_exact or posix.startswith(".env."):
        return True
    return any(posix.startswith(prefix) for prefix in protected_prefixes)


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd or PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def scan_secrets(text: str) -> list[str]:
    findings: list[str] = []
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(name)
    for match in _ASSIGNMENT_RE.finditer(text):
        value = match.group(2).strip()
        if _PLACEHOLDER_RE.match(value):
            continue
        if value.startswith(("env:", "os.environ", "getenv(")):
            continue
        findings.append("literal secret-like assignment")
        break
    return sorted(set(findings))


def extract_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    lines = text.splitlines()
    errors: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, text, ["missing opening YAML frontmatter delimiter"]

    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return {}, text, ["missing closing YAML frontmatter delimiter"]

    raw = lines[1:end]
    data: dict[str, Any] = {}
    current_key: str | None = None

    for line_number, line in enumerate(raw, start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-"):
            if current_key is None:
                errors.append(f"line {line_number}: list item has no parent key")
                continue
            value = stripped[1:].strip().strip('"\'')
            existing = data.get(current_key)
            if not isinstance(existing, list):
                existing = []
                data[current_key] = existing
            existing.append(value)
            continue
        if ":" not in line:
            errors.append(f"line {line_number}: expected key: value")
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        current_key = key
        if not value:
            data[key] = []
            continue
        value = value.strip('"\'')
        lower = value.lower()
        if lower in {"true", "yes", "on", "1"}:
            data[key] = True
        elif lower in {"false", "no", "off", "0"}:
            data[key] = False
        else:
            data[key] = value

    body = "\n".join(lines[end + 1 :]).strip()
    return data, body, errors


def markdown_validation(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    if rel.startswith(".claude/agents/"):
        frontmatter, body, parse_errors = extract_frontmatter(text)
        errors.extend(parse_errors)
        for required in ("name", "description"):
            if not frontmatter.get(required):
                errors.append(f"agent missing required frontmatter field: {required}")
        name = frontmatter.get("name")
        if isinstance(name, str) and not NAME_RE.fullmatch(name):
            errors.append(f"invalid agent name: {name!r}")
        if frontmatter.get("permissionMode") == "bypassPermissions":
            errors.append("agent may not use bypassPermissions")
        if not body:
            errors.append("agent prompt body is empty")
    elif rel.endswith("/SKILL.md") and rel.startswith(".claude/skills/"):
        frontmatter, body, parse_errors = extract_frontmatter(text)
        errors.extend(parse_errors)
        if not frontmatter.get("description"):
            errors.append("skill missing recommended description")
        name = frontmatter.get("name")
        if isinstance(name, str) and not NAME_RE.fullmatch(name):
            errors.append(f"invalid skill name: {name!r}")
        directory_name = path.parent.name
        if isinstance(name, str) and name != directory_name:
            errors.append(f"skill name {name!r} does not match directory {directory_name!r}")
        if frontmatter.get("context") == "fork" and not frontmatter.get("agent"):
            errors.append("forked skill must name an agent")
        if not body:
            errors.append("skill body is empty")
    return errors


def changed_files() -> list[Path]:
    if not (PROJECT_ROOT / ".git").exists() and run(["git", "rev-parse", "--git-dir"]).returncode != 0:
        return []

    paths: set[Path] = set()
    commands = (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    for command in commands:
        result = run(command)
        if result.returncode != 0:
            continue
        for raw in result.stdout.splitlines():
            rel = relative_project_path(raw.strip())
            if rel is not None:
                paths.add(PROJECT_ROOT / rel)
    return sorted(paths)


def compact_output(result: subprocess.CompletedProcess[str], limit: int = 1800) -> str:
    text = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} characters truncated]"


def iter_text_files(paths: Iterable[Path]) -> Iterable[Path]:
    allowed = {
        ".py", ".md", ".json", ".toml", ".yaml", ".yml", ".sh", ".txt", ".ini", ".cfg"
    }
    for path in paths:
        if path.is_file() and path.suffix.lower() in allowed:
            yield path
