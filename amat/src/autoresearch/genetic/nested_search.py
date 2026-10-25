"""Fit-only paired search with immutable evidence and null feature remapping.

This module cannot select on validation outcomes: its only market inputs are the
fit partition. Null mappings jointly reorder normalized expression observations,
while the fit labels and common observation calendar stay fixed.
"""

from __future__ import annotations

import fcntl
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.data import CandleDataset, digest_json
from autoresearch.genetic.models import PatternDefinition
from autoresearch.genetic.study import _read_checkpoint, _runtime_versions, write_json
from autoresearch.genetic.symbolic import (
    SYMBOLIC_REGISTRY_VERSION,
    SymbolicEvaluator,
    SymbolicTrace,
    symbolic_registry_digest,
)
from autoresearch.genetic.symbolic_search import SymbolicSearchProtocol, run_symbolic_search
from autoresearch.genetic.symbolic_study import (
    SymbolicScreen,
    SymbolicScreenConfig,
    _source_hashes,
    _template,
)
from autoresearch.market_scenarios import panel_digest

VERSION = "nested-symbolic-fit-search/1"
METHODS = ("genetic", "random")


class _MappedEvaluator(SymbolicEvaluator):
    def __init__(self, base: SymbolicEvaluator, mapping: np.ndarray) -> None:
        # Evaluation delegates to the already warmed evaluator; its feature cache
        # stays unmodified and every asset receives the same temporal remapping.
        self.base, self.mapping = base, mapping.copy()

    def evaluate(self, pattern: PatternDefinition) -> SymbolicTrace:
        trace = self.base.evaluate(pattern)
        return SymbolicTrace(
            trace.values[self.mapping],
            {**trace.diagnostics, "joint_feature_time_remapping": True},
        )


def _calendar(
    screen: SymbolicScreen, common_rows: np.ndarray | None, feature_mapping: np.ndarray | None
) -> np.ndarray:
    length = len(screen.rows)
    # A donor needs valid features, not a mature outcome label. Labels remain at
    # their original destination dates; final fit rows can donate within fit.
    eligible = np.ones(length, dtype=bool)
    for terminal in screen.protocol.terminals():
        eligible &= np.isfinite(screen.evaluator.feature(terminal)).all(axis=1)
    if common_rows is not None:
        common_rows = np.asarray(common_rows)
        if common_rows.shape != (length,) or common_rows.dtype != np.dtype(bool):
            raise ValueError("common_rows must be a Boolean fit-length mask")
        eligible &= common_rows
    screen.rows &= eligible
    if int(screen.rows.sum()) < screen.config.min_effect_sessions:
        raise ValueError("insufficient common fit dates")
    mapping = np.arange(length)
    if feature_mapping is not None:
        given = np.asarray(feature_mapping)
        if given.shape != (length,) or given.dtype.kind not in "iu":
            raise ValueError("feature_mapping must contain fit-length integer indices")
        if not np.array_equal(np.sort(given), mapping):
            raise ValueError("feature_mapping must be a permutation of fit indices")
        if not np.array_equal(np.sort(given[eligible]), mapping[eligible]):
            raise ValueError("feature_mapping must permute only eligible common feature rows")
        if not np.array_equal(given[~eligible], mapping[~eligible]):
            raise ValueError("feature_mapping must preserve ineligible fit rows")
        mapping = given.astype(np.int64, copy=True)
        screen.evaluator = _MappedEvaluator(screen.evaluator, mapping)
    return mapping


