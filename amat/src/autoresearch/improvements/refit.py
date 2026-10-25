"""Bounded minute GP and asynchronous prefix-only refitting with atomic activation."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

import numpy as np

MIN_POPULATION = 4
MAX_POPULATION = 64
MAX_GENERATIONS = 8
MIN_LABELS = 100
MAX_TREE_DEPTH = 4
MAX_TREE_NODES = 15
LEAF_PROBABILITY = 0.4


@dataclass(frozen=True)
class Expression:
    """A small numeric grammar, evaluated without Python eval or deserialization."""

    op: str
    terminal: int = 0
    left: Expression | None = None
    right: Expression | None = None

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.op == "terminal":
            if not 0 <= self.terminal < x.shape[-1]:
                raise ValueError("unknown feature terminal")
            return x[..., self.terminal]
        if self.left is None:
            raise ValueError("missing expression input")
        a = self.left.predict(x)
        if self.op == "neg":
            return -a
        if self.op == "tanh":
            return np.tanh(a)
        if self.right is None:
            raise ValueError("missing binary input")
        b = self.right.predict(x)
        if self.op == "add":
            return np.tanh(a + b)
        if self.op == "sub":
            return np.tanh(a - b)
        if self.op == "mul":
            return a * b
        raise ValueError("unregistered expression operation")

    @property
    def complexity(self) -> int:
        return (
            1
            + (self.left.complexity if self.left else 0)
            + (self.right.complexity if self.right else 0)
        )


@dataclass(frozen=True)
class TrainingRow:
    observed: dt.datetime
    mature: dt.datetime
    features: tuple[float, ...]
    outcome: float
    symbol: str


@dataclass(frozen=True)
class ModelArtifact:
    expression: Expression
    trained_through: dt.datetime
    last_mature_label: dt.datetime
    data_sha256: str
    seed: int
    validation_score: float
    screening_method: str = "purged forward-return utility proxy; portfolio replay required"
    feature_version: str = "minute-prefix-features/1"

    @property
    def identity(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), default=str, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()


def _row_json(row: TrainingRow) -> str:
    # Instance identity distinguishes equal-valued JSON spellings (-0.0/0.0,
    # offset-equivalent datetimes). Cache keys retain their immutable row.
    return _cached_row_json(id(row), row)


@lru_cache(maxsize=200000)
def _cached_row_json(_identity: int, row: TrainingRow) -> str:
    return json.dumps(
        {
            "observed": row.observed,
            "mature": row.mature,
            "features": row.features,
            "outcome": row.outcome,
            "symbol": row.symbol,
        },
        default=str,
        sort_keys=True,
        allow_nan=False,
    )


def _utility(expression: Expression, rows: list[TrainingRow], cost_bps: float) -> float:
    x = np.array([r.features for r in rows])
    y = np.array([r.outcome for r in rows])
    weight = np.maximum(expression.predict(x), 0)
    # This is only a label-based screen, not a sum of independent portfolio trades.
    return float(np.mean(weight * (y - 2 * cost_bps / 10000)) - 0.000001 * expression.complexity)


def fit_model(
    rows: tuple[TrainingRow, ...],
    cutoff: dt.datetime,
    *,
    seed: int = 0,
    population: int = 16,
    generations: int = 2,
    cost_bps: float = 2,
) -> ModelArtifact:
    if (
        cutoff.tzinfo is None
        or not MIN_POPULATION <= population <= MAX_POPULATION
        or not 1 <= generations <= MAX_GENERATIONS
    ):
        raise ValueError("invalid bounded fitting contract")
    eligible = sorted(
        (r for r in rows if r.mature <= cutoff and r.observed < r.mature),
        key=lambda r: (r.observed, r.symbol),
    )
    if len(eligible) < MIN_LABELS:
        raise ValueError("insufficient mature labels")
    if not np.isfinite(np.array([(*r.features, r.outcome) for r in eligible])).all():
        raise ValueError("nonfinite training observation")
    times = sorted({r.observed for r in eligible})
    split = times[max(1, int(len(times) * 0.7))]
    train = [r for r in eligible if r.mature < split]
    validation = [r for r in eligible if r.observed >= split]
    if not train or not validation:
        raise ValueError("purged training/validation interval is empty")
    train_x = np.array([r.features for r in train])
    train_y = np.array([r.outcome for r in train])

    def train_utility(expression: Expression) -> float:
        weight = np.maximum(expression.predict(train_x), 0)
        return float(
            np.mean(weight * (train_y - 2 * cost_bps / 10000)) - 0.000001 * expression.complexity
        )

    rng = np.random.default_rng(seed)
    candidates = [Expression("terminal", i) for i in range(4)]
    candidates += [Expression("neg", left=e) for e in candidates.copy()]

    def random_tree(depth: int) -> Expression:
        if depth == 0 or rng.random() < LEAF_PROBABILITY:
            return Expression("terminal", int(rng.integers(0, 4)))
        op = str(rng.choice(["add", "sub", "mul", "neg", "tanh"]))
        return Expression(
            op,
            left=random_tree(depth - 1),
            right=None if op in {"neg", "tanh"} else random_tree(depth - 1),
        )

    candidates += [random_tree(2) for _ in range(max(0, population - len(candidates)))]
    for _ in range(generations):
        candidates = sorted(candidates, key=train_utility, reverse=True)
        elites = candidates[:4]
        candidates = elites + [
            Expression(
                str(rng.choice(["add", "sub", "mul"])),
                left=elites[int(rng.integers(0, len(elites)))],
                right=random_tree(1),
            )
            for _ in range(population - len(elites))
        ]
        candidates = [e if e.complexity <= MAX_TREE_NODES else random_tree(2) for e in candidates]
    best = max(candidates, key=train_utility)
    # Canonical JSON bytes are identical to json.dumps([asdict(r), ...]).
    # Immutable rows recur across rolling prefixes; serialize each row once.
    data_hash = hashlib.sha256(
        ("[" + ", ".join(_row_json(r) for r in eligible) + "]").encode()
    ).hexdigest()
    return ModelArtifact(
        best,
        cutoff,
        max(r.mature for r in eligible),
        data_hash,
        seed,
        _utility(best, validation, cost_bps),
    )


class RefitCoordinator:
    """One outstanding fit; the incumbent stays active until a later boundary."""

    def __init__(
        self,
        interval: int = 250,
        *,
        max_fits: int = 64,
        max_age_seconds: int = 600,
        cost_bps: float = 2,
    ) -> None:
        if interval < 1 or max_fits < 1 or max_age_seconds < 1:
            raise ValueError("invalid refit budget")
        self.interval = interval
        self.max_fits = max_fits
        self.max_age_seconds = max_age_seconds
        self.cost_bps = cost_bps
        self.count = 0
        self.fit_count = 0
        self.last_minute: dt.datetime | None = None
        self.active: ModelArtifact | None = None
        self.pending: Future[ModelArtifact] | None = None
        self.trigger: dt.datetime | None = None
        self.training_rows: tuple[TrainingRow, ...] = ()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.events: list[dict[str, Any]] = []

    def observe(self, stamp: dt.datetime) -> bool:
        if stamp.tzinfo is None:
            raise ValueError("refit minute must be aware")
        if self.last_minute is not None and stamp <= self.last_minute:
            return False
        self.last_minute = stamp
        self.count += 1
        return self.count % self.interval == 0 and self.fit_count < self.max_fits

    def submit(self, rows: tuple[TrainingRow, ...], cutoff: dt.datetime, *, seed: int = 0) -> bool:
        if self.pending is not None or self.fit_count >= self.max_fits:
            self.events.append({"event": "coalesced_or_budgeted", "at": str(cutoff)})
            return False
        self.fit_count += 1
        self.trigger = cutoff
        self.training_rows = rows
        self.pending = self.pool.submit(fit_model, rows, cutoff, seed=seed, cost_bps=self.cost_bps)
        self.events.append({"event": "fit_started", "at": str(cutoff), "seed": seed})
        return True

    def activate(self, now: dt.datetime) -> bool:
        if (
            self.pending is None
            or not self.pending.done()
            or self.trigger is None
            or now <= self.trigger
        ):
            return False
        future = self.pending
        self.pending = None
        try:
            candidate = future.result()
        except (ValueError, RuntimeError) as exc:
            self.events.append({"event": "fit_failed", "at": str(now), "reason": str(exc)})
            return False
        self.events.append(
            {
                "event": "fit_completed",
                "at": str(now),
                "model_id": candidate.identity,
                "artifact": asdict(candidate),
            }
        )
        if (now - self.trigger).total_seconds() > self.max_age_seconds:
            self.events.append({"event": "fit_expired", "at": str(now)})
            return False
        if candidate.validation_score <= 0:
            self.events.append({"event": "fit_rejected", "at": str(now)})
            return False
        if self.active is not None:
            mature = sorted(
                (r for r in self.training_rows if r.mature <= self.trigger),
                key=lambda r: (r.observed, r.symbol),
            )
            times = sorted({r.observed for r in mature})
            split = times[max(1, int(len(times) * 0.7))]
            validation = [r for r in mature if r.observed >= split]
            if candidate.validation_score <= _utility(
                self.active.expression, validation, self.cost_bps
            ):
                self.events.append({"event": "incumbent_retained", "at": str(now)})
                return False
            # Compare both formulas on the same new pre-activation validation
            # slice in the replay coordinator, rather than comparing scores
            # measured on different historical windows.
            self.events.append(
                {"event": "candidate_ready", "at": str(now), "model_id": candidate.identity}
            )
        self.active = candidate
        self.events.append({"event": "activated", "at": str(now), "model_id": candidate.identity})
        return True

    def checkpoint(self) -> dict[str, Any]:
        return {
            "schema": "minute-refit-state/1",
            "count": self.count,
            "fit_count": self.fit_count,
            "last_minute": str(self.last_minute) if self.last_minute else None,
            "active_id": self.active.identity if self.active else None,
            "pending_abandoned_on_restart": self.pending is not None,
            "interval": self.interval,
            "max_fits": self.max_fits,
            "active_artifact": json.loads(json.dumps(asdict(self.active), default=str))
            if self.active
            else None,
        }

    def restore_counter(self, checkpoint: dict[str, Any]) -> None:
        if checkpoint.get("schema") != "minute-refit-state/1" or self.count or self.pending:
            raise ValueError("invalid counter restore")
        self.count = int(checkpoint["count"])
        self.fit_count = int(checkpoint["fit_count"])
        if min(self.count, self.fit_count) < 0:
            raise ValueError("negative restored counters")
        self.last_minute = (
            dt.datetime.fromisoformat(checkpoint["last_minute"])
            if checkpoint["last_minute"]
            else None
        )
        if (
            checkpoint.get("interval") != self.interval
            or checkpoint.get("max_fits") != self.max_fits
        ):
            raise ValueError("refit contract changed on restart")
        if checkpoint.get("active_id"):
            self.active = restore_artifact(checkpoint["active_artifact"], checkpoint["active_id"])
        if checkpoint.get("pending_abandoned_on_restart"):
            self.events.append({"event": "pending_fit_abandoned_on_restart"})

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)


def expression_from_dict(value: dict[str, Any], depth: int = 0) -> Expression:
    """Validate numeric artifacts instead of evaluating serialized Python code."""
    if depth > MAX_TREE_DEPTH or set(value) - {"op", "terminal", "left", "right"}:
        raise ValueError("invalid expression artifact")
    op = value.get("op")
    if op not in {"terminal", "neg", "tanh", "add", "sub", "mul"}:
        raise ValueError("unsupported expression operation")
    terminal = value.get("terminal", 0)
    if type(terminal) is not int or not 0 <= terminal < MIN_POPULATION:
        raise ValueError("invalid terminal")
    left = expression_from_dict(value["left"], depth + 1) if value.get("left") is not None else None
    right = (
        expression_from_dict(value["right"], depth + 1) if value.get("right") is not None else None
    )
    if (
        (op == "terminal" and (left is not None or right is not None))
        or (op in {"neg", "tanh"} and (left is None or right is not None))
        or (op in {"add", "sub", "mul"} and (left is None or right is None))
    ):
        raise ValueError("invalid expression arity")
    result = Expression(op, terminal, left, right)
    if result.complexity > MAX_TREE_NODES:
        raise ValueError("expression complexity exceeded")
    return result


def restore_artifact(value: dict[str, Any], identity: str) -> ModelArtifact:
    result = ModelArtifact(
        expression_from_dict(value["expression"]),
        dt.datetime.fromisoformat(value["trained_through"]),
        dt.datetime.fromisoformat(value["last_mature_label"]),
        value["data_sha256"],
        value["seed"],
        value["validation_score"],
        value["screening_method"],
        value["feature_version"],
    )
    if (
        result.identity != identity
        or result.trained_through.tzinfo is None
        or result.last_mature_label.tzinfo is None
        or result.last_mature_label > result.trained_through
        or result.feature_version != "minute-prefix-features/1"
        or not np.isfinite(result.validation_score)
    ):
        raise ValueError("changed or unsupported model artifact")
    return result
