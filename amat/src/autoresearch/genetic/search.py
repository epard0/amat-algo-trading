"""Bounded candle discovery. Scores are training diagnostics, never promotion evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from autoresearch.genetic.models import PatternDefinition, canonical_genome_digest, parse_pattern

SEARCH_VERSION = "candle-search/0.1.0"
MAX_RULES = 8
MAX_LAG = 20
MAX_SEARCH_SLOTS = 100_000
MAX_GENERATION_ATTEMPTS = 10_000
REPLACE_MUTATION_PROBABILITY = 0.5


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class SearchProtocol:
    """Immutable search settings. Generations excludes the initial population."""

    population_size: int = 64
    generations: int = 10
    seed: int = 0
    max_rules: int = 3
    max_lag: int = 2
    elite_count: int = 2
    tournament_size: int = 3
    crossover_probability: float = 0.6
    mutation_probability: float = 0.4
    fresh_probability: float = 0.1
    max_generation_attempts: int = 64
    method: str = "genetic"

    def __post_init__(self) -> None:
        integers = (
            self.population_size,
            self.generations,
            self.seed,
            self.max_rules,
            self.max_lag,
            self.elite_count,
            self.tournament_size,
            self.max_generation_attempts,
        )
        if any(type(value) is not int for value in integers):
            raise ValueError("Search integer settings must be integers")
        if self.population_size < 1 or self.generations < 0:
            raise ValueError("Population must be positive and generations nonnegative")
        if not 1 <= self.max_rules <= MAX_RULES or not 0 <= self.max_lag <= MAX_LAG:
            raise ValueError("Invalid candle grammar bounds")
        if not 0 <= self.elite_count <= self.population_size:
            raise ValueError("Elite count must fit population")
        if self.tournament_size < 1 or self.max_generation_attempts < 1:
            raise ValueError("Tournament and retry bounds must be positive")
        for probability in (
            self.crossover_probability,
            self.mutation_probability,
            self.fresh_probability,
        ):
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("Probabilities must be finite and in [0,1]")
        if self.budget > MAX_SEARCH_SLOTS:
            raise ValueError("Search budget exceeds 100000 slots")
        if self.max_generation_attempts > MAX_GENERATION_ATTEMPTS:
            raise ValueError("Generation retry budget exceeds 10000 attempts")
        if self.method not in {"genetic", "random"}:
            raise ValueError("method must be genetic or random")

    @property
    def budget(self) -> int:
        return self.population_size * (self.generations + 1)


@dataclass(frozen=True)
class EvaluationResult:
    fitness: float | None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None


def _rule(rng: random.Random, protocol: SearchProtocol) -> dict[str, Any]:
    def operand() -> dict[str, Any]:
        return {
            "field": rng.choice(("open", "high", "low", "close")),
            "lag": rng.randrange(protocol.max_lag + 1),
        }

    return {"left": operand(), "op": rng.choice(("gt", "lt")), "right": operand()}


def _rank(entry: dict[str, Any]) -> tuple[float, int]:
    return float(entry["fitness"]), -int(entry["slot"])


def _parents(
    rng: random.Random,
    previous: list[dict[str, Any]],
    protocol: SearchProtocol,
) -> dict[str, Any]:
    return max(rng.choices(previous, k=protocol.tournament_size), key=_rank)


def _propose(
    rng: random.Random,
    protocol: SearchProtocol,
    template: dict[str, Any],
    previous: list[dict[str, Any]],
    index: int,
) -> tuple[PatternDefinition | None, str, list[str], str | None]:
    successful = sorted(
        (row for row in previous if row["status"] == "evaluated"), key=_rank, reverse=True
    )
    if protocol.method == "genetic" and index < min(protocol.elite_count, len(successful)):
        elite = successful[index]
        return parse_pattern(elite["pattern"]), "elite", [elite["genome_hash"]], None
    last_error = "No valid candidate generated"
    for _ in range(protocol.max_generation_attempts):
        parents: list[str] = []
        operator = "random"
        rules = [_rule(rng, protocol) for _ in range(rng.randint(1, protocol.max_rules))]
        if (
            successful
            and protocol.method == "genetic"
            and rng.random() >= protocol.fresh_probability
        ):
            parent = _parents(rng, successful, protocol)
            parents.append(parent["genome_hash"])
            rules = copy.deepcopy(parent["pattern"]["genome"]["rules"])
            operator = "reproduction"
            if rng.random() < protocol.crossover_probability:
                mate = _parents(rng, successful, protocol)
                parents.append(mate["genome_hash"])
                pool = rules + copy.deepcopy(mate["pattern"]["genome"]["rules"])
                rules = rng.sample(pool, rng.randint(1, min(len(pool), protocol.max_rules)))
                operator = "crossover"
            if rng.random() < protocol.mutation_probability:
                if rng.random() < REPLACE_MUTATION_PROBABILITY:
                    rules[rng.randrange(len(rules))] = _rule(rng, protocol)
                elif len(rules) < protocol.max_rules:
                    rules.append(_rule(rng, protocol))
                elif len(rules) > 1:
                    rules.pop(rng.randrange(len(rules)))
                else:
                    rules[0] = _rule(rng, protocol)
                operator += "+mutation"
        candidate = copy.deepcopy(template)
        candidate["genome"] = {
            "kind": "candle_conjunction",
            "maxLag": protocol.max_lag,
            "rules": rules,
        }
        try:
            return parse_pattern(candidate), operator, parents, None
        except ValueError as exc:
            last_error = str(exc)
    return None, "generation_exhausted", [], last_error


def _evaluate(
    evaluator: Callable[[PatternDefinition], EvaluationResult],
    pattern: PatternDefinition,
) -> dict[str, Any]:
    try:
        result = evaluator(pattern)
        if not isinstance(result, EvaluationResult):
            raise TypeError("Evaluator must return EvaluationResult")
        if not isinstance(result.diagnostics, dict):
            raise TypeError("Evaluator diagnostics must be a dictionary")
        if result.failure is not None and not isinstance(result.failure, str):
            raise TypeError("Evaluator failure must be a string")
        diagnostics = copy.deepcopy(result.diagnostics)
        _digest(diagnostics)  # No NaN or executable/non-JSON diagnostic state.
        if result.failure is not None:
            return {
                "status": "failed",
                "fitness": None,
                "diagnostics": diagnostics,
                "failure": result.failure,
            }
        if result.fitness is None or not math.isfinite(result.fitness):
            raise ValueError("Evaluator fitness must be finite")
        return {
            "status": "evaluated",
            "fitness": float(result.fitness),
            "diagnostics": diagnostics,
            "failure": None,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "fitness": None,
            "diagnostics": {},
            "failure": f"{type(exc).__name__}: {exc}",
        }


def _tuples(value: Any) -> Any:
    return tuple(_tuples(item) for item in value) if isinstance(value, list) else value


def _restore(checkpoint: dict[str, Any], protocol: SearchProtocol, context: str) -> dict[str, Any]:
    state = copy.deepcopy(checkpoint)
    checksum = state.pop("checkpoint_hash", None)
    if checksum != _digest(state):
        raise ValueError("Checkpoint payload checksum mismatch")
    if state.get("version") != SEARCH_VERSION or state.get("context_hash") != context:
        raise ValueError("Checkpoint version or search context mismatch")
    if state.get("protocol") != asdict(protocol):
        raise ValueError("Checkpoint protocol mismatch")
    if state["next_slot"] != len(state["ledger"]) or not 0 <= state["next_slot"] <= protocol.budget:
        raise ValueError("Invalid checkpoint ledger length")
    return state


def run_search(
    protocol: SearchProtocol,
    evaluator: Callable[[PatternDefinition], EvaluationResult],
    *,
    pattern_template: Mapping[str, Any],
    evaluator_hash: str,
    data_hash: str,
    checkpoint: dict[str, Any] | None = None,
    max_slots: int | None = None,
) -> dict[str, Any]:
    """Maximize explicit fitness; return inert JSON state resumable at any slot.

    The evaluator must use training data only, with no holdout access.
    The caller supplies stable hashes including evaluator implementation and scoring
    protocol, and data provenance/splits. Changing any context rejects a resume.
    Duplicates, elites, failed evaluations and exhausted generation slots all count
    against the same budget. Cached failures remain failures. ``max_slots`` limits
    additional work in this invocation, not the total experiment budget.
    """
    if not evaluator_hash or not data_hash:
        raise ValueError("Evaluator and data hashes are required")
    if max_slots is not None and (type(max_slots) is not int or max_slots < 0):
        raise ValueError("max_slots must be a nonnegative integer")
    template = copy.deepcopy(dict(pattern_template))
    template.pop("genome", None)
    context = _digest(
        {
            "version": SEARCH_VERSION,
            "protocol": asdict(protocol),
            "template": template,
            "evaluator": evaluator_hash,
            "data": data_hash,
        }
    )
    rng = random.Random(protocol.seed)  # noqa: S311 - reproducible research, not cryptography.
    if checkpoint is None:
        state: dict[str, Any] = {
            "version": SEARCH_VERSION,
            "context_hash": context,
            "protocol": asdict(protocol),
            "next_slot": 0,
            "ledger": [],
            "cache": {},
            "champion": None,
        }
    else:
        state = _restore(checkpoint, protocol, context)
        rng.setstate(_tuples(state["rng_state"]))
    stop = min(
        protocol.budget, state["next_slot"] + (protocol.budget if max_slots is None else max_slots)
    )
    for slot in range(state["next_slot"], stop):
        generation, index = divmod(slot, protocol.population_size)
        start = (generation - 1) * protocol.population_size
        previous = state["ledger"][start : start + protocol.population_size] if generation else []
        pattern, operator, parents, error = _propose(rng, protocol, template, previous, index)
        row: dict[str, Any] = {
            "slot": slot,
            "generation": generation,
            "index": index,
            "operator": operator,
            "parents": parents,
            "genome_hash": None,
            "cache_key": None,
            "pattern": None,
            "cached": False,
        }
        if pattern is None:
            row.update(status="failed", fitness=None, diagnostics={}, failure=error)
        else:
            genome_hash = canonical_genome_digest(pattern)
            cache_key = _digest({"context": context, "genome": genome_hash})
            row.update(
                genome_hash=genome_hash,
                cache_key=cache_key,
                pattern=pattern.model_dump(mode="json", by_alias=True),
            )
            row["cached"] = cache_key in state["cache"]
            if not row["cached"]:
                state["cache"][cache_key] = _evaluate(evaluator, pattern)
            row.update(copy.deepcopy(state["cache"][cache_key]))
        state["ledger"].append(row)
        state["next_slot"] = slot + 1
        if row["status"] == "evaluated" and (
            state["champion"] is None or _rank(row) > _rank(state["champion"])
        ):
            state["champion"] = copy.deepcopy(row)
    state["rng_state"] = json.loads(json.dumps(rng.getstate()))
    state["checkpoint_hash"] = _digest(state)
    return state
