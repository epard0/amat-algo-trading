"""Training-only candle screens, explicit evidence limits, and frozen run artifacts.

This is a discovery harness, not an AMAT portfolio backtest or promotion adapter.
Every event enters next bar and exits one bar later with a separate round-trip
cost. Repeated events are not netted positions. No validation/holdout is loaded.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from autoresearch.genetic.data import MAX_ONE_WAY_BPS, CandleDataset, digest_json, training_labels
from autoresearch.genetic.evaluator import evaluate_candles
from autoresearch.genetic.models import PatternDefinition
from autoresearch.genetic.search import EvaluationResult, SearchProtocol, run_search

STUDY_VERSION = "training-candle-screen/1"
BPS = 10_000
MINIMUM_SESSIONS = 2


@dataclass(frozen=True)
class ScreenConfig:
    one_way_bps: float = 2.0
    min_matched_sessions: int = 30
    complexity_penalty_bps: float = 0.01

    def __post_init__(self) -> None:
        if not np.isfinite(self.one_way_bps) or not 0 <= self.one_way_bps <= MAX_ONE_WAY_BPS:
            raise ValueError("cost must be finite and between 0 and 100 bps one way")
        if (
            type(self.min_matched_sessions) is not int
            or self.min_matched_sessions < MINIMUM_SESSIONS
        ):
            raise ValueError("at least two distinct matched sessions required")
        if not np.isfinite(self.complexity_penalty_bps) or self.complexity_penalty_bps < 0:
            raise ValueError("complexity penalty must be finite and nonnegative")


class CandleScreen:
    """Only score a common fully observed training calendar across the universe."""

    def __init__(
        self, dataset: CandleDataset, search: SearchProtocol, config: ScreenConfig
    ) -> None:
        self.dataset = dataset
        self.search = search
        self.config = config
        labels, valid = training_labels(
            dataset, max_lag=search.max_lag, one_way_bps=config.one_way_bps
        )
        self.labels = labels
        # Cross-asset dependence is preserved: support is measured in distinct dates.
        self.rows = valid.all(axis=1)
        self.sessions = int(self.rows.sum())
        if self.sessions < config.min_matched_sessions:
            raise ValueError(
                "insufficient complete training sessions after warmup and label maturity"
            )

    def __call__(self, pattern: PatternDefinition) -> EvaluationResult:
        if (
            pattern.timeframe != self.dataset.timeframe
            or pattern.price_basis != self.dataset.price_basis
        ):
            raise ValueError("pattern timeframe/price basis disagrees with data")
        if pattern.genome.kind != "candle_conjunction":
            raise ValueError("this discovery screen supports candle conjunctions only")
        if pattern.binding.role != "feature" or pattern.binding.direction != "long_support":
            raise ValueError(
                "first discovery milestone supports long-support feature patterns only"
            )
        matches = evaluate_candles(pattern, self.dataset.fields)[self.rows]
        if not np.isfinite(matches).all():
            return EvaluationResult(None, failure="unavailable_pattern_on_common_training_calendar")
        selected = matches == 1
        distinct_sessions = int(selected.any(axis=1).sum())
        diagnostics: dict[str, Any] = {
            "complete_training_sessions": self.sessions,
            "matched_sessions": distinct_sessions,
            "matched_asset_events": int(selected.sum()),
            "prevalence": float(selected.mean()),
        }
        if distinct_sessions < self.config.min_matched_sessions:
            return EvaluationResult(None, diagnostics, "insufficient_distinct_matched_sessions")
        if selected.all() or not selected.any():
            return EvaluationResult(None, diagnostics, "constant_pattern_on_training_data")
        labels = self.labels[self.rows]
        contribution = np.where(selected, labels, 0.0).mean(axis=1)
        gross_score = float(contribution.mean()) * BPS
        penalty = self.config.complexity_penalty_bps * len(pattern.genome.rules)
        diagnostics.update(
            mean_matched_event_net_bps=float(labels[selected].mean()) * BPS,
            mean_all_events_net_bps=float(labels.mean()) * BPS,
            mean_equal_weight_event_contribution_bps=gross_score,
            complexity_penalty_bps=penalty,
            per_symbol={
                symbol: {
                    "matched_events": int(selected[:, i].sum()),
                    "mean_contribution_bps": float(np.where(selected[:, i], labels[:, i], 0).mean())
                    * BPS,
                }
                for i, symbol in enumerate(self.dataset.symbols)
            },
        )
        return EvaluationResult(gross_score - penalty, diagnostics)


def _template(dataset: CandleDataset) -> dict[str, Any]:
    return {
        "schemaVersion": "patternSchema/0.1.0",
        "name": "discovered_candle",
        "timeframe": dataset.timeframe,
        "priceBasis": dataset.price_basis,
        "availability": "completed_bar_close",
        "missingPolicy": "unavailable",
        "genome": {
            "kind": "candle_conjunction",
            "maxLag": 1,
            "rules": [
                {
                    "left": {"field": "close", "lag": 0},
                    "op": "gt",
                    "right": {"field": "close", "lag": 1},
                }
            ],
        },
        "binding": {
            "role": "feature",
            "direction": "long_support",
            "threshold": 0.0,
            "maxEffect": 0.25,
        },
    }


def _source_hashes() -> dict[str, str]:
    root = Path(__file__).parent
    source = root.parent.parent
    dependencies = [
        source / name
        for name in (
            "amat/data/snapshot.py",
            "amat/backtest/panel.py",
            "autoresearch/labels.py",
            "autoresearch/calendar_audit.py",
            "autoresearch/validation_contract.py",
        )
    ]
    return {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted([*root.glob("*.py"), *dependencies])
    }


def _runtime_versions() -> dict[str, str]:
    return {
        "python": sys.version,
        **{
            name: importlib.metadata.version(name)
            for name in ("numpy", "polars", "pydantic", "exchange-calendars")
        },
    }


def write_json(path: Path, data: Any) -> None:
    """Atomically publish a complete JSON document; never allow NaN/Infinity."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def _read_checkpoint(path: Path) -> dict[str, Any]:
    wrapper = json.loads(path.read_text())
    state: dict[str, Any] = wrapper["state"]
    if wrapper["sha256"] != digest_json(state):
        raise ValueError("checkpoint checksum mismatch")
    return state


