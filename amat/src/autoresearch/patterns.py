"""Causal temporal predicates and agent-authored path templates."""

from __future__ import annotations

import numpy as np

from autoresearch.registry import lag, rolling
from autoresearch.specs import SignalSpec


def pattern_values(values: np.ndarray, signal: SignalSpec) -> np.ndarray:
    """Return 0/1 matches; incomplete observations remain unavailable.

    Conditions use transformed feature units, before term center/scale/tanh.
    Sequences are conjunctions over contract-validated non-overlapping windows.
    """
    outputs = []
    for pattern in signal.patterns:
        matches = []
        for condition in pattern.conditions:
            source = values[:, :, condition.term]
            if condition.predicate in ("rising", "falling"):
                source = source - lag(source, 1)
            observed = np.isfinite(source)
            matched = (
                source >= condition.threshold
                if condition.predicate in ("above", "rising")
                else source <= condition.threshold
            ).astype(float)
            matched[~observed] = np.nan
            frequency = rolling(matched, condition.window, "mean")
            if condition.lag:
                frequency = lag(frequency, condition.lag)
            event = (frequency >= condition.min_fraction).astype(float)
            event[~np.isfinite(frequency)] = np.nan
            matches.append(event)
        stack = np.stack(matches, axis=-1)
        # No short-circuit around unavailable inputs, including an OR pattern.
        outputs.append(np.max(stack, axis=-1) if pattern.mode == "any" else np.min(stack, axis=-1))
    return np.stack(outputs, axis=-1) if outputs else np.empty((*values.shape[:2], 0))
