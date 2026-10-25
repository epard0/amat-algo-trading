"""Bounded, reproducible genetic programming over dimensionless numerical trees.

The engine only proposes inert formulas. Fitness callbacks must consume training
observations only. There is no promotion, execution, or validation access here.
"""

from __future__ import annotations

import copy
import json
import math
import random
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Self

from autoresearch.genetic.models import (
    FeatureNode,
    PatternDefinition,
    canonical_genome_digest,
    parse_pattern,
)
from autoresearch.genetic.search import (
    EvaluationResult,
    SearchProtocol,
    _digest,
    _evaluate,
    _rank,
    _tuples,
)

SYMBOLIC_SEARCH_VERSION = "symbolic-search/0.1.0"
UNARY_OPERATORS = ("neg", "abs", "tanh")
BINARY_OPERATORS = ("add", "sub", "mul", "safe_div", "min", "max")
MAX_DEPTH = 4
MAX_NODES = 31
CONSTANT_LIMIT = 10
MAX_TERMINALS = 128
MAX_CONSTANTS = 32
UNARY_MIN_NODES = 2
BINARY_MIN_NODES = 3
LEAF_PROBABILITY = 0.35
CONSTANT_PROBABILITY = 0.25


def _is_frozen_tuple(value: object) -> bool:
    """Validate untyped runtime callers without trusting dataclass annotations."""
    return isinstance(value, tuple)


@dataclass(frozen=True)
class SymbolicTerminal:
    name: str
    lookback: int = 21
    lag: int = 0
    transform: str = "time_series_rank"
    source: str = "self"

    def __post_init__(self) -> None:
        self.feature_node()

    def feature_node(self) -> FeatureNode:
        return FeatureNode.model_validate({"node": "feature", **asdict(self)})


DEFAULT_TERMINALS = (
    SymbolicTerminal("efficiency"),
    SymbolicTerminal("volume_ratio"),
    SymbolicTerminal("volatility_shock", lookback=63),
)


@dataclass(frozen=True)
class SymbolicSearchProtocol:
    """Frozen grammar and adaptive budget; generations excludes initial population.

    All operators take dimensionless inputs. Constants are a finite allowed set;
    constant mutation selects another member rather than introducing unregistered
    values. Named sources must additionally be checked against the frozen dataset
    by the caller before computing any outcomes.
    """

    population_size: int = 64
    generations: int = 10
    seed: int = 0
    max_depth: int = 4
    max_nodes: int = 31
    feature_terminals: tuple[SymbolicTerminal, ...] = DEFAULT_TERMINALS
    constants: tuple[float, ...] = (-1.0, -0.5, 0.0, 0.5, 1.0)
    unary_ops: tuple[str, ...] = UNARY_OPERATORS
    binary_ops: tuple[str, ...] = BINARY_OPERATORS
    elite_count: int = 2
    tournament_size: int = 3
    crossover_probability: float = 0.6
    mutation_probability: float = 0.4
    fresh_probability: float = 0.1
    max_generation_attempts: int = 64
    method: str = "genetic"

    def __post_init__(self) -> None:
        # Reuse the existing explicit budget bounds without altering candle state.
        SearchProtocol(
            population_size=self.population_size,
            generations=self.generations,
            seed=self.seed,
            elite_count=self.elite_count,
            tournament_size=self.tournament_size,
            crossover_probability=self.crossover_probability,
            mutation_probability=self.mutation_probability,
            fresh_probability=self.fresh_probability,
            max_generation_attempts=self.max_generation_attempts,
            method=self.method,
        )
        if type(self.max_depth) is not int or not 0 <= self.max_depth <= MAX_DEPTH:
            raise ValueError("symbolic max_depth must be an integer between 0 and 4")
        if type(self.max_nodes) is not int or not 1 <= self.max_nodes <= MAX_NODES:
            raise ValueError("symbolic max_nodes must be an integer between 1 and 31")
        if (
            not _is_frozen_tuple(self.feature_terminals)
            or not 1 <= len(self.feature_terminals) <= MAX_TERMINALS
        ):
            raise ValueError(
                "feature_terminals must be a nonempty frozen tuple of at most 128 terminals"
            )
        if any(not isinstance(item, SymbolicTerminal) for item in self.feature_terminals):
            raise ValueError("feature_terminals must contain SymbolicTerminal values")
        if len(set(self.feature_terminals)) != len(self.feature_terminals):
            raise ValueError("duplicate feature terminals")
        if not _is_frozen_tuple(self.constants) or len(self.constants) > MAX_CONSTANTS:
            raise ValueError("constants must be a frozen tuple of at most 32 values")
        if any(
            type(value) not in (int, float)
            or not math.isfinite(value)
            or abs(value) > CONSTANT_LIMIT
            for value in self.constants
        ):
            raise ValueError("constants must be finite numerical values in [-10,10]")
        if len(set(self.constants)) != len(self.constants):
            raise ValueError("duplicate constants")
        for operators, allowed in (
            (self.unary_ops, UNARY_OPERATORS),
            (self.binary_ops, BINARY_OPERATORS),
        ):
            if (
                not _is_frozen_tuple(operators)
                or len(set(operators)) != len(operators)
                or not set(operators).issubset(allowed)
            ):
                raise ValueError("operators must be frozen unique allowed operator tuples")

    @property
    def budget(self) -> int:
        return self.population_size * (self.generations + 1)

    def terminals(self) -> tuple[FeatureNode, ...]:
        return tuple(item.feature_node() for item in self.feature_terminals)

    def to_dict(self) -> dict[str, Any]:
        """Canonical JSON shape, including lists rather than Python-only tuples."""
        result: dict[str, Any] = json.loads(json.dumps(asdict(self), allow_nan=False))
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        values = dict(value)
        if "feature_terminals" in values:
            values["feature_terminals"] = tuple(
                SymbolicTerminal(**item) for item in values["feature_terminals"]
            )
        for name in ("constants", "unary_ops", "binary_ops"):
            if name in values:
                values[name] = tuple(values[name])
        return cls(**values)


