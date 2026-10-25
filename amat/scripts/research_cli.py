"""Small operator front door; existing autoresearch remains the full interface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELP = """AMAT research

  ./research run [N]       Controller plans, launches and oversees research (default: 5)
  ./research status        Show progress
  ./research report        Open results

Configuration: amat/.env and configs/autoresearch/llm-chain.example.toml.
Controller/coding: Luna → Gemini → Groq → Space Bunny.
Proposals and judges use their configured role routes. Existing frozen sessions retain their policy.
Run parameters pass through, e.g. --latest-data, --workspace, --chain, --no-open.
Full diagnostic interface: ./research advanced --help.
Legacy shortcuts remain available. UV_ENV_FILE overrides the credentials file.
"""


def command(argv: list[str], root: Path = ROOT) -> tuple[list[str], dict[str, str]]:  # noqa: PLR0912 - explicit command routing
    """Resolve shortcuts without executing research (also used by routing tests)."""
    args = list(argv)
    name = args.pop(0) if args else "help"
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    base = [sys.executable, "-m", "autoresearch.cli"]
    if name == "trades":
        # This reads saved JSON only; it must not load an older frozen controller
        # or modify the strategy/runtime belonging to an earlier experiment.
        env["PYTHONPATH"] = str(root / "src")
        return [*base, "trades", *args], env
    if name == "advanced":
        return base + args, env
    if name == "returns":
        return [
            sys.executable,
            str(root / "scripts/return_target_study.py"),
            *(args or ["--help"]),
        ], env
    if name in ("run", "start", "control", "intraday") or name.isdecimal() or name.startswith("--"):
        if name.isdecimal() or name.startswith("--"):
            args.insert(0, name)
        if name == "intraday":
            args.insert(0, "--intraday")
        if "--latest-data" in args:
            if "--intraday" in args:
                raise ValueError(
                    "--latest-data is daily-only; 2026 minute data is reserved "
                    "for a separate validation study"
                )
            args.remove("--latest-data")
            if any(value.split("=")[0] in ("--snapshot", "--validation-end") for value in args):
                raise ValueError("Use --latest-data or explicit snapshot/date options, not both")
            registry = root / "data/research-datasets.json"
            if not registry.is_file():
                raise ValueError("Run ./research data update --only daily first")
            snapshot = Path(json.loads(registry.read_text())["daily"])
            manifest = json.loads((snapshot / "manifest.json").read_text())
            args += ["--snapshot", str(snapshot), "--validation-end", manifest["last_session"]]
        return [*base, "control", *args], env
    name = {"report": "view", "memory": "related"}.get(name, name)
    session = None
    if "--session" in args:
        index = args.index("--session")
        if index + 1 == len(args):
            raise ValueError("--session requires a directory")
        session = Path(args[index + 1]).resolve()
        del args[index : index + 2]
    if name in ("view", "status", "analyze", "failures", "charts", "related"):
        # Preserve the older positional workspace syntax too.
        if session is None and args and not args[0].startswith("-"):
            session = Path(args.pop(0)).resolve()
        if session is None:
            pointer = root.parent.parent / "amat-research/latest-session.json"
            if not pointer.is_file():
                raise ValueError("No session yet. Start with ./research run 5")
            session = Path(json.loads(pointer.read_text())["workspace"])
        program = json.loads((session / "program.json").read_text())
        intraday = program.get("schema") == "intraday-research/1"
        if intraday and name in ("charts", "related"):
            raise ValueError(
                "Charts/memory inspect daily sessions. Use report for intraday, "
                "or select a daily session with --session PATH."
            )
        # Use the session's controller, without adding bytecode to frozen evidence.
        env["PYTHONPATH"] = str(session / "runtime")
        return [*base, name, str(session), *(["--human"] if name == "status" else []), *args], env
    if name in ("active", "promote", "features"):
        return [*base, name, *args], env
    raise ValueError(f"Unknown command: {name}. Run ./research help")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("help", "--help", "-h"):
        print(HELP)
        return 0
    if args[0] == "paths":
        for label, path in {
            "Project": ROOT,
            "Sessions": ROOT.parent.parent / "amat-research",
            "Latest session": ROOT.parent.parent / "amat-research/latest-session.json",
            "Strategy": ROOT / "configs/strategy/amat_v6.toml",
            "Models": ROOT / "configs/autoresearch/llm-chain.example.toml",
            "Datasets": ROOT / "data/research-datasets.json",
            "Credentials": Path(os.environ.get("UV_ENV_FILE", str(ROOT / ".env"))),
            "Cheatsheet": ROOT.parent / "COMMANDS_CHEATSHEET.md",
        }.items():
            print(f"{label}: {path}")
        return 0
    if args[0] == "data":
        return subprocess.call(  # noqa: S603 - fixed interpreter/script, no shell
            [sys.executable, str(ROOT / "scripts/update_research_data.py"), *args[1:]]
        )
    try:
        cmd, env = command(args)
        return subprocess.call(cmd, cwd=ROOT, env=env)  # noqa: S603 - argv only, no shell
    except (ValueError, OSError, KeyError) as exc:
        print(f"research: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
