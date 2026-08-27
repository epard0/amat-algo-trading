"""Unsupervised regime model: a Gaussian mixture posterior over market states.

``market_regime.classify`` assigns one hard label out of five and looks up
hand-tuned multipliers for it. That has two costs. The labels are a human's
partition of the feature space rather than the data's, and a hard assignment
throws away the fact that most minutes sit between regimes — a bar that is 51%
consolidation and 49% reversal gets treated identically to one that is 99%
consolidation.

This module replaces the lookup with a *soft* posterior. States are learned by
fitting a Gaussian mixture to the 15-dimensional ``RegimeFeatures`` vector
offline; at runtime the posterior ``P(state | features)`` becomes the weights of
a convex blend over per-state multipliers. That is the "weighted heuristic"
version of the same idea, and it degrades gracefully: an ambiguous bar produces
a blended, less confident response rather than a coin-flip label.

Two deliberate constraints:

**No new runtime dependency.** The fitting happens offline in ``research/``
where scikit-learn is available; what ships to the trading host is a JSON file
of means, variances and weights, scored here in pure Python. The repo has
already been broken once by an undeclared import, and the genetic-programming
plan makes the same argument: model output crosses into production as *data*,
never as imported code.

**The sign invariant survives.** A regime response must
never flip the sign of measured drift. A convex combination of non-negative
multipliers is non-negative, so the blend preserves it automatically — provided
no individual multiplier is negative. ``RegimeMixture.validate`` enforces that
at load time and refuses the artifact otherwise, rather than trusting whatever
the fitter produced.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

# Matches market_regime.RegimeAssessment's knobs so a blended result is a
# drop-in for a classified one.
DEFAULT_MULTIPLIERS = {
    "drift_multiplier": 1.0,
    "momentum_half_life_minutes": 5.0,
    "volatility_multiplier": 1.0,
    "extra_shrinkage": 0.0,
}

SCHEMA_VERSION = 1


class RegimeModelError(ValueError):
    """Raised when an artifact is malformed or violates a safety invariant."""


@dataclass(frozen=True)
class MixtureState:
    """One learned state: a diagonal Gaussian plus the response it implies."""

    name: str
    weight: float
    means: tuple[float, ...]
    variances: tuple[float, ...]
    drift_multiplier: float
    momentum_half_life_minutes: float
    volatility_multiplier: float
    extra_shrinkage: float
    # Optional human-readable note from the fitter, e.g. its nearest hand label.
    description: str = ""

    def log_likelihood(self, vector: Sequence[float]) -> float:
        """Log density of a diagonal Gaussian.

        Diagonal rather than full covariance on purpose: a full 15x15 covariance
        needs far more samples than this project has to estimate stably, and a
        badly conditioned covariance produces confident nonsense.
        """
        total = 0.0
        for value, mean, variance in zip(vector, self.means, self.variances):
            safe = max(variance, 1e-12)
            delta = value - mean
            total += -0.5 * (math.log(2.0 * math.pi * safe) + (delta * delta) / safe)
        return total


@dataclass(frozen=True)
class RegimeBlend:
    """The blended response, plus the posterior that produced it."""

    drift_multiplier: float
    momentum_half_life_minutes: float
    volatility_multiplier: float
    extra_shrinkage: float
    posterior: dict[str, float]
    dominant_state: str
    confidence: float


@dataclass(frozen=True)
class RegimeMixture:
    """A fitted mixture, loaded from JSON and scored in pure Python."""

    feature_names: tuple[str, ...]
    states: tuple[MixtureState, ...]
    # z-scoring statistics from the training set. Applying the training scale is
    # what makes the densities comparable; refitting it at runtime would leak
    # the present into a model fitted on the past.
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    schema_version: int = SCHEMA_VERSION
    metadata: Mapping[str, Any] = None  # type: ignore[assignment]

    # -- loading ------------------------------------------------------------
    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "RegimeMixture":
        try:
            names = tuple(str(n) for n in payload["feature_names"])
            states = tuple(
                MixtureState(
                    name=str(s["name"]),
                    weight=float(s["weight"]),
                    means=tuple(float(v) for v in s["means"]),
                    variances=tuple(float(v) for v in s["variances"]),
                    drift_multiplier=float(
                        s.get("drift_multiplier", DEFAULT_MULTIPLIERS["drift_multiplier"])
                    ),
                    momentum_half_life_minutes=float(
                        s.get(
                            "momentum_half_life_minutes",
                            DEFAULT_MULTIPLIERS["momentum_half_life_minutes"],
                        )
                    ),
                    volatility_multiplier=float(
                        s.get(
                            "volatility_multiplier",
                            DEFAULT_MULTIPLIERS["volatility_multiplier"],
                        )
                    ),
                    extra_shrinkage=float(
                        s.get("extra_shrinkage", DEFAULT_MULTIPLIERS["extra_shrinkage"])
                    ),
                    description=str(s.get("description", "")),
                )
                for s in payload["states"]
            )
            mixture = RegimeMixture(
                feature_names=names,
                states=states,
                feature_means=tuple(float(v) for v in payload["feature_means"]),
                feature_scales=tuple(float(v) for v in payload["feature_scales"]),
                schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
                metadata=dict(payload.get("metadata") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RegimeModelError(f"malformed regime artifact: {exc}") from exc
        mixture.validate()
        return mixture

    @staticmethod
    def load(path: Path) -> "RegimeMixture":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegimeModelError(f"cannot read regime artifact {path}: {exc}") from exc
        return RegimeMixture.from_dict(payload)

    def validate(self) -> None:
        """Reject any artifact that is malformed or unsafe.

        The sign check is the important one: a negative drift multiplier would
        let a fitted state reverse the model's direction, which is a second,
        untested strategy hiding inside the first.
        """
        if self.schema_version != SCHEMA_VERSION:
            raise RegimeModelError(
                f"unsupported schema_version {self.schema_version}"
            )
        if not self.states:
            raise RegimeModelError("artifact declares no states")
        width = len(self.feature_names)
        if width == 0:
            raise RegimeModelError("artifact declares no features")
        if len(self.feature_means) != width or len(self.feature_scales) != width:
            raise RegimeModelError("feature statistics do not match feature_names")
        if any(scale <= 0 for scale in self.feature_scales):
            raise RegimeModelError("feature_scales must all be positive")

        total_weight = 0.0
        for state in self.states:
            if len(state.means) != width or len(state.variances) != width:
                raise RegimeModelError(f"state {state.name!r} has the wrong width")
            if state.weight < 0:
                raise RegimeModelError(f"state {state.name!r} has a negative weight")
            if any(v <= 0 for v in state.variances):
                raise RegimeModelError(f"state {state.name!r} has a non-positive variance")
            if state.drift_multiplier < 0:
                raise RegimeModelError(
                    f"state {state.name!r} has a negative drift_multiplier; a regime "
                    "response may damp the measured drift but never invert it"
                )
            if state.volatility_multiplier <= 0:
                raise RegimeModelError(
                    f"state {state.name!r} has a non-positive volatility_multiplier"
                )
            if not 0.0 <= state.extra_shrinkage <= 1.0:
                raise RegimeModelError(
                    f"state {state.name!r} has extra_shrinkage outside [0, 1]"
                )
            if state.momentum_half_life_minutes <= 0:
                raise RegimeModelError(
                    f"state {state.name!r} has a non-positive momentum half-life"
                )
            total_weight += state.weight
        if total_weight <= 0:
            raise RegimeModelError("state weights sum to zero")

    # -- scoring ------------------------------------------------------------
    def vectorise(self, features: Mapping[str, float]) -> list[float]:
        """Order and standardise a feature mapping using the training scale.

        A missing feature becomes the training mean, i.e. z=0 — the least
        informative value available, rather than a zero that would read as an
        extreme after scaling.
        """
        vector: list[float] = []
        for index, name in enumerate(self.feature_names):
            raw = features.get(name)
            mean = self.feature_means[index]
            scale = self.feature_scales[index]
            value = float(raw) if isinstance(raw, (int, float)) else mean
            if not math.isfinite(value):
                value = mean
            vector.append((value - mean) / scale)
        return vector

    def posterior(self, features: Mapping[str, float]) -> dict[str, float]:
        """P(state | features), normalised. Log-sum-exp for numerical stability."""
        vector = self.vectorise(features)
        scores: list[tuple[str, float]] = []
        for state in self.states:
            if state.weight <= 0:
                continue
            scores.append(
                (state.name, math.log(state.weight) + state.log_likelihood(vector))
            )
        if not scores:
            return {}
        peak = max(score for _, score in scores)
        exponentiated = [(name, math.exp(score - peak)) for name, score in scores]
        total = sum(value for _, value in exponentiated)
        if total <= 0:
            uniform = 1.0 / len(exponentiated)
            return {name: uniform for name, _ in exponentiated}
        return {name: value / total for name, value in exponentiated}

    def blend(self, features: Mapping[str, float]) -> RegimeBlend:
        """Convex blend of per-state multipliers, weighted by the posterior."""
        posterior = self.posterior(features)
        if not posterior:
            return RegimeBlend(
                **DEFAULT_MULTIPLIERS,
                posterior={},
                dominant_state="UNKNOWN",
                confidence=0.0,
            )
        by_name = {state.name: state for state in self.states}
        drift = half_life = volatility = shrinkage = 0.0
        for name, weight in posterior.items():
            state = by_name[name]
            drift += weight * state.drift_multiplier
            half_life += weight * state.momentum_half_life_minutes
            volatility += weight * state.volatility_multiplier
            shrinkage += weight * state.extra_shrinkage
        dominant = max(posterior, key=lambda k: posterior[k])
        return RegimeBlend(
            # A convex combination of non-negative values is non-negative, so
            # the drift sign cannot flip. Clamped anyway: cheap, and this is the
            # invariant the whole design rests on.
            drift_multiplier=max(0.0, drift),
            momentum_half_life_minutes=max(1e-6, half_life),
            volatility_multiplier=max(1e-6, volatility),
            extra_shrinkage=min(1.0, max(0.0, shrinkage)),
            posterior=posterior,
            dominant_state=dominant,
            confidence=posterior[dominant],
        )


def load_for_assets(
    path: Path | str | None, assets: Sequence[str]
) -> dict[str, "RegimeMixture"]:
    """Per-asset mixtures, keyed by asset, for whichever assets have one.

    ``path`` may name a single artifact, which every asset then shares, or a
    directory, in which case ``regime_mixture_<ASSET>.json`` is loaded for each
    asset that has one. An asset with no artifact is simply absent from the
    result and falls back to the hand-tuned classifier — a partial rollout is a
    legitimate state, and it is the one you want while shadowing a new fit on a
    single asset.

    A file that exists but does not load is an error, not a silent fallback:
    the whole point of pointing at it is that you meant to use it.
    """
    if path is None:
        return {}
    candidate = Path(path)
    if candidate.is_dir():
        found: dict[str, RegimeMixture] = {}
        for asset in assets:
            artifact = candidate / f"regime_mixture_{asset}.json"
            if artifact.is_file():
                found[asset] = RegimeMixture.load(artifact)
        return found
    shared = RegimeMixture.load(candidate)
    return {asset: shared for asset in assets}


def load_optional(path: Path | str | None) -> RegimeMixture | None:
    """Load an artifact if configured and present; otherwise return None.

    A missing artifact is not an error — it means the bot runs on the
    hand-tuned classifier, which is the default and the fallback.
    """
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    return RegimeMixture.load(candidate)