def node_paths(root: dict[str, Any], path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """All typed subtree locations, including the complete tree at the root."""
    result = [path]
    children = (
        ("arg",)
        if root["node"] == "unary"
        else ("left", "right")
        if root["node"] == "binary"
        else ()
    )
    for child in children:
        result.extend(node_paths(root[child], (*path, child)))
    return result


def _at(root: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    for part in path:
        root = root[part]
    return root


def _replace(
    root: dict[str, Any], path: tuple[str, ...], replacement: dict[str, Any]
) -> dict[str, Any]:
    if not path:
        return copy.deepcopy(replacement)
    result = copy.deepcopy(root)
    _at(result, path[:-1])[path[-1]] = copy.deepcopy(replacement)
    return result


def tree_size(root: dict[str, Any]) -> tuple[int, int]:
    paths = node_paths(root)
    return max(map(len, paths)), len(paths)


def validate_symbolic_pattern(  # noqa: PLR0912 - each typed grammar branch is checked explicitly
    pattern: PatternDefinition, protocol: SymbolicSearchProtocol
) -> None:
    """Recheck operational type, grammar membership, caps and nonconstant syntax."""
    if (pattern.timeframe, pattern.price_basis) != ("1d", "corporate_action_adjusted"):
        raise ValueError("initial symbolic search requires daily adjusted inputs")
    if pattern.binding.role != "feature" or pattern.binding.direction != "long_support":
        raise ValueError("initial symbolic search requires long-support feature binding")
    if pattern.genome.kind != "symbolic_indicator":
        raise ValueError("symbolic search requires a symbolic_indicator genome")
    root = pattern.genome.root.model_dump(mode="json")
    depth, count = tree_size(root)
    if depth > protocol.max_depth or count > protocol.max_nodes:
        raise ValueError("symbolic candidate exceeds registered structural caps")
    features = set(protocol.terminals())
    has_feature = False
    for path in node_paths(root):
        node = _at(root, path)
        if node["node"] == "feature":
            if FeatureNode.model_validate(node) not in features:
                raise ValueError("feature terminal outside frozen grammar")
            has_feature = True
        elif node["node"] == "constant":
            if node["value"] not in protocol.constants:
                raise ValueError("constant outside frozen grammar")
        elif node["node"] == "unary":
            if node["op"] not in protocol.unary_ops:
                raise ValueError("unary operator outside frozen grammar")
        elif node["op"] not in protocol.binary_ops:
            raise ValueError("binary operator outside frozen grammar")
    if not has_feature:
        raise ValueError("constant-only symbolic candidate")


def _grow(
    rng: random.Random, protocol: SymbolicSearchProtocol, depth: int, nodes: int
) -> dict[str, Any]:
    operators = (["unary"] if protocol.unary_ops and nodes >= UNARY_MIN_NODES else []) + (
        ["binary"] if protocol.binary_ops and nodes >= BINARY_MIN_NODES else []
    )
    if depth == 0 or not operators or rng.random() < LEAF_PROBABILITY:
        if protocol.constants and rng.random() < CONSTANT_PROBABILITY:
            return {"node": "constant", "value": float(rng.choice(protocol.constants))}
        return rng.choice(protocol.terminals()).model_dump(mode="json")
    kind = rng.choice(operators)
    if kind == "unary":
        return {
            "node": kind,
            "op": rng.choice(protocol.unary_ops),
            "arg": _grow(rng, protocol, depth - 1, nodes - 1),
        }
    left_budget = rng.randint(1, nodes - 2)
    left = _grow(rng, protocol, depth - 1, left_budget)
    right_budget = nodes - 1 - tree_size(left)[1]
    return {
        "node": kind,
        "op": rng.choice(protocol.binary_ops),
        "left": left,
        "right": _grow(rng, protocol, depth - 1, right_budget),
    }


def _mutate(
    root: dict[str, Any], rng: random.Random, protocol: SymbolicSearchProtocol
) -> tuple[dict[str, Any], dict[str, Any]]:
    paths = node_paths(root)
    operator_paths = [path for path in paths if _at(root, path)["node"] in {"unary", "binary"}]
    constant_paths = [path for path in paths if _at(root, path)["node"] == "constant"]
    modes = (
        ["subtree"]
        + (["operator"] if operator_paths else [])
        + (["constant"] if constant_paths and len(protocol.constants) > 1 else [])
    )
    mode = rng.choice(modes)
    path = rng.choice(
        operator_paths if mode == "operator" else constant_paths if mode == "constant" else paths
    )
    old = _at(root, path)
    replacement = copy.deepcopy(old)
    if mode == "operator":
        allowed = protocol.unary_ops if old["node"] == "unary" else protocol.binary_ops
        choices = [op for op in allowed if op != old["op"]]
        if choices:
            replacement["op"] = rng.choice(choices)
        else:
            mode = "subtree"
    elif mode == "constant":
        replacement["value"] = float(
            rng.choice([value for value in protocol.constants if value != old["value"]])
        )
    if mode == "subtree":
        remaining = protocol.max_nodes - tree_size(root)[1] + tree_size(old)[1]
        replacement = _grow(rng, protocol, protocol.max_depth - len(path), max(1, remaining))
    return _replace(root, path, replacement), {
        "mutation": mode,
        "path": list(path),
        "before": old,
        "after": replacement,
    }


def _propose(
    rng: random.Random,
    protocol: SymbolicSearchProtocol,
    template: dict[str, Any],
    previous: list[dict[str, Any]],
    index: int,
) -> tuple[PatternDefinition | None, str, list[str], dict[str, Any], int, str | None]:
    successful = sorted(
        (row for row in previous if row["status"] == "evaluated"), key=_rank, reverse=True
    )
    if protocol.method == "genetic" and index < min(protocol.elite_count, len(successful)):
        elite = successful[index]
        return parse_pattern(elite["pattern"]), "elite", [elite["genome_hash"]], {}, 0, None
    failure = None
    details: dict[str, Any] = {}
    parents: list[str] = []
    for attempt in range(protocol.max_generation_attempts):
        parents, details = [], {}
        operator = "random"
        root = _grow(rng, protocol, protocol.max_depth, protocol.max_nodes)
        if (
            successful
            and protocol.method == "genetic"
            and rng.random() >= protocol.fresh_probability
        ):
            first = max(rng.choices(successful, k=protocol.tournament_size), key=_rank)
            parents.append(first["genome_hash"])
            root = copy.deepcopy(first["pattern"]["genome"]["root"])
            operator = "reproduction"
            if rng.random() < protocol.crossover_probability:
                donor = max(rng.choices(successful, k=protocol.tournament_size), key=_rank)
                donor_root = donor["pattern"]["genome"]["root"]
                recipient_path, donor_path = (
                    rng.choice(node_paths(root)),
                    rng.choice(node_paths(donor_root)),
                )
                root = _replace(root, recipient_path, _at(donor_root, donor_path))
                parents.append(donor["genome_hash"])
                details["crossover"] = {
                    "recipient_path": list(recipient_path),
                    "donor_path": list(donor_path),
                }
                operator = "subtree_crossover"
            if rng.random() < protocol.mutation_probability:
                # Oversized crossover offspring must be rejected before mutations
                # can receive a negative remaining depth or allocation budget.
                depth, count = tree_size(root)
                if depth > protocol.max_depth or count > protocol.max_nodes:
                    failure = "crossover exceeds registered structural caps"
                    continue
                root, mutation = _mutate(root, rng, protocol)
                details.update(mutation)
                operator += "+" + mutation["mutation"] + "_mutation"
        candidate = copy.deepcopy(template)
        candidate["genome"] = {
            "kind": "symbolic_indicator",
            "root": root,
            "outputTransform": "tanh",
        }
        try:
            pattern = parse_pattern(candidate)
            validate_symbolic_pattern(pattern, protocol)
            return pattern, operator, parents, details, attempt, None
        except ValueError as exc:
            failure = str(exc)
    return None, "generation_exhausted", parents, details, protocol.max_generation_attempts, failure


def _restore(
    checkpoint: dict[str, Any], protocol: SymbolicSearchProtocol, context: str
) -> dict[str, Any]:
    state = copy.deepcopy(checkpoint)
    if state.pop("checkpoint_hash", None) != _digest(state):
        raise ValueError("Checkpoint payload checksum mismatch")
    if state.get("version") != SYMBOLIC_SEARCH_VERSION or state.get("context_hash") != context:
        raise ValueError("Checkpoint version or search context mismatch")
    if state.get("protocol") != protocol.to_dict():
        raise ValueError("Checkpoint protocol mismatch")
    if state["next_slot"] != len(state["ledger"]) or not 0 <= state["next_slot"] <= protocol.budget:
        raise ValueError("Invalid checkpoint ledger length")
    return state


def run_symbolic_search(
    protocol: SymbolicSearchProtocol,
    evaluator: Callable[[PatternDefinition], EvaluationResult],
    *,
    pattern_template: Mapping[str, Any],
    evaluator_hash: str,
    data_hash: str,
    checkpoint: dict[str, Any] | None = None,
    max_slots: int | None = None,
) -> dict[str, Any]:
    """Search training-only fitness with exact JSON checkpoint resumption.

    Every slot including cached elites, failed evaluations and exhausted proposals
    counts toward population_size*(generations+1). The caller's evaluator hash must
    bind evaluator implementation and scoring configuration; data hash must bind
    actual training arrays, dates, universe and feature registry semantics.
    """
    if not evaluator_hash or not data_hash:
        raise ValueError("Evaluator and data hashes are required")
    if max_slots is not None and (type(max_slots) is not int or max_slots < 0):
        raise ValueError("max_slots must be a nonnegative integer")
    template = copy.deepcopy(dict(pattern_template))
    template.pop("genome", None)
    # Fail invalid metadata before allocating search slots, independently of RNG.
    metadata_probe = parse_pattern(
        {
            **template,
            "genome": {
                "kind": "symbolic_indicator",
                "root": protocol.terminals()[0].model_dump(mode="json"),
                "outputTransform": "tanh",
            },
        }
    )
    validate_symbolic_pattern(metadata_probe, protocol)
    context = _digest(
        {
            "version": SYMBOLIC_SEARCH_VERSION,
            "protocol": protocol.to_dict(),
            "template": template,
            "evaluator": evaluator_hash,
            "data": data_hash,
        }
    )
    rng = random.Random(protocol.seed)  # noqa: S311 - deterministic scientific search.
    if checkpoint is None:
        state: dict[str, Any] = {
            "version": SYMBOLIC_SEARCH_VERSION,
            "context_hash": context,
            "protocol": protocol.to_dict(),
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
        pattern, operator, parents, details, retries, error = _propose(
            rng, protocol, template, previous, index
        )
        row: dict[str, Any] = {
            "slot": slot,
            "generation": generation,
            "index": index,
            "operator": operator,
            "operator_details": details,
            "retry_count": retries,
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
