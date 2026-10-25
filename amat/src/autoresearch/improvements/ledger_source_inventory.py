"""Read-only source binding for legacy research; no return comparability waivers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from amat.config.models import StrategyConfig
from autoresearch.improvements.campaign_audit import economic_digest

_HEX_SHA_LENGTH = 64


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_identity(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    candidate = value.get("strategy", value)
    try:
        config = StrategyConfig.model_validate(candidate).model_dump(mode="json")
        return economic_digest(config)
    except (ValueError, TypeError):
        return None


def inventory_session(database: Path) -> dict[str, Any]:
    """Bind frozen sources and every experiment, without publishing provider/proposal text."""
    root = database.parent.resolve()
    item: dict[str, Any] = {
        "database": str(database),
        "status": "requires_contract_review",
        "experiments": [],
    }
    program_path = root / "program.json"
    program = json.loads(program_path.read_text())
    seal = program_path.with_suffix(".sha256")
    item["program_seal_status"] = (
        "verified_raw_sha256"
        if seal.is_file() and seal.read_text().strip() == sha(program_path)
        else "missing_or_changed"
    )
    item.update(
        program_sha256=sha(program_path),
        program_schema=program.get("schema", program.get("schema_version")),
        execution_authority=program.get("execution_authority"),
        dates={
            k: program.get(k)
            for k in [
                "load_start",
                "measured_start",
                "train_start",
                "train_end",
                "validation_start",
                "validation_end",
                "holdout_after",
            ]
        },
    )
    baseline = program.get("baseline", {})
    item["baseline_economic_sha256"] = native_identity(baseline.get("config"))
    registered = program.get("frozen_files", program.get("files", {}))
    checks: dict[str, dict[str, Any]] = {}
    for name, expected in registered.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            checks[name] = {"status": "outside_session"}
            continue
        if path.suffix != ".py" and name not in {
            "base.toml",
            "catalog.toml",
            "snapshot/manifest.json",
        }:
            continue
        observed = sha(path) if path.is_file() else None
        checks[name] = {
            "expected_sha256": expected,
            "observed_sha256": observed,
            "status": "verified" if observed == expected else "missing_or_changed",
        }
    item["source_file_checks"] = checks
    manifest_path = root / "snapshot/manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        item["snapshot"] = {
            k: manifest.get(k)
            for k in [
                "snapshot_id",
                "provider",
                "feed",
                "first_session",
                "last_session",
                "session_count",
                "symbols",
                "partition_sha256",
            ]
        }
    before = sha(database)
    db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.execute("BEGIN")
        for identifier, candidate, status, raw in db.execute(
            "SELECT id,candidate,status,result FROM experiments ORDER BY id"
        ):
            row: dict[str, Any] = {
                "id": identifier,
                "candidate": candidate,
                "status": status,
                "result_sha256": hashlib.sha256(raw.encode()).hexdigest() if raw else None,
            }
            if raw:
                value = json.loads(raw)
                row.update(
                    fit_count=value.get("fit_count"),
                    entry_model_fit_count=value.get("entry_model_fit_count"),
                    fold_count=len(value.get("folds", [])),
                    result_fields=sorted(value),
                    eligible=value.get("eligible"),
                )
            # Content hashes are expected, not arbitrary paths supplied by a candidate.
            if (
                isinstance(candidate, str)
                and len(candidate) == _HEX_SHA_LENGTH
                and all(x in "0123456789abcdef" for x in candidate)
            ):
                path = root / "candidates" / (candidate + ".json")
                if path.is_file():
                    content = json.loads(path.read_text())
                    row.update(
                        candidate_file_sha256=sha(path),
                        candidate_fields=sorted(content),
                        economic_sha256=native_identity(content.get("config")),
                        feature_count=len(content.get("features") or []),
                        has_signal=content.get("signal") is not None,
                    )
            item["experiments"].append(row)
    finally:
        db.close()
    if sha(database) != before:
        raise ValueError("Legacy ledger changed while source inventory was read")
    item["ledger_file_sha256"] = before
    item["limitations"] = (
        "Source/config identity only. Feature/signal models need causal fitted-fold replay; "
        "bare native config cannot stand in for overlays. "
        "No cost/full-history return floor or coverage waiver."
    )
    return item


def static_native_contract(evaluator: Path, runner: Path) -> dict[str, Any]:
    """Inspect frozen call/default syntax without importing or executing legacy code."""
    import ast  # noqa: PLC0415 - used only by the optional static source review
    import math  # noqa: PLC0415

    def describe(node: ast.AST | None) -> dict[str, Any]:
        if node is None:
            return {"kind": "not_explicit"}
        if isinstance(node, ast.Constant) and (
            node.value is None
            or isinstance(node.value, (bool, int))
            or (isinstance(node.value, float) and math.isfinite(node.value))
        ):
            return {"kind": "literal", "value": node.value}
        return {
            "kind": type(node).__name__,
            "ast_sha256": hashlib.sha256(
                ast.dump(node, include_attributes=False).encode()
            ).hexdigest(),
        }

    tree = ast.parse(evaluator.read_text())
    calls: dict[tuple[int, int], dict[str, Any]] = {}
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "run_backtest"
            ):
                continue
            keywords = {x.arg: x.value for x in node.keywords}
            cap = keywords.get("max_entry_window_participation")
            calls[(node.lineno, node.col_offset)] = {
                "function": function.name,
                "line": node.lineno,
                "keyword_names": sorted(k for k in keywords if k is not None),
                "has_keyword_unpack": None in keywords,
                "explicit_execution_start": "execution_start" in keywords,
                "explicit_initial_cash": "initial_cash" in keywords,
                "explicit_whole_share_buys": "whole_share_buys" in keywords,
                "opening_cap_expression": describe(cap),
                "uses_decision_overlay": "decision_overlay" in keywords,
                "uses_signal_override": "signal_override" in keywords,
            }
    defaults: dict[str, dict[str, Any]] = {}
    for node in ast.walk(ast.parse(runner.read_text())):
        if isinstance(node, ast.FunctionDef) and node.name == "run_backtest":
            positional = node.args.posonlyargs + node.args.args
            values: dict[str, ast.AST | None] = (
                {
                    arg.arg: value
                    for arg, value in zip(
                        positional[len(positional) - len(node.args.defaults) :],
                        node.args.defaults,
                        strict=True,
                    )
                }
                if node.args.defaults
                else {}
            )
            values.update(
                {
                    arg.arg: value
                    for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
                }
            )
            defaults = {
                name: describe(values.get(name))
                for name in [
                    "execution_start",
                    "initial_cash",
                    "whole_share_buys",
                    "max_entry_window_participation",
                    "one_way_bps",
                ]
            }
            parameters = {arg.arg for arg in positional + node.args.kwonlyargs}
            for name in defaults:
                defaults[name]["present_in_signature"] = name in parameters
            break
    return {
        "evaluator_source_sha256": sha(evaluator),
        "runner_source_sha256": sha(runner),
        "calls": [calls[k] for k in sorted(calls)],
        "runner_defaults": defaults,
        "limitations": (
            "Static syntax only; evaluator routing and fitted-fold causality still need review. "
            "No simulation, return floor or coverage waiver."
        ),
    }
