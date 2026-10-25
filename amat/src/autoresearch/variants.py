"""Small, explicit experiment mutations; all outputs revalidate the full candidate."""

from __future__ import annotations

from autoresearch.contracts import BOUNDS, Candidate, Patch, Proposal
from autoresearch.specs import SignalSpec


def ablations(candidate: Candidate) -> list[tuple[str, Candidate]]:
    if candidate.entry_model:
        return entry_controls(candidate)
    selection = selection_controls(candidate)
    if candidate.genetic_pattern:
        payload = candidate.model_dump(mode="json")
        payload["genetic_pattern"] = None
        selection.append(("genetic_pattern_disabled", Candidate.model_validate(payload)))
    if candidate.signal and candidate.signal.router:
        gates = []
        for index in range(len(candidate.features)):
            payload = candidate.model_dump(mode="json")
            payload["features"].pop(index)
            gates.append((f"gate_{index}", Candidate.model_validate(payload)))
        return selection + gates + router_controls(candidate)
    return selection + signal_ablations(candidate)


def entry_controls(candidate: Candidate) -> list[tuple[str, Candidate]]:
    variants = []
    for name, update in (
        ("entry_disabled", None),
        ("entry_no_adverse", {"adverse_weight": 0.0}),
        ("entry_no_uncertainty", {"uncertainty_weight": 0.0}),
        ("entry_pooled_regimes", {"regime_validation": False}),
        ("entry_no_recent_health", {"recent_health": False}),
    ):
        payload = candidate.model_dump(mode="json")
        if update is None:
            payload["entry_model"] = None
        else:
            payload["entry_model"].update(update)
        variant = Candidate.model_validate(payload)
        if variant != candidate:
            variants.append((name, variant))
    return variants


def selection_controls(candidate: Candidate) -> list[tuple[str, Candidate]]:
    if candidate.signal is None or candidate.signal.selection is None:
        return []
    variants = []
    for name, update in (
        ("selection_disabled", None),
        ("selection_no_retention", {"retention_bonus": 0.0}),
        ("selection_force_exits", {"mode": "portfolio"}),
        ("selection_entries_only", {"mode": "entries"}),
    ):
        payload = candidate.model_dump(mode="json")
        if update is None:
            payload["signal"]["selection"] = None
        else:
            payload["signal"]["selection"].update(update)
        variant = Candidate.model_validate(payload)
        if variant != candidate:
            variants.append((name, variant))
    return variants


def signal_ablations(candidate: Candidate) -> list[tuple[str, Candidate]]:  # noqa: PLR0912 - typed ablations
    variants = []
    for index in range(len(candidate.features)):
        payload = candidate.model_dump(mode="json")
        payload["features"].pop(index)
        variants.append((f"gate_{index}", Candidate.model_validate(payload)))
    if candidate.signal:
        for index in range(len(candidate.signal.terms)):
            payload = candidate.model_dump(mode="json")
            spec = payload["signal"]
            spec["terms"].pop(index)
            spec["interactions"] = [
                {
                    **item,
                    "left": item["left"] - int(item["left"] > index),
                    "right": item["right"] - int(item["right"] > index),
                }
                for item in spec["interactions"]
                if index not in (item["left"], item["right"])
            ]
            remaining = []
            for pattern in spec["patterns"]:
                conditions = [
                    {**condition, "term": condition["term"] - int(condition["term"] > index)}
                    for condition in pattern["conditions"]
                    if condition["term"] != index
                ]
                if conditions:
                    remaining.append(
                        {
                            **pattern,
                            "conditions": conditions,
                            "mode": "all" if len(conditions) == 1 else pattern["mode"],
                        }
                    )
            spec["patterns"] = remaining
            if not spec["terms"] or not (
                any(term["weight"] for term in spec["terms"])
                or any(i["weight"] for i in spec["interactions"])
                or any(pattern["weight"] for pattern in spec["patterns"])
            ):
                payload["signal"] = None
            variants.append((f"signal_{index}", Candidate.model_validate(payload)))
    if candidate.signal:
        for index in range(len(candidate.signal.interactions)):
            payload = candidate.model_dump(mode="json")
            payload["signal"]["interactions"].pop(index)
            spec = payload["signal"]
            if (
                not any(t["weight"] for t in spec["terms"])
                and not any(i["weight"] for i in spec["interactions"])
                and not any(p["weight"] for p in spec["patterns"])
            ):
                payload["signal"] = None
            variants.append((f"interaction_{index}", Candidate.model_validate(payload)))
        for index in range(len(candidate.signal.patterns)):
            payload = candidate.model_dump(mode="json")
            spec = payload["signal"]
            spec["patterns"].pop(index)
            if (
                not any(term["weight"] for term in spec["terms"])
                and not any(i["weight"] for i in spec["interactions"])
                and not any(pattern["weight"] for pattern in spec["patterns"])
            ):
                payload["signal"] = None
            variants.append((f"pattern_{index}", Candidate.model_validate(payload)))
            if candidate.signal.patterns[index].mode == "sequence":
                payload = candidate.model_dump(mode="json")
                pattern = payload["signal"]["patterns"][index]
                pattern["mode"] = "all"
                for condition in pattern["conditions"]:
                    condition["lag"] = 0
                variants.append((f"pattern_timing_{index}", Candidate.model_validate(payload)))
    return variants


