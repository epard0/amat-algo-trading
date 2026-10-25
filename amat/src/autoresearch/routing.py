"""Causal trend/range routing with inspectable components and fixed controls."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from autoresearch.registry import rolling
from autoresearch.specs import SignalSpec

ROUTING_COLUMNS = ("trend_sleeve", "range_sleeve", "trend_weight", "stress_multiplier")
MIN_STATIC_HISTORY = 40
FEATURE_AXES = 3


@dataclass(frozen=True)
class RoutingTrace:
    raw: np.ndarray
    available: np.ndarray
    components: np.ndarray


def route_scores(values: np.ndarray, signal: SignalSpec, begin: int, first: int) -> RoutingTrace:
    """Feature axes are (session, symbol, term); scores are preferences, not probabilities.

    training_mean fixes per-asset weights from [begin, first) for the entire run.
    Adaptive weights use a complete trailing window; missing data never carries
    a stale state forward. The caller starts trading policy at the common anchor.
    """
    router = signal.router
    if router is None:
        raise ValueError("routing requires a router specification")
    if values.ndim != FEATURE_AXES or values.shape[-1] != len(signal.terms):
        raise ValueError("routing feature axes do not match the signal")
    if not 0 <= begin < first <= len(values):
        raise ValueError("routing requires an ordered training window")

    def sleeve(indices: tuple[int, ...]) -> np.ndarray:
        columns = np.stack(
            [
                np.tanh((values[:, :, i] - signal.terms[i].center) / signal.terms[i].scale)
                for i in indices
            ],
            axis=-1,
        )
        weights = np.array([signal.terms[i].weight for i in indices])
        return np.asarray((1 + (columns @ weights) / np.abs(weights).sum()) / 2)

    trend = sleeve(router.trend_terms)
    reversion = sleeve(router.range_terms)
    # Avoid buying an oversold persistent decline just because it looks like a range.
    reversion = np.where(trend >= router.range_trend_floor, reversion, 0.0)
    state = values[:, :, router.state_term]
    adaptive = rolling(
        np.clip((state - router.state_floor) / (router.state_ceiling - router.state_floor), 0, 1),
        router.smoothing_sessions,
        "mean",
    )
    weight = adaptive.copy()
    if router.mode == "static":
        weight[:] = router.static_trend_weight
    elif router.mode == "training_mean":
        sample = adaptive[begin:first]
        valid = np.isfinite(sample)
        count = valid.sum(axis=0)
        mean = np.nansum(sample, axis=0) / np.maximum(count, 1)
        weight[:] = np.where(count >= MIN_STATIC_HISTORY, mean, np.nan)
    gate = np.ones(values.shape[:2])
    if router.stress_term is not None:
        stress = values[:, :, router.stress_term]
        gate = 1 - router.max_attenuation * np.clip(
            (stress - router.stress_threshold) / router.stress_scale, 0, 1
        )
    available = np.isfinite(values).all(axis=-1) & np.isfinite(adaptive) & np.isfinite(weight)
    raw = gate * (weight * trend + (1 - weight) * reversion)
    raw = np.where(available, np.clip(raw, 0, 1), np.nan)
    components = np.stack([trend, reversion, weight, gate], axis=-1)
    components[~available] = np.nan
    return RoutingTrace(raw, available, components)
