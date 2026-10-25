"""Executable starting hypotheses; agents may author other bounded compositions."""

from __future__ import annotations

from autoresearch.contracts import Proposal, ResearchIntent
from autoresearch.specs import (
    FeatureSpec,
    PatternCondition,
    PatternSpec,
    RouterSpec,
    SignalSpec,
    SignalTerm,
)


def router_proposals() -> list[Proposal]:
    proposals = []
    for state_kind, floor, ceiling in (("efficiency", 0.15, 0.45), ("breadth", 0.35, 0.65)):
        proposals.append(
            Proposal(
                hypothesis=f"Test {state_kind} routing between trend and guarded pullback signals.",
                stage="route",
                research=ResearchIntent(
                    mechanism=(
                        "Persistent participation favors continuation; weaker structure may favor "
                        "pullback entries in positive trends."
                    ),
                    falsification=(
                        "The same sleeves with equal or pre-anchor mean weights should "
                        "underperform causal routing after costs."
                    ),
                ),
                signal=SignalSpec(
                    terms=(
                        SignalTerm(feature=FeatureSpec(kind="normalized_momentum", lookback=126)),
                        SignalTerm(
                            feature=FeatureSpec(kind="rsi", lookback=14),
                            weight=-1,
                            center=0.5,
                            scale=0.25,
                        ),
                        SignalTerm(
                            feature=FeatureSpec.model_validate(
                                {"kind": state_kind, "lookback": 63}
                            ),
                            weight=0,
                        ),
                        SignalTerm(
                            feature=FeatureSpec(
                                kind="volatility_shock", lookback=63, secondary_lookback=10
                            ),
                            weight=0,
                        ),
                    ),
                    router=RouterSpec(state_floor=floor, state_ceiling=ceiling, stress_term=3),
                ),
            )
        )
    return proposals


def pattern_proposals() -> list[Proposal]:
    return [
        Proposal(
            hypothesis="Test whether a quiet period followed by a breakout predicts continuation.",
            stage="pattern",
            research=ResearchIntent(
                mechanism="A sustained volatility compression precedes a positive range breakout.",
                falsification=(
                    "Removing temporal order or the breakout condition should erase the advantage."
                ),
            ),
            signal=SignalSpec(
                terms=(
                    SignalTerm(
                        feature=FeatureSpec(
                            kind="volatility", lookback=21, transform="time_series_rank"
                        ),
                        weight=0,
                    ),
                    SignalTerm(
                        feature=FeatureSpec(kind="breakout_distance", lookback=21), weight=0
                    ),
                ),
                patterns=(
                    PatternSpec(
                        name="compression_then_breakout",
                        mode="sequence",
                        conditions=(
                            PatternCondition(
                                term=0,
                                predicate="below",
                                threshold=0.35,
                                lag=2,
                                window=5,
                                min_fraction=0.6,
                            ),
                            PatternCondition(
                                term=1, predicate="above", threshold=0.1, window=2, min_fraction=0.5
                            ),
                        ),
                    ),
                ),
            ),
        ),
        Proposal(
            hypothesis="Test a trough-and-recovery path confirmed by positive participation.",
            stage="pattern",
            research=ResearchIntent(
                mechanism=(
                    "A recovered price trough with positive volume-price participation may persist."
                ),
                falsification=(
                    "Removing participation or changing the trough template "
                    "should reduce the effect."
                ),
            ),
            signal=SignalSpec(
                terms=(
                    SignalTerm(
                        feature=FeatureSpec(
                            kind="path_similarity", lookback=21, template=(1, 0, -1, 0, 1)
                        ),
                        weight=0,
                    ),
                    SignalTerm(feature=FeatureSpec(kind="volume_price", lookback=21), weight=0),
                ),
                patterns=(
                    PatternSpec(
                        name="recovery_with_participation",
                        conditions=(
                            PatternCondition(term=0, threshold=0.65),
                            PatternCondition(term=1, threshold=0.0, window=3, min_fraction=2 / 3),
                        ),
                    ),
                ),
            ),
        ),
    ]
