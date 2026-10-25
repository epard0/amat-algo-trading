"""Frozen training-only symbolic screens with a grammar-wide observation calendar.

The numerical expression maps to a hypothetical long-only event allocation
(value + 1) / 2. Fitness measures its net event contribution over a fixed 0.5
allocation, less node complexity. It is not AMAT portfolio fitness.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.data import (
    MAX_ONE_WAY_BPS,
    CandleDataset,
    digest_json,
    training_labels,
)
from autoresearch.genetic.models import BinaryNode, PatternDefinition, SymbolicGenome, UnaryNode
from autoresearch.genetic.search import EvaluationResult
from autoresearch.genetic.study import _read_checkpoint, _runtime_versions, write_json
from autoresearch.genetic.symbolic import (
    SYMBOLIC_REGISTRY_VERSION,
    SymbolicEvaluator,
    symbolic_registry_digest,
)
from autoresearch.genetic.symbolic_search import (
    SymbolicSearchProtocol,
    run_symbolic_search,
    validate_symbolic_pattern,
)
from autoresearch.market_scenarios import panel_digest

STUDY_VERSION = "training-symbolic-screen/1"
BPS = 10_000
MIN_SESSIONS = 2


@dataclass(frozen=True)
class SymbolicScreenConfig:
    one_way_bps: float = 2.0
    min_effect_sessions: int = 30
    complexity_penalty_bps: float = 0.01
    min_output_std: float = 1e-6

    def __post_init__(self) -> None:
        if not np.isfinite(self.one_way_bps) or not 0 <= self.one_way_bps <= MAX_ONE_WAY_BPS:
            raise ValueError("cost must be finite and between 0 and 100 bps one way")
        if type(self.min_effect_sessions) is not int or self.min_effect_sessions < MIN_SESSIONS:
            raise ValueError("at least two distinct effect sessions required")
        if not np.isfinite(self.complexity_penalty_bps) or self.complexity_penalty_bps < 0:
            raise ValueError("complexity penalty must be finite and nonnegative")
        if not np.isfinite(self.min_output_std) or not 0 < self.min_output_std < 1:
            raise ValueError("minimum output standard deviation must be in (0,1)")


def node_count(pattern: PatternDefinition) -> int:
    if not isinstance(pattern.genome, SymbolicGenome):
        raise ValueError("symbolic screen requires a numerical tree")
    pending, total = [pattern.genome.root], 0
    while pending:
        node = pending.pop()
        total += 1
        if isinstance(node, UnaryNode):
            pending.append(node.arg)
        elif isinstance(node, BinaryNode):
            pending.extend((node.left, node.right))
    return total


class SymbolicScreen:
    def __init__(
        self,
        dataset: CandleDataset,
        panel: MarketPanel,
        protocol: SymbolicSearchProtocol,
        config: SymbolicScreenConfig,
    ) -> None:
        if dataset.timeframe != "1d" or dataset.price_basis != "corporate_action_adjusted":
            raise ValueError("symbolic research currently requires adjusted daily data")
        if dataset.timestamps != tuple(day.isoformat() for day in panel.sessions) or (
            dataset.symbols != tuple(str(symbol) for symbol in panel.symbols)
        ):
            raise ValueError("symbolic feature panel must match training dataset dates and symbols")
        for name, array in zip(
            ("open", "high", "low", "close"), panel.candle_inputs(), strict=True
        ):
            if not np.array_equal(array, dataset.fields[name], equal_nan=True):
                raise ValueError("symbolic feature panel differs from training candles")
        self.dataset, self.panel, self.protocol, self.config = dataset, panel, protocol, config
        self.evaluator = SymbolicEvaluator(panel)
        self.labels, usable = training_labels(dataset, max_lag=0, one_way_bps=config.one_way_bps)
        # Every possible leaf has the same usable calendar. Arithmetic failures
        # are rejected later rather than allowing a tree to cherry-pick dates.
        for terminal in protocol.terminals():
            usable &= np.isfinite(self.evaluator.feature(terminal))
        self.rows = usable.all(axis=1)
        if int(self.rows.sum()) < config.min_effect_sessions:
            raise ValueError("insufficient common training dates for symbolic grammar")

    def __call__(self, pattern: PatternDefinition) -> EvaluationResult:
        validate_symbolic_pattern(pattern, self.protocol)
        count = node_count(pattern)
        if pattern.binding.role != "feature" or pattern.binding.direction != "long_support":
            raise ValueError("symbolic screen supports long-support feature binding only")
        trace = self.evaluator.evaluate(pattern)
        values = trace.values[self.rows]
        diagnostics: dict[str, Any] = {
            "complete_training_sessions": int(self.rows.sum()),
            "node_count": count,
            "evaluation": trace.diagnostics,
        }
        if not np.isfinite(values).all():
            return EvaluationResult(None, diagnostics, "unavailable_expression_on_common_calendar")
        if np.any(np.abs(values) > 1):
            return EvaluationResult(None, diagnostics, "unbounded_expression_output")
        std = float(values.std())
        diagnostics["output_std"] = std
        if std < self.config.min_output_std:
            return EvaluationResult(None, diagnostics, "constant_or_nearly_constant_expression")
        effects = np.abs(values) > self.config.min_output_std
        sessions = int(effects.any(axis=1).sum())
        diagnostics["effect_sessions"] = sessions
        if sessions < self.config.min_effect_sessions:
            return EvaluationResult(None, diagnostics, "insufficient_distinct_effect_sessions")
        labels = self.labels[self.rows]
        weights = (values + 1.0) / 2.0
        contributions = (weights - 0.5) * labels
        penalty = self.config.complexity_penalty_bps * count
        diagnostics.update(
            mean_long_event_contribution_bps=float((weights * labels).mean()) * BPS,
            neutral_event_contribution_bps=float((0.5 * labels).mean()) * BPS,
            incremental_event_contribution_bps=float(contributions.mean()) * BPS,
            mean_event_allocation=float(weights.mean()),
            complexity_penalty_bps=penalty,
            per_symbol={
                symbol: {
                    "mean_output": float(values[:, col].mean()),
                    "effect_bars": int(effects[:, col].sum()),
                    "incremental_event_contribution_bps": float(contributions[:, col].mean()) * BPS,
                }
                for col, symbol in enumerate(self.dataset.symbols)
            },
        )
        return EvaluationResult(float(contributions.mean()) * BPS - penalty, diagnostics)


def _template() -> dict[str, Any]:
    return {
        "schemaVersion": "patternSchema/0.1.0",
        "name": "discovered_symbolic",
        "timeframe": "1d",
        "priceBasis": "corporate_action_adjusted",
        "availability": "completed_bar_close",
        "missingPolicy": "unavailable",
        "binding": {
            "role": "feature",
            "direction": "long_support",
            "threshold": 0.0,
            "maxEffect": 0.25,
        },
    }


def _source_hashes() -> dict[str, str]:
    source = Path(__file__).parents[2]
    return {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for package in ("amat", "autoresearch", "llmrouter")
        for path in sorted((source / package).rglob("*.py"))
    }


def _report(output: Path, frozen: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    ledger, champion = state["ledger"], state["champion"]
    report = {
        "schema_version": STUDY_VERSION,
        "status": "completed"
        if state["next_slot"] == frozen["candidate_slot_budget"]
        else "paused",
        "execution_authority": "none",
        "evidence_level": "adaptive_training_symbolic_screen_only",
        "protocol_sha256": digest_json(frozen),
        "candidate_slot_budget": frozen["candidate_slot_budget"],
        "recorded_slots": len(ledger),
        "unique_genomes": len({row["genome_hash"] for row in ledger if row["genome_hash"]}),
        "cached_slots": sum(bool(row["cached"]) for row in ledger),
        "failed_slots": sum(row["fitness"] is None for row in ledger),
        "champion": champion,
        "dataset": frozen["dataset"],
        "screen": frozen["screen"],
        "symbolic_context": frozen["symbolic_context"],
        "fitness": (
            "incremental net long-only event contribution vs 0.5 allocation, bps minus node penalty"
        ),
        "limitations": [
            "Adaptive training screen; not portfolio performance or statistical significance.",
            "Weights=(value+1)/2 in independent next-bar events; no netting or dividends.",
            "Replay adds maxEffect*value to active scores; portfolio effects need measurement.",
            "The calendar includes every terminal; arithmetic-unavailable expressions fail.",
            "No promotion authority; nested whole-search validation and nulls remain future work.",
        ],
    }
    write_json(output / "report.json", report)
    if champion is not None:
        write_json(output / "champion.pattern.json", champion["pattern"])
    lines = [
        "# Symbolic genetic-programming discovery",
        "",
        f"Status: {report['status']}",
        "",
        "Adaptive training screen only. No execution or promotion authority.",
        "",
        f"Slots: {len(ledger)} / {frozen['candidate_slot_budget']}; "
        f"distinct genomes: {report['unique_genomes']}; failed slots: {report['failed_slots']}.",
        "",
    ]
    if champion is not None:
        lines.extend(
            [
                f"Diagnostic fitness: {champion['fitness']:.6f} bps after complexity penalty.",
                "",
                "```json",
                json.dumps(champion["pattern"], indent=2),
                "```",
                "",
            ]
        )
    else:
        lines.extend(["No eligible numerical expression found.", ""])
    lines.extend([*report["limitations"], ""])
    (output / "report.md").write_text("\n".join(lines))
    return report


def run_symbolic_study(
    dataset: CandleDataset,
    panel: MarketPanel,
    protocol: SymbolicSearchProtocol,
    screen_config: SymbolicScreenConfig,
    output: Path,
    *,
    resume: bool = False,
    max_slots: int | None = None,
) -> dict[str, Any]:
    """Freeze full feature dependencies and checkpoint deterministic, bounded work."""
    if max_slots is not None and (type(max_slots) is not int or max_slots < 1):
        raise ValueError("max_slots must be a positive integer")
    screen = SymbolicScreen(dataset, panel, protocol, screen_config)
    symbolic_context = {
        "registry_version": SYMBOLIC_REGISTRY_VERSION,
        "registry_sha256": symbolic_registry_digest(),
        "feature_panel_sha256": panel_digest(panel),
        "common_calendar_sha256": digest_json(screen.rows.tolist()),
    }
    frozen = {
        "schema_version": STUDY_VERSION,
        "search": asdict(protocol),
        "screen": asdict(screen_config),
        "candidate_slot_budget": protocol.budget,
        "dataset": dataset.manifest,
        "dataset_sha256": dataset.digest,
        "symbolic_context": symbolic_context,
        "code_sha256": _source_hashes(),
        "runtime_versions": _runtime_versions(),
        "execution_authority": "none",
        "training_only": True,
    }
    context_hash = digest_json(
        {
            "code": frozen["code_sha256"],
            "screen": frozen["screen"],
            "runtime_versions": frozen["runtime_versions"],
            "symbolic_context": symbolic_context,
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("symbolic discovery workspace is already in use") from exc
        protocol_file, checkpoint_file = output / "protocol.json", output / "checkpoint.json"
        state = None
        if resume:
            if not protocol_file.exists():
                raise ValueError("resume requires frozen protocol")
            if digest_json(json.loads(protocol_file.read_text())) != digest_json(frozen):
                raise ValueError("resume protocol/data/code differs from frozen symbolic study")
            if checkpoint_file.exists():
                state = _read_checkpoint(checkpoint_file)
        elif protocol_file.exists() or checkpoint_file.exists():
            raise ValueError("workspace already exists; use --resume or a new output directory")
        else:
            write_json(protocol_file, frozen)
        state = run_symbolic_search(
            protocol,
            screen,
            pattern_template=_template(),
            evaluator_hash=context_hash,
            data_hash=dataset.digest,
            checkpoint=state,
            max_slots=0,
        )
        write_json(checkpoint_file, {"sha256": digest_json(state), "state": state})
        remaining = protocol.budget - state["next_slot"]
        if max_slots is not None:
            remaining = min(remaining, max_slots)
        while remaining:
            count = min(protocol.population_size, remaining)
            state = run_symbolic_search(
                protocol,
                screen,
                pattern_template=_template(),
                evaluator_hash=context_hash,
                data_hash=dataset.digest,
                checkpoint=state,
                max_slots=count,
            )
            write_json(checkpoint_file, {"sha256": digest_json(state), "state": state})
            remaining -= count
        if (
            _source_hashes() != frozen["code_sha256"]
            or panel_digest(panel) != symbolic_context["feature_panel_sha256"]
            or dataset.digest != frozen["dataset_sha256"]
        ):
            raise ValueError(
                "symbolic research inputs changed during evaluation; output incomplete"
            )
        return _report(output, frozen, state)
