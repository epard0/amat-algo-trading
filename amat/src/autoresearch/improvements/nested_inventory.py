"""Read legacy envelope receipts and paths without running source code or granting comparability."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from autoresearch.improvements.study import digest


def envelope(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if (
        set(value) != {"payload", "sha256"}
        or hashlib.sha256(
            json.dumps(
                value["payload"], sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        != value["sha256"]
    ):
        raise ValueError("Legacy envelope checksum differs")
    return dict(value["payload"])


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def within(root: Path, path: Path) -> Path:
    result = path.resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Legacy evidence path escapes study")
    return result


def inspect_nested(study: Path) -> dict[str, Any]:  # noqa: PLR0912 - explicit evidence checks
    """Preserve every run identity; report failures and incomparable return groups explicitly."""
    protocol_path = within(study, study / "protocol.json")
    protocol = envelope(protocol_path)
    files = {"protocol.json": file_hash(protocol_path)}
    source: dict[str, Any] = {"verified": 0, "missing": [], "mismatched": []}
    for name, expected in protocol.get("runtime_source_sha256", {}).items():
        path = within(study, study / "runtime/src" / name)
        if not path.is_file():
            source["missing"].append(name)
        elif file_hash(path) != expected:
            source["mismatched"].append(name)
        else:
            source["verified"] += 1
    driver_paths = [study / "implementation/nested_strategy_campaign.py", study / "driver.py"]
    source["driver_verified"] = any(
        p.is_file() and file_hash(within(study, p)) == protocol.get("driver_sha256")
        for p in driver_paths
    )
    rows: list[dict[str, Any]] = []
    failures = []
    for raw_receipt_path in sorted((study / "runs").glob("*/receipt.json")):
        receipt_path = raw_receipt_path
        row: dict[str, Any] = {"run_id": receipt_path.parent.name}
        try:
            receipt_path = within(study, receipt_path)
            receipt = envelope(receipt_path)
            path = within(study, receipt_path.parent / "path.npz")
            files[str(receipt_path.relative_to(study.resolve()))] = file_hash(receipt_path)
            if file_hash(path) != receipt["path_sha256"]:
                raise ValueError("Legacy path checksum differs")
            files[str(path.relative_to(study.resolve()))] = receipt["path_sha256"]
            task, metrics = receipt["task"], receipt["metrics"]
            with np.load(path, allow_pickle=False) as arrays:
                if set(arrays.files) != {"dates", "equity", "exposure"}:
                    raise ValueError("Legacy path fields differ")
                dates, equity, exposure = (arrays[k] for k in ["dates", "equity", "exposure"])
                if (
                    dates.ndim != 1
                    or equity.shape != dates.shape
                    or exposure.shape != dates.shape
                    or not np.isfinite(equity).all()
                    or np.any(equity <= 0)
                    or not np.isfinite(exposure).all()
                    or np.any(dates[1:] <= dates[:-1])
                ):
                    raise ValueError("Invalid legacy path arrays")
                indices = np.flatnonzero((dates > task["lower"]) & (dates <= task["upper"]))
                if not len(indices) or indices[0] == 0:
                    raise ValueError("Legacy slice lacks prior equity anchor")
                returns = equity[indices] / equity[indices - 1] - 1
                if (
                    metrics["dates"] != dates[indices].tolist()
                    or metrics["n"] != len(indices)
                    or not np.allclose(returns, metrics["returns"], rtol=0, atol=1e-12)
                ):
                    raise ValueError("Legacy recorded returns differ from equity path")
                net_return = float(equity[indices[-1]] / equity[indices[0] - 1] - 1)
            row.update(
                status="verified_incomparable_path",
                task_sha256=digest(task),
                recipe_sha256=digest(task["recipe"]),
                world=task["world"],
                phase=task["phase"],
                fold=task["fold"],
                cost_bps=task["cost"],
                lower=task["lower"],
                upper=task["upper"],
                net_return=net_return,
                receipt_sha256=files[str(receipt_path.relative_to(study.resolve()))],
                path_sha256=receipt["path_sha256"],
                market_sha256=receipt["market_sha256"],
            )
            rows.append(row)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            failures.append({**row, "status": "invalid_or_unreadable", "reason": str(exc)})
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = digest({k: row[k] for k in ["world", "phase", "fold", "cost_bps", "lower", "upper"]})
        group = groups.setdefault(
            key,
            {
                "scope": {
                    k: row[k] for k in ["world", "phase", "fold", "cost_bps", "lower", "upper"]
                },
                "runs": 0,
                "strongest": row,
            },
        )
        group["runs"] += 1
        if row["net_return"] > group["strongest"]["net_return"]:
            group["strongest"] = row
    return dict(  # noqa: C408 - structured inventory
        schema="legacy-nested-inventory/1",
        study=str(study.resolve()),
        protocol_payload_sha256=digest(protocol),
        files_sha256=files,
        source=source,
        rows=rows,
        failures=failures,
        groups=list(groups.values()),
        phases=dict(Counter(x["phase"] for x in rows)),
        folds=protocol.get("folds"),
        purge=protocol.get("purge"),
        costs=protocol.get("costs"),
        worlds=protocol.get("worlds"),
        comparison_coverage=False,
        limitation=(
            "Read-only checksum/path reconstruction, not source execution, fit causality, "
            "full2017 flat-account parity or campaign execution comparability. Synthetic worlds "
            "and train/selection/test scopes remain separate; stronger raw histories "
            "preserved without return-frontier admission."
        ),
    )
