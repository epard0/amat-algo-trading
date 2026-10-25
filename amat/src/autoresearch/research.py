"""Verified experimental ancestry available to the research proposer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoresearch.contracts import BOUNDS, Candidate, Program, Proposal, validate_candidate


def research_parents(
    _root: Path, program: Program, incumbent: Candidate, _history: list[dict[str, Any]]
) -> dict[str, Candidate]:
    candidates = [incumbent]
    for candidate in candidates:
        validate_candidate(program.baseline, candidate)
    return {candidate.digest(): candidate for candidate in candidates}


def selected_parent(
    proposal: Proposal, incumbent: Candidate, parents: dict[str, Candidate]
) -> Candidate:
    if proposal.parent_candidate is None:
        return incumbent
    if proposal.parent_candidate != incumbent.digest() or proposal.parent_candidate not in parents:
        raise ValueError("parent_candidate must be the current retained best research parent")
    return parents[proposal.parent_candidate]


def parent_recipe(candidate: Candidate) -> dict[str, Any]:
    strategy = candidate.config.strategy.model_dump(mode="json")
    return {
        "candidate": candidate.digest(),
        "editable_config": {
            path: strategy[path.split(".")[0]][path.split(".")[1]]
            for path in (*BOUNDS, "trend.horizons_sessions")
        },
        "features": [feature.model_dump(mode="json") for feature in candidate.features],
        "genetic_pattern": candidate.genetic_pattern.model_dump(mode="json")
        if candidate.genetic_pattern
        else None,
        "signal": candidate.signal.model_dump(mode="json") if candidate.signal else None,
        "entry_model": candidate.entry_model.model_dump(mode="json")
        if candidate.entry_model
        else None,
    }