def router_controls(candidate: Candidate) -> list[tuple[str, Candidate]]:
    """Keep sleeve definitions and data coverage fixed while testing routing value."""
    if candidate.signal is None or candidate.signal.router is None:
        return []
    changes = (
        ("router_trend_only", {"mode": "static", "static_trend_weight": 1.0}),
        ("router_range_only", {"mode": "static", "static_trend_weight": 0.0}),
        ("router_equal_blend", {"mode": "static", "static_trend_weight": 0.5}),
        ("router_training_mean", {"mode": "training_mean"}),
        ("router_no_stress", {"max_attenuation": 0.0}),
        (
            "router_equal_ungated",
            {"mode": "static", "static_trend_weight": 0.5, "max_attenuation": 0.0},
        ),
        ("router_no_range_guard", {"range_trend_floor": 0.0}),
    )
    variants = []
    for name, update in changes:
        payload = candidate.model_dump(mode="json")
        payload["signal"]["router"].update(update)
        variant = Candidate.model_validate(payload)
        if variant != candidate:
            variants.append((name, variant))
    return variants


def neighbors(candidate: Candidate) -> list[tuple[str, Candidate]]:
    if candidate.entry_model:
        return entry_neighbors(candidate)
    variants = []
    for factor in (0.8, 1.2):
        payload = candidate.model_dump(mode="json")
        recipes = list(payload["features"])
        if candidate.signal:
            recipes.extend(term["feature"] for term in payload["signal"]["terms"])
        if recipes:
            for feature in recipes:
                feature["lookback"] = max(
                    5,
                    min(
                        251 if "transform" in feature else 126, round(feature["lookback"] * factor)
                    ),
                )
        else:
            trend = payload["config"]["strategy"]["trend"]
            trend["deadband_abs"] = max(0.05, min(0.5, trend["deadband_abs"] * factor))
        try:
            variant = Candidate.model_validate(payload)
        except ValueError:
            continue
        if variant != candidate:
            variants.append((f"nearby_{factor}", variant))
    if candidate.signal and candidate.signal.selection:
        for factor in (0.8, 1.2):
            payload = candidate.model_dump(mode="json")
            selection = payload["signal"]["selection"]
            selection["min_dollar_volume"] *= factor
            selection["min_window_dollar_volume"] *= factor
            selection["top_k"] = max(1, min(30, round(selection["top_k"] * factor)))
            try:
                variant = Candidate.model_validate(payload)
            except ValueError:
                continue
            if variant != candidate:
                variants.append((f"nearby_selection_{factor}", variant))
    return variants


def entry_neighbors(candidate: Candidate) -> list[tuple[str, Candidate]]:
    variants = []
    for factor in (0.8, 1.2):
        payload = candidate.model_dump(mode="json")
        model = payload["entry_model"]
        model["minimum_defer_bps"] = min(100, model["minimum_defer_bps"] * factor)
        model["max_entry_reduction"] = min(0.75, model["max_entry_reduction"] * factor)
        variant = Candidate.model_validate(payload)
        if variant != candidate:
            variants.append((f"nearby_entry_{factor}", variant))
    return variants


def proposal_for(
    parent: Candidate, candidate: Candidate, *, hypothesis: str, stage: str
) -> Proposal:
    patches = []
    original = parent.config.strategy.model_dump(mode="json")
    changed = candidate.config.strategy.model_dump(mode="json")
    for path in (*BOUNDS, "trend.horizons_sessions"):
        section, key = path.split(".")
        if original[section][key] != changed[section][key]:
            patches.append(Patch(path=path, value=changed[section][key]))
    return Proposal.model_validate(
        {
            "hypothesis": hypothesis,
            "patches": patches,
            "features": candidate.features,
            "signal": candidate.signal,
            "entry_model": candidate.entry_model,
            "genetic_pattern": candidate.genetic_pattern,
            "stage": stage,
        }
    )


def replace_signal(candidate: Candidate, signal: SignalSpec | None) -> Candidate:
    return Candidate(
        config=candidate.config,
        features=candidate.features,
        signal=signal,
        entry_model=candidate.entry_model,
        genetic_pattern=candidate.genetic_pattern,
    )
