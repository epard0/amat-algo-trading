"""Executable constraints for small extensions of the retained strategy."""

from autoresearch.contracts import Candidate

EXTENSION_CONTRACT = (
    "Extend the retained strategy; never invent a replacement strategy. Preserve the "
    "baseline signal, allocator, assets and risk controls. Test one component at a time: "
    "bounded parameter tuning, one registered attenuation feature, or a bounded existing "
    "genetic-pattern modifier. Preserve unrelated retained overlays. State the parent, "
    "exact change, mechanism and falsification; use ablations to test incremental value."
)


def one_edit(before: tuple[object, ...], after: tuple[object, ...]) -> bool:
    """One append, removal or substitution, preserving all other ordered components."""
    if len(before) == len(after):
        return sum(a != b for a, b in zip(before, after, strict=True)) <= 1
    shorter, longer = sorted((before, after), key=len)
    return len(longer) == len(shorter) + 1 and any(
        longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer))
    )


def validate_daily_core(baseline: Candidate, candidate: Candidate) -> None:
    if candidate.signal != baseline.signal or candidate.entry_model != baseline.entry_model:
        raise ValueError("extension-only research must preserve baseline signal and entry model")


def validate_daily_extension(parent: Candidate, candidate: Candidate) -> None:
    validate_daily_core(parent, candidate)
    changes = sum(
        (
            parent.config != candidate.config,
            parent.features != candidate.features,
            parent.genetic_pattern != candidate.genetic_pattern,
        )
    )
    if changes > 1 or not one_edit(parent.features, candidate.features):
        raise ValueError("change only one component: parameters, one feature, or genetic modifier")
