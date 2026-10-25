"""Budgeted screening, local refinement, diverse combination and policy experiments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from autoresearch.contracts import (
    MAX_FEATURES,
    Candidate,
    Feature,
    Program,
    Proposal,
    apply_proposal,
)
from autoresearch.diagnostics import correlation
from autoresearch.exploration import choose_exploration
from autoresearch.inspirations import pattern_proposals, router_proposals
from autoresearch.memory import known_candidates
from autoresearch.specs import FeatureSpec, Interaction, SignalSpec, SignalTerm, TradingPolicy
from autoresearch.variants import ablations, neighbors, proposal_for, replace_signal
from autoresearch.workspace import digest_file

MAX_PATTERNS = 3
EXPLORATION_INTERVAL = 3

SEARCH_STAGES = ("route", "pattern", "refine", "combine", "ablate", "policy")
EXPLORATION_FAILURES = {
    "drawdown",
    "no_fills",
    "accounting_or_runner_warning",
    "measured_window",
    "stress_window",
}


def seed_proposals() -> list[Proposal]:
    proposals = [
        Proposal(
            hypothesis="Screen causal short-horizon momentum attenuation.",
            features=(Feature(kind="momentum", lookback=21, threshold=0),),
            stage="screen",
        )
    ]
    for kind, center, scale, transform in (
        ("normalized_momentum", 0, 1, "identity"),
        ("trend_agreement", 0, 1, "identity"),
        ("trend_acceleration", 0, 1, "identity"),
        ("donchian_position", 0.5, 0.25, "identity"),
        ("breakout_distance", 0, 1, "identity"),
        ("breakout_persistence", 0.5, 0.25, "identity"),
        ("relative_strength", 0.5, 0.25, "time_series_rank"),
        ("volume_price", 0, 1, "identity"),
    ):
        feature = FeatureSpec.model_validate({"kind": kind, "transform": transform})
        proposals.append(
            Proposal(
                hypothesis=f"Screen whether {kind} forms a useful continuous forecast.",
                signal=SignalSpec(terms=(SignalTerm(feature=feature, center=center, scale=scale),)),
                stage="screen",
            )
        )
    proposals.append(
        Proposal(
            hypothesis=(
                "Fit trend and downside structure using matured labels and purged calibration."
            ),
            signal=SignalSpec(
                terms=(
                    SignalTerm(feature=FeatureSpec(kind="normalized_momentum")),
                    SignalTerm(
                        feature=FeatureSpec(kind="downside_variance_share"),
                        center=0.5,
                        scale=0.25,
                        weight=-1,
                    ),
                ),
                learner="ridge",
            ),
            stage="screen",
        )
    )
    return proposals


def frontier(root: Path, program: Program, history: list[dict[str, Any]]) -> list[Candidate]:
    rows = [row for row in history if row["result"] and row["status"] != "baseline"]
    rows.sort(
        key=lambda row: (
            float(json.loads(row["result"]).get("score"))
            if json.loads(row["result"]).get("score") is not None
            else -np.inf
        ),
        reverse=True,
    )
    candidates: list[Candidate] = []
    signatures: set[tuple[Any, ...]] = set()
    traces: list[np.ndarray] = []
    for row in rows:
        result = json.loads(row["result"])
        if result.get("score") is None or EXPLORATION_FAILURES.intersection(
            result.get("failed_gates", [])
        ):
            continue
        candidate = Candidate.model_validate_json(
            (root / "candidates" / f"{row['candidate']}.json").read_text()
        )
        if candidate.digest() != row["candidate"]:
            raise ValueError("frontier candidate digest mismatch")
        signature = (
            tuple(sorted(feature.kind for feature in candidate.features)),
            tuple(sorted(term.feature.kind for term in candidate.signal.terms))
            if candidate.signal
            else (),
            candidate.signal.learner if candidate.signal else "legacy",
            candidate.signal.router.digest()
            if candidate.signal and candidate.signal.router
            else None,
            tuple(pattern.digest() for pattern in candidate.signal.patterns)
            if candidate.signal
            else (),
            tuple(
                (term.feature.template, term.feature.lag_sessions)
                for term in candidate.signal.terms
            )
            if candidate.signal
            else (),
            candidate.genetic_pattern.genome_sha256 if candidate.genetic_pattern else None,
        )
        if signature in signatures:
            continue
        relative = f"outputs/runs/{row['candidate']}/signals.npz"
        path = root / relative
        values = None
        if not path.exists() and relative in result.get("artifact_hashes", {}):
            raise ValueError("frontier prediction artifact is missing")
        if path.exists():
            if digest_file(path) != result.get("artifact_hashes", {}).get(relative):
                raise ValueError("frontier prediction artifact changed")
            with np.load(path, allow_pickle=False) as arrays:
                values = arrays["effective_scores"][
                    arrays["sessions"] >= str(program.measured_start)
                ]
            if any(
                (correlation(values, previous) or 0) > program.evaluation.max_forecast_correlation
                for previous in traces
            ):
                continue
        candidates.append(candidate)
        signatures.add(signature)
        if values is not None:
            traces.append(values)
        if len(candidates) >= program.evaluation.beam_width:
            break
    return candidates


def refinements(parent: Candidate) -> list[Candidate]:
    candidates = [candidate for _, candidate in neighbors(parent)]
    if parent.signal:
        for smoothing, transform in (
            (3, "identity"),
            (5, "time_series_rank"),
            (1, "rolling_zscore"),
            (1, "group_rank"),
            (1, "train_zscore"),
        ):
            payload = parent.model_dump(mode="json")
            term = payload["signal"]["terms"][0]
            term["feature"].update(smoothing=smoothing, transform=transform)
            term.update(
                center=0.5 if "rank" in transform else 0, scale=0.25 if "rank" in transform else 1
            )
            try:
                candidates.append(Candidate.model_validate(payload))
            except ValueError:
                continue
    else:
        for attenuation, smoothing, rank in ((0.15, 3, False), (0.5, 5, False), (0.25, 1, True)):
            payload = parent.model_dump(mode="json")
            if payload["features"]:
                feature = payload["features"][0]
                feature.update(
                    attenuation=attenuation, smoothing=smoothing, cross_sectional_rank=rank
                )
                if rank:
                    feature["threshold"] = 0.5
                candidates.append(Candidate.model_validate(payload))
    return candidates


def combined_signal(left: SignalSpec, right: SignalSpec) -> SignalSpec | None:
    if left.router or right.router:
        return None
    terms = list(left.terms)
    mapping = {}
    for index, term in enumerate(right.terms):
        key = term.feature.digest()
        existing = next((i for i, item in enumerate(terms) if item.feature.digest() == key), None)
        if existing is None:
            existing = len(terms)
            terms.append(term)
        mapping[index] = existing
    if len(terms) > MAX_FEATURES:
        return None
    payload = left.model_dump(mode="json")
    payload["terms"] = [term.model_dump(mode="json") for term in terms]
    names = {pattern["name"] for pattern in payload["patterns"]}
    for pattern in right.patterns:
        item = pattern.model_dump(mode="json")
        for condition in item["conditions"]:
            condition["term"] = mapping[condition["term"]]
        if item in payload["patterns"]:
            continue
        original_name, suffix = item["name"], len(names)
        while item["name"] in names:
            item["name"] = original_name[:40] + "_" + str(suffix)
            suffix += 1
        names.add(item["name"])
        payload["patterns"].append(item)
    if len(payload["patterns"]) > MAX_PATTERNS:
        return None
    signal = SignalSpec.model_validate(payload)
    return signal if signal != left else None


def combinations(parent: Candidate, pool: list[Candidate]) -> list[Candidate]:
    candidates: list[Candidate] = []
    # Borrow useful components without changing ancestry: the resulting recipe
    # still inherits the current incumbent's configuration and feature overlays.
    if parent.signal is None:
        for index, left in enumerate(pool):
            if left.signal is None:
                continue
            for right in pool[index + 1 :]:
                if right.signal is None:
                    continue
                signal = combined_signal(left.signal, right.signal)
                if signal is not None:
                    candidates.append(replace_signal(parent, signal))
        return candidates
    for other in pool:
        if other.signal is None:
            continue
        signal = combined_signal(parent.signal, other.signal)
        if signal is None:
            continue
        candidates.append(replace_signal(parent, signal))
        if len(signal.terms) > 1:
            interacted = signal.model_dump(mode="json")
            interacted["interactions"] = [
                Interaction(left=0, right=len(signal.terms) - 1).model_dump(mode="json")
            ]
            variant = replace_signal(parent, SignalSpec.model_validate(interacted))
            if variant != candidates[-1]:
                candidates.append(variant)
    return candidates


def policy_variants(parent: Candidate) -> list[Candidate]:
    if parent.signal is None:
        return []
    candidates: list[Candidate] = []
    for confirmation, hold, entry, exit_level in (
        (2, 0, 0.55, 0.45),
        (3, 5, 0.6, 0.4),
        (1, 5, 0.5, 0.3),
    ):
        policy = TradingPolicy(
            confirmation_sessions=confirmation,
            min_hold_sessions=hold,
            entry_threshold=entry,
            exit_threshold=exit_level,
        )
        signal = SignalSpec.model_validate(
            {**parent.signal.model_dump(mode="json"), "policy": policy}
        )
        candidates.append(replace_signal(parent, signal))
    if parent.signal.learner == "weighted" and parent.signal.router is None:
        candidates.append(
            replace_signal(
                parent,
                SignalSpec.model_validate(
                    {**parent.signal.model_dump(mode="json"), "learner": "ridge"}
                ),
            )
        )
    return candidates


def choose_experiment(
    root: Path, program: Program, incumbent: Candidate, history: list[dict[str, Any]]
) -> tuple[Candidate, Proposal] | None:
    tried = {row["candidate"] for row in history}
    tried |= known_candidates(root, program)
    if program.extensions_only or incumbent.config.strategy.version.startswith("13."):
        from autoresearch.proposer import v13_proposal  # noqa: PLC0415 - bounded v13 branch

        proposal = v13_proposal(incumbent, tried)
        return (incumbent, proposal) if proposal is not None else None

    seeds = seed_proposals()[: program.evaluation.screening_trials]
    for proposal in seeds:
        if apply_proposal(incumbent, proposal).digest() not in tried:
            return incumbent, proposal
    exploration = choose_exploration(incumbent, tried)
    # Regularly leave the current frontier, even when all early families lose.
    completed_trials = sum(bool(row["parent"]) for row in history)
    if exploration is not None and (completed_trials - len(seeds) + 1) % EXPLORATION_INTERVAL == 0:
        return incumbent, exploration
    pool = frontier(root, program, history)
    if incumbent.digest() not in {candidate.digest() for candidate in pool}:
        pool.insert(0, incumbent)
    completed = len([row for row in history if row["parent"]]) - len(seeds)
    rotation = (max(0, completed) - max(0, completed) // EXPLORATION_INTERVAL) % len(SEARCH_STAGES)
    stages = SEARCH_STAGES[rotation:] + SEARCH_STAGES[:rotation]
    for stage in stages:
        for parent in [incumbent]:
            variants = (
                [apply_proposal(parent, proposal) for proposal in router_proposals()]
                if stage == "route"
                else [apply_proposal(parent, proposal) for proposal in pattern_proposals()]
                if stage == "pattern"
                else refinements(parent)
                if stage == "refine"
                else combinations(parent, pool)
                if stage == "combine"
                else [candidate for _, candidate in ablations(parent)]
                if stage == "ablate"
                else policy_variants(parent)
            )
            for variant in variants:
                if variant.digest() not in tried:
                    proposal = proposal_for(
                        parent,
                        variant,
                        hypothesis=f"{stage.capitalize()} recipe; test incremental value.",
                        stage=stage,
                    )
                    if stage in ("pattern", "route"):
                        inspiration = next(
                            item
                            for item in (
                                router_proposals() if stage == "route" else pattern_proposals()
                            )
                            if apply_proposal(parent, item) == variant
                        )
                        proposal = proposal.model_copy(
                            update={
                                "hypothesis": inspiration.hypothesis,
                                "research": inspiration.research,
                            }
                        )
                    return parent, proposal
    return (incumbent, exploration) if exploration is not None else None
