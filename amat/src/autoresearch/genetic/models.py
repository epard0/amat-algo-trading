"""Inert, immutable patternSchema/0.1.0 contracts and canonical identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

SECONDARY_LOOKBACK = 5
MAX_TREE_DEPTH = 4
MAX_TREE_NODES = 31
PriceField = Literal["open", "high", "low", "close"]
OHLC: tuple[PriceField, ...] = ("open", "high", "low", "close")


class PatternRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, allow_inf_nan=False
    )


class CandleOperand(PatternRecord):
    field: PriceField
    lag: StrictInt = Field(ge=0, le=20)


class CandleRule(PatternRecord):
    left: CandleOperand
    op: Literal["gt", "lt"]
    right: CandleOperand

    def ordered(self) -> tuple[CandleOperand, CandleOperand]:
        """Return operands in strictly-less-than order."""
        return (self.left, self.right) if self.op == "lt" else (self.right, self.left)


class CandleGenome(PatternRecord):
    kind: Literal["candle_conjunction"]
    max_lag: StrictInt = Field(alias="maxLag", ge=0, le=20)
    rules: tuple[CandleRule, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def valid_rules(self) -> Self:
        pairs = [rule.ordered() for rule in self.rules]
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate or equivalent candle rules")
        graph: dict[CandleOperand, set[CandleOperand]] = {}
        for lag in {operand.lag for pair in pairs for operand in pair}:
            if lag > self.max_lag:
                raise ValueError("operand lag exceeds maxLag")
            operands = {field: CandleOperand(field=field, lag=lag) for field in OHLC}
            for field in ("open", "close"):
                graph.setdefault(operands["low"], set()).add(operands[field])
                graph.setdefault(operands[field], set()).add(operands["high"])
        for lower, upper in pairs:
            graph.setdefault(lower, set()).add(upper)
        for lower, upper in pairs:
            # An upper <= ... <= lower path plus lower < upper is impossible.
            pending, visited = [upper], set()
            while pending:
                current = pending.pop()
                if current == lower:
                    raise ValueError("self comparison or contradictory candle inequalities")
                if current not in visited:
                    visited.add(current)
                    pending.extend(graph.get(current, ()))
        # OHLC ordering is nonstrict: high > low is NOT a tautology on flat bars.
        return self


FeatureName = Literal[
    "momentum",
    "volatility",
    "drawdown",
    "volume_ratio",
    "efficiency",
    "normalized_momentum",
    "trend_agreement",
    "trend_acceleration",
    "donchian_position",
    "breakout_distance",
    "breakout_persistence",
    "relative_strength",
    "volume_price",
    "variance_ratio",
    "downside_variance_share",
    "rsi",
    "breadth",
    "volatility_shock",
    "volatility_of_volatility",
    "close_location",
    "opening_gap",
    "window_markout",
    "illiquidity",
    "body_atr",
    "range_atr",
    "upper_wick_fraction",
    "lower_wick_fraction",
]


class FeatureNode(PatternRecord):
    node: Literal["feature"]
    name: FeatureName
    source: str = Field(pattern=r"^(self|[A-Z][A-Z0-9.]{0,9})$")
    lookback: StrictInt = Field(ge=5, le=252)
    lag: StrictInt = Field(ge=0, le=63)
    transform: Literal["time_series_rank", "rolling_zscore"]

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if (
            self.name in {"volatility_shock", "variance_ratio"}
            and self.lookback <= SECONDARY_LOOKBACK
        ):
            raise ValueError("primary lookback must exceed frozen secondary lookback of 5")
        return self


class ConstantNode(PatternRecord):
    node: Literal["constant"]
    value: float = Field(ge=-10, le=10, strict=True)


class UnaryNode(PatternRecord):
    node: Literal["unary"]
    op: Literal["neg", "abs", "tanh"]
    arg: NumericNode


class BinaryNode(PatternRecord):
    node: Literal["binary"]
    op: Literal["add", "sub", "mul", "safe_div", "min", "max"]
    left: NumericNode
    right: NumericNode


NumericNode = Annotated[
    FeatureNode | ConstantNode | UnaryNode | BinaryNode, Field(discriminator="node")
]
UnaryNode.model_rebuild()
BinaryNode.model_rebuild()


class SymbolicGenome(PatternRecord):
    kind: Literal["symbolic_indicator"]
    root: NumericNode
    output_transform: Literal["tanh"] = Field(alias="outputTransform")

    @model_validator(mode="after")
    def bounded_tree(self) -> Self:
        """Depth counts edges from root (depth zero); 4 permits at most 31 nodes."""
        pending: list[tuple[NumericNode, int]] = [(self.root, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if depth > MAX_TREE_DEPTH or count > MAX_TREE_NODES:
                raise ValueError("symbolic tree exceeds depth 4 or node count 31")
            if isinstance(node, UnaryNode):
                pending.append((node.arg, depth + 1))
            elif isinstance(node, BinaryNode):
                pending.extend(((node.left, depth + 1), (node.right, depth + 1)))
        return self


class PatternBinding(PatternRecord):
    role: Literal["feature", "entry_filter", "risk_attenuator"]
    direction: Literal["long_support", "risk_warning"]
    threshold: float = Field(ge=-1, le=1, strict=True)
    max_effect: float = Field(alias="maxEffect", gt=0, le=0.25, strict=True)


class PatternDefinition(PatternRecord):
    schema_version: Literal["patternSchema/0.1.0"] = Field(alias="schemaVersion")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    timeframe: Literal["1d", "1w", "1mo", "1h", "15m", "5m"]
    price_basis: Literal["corporate_action_adjusted", "spot_raw"] = Field(alias="priceBasis")
    availability: Literal["completed_bar_close"]
    missing_policy: Literal["unavailable"] = Field(alias="missingPolicy")
    genome: Annotated[CandleGenome | SymbolicGenome, Field(discriminator="kind")]
    binding: PatternBinding


def parse_pattern(value: str | bytes | Mapping[str, Any]) -> PatternDefinition:
    """Decode inert JSON or a mapping; reject unknown fields and nonfinite numbers."""
    if isinstance(value, (str, bytes)):
        return PatternDefinition.model_validate_json(value)
    return PatternDefinition.model_validate(value)


def _canonical_node(node: NumericNode) -> dict[str, Any]:
    result = node.model_dump(by_alias=True)
    if isinstance(node, UnaryNode):
        result["arg"] = _canonical_node(node.arg)
    elif isinstance(node, BinaryNode):
        left, right = _canonical_node(node.left), _canonical_node(node.right)
        if node.op in {"add", "mul", "min", "max"}:
            left, right = sorted((left, right), key=lambda item: json.dumps(item, sort_keys=True))
        result.update(left=left, right=right)
    return result


def canonical_genome_digest(pattern: PatternDefinition) -> str:
    """Hash operational semantics, excluding human name (evidence is a separate record).

    Includes schema, timeframe, basis, availability, missing policy, binding and
    declared maxLag. Normalizes rule order and reversed comparisons. Symbolic
    commutative operands are sorted without reassociation or constant folding.
    This is a syntax identity, not a claim of equivalent observed match vectors.
    """
    payload = pattern.model_dump(by_alias=True, exclude={"name"})
    if isinstance(pattern.genome, CandleGenome):
        rules = [
            {"left": low.model_dump(), "op": "lt", "right": high.model_dump()}
            for low, high in (rule.ordered() for rule in pattern.genome.rules)
        ]
        payload["genome"]["rules"] = sorted(
            rules, key=lambda item: json.dumps(item, sort_keys=True)
        )
    else:
        payload["genome"]["root"] = _canonical_node(pattern.genome.root)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()
