"Executable agent controls; evaluation data and fill assumptions are not strategy knobs."

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from llmrouter.contracts import Record

MAX_SUPPORT_WEIGHT = 0.25
MAX_PARAMETER_EDITS = 2

HORIZONS = (1, 5, 10, 15, 30, 45, 60)
Horizon = Literal[1, 5, 10, 15, 30, 45, 60]


class Term(Record):
    feature: Literal[
        "momentum",
        "mean_reversion",
        "channel_position",
        "breakout",
        "efficiency",
        "volatility",
        "volume_surprise",
        "vwap_distance",
        "candle_body",
        "range_expansion",
        "trend_agreement",
        "acceleration",
    ]
    lookback: int = Field(default=20, ge=2, le=120)
    lag: int = Field(default=0, ge=0, le=30)
    smoothing: int = Field(default=1, ge=1, le=15)
    weight: float = Field(default=1, ge=-5, le=5)
    center: float = Field(default=0, ge=-5, le=5)
    scale: float = Field(default=1, ge=0.01, le=10)
    transform: Literal["identity", "absolute", "signed_square"] = "identity"


class Interaction(Record):
    left: int = Field(ge=0)
    right: int = Field(ge=0)
    operation: Literal["product", "difference", "minimum", "maximum", "gate"] = "product"
    weight: float = Field(default=1, ge=-5, le=5)


class Recipe(Record):
    "Executable recipe schema; research additionally enforces baseline extension constraints."

    style: str = Field(default="trend_confirmation", min_length=3, max_length=80)
    symbols: tuple[str, ...] = Field(
        default=("SPY", "QQQ", "IWM", "GLD"), min_length=1, max_length=4
    )
    decision_minutes: Horizon = 5
    max_hold_minutes: Horizon = 15
    terms: tuple[Term, ...] = Field(min_length=1, max_length=8)
    interactions: tuple[Interaction, ...] = Field(default=(), max_length=8)
    combine: Literal["weighted", "minimum", "maximum", "product"] = "weighted"
    direction: Literal["long_only", "short_only", "both"] = "long_only"
    entry_threshold: float = Field(default=0.3, ge=0.01, le=5)
    exit_threshold: float = Field(default=0, ge=-5, le=5)
    confirmation_bars: int = Field(default=1, ge=1, le=5)
    min_hold_minutes: int = Field(default=1, ge=1, le=60)
    cooldown_minutes: int = Field(default=0, ge=0, le=60)
    stop_loss_bps: float = Field(default=100, ge=5, le=500)
    take_profit_bps: float = Field(default=150, ge=5, le=1000)
    trailing_stop_bps: float | None = Field(default=None, ge=5, le=500)
    start_minute: int = Field(default=120, ge=2, le=300)
    end_minute: int = Field(default=380, ge=10, le=388)
    allocation_per_asset: float = Field(default=0.2, ge=0.01, le=0.25)
    max_trades_per_asset_day: int = Field(default=6, ge=1, le=100)
    daily_loss_limit_bps: float = Field(default=100, ge=5, le=500)

    @model_validator(mode="after")
    def coherent(self) -> Recipe:
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("duplicate symbols")
        if self.start_minute >= self.end_minute:
            raise ValueError("entry window must be ordered")
        if self.min_hold_minutes > self.max_hold_minutes:
            raise ValueError("minimum holding time exceeds maximum")
        if self.exit_threshold >= self.entry_threshold:
            raise ValueError("exit threshold must be below entry threshold")
        if any(max(item.left, item.right) >= len(self.terms) for item in self.interactions):
            raise ValueError("interaction references missing term")
        if not any(t.weight for t in self.terms) and not any(i.weight for i in self.interactions):
            raise ValueError("recipe has no active signal")
        return self


class Proposal(Record):
    parent_candidate: str = Field(pattern=r"^[a-f0-9]{64}$")
    hypothesis: str = Field(min_length=10, max_length=1200)
    mechanism: str = Field(min_length=10, max_length=1000)
    falsification: str = Field(min_length=10, max_length=1000)
    change_from_best: str = Field(min_length=10, max_length=1200)
    recipe: Recipe


def baseline() -> Recipe:
    # E73-inspired interaction, explicitly a NEW intraday benchmark, not E73 itself.
    return Recipe(
        style="E73_inspired_intraday_agreement",
        terms=(
            Term(feature="trend_agreement", lookback=76, weight=0),
            Term(feature="channel_position", lookback=76, weight=0),
        ),
        interactions=(Interaction(left=0, right=1),),
    )


def validate_extension(reference: Recipe, parent: Recipe, candidate: Recipe) -> None:
    """Keep original executable anchors across all generations, not just parent IDs."""
    protected = (
        "symbols",
        "direction",
        "combine",
        "allocation_per_asset",
        "max_trades_per_asset_day",
        "daily_loss_limit_bps",
        "interactions",
    )
    if any(getattr(candidate, key) != getattr(reference, key) for key in protected):
        raise ValueError("intraday extension changes protected baseline controls or interactions")
    anchors = len(reference.terms)
    if candidate.terms[:anchors] != reference.terms:
        raise ValueError("intraday extension must retain the original signal terms")
    if len(candidate.terms) > anchors + 2 or any(
        abs(term.weight) > MAX_SUPPORT_WEIGHT for term in candidate.terms[anchors:]
    ):
        raise ValueError("allow at most two supporting terms with absolute weight <= 0.25")
    from autoresearch.extensions import one_edit  # noqa: PLC0415 - shared structural check

    if not one_edit(parent.terms, candidate.terms):
        raise ValueError("change only one supporting term per experiment")
    changes = [
        key
        for key in Recipe.model_fields
        if key != "style" and getattr(parent, key) != getattr(candidate, key)
    ]
    if len(changes) > MAX_PARAMETER_EDITS or ("terms" in changes and len(changes) > 1):
        raise ValueError("change one supporting term or at most two scalar parameters")
