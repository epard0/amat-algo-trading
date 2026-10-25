"""Read-only planning inventory. Run with amat/.venv/bin/python from any directory.

Imports parser builders, never dispatchers; does not run research or contact providers.
Config records below are static discovery candidates, not an editable-field schema.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "amat/src"), str(ROOT / "executor/src")]
BUILDERS = {
    "amat": ("amat.cli.main", "build_parser"),
    "autoresearch": ("autoresearch.cli", "argument_parser"),
    "pmteam": ("portfolio_team.cli", "_parser"),
    "newsdesk": ("newsdesk.cli", "build_parser"),
    "deskreview": ("deskreview.cli", "build_parser"),
    "executor": ("executor.cli", "_build_parser"),
}


def plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set, frozenset, range)):
        return [plain(item) for item in value]
    if isinstance(value, Path):
        return str(value).replace(str(ROOT), "${WORKSPACE}")
    return getattr(value, "__name__", type(value).__name__)


def collect(parser, path, inherited=()):
    options = list(inherited)
    children = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            children.extend(action.choices.items())
        elif action.dest != "help":
            options.append({
                "name": action.dest, "flags": action.option_strings,
                "required": action.required, "default": plain(action.default),
                "choices": plain(action.choices), "nargs": action.nargs,
                "type": plain(action.type), "action": type(action).__name__,
                "help": action.help,
            })
    if not children:
        return [{"command": path, "arguments": options}]
    return [item for name, child in children for item in collect(child, path + " " + name, options)]


def surface(command):
    if command.startswith("executor"):
        return "/execution"
    if command.startswith("pmteam"):
        return "/reviews"
    if command.startswith(("newsdesk", "deskreview")) or command == "amat scan":
        return "/markets"
    if command.startswith("autoresearch patterns"):
        return "/patterns"
    if command.startswith("autoresearch"):
        return "/research"
    if command.split()[-1] in {"validate-config", "show-config", "show-universe", "show-layout", "show-environment"}:
        return "/settings"
    if command == "amat plan":
        return "/strategies"
    return "/analysis"


def main():
    # Parser defaults depend on cwd; normalize to the owning research project.
    os.chdir(ROOT / "amat")
    commands = []
    for name, (module, function) in BUILDERS.items():
        commands.extend(collect(getattr(importlib.import_module(module), function)(), name))
    candidates = []
    sources = {}
    for package in (ROOT / "amat/src", ROOT / "executor/src"):
        for path in sorted(package.rglob("*.py")):
            relative = str(path.relative_to(ROOT))
            source = path.read_text()
            sources[relative] = hashlib.sha256(source.encode()).hexdigest()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not any(term in node.name.lower() for term in ("config", "settings", "policy", "program", "protocol", "spec", "chain", "limits")):
                    continue
                fields = [{"name": n.target.id, "annotation": ast.unparse(n.annotation), "line": n.lineno}
                          for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
                if fields:
                    candidates.append({"source": relative, "class": node.name, "fields": fields})
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    document = {
        "schema": "workbench-planning-inventory/1", "git_head": revision,
        "basis": "Current working-tree source; includes pre-existing uncommitted changes.",
        "scope": "Six parser trees; wrapper aliases, scripts, notebooks, artifact schemas and environment options require supplemental discovery.",
        "commands": commands, "config_candidates": candidates,
        "source_sha256": sources,
        "scripts": sorted(str(p.relative_to(ROOT)) for p in (ROOT / "amat/scripts").glob("*") if p.is_file()),
        "notebooks": sorted(str(p.relative_to(ROOT)) for p in (ROOT / "amat/notebooks").glob("*.ipynb")),
    }
    (OUT / "capability-inventory.json").write_text(json.dumps(document, indent=2) + "\n")
    with (OUT / "command-coverage.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "command", "argument_count", "primary_route", "ui_status", "required_acceptance"])
        writer.writeheader()
        for index, command in enumerate(commands, 1):
            writer.writerow({"id": f"CMD-{index:03}", "command": command["command"],
                "argument_count": len(command["arguments"]), "primary_route": surface(command["command"]),
                "ui_status": "planned", "required_acceptance": "Typed input parity; backend validation; lifecycle/error rendering; source-linked output; side-effect classification"})
    print(json.dumps({"commands": len(commands), "argument_occurrences": sum(len(c["arguments"]) for c in commands),
                      "config_candidate_classes": len(candidates), "source_files": len(sources)}, indent=2))


if __name__ == "__main__":
    main()