def _seed_search(
    output: Path, protocol: SymbolicSearchProtocol, screen: SymbolicScreen, context: dict[str, Any]
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    frozen = {"context": context, "search": protocol.to_dict()}
    protocol_path, checkpoint_path = output / "protocol.json", output / "checkpoint.json"
    if protocol_path.exists():
        if digest_json(json.loads(protocol_path.read_text())) != digest_json(frozen):
            raise ValueError("nested search resume protocol differs from frozen fit search")
    else:
        if checkpoint_path.exists():
            raise ValueError("nested search checkpoint has no frozen protocol")
        write_json(protocol_path, frozen)
    state = _read_checkpoint(checkpoint_path) if checkpoint_path.exists() else None
    state = run_symbolic_search(
        protocol,
        screen,
        pattern_template=_template(),
        evaluator_hash=digest_json(context),
        data_hash=context["dataset_sha256"],
        checkpoint=state,
        max_slots=0,
    )
    write_json(checkpoint_path, {"sha256": digest_json(state), "state": state})
    while state["next_slot"] < protocol.budget:
        state = run_symbolic_search(
            protocol,
            screen,
            pattern_template=_template(),
            evaluator_hash=digest_json(context),
            data_hash=context["dataset_sha256"],
            checkpoint=state,
            max_slots=protocol.population_size,
        )
        write_json(checkpoint_path, {"sha256": digest_json(state), "state": state})
    return state


def discover_fold(
    dataset: CandleDataset,
    panel: MarketPanel,
    protocol: SymbolicSearchProtocol,
    screen: SymbolicScreenConfig,
    *,
    seeds: tuple[int, ...],
    shortlist_size: int,
    output: Path,
    contract_hash: str,
    feature_mapping: np.ndarray | None = None,
    common_rows: np.ndarray | None = None,
) -> dict[str, Any]:
    """Run complete equal-budget searches per seed; return fit-ranked shortlists.

    Existing checkpoints resume only under identical data, code, grammar, and
    null mappings. A search with no eligible expression is recorded as ineligible
    and consumes its full slot budget, rather than disappearing from evidence.
    """
    if not seeds or len(set(seeds)) != len(seeds) or any(type(seed) is not int for seed in seeds):
        raise ValueError("seeds must be distinct integers and nonempty")
    if type(shortlist_size) is not int or shortlist_size < 1:
        raise ValueError("shortlist_size must be a positive integer")
    if not isinstance(contract_hash, str) or not contract_hash:  # type: ignore[redundant-expr]
        raise ValueError("contract_hash must identify the frozen parent protocol")
    evaluator = SymbolicScreen(dataset, panel, protocol, screen)
    mapping = _calendar(evaluator, common_rows, feature_mapping)
    context = {
        "schema_version": VERSION,
        "contract_sha256": contract_hash,
        "dataset_sha256": dataset.digest,
        "feature_panel_sha256": panel_digest(panel),
        "registry_version": SYMBOLIC_REGISTRY_VERSION,
        "registry_sha256": symbolic_registry_digest(),
        "common_calendar_sha256": digest_json(evaluator.rows.tolist()),
        "feature_mapping_sha256": digest_json(mapping.tolist()),
        "screen": asdict(screen),
        "code_sha256": _source_hashes(),
        "runtime_versions": _runtime_versions(),
        "seeds": list(seeds),
        "shortlist_size": shortlist_size,
        "training_only": True,
    }
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("nested discovery workspace already in use") from exc
        return _discover_locked(output, protocol, evaluator, context)


def _discover_locked(
    output: Path,
    protocol: SymbolicSearchProtocol,
    evaluator: SymbolicScreen,
    context: dict[str, Any],
) -> dict[str, Any]:
    frozen = {"context": context, "search": protocol.to_dict()}
    protocol_path = output / "protocol.json"
    if protocol_path.exists():
        if digest_json(json.loads(protocol_path.read_text())) != digest_json(frozen):
            raise ValueError("nested discovery protocol differs from frozen fit search")
    else:
        write_json(protocol_path, frozen)
    report: dict[str, Any] = {
        "schema_version": VERSION,
        "execution_authority": "none",
        "context": context,
        "contenders": {},
        "ranked_contenders": {},
        "searches": [],
    }
    for method in METHODS:
        distinct: dict[str, dict[str, Any]] = {}
        for seed in context["seeds"]:
            configured = replace(protocol, method=method, seed=seed)
            directory = output / method / f"seed-{seed}"
            state = _seed_search(directory, configured, evaluator, context)
            ledger = state["ledger"]
            report["searches"].append(
                {
                    "method": method,
                    "seed": seed,
                    "budget": configured.budget,
                    "recorded_slots": len(ledger),
                    "failed_slots": sum(row["fitness"] is None for row in ledger),
                    "cached_slots": sum(bool(row["cached"]) for row in ledger),
                    "unique_genomes": len(
                        {row["genome_hash"] for row in ledger if row["genome_hash"]}
                    ),
                    "status": "eligible" if state["champion"] is not None else "ineligible",
                    "checkpoint": str((directory / "checkpoint.json").relative_to(output)),
                    "checkpoint_sha256": digest_json(state),
                }
            )
            for row in ledger:
                if row["fitness"] is not None:
                    distinct.setdefault(
                        row["genome_hash"],
                        {
                            "pattern": row["pattern"],
                            "genome_sha256": row["genome_hash"],
                            "fitness": row["fitness"],
                            "seed": seed,
                            "slot": row["slot"],
                        },
                    )
        ranked = sorted(distinct.values(), key=lambda row: (-row["fitness"], row["genome_sha256"]))
        shortlisted = ranked[: context["shortlist_size"]]
        report["ranked_contenders"][method] = shortlisted
        report["contenders"][method] = [row["pattern"] for row in shortlisted]
    if (
        _source_hashes() != context["code_sha256"]
        or panel_digest(evaluator.panel) != context["feature_panel_sha256"]
        or evaluator.dataset.digest != context["dataset_sha256"]
    ):
        raise ValueError("nested search inputs changed during evaluation")
    report["status"] = "eligible" if all(report["contenders"].values()) else "ineligible"
    report["recorded_slots"] = sum(row["recorded_slots"] for row in report["searches"])
    report["failed_slots"] = sum(row["failed_slots"] for row in report["searches"])
    write_json(output / "report.json", report)
    return report
