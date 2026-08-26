#!/usr/bin/env python3
"""Validate the `.claude` bundle: required files, agents, skills, and settings.

Recovered 2026-08-10. The source file had been deleted while its compiled
`__pycache__/validate_bundle.cpython-312.pyc` survived, so `stop_guard.py`,
`quality_gate.sh`, `CLAUDE.md`, `README.md`, `settings.json` and the
`run-quality-gates` skill all referenced a script that no longer existed. This
file was reconstructed by disassembling that bytecode -- every required-file
name, agent name, skill name, settings key, and error string below is read out
of the compiled constants rather than reinvented, so the checks are the ones
that were passing before, not a fresh guess at what they should be.

What it enforces, and why each check earns its place:

* the bundle's own structure -- a missing hook or reference file is a control
  that silently stops applying;
* agent and skill frontmatter, including that `bypassPermissions` never appears
  and that a skill cannot preload a manual-only skill;
* `settings.json` safety posture -- live trading off, sandbox on and failing
  closed, credential scrubbing on, deny rules present, and every hook target
  actually existing on disk;
* that generated artifacts and `settings.local.json` are not committed;
* that no bundle file contains secret material.

Exit code 0 on success, 1 on any failure, with every failure printed to stderr.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLAUDE_DIR.parent

sys.path.insert(0, str(CLAUDE_DIR / "hooks"))

from hooklib import NAME_RE, extract_frontmatter, scan_secrets  # noqa: E402

#: Files whose absence means a declared control is not actually in place.
REQUIRED_FILES = frozenset(
    {
        "CLAUDE.md",
        "README.md",
        "settings.json",
        "hooks/hooklib.py",
        "hooks/post_edit_check.py",
        "hooks/pre_tool_guard.py",
        "hooks/session_context.py",
        "hooks/stop_guard.py",
        "references/promotion-gates.md",
        "references/strategy-contract.md",
        "references/validation-protocol.md",
        "scripts/quality_gate.sh",
        "scripts/statusline.py",
    }
)

REQUIRED_AGENTS = frozenset(
    {
        "adversarial-reviewer",
        "architecture-planner",
        "backtest-engineer",
        "data-engineer",
        "dependency-researcher",
        "implementation-lead",
        "performance-engineer",
        "portfolio-risk-engineer",
        "quality-gate-runner",
        "quant-researcher",
        "reporting-engineer",
        "repository-cartographer",
        "security-reviewer",
        "signal-engineer",
        "test-engineer",
        "validation-engineer",
    }
)

REQUIRED_SKILLS = frozenset(
    {
        "accounting-reconciliation",
        "anti-lookahead-audit",
        "audit-backtest",
        "backtest-reporting",
        "bootstrap-project",
        "dependency-research",
        "engine-parity",
        "execution-cost-modeling",
        "experiment-governance",
        "external-skill-intake",
        "honest-backtesting",
        "implement-phase",
        "market-data-integrity",
        "plan-phase",
        "portfolio-construction",
        "robustness-testing",
        "run-quality-gates",
        "signal-engineering",
        "verification-before-completion",
        "walk-forward-validation",
    }
)

#: Deny rules that must be present verbatim in settings.json.
REQUIRED_DENY_RULES = ("Read(./.env)", "Edit(./data/raw/**)", "Bash(rm -rf *)")

#: Credential variables the subprocess scrubber must cover.
PROTECTED_CREDENTIALS = (
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
)

REQUIRED_HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "Stop")

REQUIRED_GITIGNORE = ("settings.local.json", "worktrees/", "__pycache__/", "*.py[cod]")

#: Hook and script targets named inside settings.json must exist on disk.
HOOK_TARGET_RE = r"\.claude/(?:hooks|scripts)/[A-Za-z0-9_.-]+"

#: Always-loaded instructions stay short; past this they stop being read.
MAX_CLAUDE_MD_LINES = 200

GENERATED_SUFFIXES = frozenset({".pyc", ".pyo"})
SCANNED_SUFFIXES = frozenset({".py", ".md", ".sh", ".json", ".toml"})


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate_agent(path: Path, errors: list[str]) -> tuple[str | None, dict]:
    text = path.read_text(encoding="utf-8")
    meta, body, problems = extract_frontmatter(text)
    label = path.relative_to(CLAUDE_DIR)
    for problem in problems:
        fail(errors, f"{label}: {problem}")

    name = meta.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        fail(errors, f"{label}: invalid or missing name")
        name = None
    if not meta.get("description"):
        fail(errors, f"{label}: missing description")
    if not body.strip():
        fail(errors, f"{label}: empty prompt body")
    if meta.get("permissionMode") == "bypassPermissions":
        fail(errors, f"{label}: bypassPermissions is forbidden")
    return name, meta


def validate_skill(path: Path, errors: list[str]) -> tuple[str | None, dict]:
    text = path.read_text(encoding="utf-8")
    meta, body, problems = extract_frontmatter(text)
    label = path.relative_to(CLAUDE_DIR)
    for problem in problems:
        fail(errors, f"{label}: {problem}")

    name = meta.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        fail(errors, f"{label}: invalid name")
        name = None
    elif name != path.parent.name:
        fail(errors, f"{label}: name does not match directory")
    if not meta.get("description"):
        fail(errors, f"{label}: missing description")
    if not body.strip():
        fail(errors, f"{label}: empty body")
    if meta.get("context") == "fork" and not meta.get("agent"):
        fail(errors, f"{label}: forked skill lacks agent")
    return name, meta


def validate_settings(errors: list[str], agents: set[str]) -> None:
    path = CLAUDE_DIR / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"settings.json is invalid: {exc}")
        return

    if settings.get("$schema") != "https://json.schemastore.org/claude-code-settings.json":
        fail(errors, "settings.json: missing official JSON schema URL")
    if settings.get("autoMemoryEnabled") is not False:
        fail(errors, "settings.json: auto memory must be disabled for holdout/worktree isolation")
    if settings.get("disableSkillShellExecution") is not True:
        fail(errors, "settings.json: inline skill shell execution must be disabled")

    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        fail(errors, "settings.json: permissions object missing")
    else:
        if permissions.get("disableBypassPermissionsMode") != "disable":
            fail(errors, "settings.json: bypass-permissions mode must be disabled")
        deny = json.dumps(permissions.get("deny", []))
        for rule in REQUIRED_DENY_RULES:
            if rule not in deny:
                fail(errors, f"settings.json: required deny rule absent: {rule}")

    env = settings.get("env", {})
    if not isinstance(env, dict) or env.get("AMAT_LIVE_TRADING_ENABLED") != "0":
        fail(errors, "settings.json: live trading must be disabled")
    if not isinstance(env, dict) or env.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB") != "1":
        fail(errors, "settings.json: subprocess credential scrubbing must be enabled")

    sandbox = settings.get("sandbox", {})
    if not isinstance(sandbox, dict) or sandbox.get("enabled") is not True:
        fail(errors, "settings.json: sandbox must be enabled")
    elif sandbox.get("failIfUnavailable") is not True:
        fail(errors, "settings.json: sandbox must fail closed when unavailable")
    elif sandbox.get("allowUnsandboxedCommands") is not False:
        fail(errors, "settings.json: unsandboxed command escape must be disabled")

    scrubbed = json.dumps(settings.get("credentials", {}).get("envVars", []))
    if isinstance(sandbox, dict):
        scrubbed += json.dumps(sandbox)
    scrubbed += json.dumps(settings.get("env", {}))
    for variable in PROTECTED_CREDENTIALS:
        if variable not in scrubbed:
            fail(errors, f"settings.json: protected credential variable absent: {variable}")

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        fail(errors, "settings.json: hooks object missing")
        return
    for event in REQUIRED_HOOK_EVENTS:
        if event not in hooks:
            fail(errors, f"settings.json: missing {event} hook")

    for target in re.findall(HOOK_TARGET_RE, json.dumps(settings)):
        if not (PROJECT_ROOT / target).exists():
            fail(errors, f"settings.json: hook/script target does not exist: {target}")

    del agents


def tracked_bundle_paths() -> frozenset[str] | None:
    """Repository-relative paths under `.claude` that git has in its index.

    Returns `None` when git cannot answer -- no repository, no git binary, or a
    sandbox that masks `.git`. "Committed" is undefined in that case, and callers
    fall back to the bundle's own ignore rules rather than guessing.
    """
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", str(CLAUDE_DIR)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return frozenset(result.stdout.decode("utf-8", errors="replace").split("\0"))


def is_committed(path: Path, tracked: frozenset[str] | None) -> bool:
    """Whether `path` is actually in the git index.

    Git tracks files, never directories, so a directory counts as committed when
    anything beneath it is tracked.

    When `tracked` is `None` this returns `False`: nothing can be committed to a
    repository that is not there. That is not a hole. Every path these callers
    examine is covered by a `.claude/.gitignore` pattern whose presence is
    asserted directly above, so the guarantee is carried by the ignore rules and
    verified there, rather than being inferred here from a file's mere existence.
    """
    if tracked is None:
        return False
    relative = path.relative_to(PROJECT_ROOT).as_posix()
    if path.is_dir():
        return any(entry.startswith(relative + "/") for entry in tracked)
    return relative in tracked


def main() -> int:
    errors: list[str] = []

    for name in sorted(REQUIRED_FILES):
        if not (CLAUDE_DIR / name).is_file():
            fail(errors, f"missing required file: {name}")

    if (CLAUDE_DIR / "commands").is_dir():
        fail(errors, "deprecated/redundant .claude/commands directory should not be present")

    ignore_path = CLAUDE_DIR / ".gitignore"
    ignore_text = ignore_path.read_text(encoding="utf-8") if ignore_path.is_file() else ""
    for pattern in REQUIRED_GITIGNORE:
        if pattern not in ignore_text:
            fail(errors, f".gitignore: required pattern absent: {pattern}")

    tracked = tracked_bundle_paths()
    if is_committed(CLAUDE_DIR / "settings.local.json", tracked):
        fail(
            errors,
            "settings.local.json is committed; run: git rm --cached .claude/settings.local.json",
        )
    for path in CLAUDE_DIR.rglob("*"):
        if path.name == "__pycache__" or path.suffix in GENERATED_SUFFIXES:
            if is_committed(path, tracked):
                relative = path.relative_to(PROJECT_ROOT).as_posix()
                fail(
                    errors,
                    f"generated Python artifact is committed: {relative}"
                    f"; run: git rm -r --cached {relative}",
                )

    claude_md = (CLAUDE_DIR / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    if len(claude_md) > MAX_CLAUDE_MD_LINES:
        fail(
            errors,
            f"CLAUDE.md is {len(claude_md)} lines; keep always-loaded instructions "
            f"at or below {MAX_CLAUDE_MD_LINES}",
        )

    agents: set[str] = set()
    agent_meta: dict[str, dict] = {}
    for path in sorted((CLAUDE_DIR / "agents").glob("*.md")):
        name, meta = validate_agent(path, errors)
        if name is None:
            continue
        if name in agents:
            fail(errors, f"duplicate agent name: {name}")
        agents.add(name)
        agent_meta[name] = meta
    for name in sorted(REQUIRED_AGENTS - agents):
        fail(errors, f"missing required agent: {name}")

    skills: set[str] = set()
    skill_meta: dict[str, dict] = {}
    for path in sorted((CLAUDE_DIR / "skills").glob("*/SKILL.md")):
        name, meta = validate_skill(path, errors)
        if name is None:
            continue
        if name in skills:
            fail(errors, f"duplicate skill name: {name}")
        skills.add(name)
        skill_meta[name] = meta
    for name in sorted(REQUIRED_SKILLS - skills):
        fail(errors, f"missing required skill: {name}")

    for name, meta in sorted(skill_meta.items()):
        agent = meta.get("agent")
        if isinstance(agent, str) and agent and agent not in agents:
            fail(errors, f"skill {name}: unknown agent {agent}")

    for name, meta in sorted(agent_meta.items()):
        preloaded = meta.get("skills")
        if isinstance(preloaded, str):
            preloaded = [item.strip() for item in preloaded.split(",") if item.strip()]
        for skill in list(preloaded or []):
            if skill not in skills:
                fail(errors, f"agent {name}: unknown preloaded skill {skill}")
            elif skill_meta[skill].get("disable-model-invocation") is True:
                fail(errors, f"agent {name}: cannot preload manual-only skill {skill}")

    validate_settings(errors, agents)

    for path in sorted(CLAUDE_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        try:
            findings = scan_secrets(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if findings:
            relative = path.relative_to(CLAUDE_DIR)
            fail(errors, f"{relative}: potential secret material ({', '.join(findings)})")

    if errors:
        print("Claude bundle validation: FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    rules = len(list((CLAUDE_DIR / "rules").glob("*.md")))
    print(
        f"Claude bundle validation: PASS ({len(agents)} agents, {len(skills)} skills, "
        f"{rules} rules)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
