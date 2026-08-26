"""Unsupervised regime detection under causal constraints.

Why unsupervised, and what it may and may not do
------------------------------------------------
There are no ground-truth regime labels, so any supervised approach would be
fitting labels someone invented after the fact. Clustering the *conditions*
(dispersion, correlation, volatility, volume, trend agreement) is the honest
formulation: it asks "does today resemble a distinct historical state?" without
asserting what that state means or which way it predicts.

The model is a **conditioning layer, never a signal**. It scales and gates risk;
it does not decide direction. That separation is deliberate: an unsupervised
model given directional authority is an unfalsifiable free parameter, and the
project rules forbid a model generating signal values.

The three constraints that make this legitimate
-----------------------------------------------
1. **Causal fitting.** The model is fitted on a trailing window ending at the
   decision session and never sees a later row. :class:`CausalRegimeModel`
   enforces this in its type: ``fit_through`` takes a row index and slices
   before touching the estimator, so a caller cannot pass the full panel by
   accident.
2. **Standardization inside the window.** Feature scaling uses the trailing
   window's own mean and variance. Scaling with full-sample statistics is a
   classic subtle leak -- the scaler encodes the future's distribution.
3. **Label stability.** ``KMeans`` labels are arbitrary integers that permute
   between refits, which would make a "regime change" fire on every refit.
   Clusters are therefore re-indexed by a deterministic, economically meaningful
   key (ascending realized volatility), so label ``0`` always means the calmest
   state and a transition means something.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.cluster import KMeans

from amat.errors import CausalityError, DataIntegrityError

__all__ = ["CausalRegimeModel", "RegimeAssessment", "RegimeState"]

#: Minimum trailing rows before a regime may be asserted. Below this the
#: clustering is fitting noise and the caller is told "unknown" instead.
MIN_FIT_ROWS = 252

#: Fewest clusters that can express "calm vs stressed".
_MIN_REGIMES = 2


@dataclass(frozen=True, slots=True)
class RegimeState:
    """One fitted regime and its measured characteristics.

    Attributes:
        label: Stable index. ``0`` is always the lowest-volatility state.
        share: Fraction of the fitting window assigned to this state.
        mean_volatility: Mean of the volatility feature within the state.
        centroid: Cluster centre in standardized feature space.
    """

    label: int
    share: float
    mean_volatility: float
    centroid: np.ndarray


@dataclass(frozen=True, slots=True)
class RegimeAssessment:
    """The regime verdict for one decision session.

    Attributes:
        label: Current stable regime index, or ``None`` when unknown.
        confidence: ``0..1``. Derived from relative distance to the nearest and
            second-nearest centroids: a point equidistant between two clusters
            is genuinely ambiguous and says so rather than committing.
        changed: Whether the label differs from the previous session's.
        stability: Fraction of the recent lookback sharing the current label.
            A regime asserted for one session is not yet a regime.
        risk_scalar: Multiplier in ``[floor, 1.0]`` applied to position sizing.
        rationale: Human-readable reason, recorded on every trade.
    """

    label: int | None
    confidence: float
    changed: bool
    stability: float
    risk_scalar: float
    rationale: str


class CausalRegimeModel:
    """Trailing-window KMeans over market-condition features.

    The estimator is refitted on a schedule rather than every session, because
    refitting daily makes labels jitter and turns regime change into noise.
    """

    def __init__(
        self,
        *,
        n_regimes: int = 3,
        fit_window: int = 504,
        refit_every: int = 21,
        min_confidence: float = 0.15,
        min_stability_sessions: int = 3,
        risk_floor: float = 0.25,
        unknown_scalar: float = 1.0,
        seed: int = 0,
    ) -> None:
        if n_regimes < _MIN_REGIMES:
            message = f"n_regimes must be at least 2, received {n_regimes}"
            raise DataIntegrityError(message)
        if fit_window < MIN_FIT_ROWS:
            message = f"fit_window must be at least {MIN_FIT_ROWS}, received {fit_window}"
            raise DataIntegrityError(message)
        if not 0.0 < risk_floor <= 1.0:
            message = f"risk_floor must be in (0, 1], received {risk_floor}"
            raise DataIntegrityError(message)
        if not 0.0 < unknown_scalar <= 1.0:
            message = f"unknown_scalar must be in (0, 1], received {unknown_scalar}"
            raise DataIntegrityError(message)

        self.n_regimes = n_regimes
        self.fit_window = fit_window
        self.refit_every = refit_every
        self.min_confidence = min_confidence
        self.min_stability_sessions = min_stability_sessions
        self.risk_floor = risk_floor
        #: Sizing multiplier while no regime has been established. Deliberately
        #: distinct from ``risk_floor``: the floor is the smallest size a
        #: *measured* regime may justify, whereas this applies when there is no
        #: measurement at all. Reusing the floor for both encoded "no estimate"
        #: as "worst estimate" and held every run at quarter size for its whole
        #: ``fit_window``.
        self.unknown_scalar = unknown_scalar
        self.seed = seed

        self._estimator: KMeans | None = None
        self._mean: np.ndarray | None = None
        self._scale: np.ndarray | None = None
        self._order: np.ndarray | None = None
        self._states: tuple[RegimeState, ...] = ()
        self._fitted_at: int | None = None
        self._recent_labels: list[int] = []

    @property
    def states(self) -> tuple[RegimeState, ...]:
        """Fitted regimes, ordered by ascending volatility."""
        return self._states

    def fit_through(self, features: np.ndarray, volatility: np.ndarray, *, index: int) -> None:
        """Fit on the trailing window ending at ``index`` inclusive.

        Args:
            features: ``(T, F)`` per-session condition features.
            volatility: ``(T,)`` volatility series used to order labels stably.
            index: Decision row. Rows after it are never touched.

        Raises:
            CausalityError: if ``index`` is outside the panel.
            DataIntegrityError: if the trailing window is too short.
        """
        if not 0 <= index < features.shape[0]:
            message = f"index {index} outside panel of {features.shape[0]} rows"
            raise CausalityError(message)

        start = max(0, index + 1 - self.fit_window)
        # Slice first, fit second. Nothing downstream can see a later row
        # because a later row is never materialized.
        window = features[start : index + 1]
        window_volatility = volatility[start : index + 1]

        usable = np.isfinite(window).all(axis=1) & np.isfinite(window_volatility)
        window = window[usable]
        window_volatility = window_volatility[usable]

        if window.shape[0] < MIN_FIT_ROWS:
            message = (
                f"regime fit needs {MIN_FIT_ROWS} usable rows, "
                f"window through index {index} has {window.shape[0]}"
            )
            raise DataIntegrityError(message)

        # Standardize with the WINDOW's statistics. Using full-sample statistics
        # would leak the future's distribution into the current scaling.
        mean = window.mean(axis=0)
        scale = window.std(axis=0, ddof=1)
        scale[scale <= 0] = 1.0
        standardized = (window - mean) / scale

        estimator = KMeans(n_clusters=self.n_regimes, n_init=10, random_state=self.seed).fit(
            standardized
        )
        raw_labels = estimator.labels_

        # Re-index clusters by ascending realized volatility so that a label
        # means the same thing across refits. Without this, KMeans' arbitrary
        # integer assignment would make `changed` fire on every refit.
        volatility_by_cluster = np.array(
            [
                window_volatility[raw_labels == cluster].mean()
                if np.any(raw_labels == cluster)
                else np.inf
                for cluster in range(self.n_regimes)
            ]
        )
        order = np.argsort(volatility_by_cluster)
        rank_of = np.empty(self.n_regimes, dtype=int)
        rank_of[order] = np.arange(self.n_regimes)

        self._estimator = estimator
        self._mean = mean
        self._scale = scale
        self._order = rank_of
        self._fitted_at = index
        self._states = tuple(
            RegimeState(
                label=int(rank_of[cluster]),
                share=float(np.mean(raw_labels == cluster)),
                mean_volatility=float(volatility_by_cluster[cluster]),
                centroid=estimator.cluster_centers_[cluster],
            )
            for cluster in range(self.n_regimes)
        )

    def needs_refit(self, index: int) -> bool:
        """Whether the estimator is stale at ``index``."""
        if self._fitted_at is None:
            return True
        return index - self._fitted_at >= self.refit_every

    def assess(self, row: np.ndarray) -> RegimeAssessment:
        """Classify one session's features into a stable regime.

        A low-confidence or unstable classification deliberately produces a
        *reduced* risk scalar rather than a confident label: not knowing the
        regime is itself a reason to size down.
        """
        if self._estimator is None or self._mean is None or self._scale is None:
            return self._unknown("regime model not yet fitted")
        if not np.all(np.isfinite(row)):
            return self._unknown("context features incomplete")

        standardized = (row - self._mean) / self._scale
        distances = np.linalg.norm(self._estimator.cluster_centers_ - standardized, axis=1)
        nearest = int(np.argmin(distances))
        if self._order is None:  # pragma: no cover - set together with _estimator
            return self._unknown("regime label ordering missing")
        label = int(self._order[nearest])

        ordered = np.sort(distances)
        if len(ordered) > 1 and (ordered[0] + ordered[1]) > 0:
            confidence = float((ordered[1] - ordered[0]) / (ordered[0] + ordered[1]))
        else:  # pragma: no cover - n_regimes >= 2 is enforced in __init__
            confidence = 0.0

        previous = self._recent_labels[-1] if self._recent_labels else None
        changed = previous is not None and previous != label

        # Stability is measured over PRIOR observations only, and is withheld
        # until there are enough of them. Including the label just produced --
        # and counting a one-session history as fully stable -- reported
        # stability 1.0 from a single observation, the opposite of the intent.
        history = self._recent_labels[-self.min_stability_sessions :]
        stability = (
            float(np.mean([value == label for value in history]))
            if len(history) >= self.min_stability_sessions
            else 0.0
        )

        return RegimeAssessment(
            label=label,
            confidence=confidence,
            changed=changed,
            stability=stability,
            risk_scalar=self._risk_scalar(label, confidence, stability, changed=changed),
            rationale=(
                f"regime {label} (0=calmest of {self.n_regimes}), "
                f"confidence {confidence:.2f}, stability {stability:.2f}"
                + (", CHANGED" if changed else "")
            ),
        )

    def _risk_scalar(
        self, label: int, confidence: float, stability: float, *, changed: bool
    ) -> float:
        """Translate a regime verdict into a sizing multiplier.

        Only ever reduces risk. A regime model that could *increase* exposure
        would be acting as a signal, which is the thing this layer must not be.
        """
        scalar = 1.0

        # Higher-volatility regimes take proportionally less risk. Label order is
        # volatility-ascending, so this is well defined without naming regimes.
        if self.n_regimes > 1:
            scalar *= 1.0 - 0.5 * (label / (self.n_regimes - 1))

        if confidence < self.min_confidence:
            scalar *= 0.5
        if stability < 1.0:
            scalar *= 0.5 + 0.5 * stability
        if changed:
            # A fresh transition is the least reliable moment to hold full size.
            scalar *= 0.5

        return float(max(self.risk_floor, min(1.0, scalar)))

    def observe(self, assessment: RegimeAssessment) -> None:
        """Commit an assessment to the label history.

        Separated from :meth:`assess` so that classification is a pure query.
        Mutating history inside ``assess`` made it non-idempotent: calling it
        twice on the same feature row returned rising stability and a changing
        risk scalar, so a diagnostic re-read silently rewrote the risk state it
        was reporting on. The session loop calls ``assess`` then ``observe``
        exactly once per session.
        """
        if assessment.label is None:
            return
        self._recent_labels.append(assessment.label)
        if len(self._recent_labels) > self.min_stability_sessions * 4:
            self._recent_labels.pop(0)

    def unknown(self, reason: str) -> RegimeAssessment:
        """An explicit "regime not established" verdict, sized at the floor.

        Public because callers legitimately need it during warm-up, and reaching
        into a private helper to express "I don't know yet" would be worse.
        """
        return self._unknown(reason)

    def _unknown(self, reason: str) -> RegimeAssessment:
        return RegimeAssessment(
            label=None,
            confidence=0.0,
            changed=False,
            stability=0.0,
            risk_scalar=self.unknown_scalar,
            rationale=f"regime unknown: {reason}; sizing at the unknown-regime scalar",
        )