def _write_report(output: Path, frozen: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    ledger = state["ledger"]
    champion = state["champion"]
    report = {
        "schema_version": STUDY_VERSION,
        "status": "completed"
        if state["next_slot"] == frozen["candidate_slot_budget"]
        else "paused",
        "execution_authority": "none",
        "evidence_level": "adaptive_training_screen_only",
        "protocol_sha256": digest_json(frozen),
        "candidate_slot_budget": frozen["candidate_slot_budget"],
        "recorded_slots": len(ledger),
        "cached_slots": sum(bool(row.get("cached")) for row in ledger),
        "failed_slots": sum(row["fitness"] is None for row in ledger),
        "unique_genomes": len({row["genome_hash"] for row in ledger if row["genome_hash"]}),
        "champion": champion,
        "dataset": frozen["dataset"],
        "screen": frozen["screen"],
        "fitness": "mean equal-weight net event contribution in bps minus per-rule penalty",
        "limitations": [
            "Training selection only: neither validation performance nor statistical significance.",
            "Next-bar entry and following-bar exit proxies; each event pays a full round trip.",
            "Not a portfolio equity curve, incremental AMAT score, or permission to promote.",
            "Screen dates require complete feature/label windows across all selected assets.",
            "Historical universe selection and adjusted prices are inherited from the dataset.",
        ],
        "next_required_gate": "P2: AMAT replay against incumbent and matched controls",
    }
    write_json(output / "report.json", report)
    if champion is not None:
        write_json(output / "champion.pattern.json", champion["pattern"])
    lines = [
        "# Genetic candle discovery",
        "",
        f"Status: {report['status']}",
        "",
        "**Adaptive training screen only. No execution or promotion authority.**",
        "",
        f"Slots: {len(ledger)} / {frozen['candidate_slot_budget']}; "
        f"unique genomes: {report['unique_genomes']}.",
        f"Training: {frozen['dataset']['training_start']} "
        f"through {frozen['dataset']['training_end']}.",
        "",
    ]
    if champion is not None:
        lines.extend(
            [
                f"Best diagnostic fitness: {champion['fitness']:.6f} bps after complexity penalty.",
                "",
                "```json",
                json.dumps(champion["pattern"], indent=2),
                "```",
                "",
            ]
        )
    else:
        lines.extend(["No eligible candidate found.", ""])
    lines.extend([*report["limitations"], "", str(report["next_required_gate"]), ""])
    (output / "report.md").write_text("\n".join(lines))
    return report


def run_study(
    dataset: CandleDataset,
    protocol: SearchProtocol,
    screen_config: ScreenConfig,
    output: Path,
    *,
    resume: bool = False,
    max_slots: int | None = None,
) -> dict[str, Any]:
    """Freeze a run, checkpoint each population-sized chunk, and write evidence."""
    screen = CandleScreen(dataset, protocol, screen_config)
    frozen: dict[str, Any] = {
        "schema_version": STUDY_VERSION,
        "search": asdict(protocol),
        "screen": asdict(screen_config),
        "candidate_slot_budget": protocol.budget,
        "dataset": dataset.manifest,
        "dataset_sha256": dataset.digest,
        "code_sha256": _source_hashes(),
        "runtime_versions": _runtime_versions(),
        "execution_authority": "none",
        "training_only": True,
    }
    if max_slots is not None and (type(max_slots) is not int or max_slots < 1):
        raise ValueError("max_slots must be a positive integer")
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("discovery workspace is already in use") from exc
        protocol_file = output / "protocol.json"
        checkpoint_file = output / "checkpoint.json"
        state = None
        if resume:
            if not protocol_file.exists():
                raise ValueError("resume requires frozen protocol")
            if digest_json(json.loads(protocol_file.read_text())) != digest_json(frozen):
                raise ValueError("resume protocol/data/code differs from frozen study")
            if checkpoint_file.exists():
                state = _read_checkpoint(checkpoint_file)
        elif protocol_file.exists() or checkpoint_file.exists():
            raise ValueError("workspace already exists; use --resume or a new output directory")
        else:
            write_json(protocol_file, frozen)
        context_hash = digest_json(
            {
                "code": frozen["code_sha256"],
                "screen": frozen["screen"],
                "runtime_versions": frozen["runtime_versions"],
            }
        )
        # Persist an empty, context-validated checkpoint before any potentially
        # interruptible evaluations. Also validates a completed resume (zero work).
        state = run_search(
            protocol,
            screen,
            pattern_template=_template(dataset),
            evaluator_hash=context_hash,
            data_hash=frozen["dataset_sha256"],
            checkpoint=state,
            max_slots=0,
        )
        write_json(checkpoint_file, {"sha256": digest_json(state), "state": state})
        previous_slot = state["next_slot"]
        remaining = protocol.budget - previous_slot
        if max_slots is not None:
            remaining = min(remaining, max_slots)
        while remaining:
            count = min(protocol.population_size, remaining)
            state = run_search(
                protocol,
                screen,
                pattern_template=_template(dataset),
                evaluator_hash=context_hash,
                data_hash=frozen["dataset_sha256"],
                checkpoint=state,
                max_slots=count,
            )
            write_json(checkpoint_file, {"sha256": digest_json(state), "state": state})
            remaining -= count
        return _write_report(output, frozen, state)
