"""Diverse, reproducible hypotheses spanning signal families and time scales."""

# ruff: noqa: E501 - complete research explanations and standalone HTML

from __future__ import annotations

from collections.abc import Iterator

from autoresearch.contracts import Candidate, Proposal, ResearchIntent, apply_proposal
from autoresearch.specs import FeatureSpec, Interaction, SignalSpec, SignalTerm, TradingPolicy

# Centers/scales describe formula units; ranks are centered separately below.
SHORT_WINDOW = 21

FAMILIES = (
    ("rsi", 0.5, 0.25, -1),
    ("variance_ratio", 1.0, 0.5, 1),
    ("downside_variance_share", 0.5, 0.25, -1),
    ("volatility_shock", 1.0, 0.5, -1),
    ("efficiency", 0.3, 0.2, 1),
    ("breadth", 0.5, 0.25, 1),
    ("volume_ratio", 1.0, 0.5, 1),
    ("drawdown", -0.1, 0.1, 1),
    ("volatility_of_volatility", 0.1, 0.1, -1),
    ("relative_strength", 0.0, 0.1, 1),
    ("breakout_persistence", 0.5, 0.25, 1),
    ("volume_price", 0.0, 1.0, 1),
    ("trend_acceleration", 0.0, 1.0, 1),
    ("normalized_momentum", 0.0, 1.0, 1),
    ("donchian_position", 0.5, 0.25, -1),
    ("breakout_distance", 0.0, 1.0, 1),
    ("momentum", 0.0, 0.1, 1),
    ("trend_agreement", 0.0, 1.0, 1),
)


def proposal_grid() -> Iterator[Proposal]:
    """Interleave families first, then change horizon, transform, polarity and timing."""
    for window, transform, polarity, lag, hold in (
        (21, "identity", 1, 0, 0),
        (126, "time_series_rank", 1, 0, 5),
        (63, "rolling_zscore", -1, 0, 0),
        (189, "identity", 1, 0, 21),
        (42, "cross_sectional_rank", 1, 5, 5),
        (63, "identity", -1, 0, 0),
        (126, "identity", 1, 21, 63),
    ):
        for kind, center, scale, direction in FAMILIES:
            ranked = "rank" in transform
            term = SignalTerm(
                feature=FeatureSpec.model_validate(
                    {
                        "kind": kind,
                        "lookback": window,
                        "secondary_lookback": 5 if window == SHORT_WINDOW else 21,
                        "transform": transform,
                        "normalization_window": 42,
                        "lag_sessions": lag,
                    }
                ),
                center=0.5 if ranked else 0 if transform == "rolling_zscore" else center,
                scale=0.25 if ranked else 1 if transform == "rolling_zscore" else scale,
                weight=direction * polarity,
            )
            hypothesis = f"Explore {window}-session {kind} with {transform}, weight {term.weight:g}, lag {lag}, and hold {hold}."
            yield Proposal(
                hypothesis=hypothesis,
                stage="explore",
                signal=SignalSpec(terms=(term,), policy=TradingPolicy(min_hold_sessions=hold)),
                research=ResearchIntent(
                    mechanism=f"Test whether {kind} at this horizon ranks assets by subsequent net returns; polarity is an explicit falsifiable choice.",
                    falsification="Reject the mechanism if its exposure-matched result and chronological feature diagnostics do not support the proposed direction.",
                ),
            )
    # Joint rules test agreement (minimum), alternatives (maximum), and gating.
    for operation in ("minimum", "maximum", "gate", "difference", "product"):
        for window in (21, 63, 126):
            yield Proposal(
                hypothesis=f"Test {operation} composition of trend and participation over {window} sessions.",
                stage="explore",
                signal=SignalSpec(
                    terms=(
                        SignalTerm(
                            feature=FeatureSpec(kind="normalized_momentum", lookback=window),
                            weight=0,
                        ),
                        SignalTerm(
                            feature=FeatureSpec(kind="volume_ratio", lookback=window),
                            center=1,
                            scale=0.5,
                            weight=0,
                        ),
                    ),
                    interactions=(
                        Interaction.model_validate(
                            {"left": 0, "right": 1, "operation": operation, "weight": 1}
                        ),
                    ),
                ),
                research=ResearchIntent(
                    mechanism="Participation changes the usefulness of a trend; the composition isolates their joint effect.",
                    falsification="Compare with each single-term sleeve and remove the interaction to test incremental value.",
                ),
            )


def exploratory_proposals() -> Iterator[Proposal]:
    """Cover time scales and interactions early, instead of exhausting one grid axis."""
    grid = list(proposal_grid())
    features = [p for p in grid if p.signal and not p.signal.interactions]
    interactions = iter(p for p in grid if p.signal and p.signal.interactions)
    variants = len(features) // len(FAMILIES)
    for cycle in range(variants):
        for family in range(len(FAMILIES)):
            yield features[((cycle + family) % variants) * len(FAMILIES) + family]
            if family % 2:
                interaction = next(interactions, None)
                if interaction is not None:
                    yield interaction
    yield from interactions


def choose_exploration(baseline: Candidate, tried: set[str]) -> Proposal | None:
    for proposal in exploratory_proposals():
        try:
            candidate = apply_proposal(baseline, proposal)
        except ValueError:
            continue
        if candidate.digest() not in tried:
            return proposal
    return None
