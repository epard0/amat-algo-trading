"""Chronological entry forecasts, opportunity-cost checks and bounded deferral."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from amat.backtest.panel import MarketPanel
from autoresearch.contracts import Program
from autoresearch.entry_labels import EntryOutcomes, entry_outcomes
from autoresearch.registry import describe, raw_values
from autoresearch.specs import EntryModelSpec, FeatureSpec

FEATURES = (
    FeatureSpec(kind="normalized_momentum", lookback=5),
    FeatureSpec(kind="efficiency", lookback=21),
    FeatureSpec(kind="normalized_momentum", lookback=63),
    FeatureSpec(kind="volatility_shock", lookback=126, secondary_lookback=21),
    FeatureSpec(kind="volume_ratio", lookback=20),
    FeatureSpec(kind="close_location", lookback=5),
    FeatureSpec(kind="opening_gap", lookback=20),
    FeatureSpec(kind="window_markout", lookback=5),
    FeatureSpec(kind="illiquidity", lookback=20),
)
PREDICTION_NAMES = ("net_return", "adverse_excursion", "deferral_advantage", "deferral_q25")
REGIMES = ("range", "trend", "stress")
VALIDATION_FOLDS = 3
MIN_TRAINING_DATES = 40
MIN_POSITIVE_FOLDS = 2
MIN_VALIDATION_DATES = 5
TREND_EFFICIENCY = 0.2
TIMING_HEAD = PREDICTION_NAMES.index("deferral_advantage")
QUANTILE_HEAD = PREDICTION_NAMES.index("deferral_q25")
RECENT_SESSIONS = 63
MODEL_VERSION = "entry-quality/1"


def forecast_key(spec: EntryModelSpec) -> tuple[int, ...]:
    """Policy-only ablations share forecasts, never change their training inputs."""
    return (
        spec.horizon,
        spec.training_sessions,
        spec.recency_half_life,
        spec.refit_sessions,
        spec.min_training_events,
    )


def entry_features(panel: MarketPanel) -> tuple[np.ndarray, np.ndarray]:
    features = np.stack([raw_values(panel, spec) for spec in FEATURES], axis=-1)
    # These are causal observed states, not forecasts of the next regime.
    regime = np.where(features[:, :, 1] >= TREND_EFFICIENCY, 1, 0)
    regime[features[:, :, 3] > np.log(1.5)] = 2
    return features, regime


@dataclass(frozen=True)
class ForecastBlock:
    start: int
    stop: int
    predictions: np.ndarray
    feature_z: np.ndarray
    fits: list[dict[str, Any]]


@dataclass(frozen=True)
class EntryForecast:
    features: np.ndarray
    regimes: np.ndarray
    opportunities: np.ndarray
    outcomes: EntryOutcomes
    blocks: tuple[tuple[ForecastBlock, tuple[ForecastBlock, ...]], ...]


class _Fitter:
    def __init__(
        self,
        panel: MarketPanel,
        program: Program,
        spec: EntryModelSpec,
        *,
        features: np.ndarray,
        opportunities: np.ndarray,
        outcomes: EntryOutcomes,
    ) -> None:
        self.panel, self.program, self.spec = panel, program, spec
        self.features, self.opportunities = features, opportunities
        self.targets = (
            outcomes.net_return,
            outcomes.adverse_excursion,
            outcomes.deferral_advantage,
            outcomes.deferral_advantage,
        )
        self.offsets = (spec.horizon + 1, 5, 2, 2)
        self.cache: dict[int, tuple[list[Any], list[dict[str, Any]], np.ndarray, np.ndarray]] = {}

    def fit(self, start: int) -> tuple[list[Any], list[dict[str, Any]], np.ndarray, np.ndarray]:
        if start in self.cache:
            return self.cache[start]
        models: list[Any] = []
        records: list[dict[str, Any]] = []
        center = np.zeros(self.features.shape[-1])
        scale = np.ones_like(center)
        for head, (target, offset) in enumerate(zip(self.targets, self.offsets, strict=True)):
            stop = max(0, start - offset - self.program.evaluation.embargo_sessions)
            lower = max(0, stop - self.spec.training_sessions)
            x = self.features[lower:stop]
            valid = (
                self.opportunities[lower:stop]
                & np.isfinite(target[lower:stop])
                & np.isfinite(x).all(axis=-1)
            )
            row, _ = np.nonzero(valid)
            dates = np.unique(row)
            record: dict[str, Any] = {
                "head": PREDICTION_NAMES[head],
                "events": len(row),
                "dates": len(dates),
                "training_start_index": lower,
                "training_stop_exclusive": stop,
                "prediction_start_index": start,
                "label_offset_sessions": offset,
                "latest_label_session": str(self.panel.sessions[lower + int(row.max()) + offset])
                if len(row)
                else None,
                "prediction_start_session": str(self.panel.sessions[start]),
            }
            if len(row) < self.spec.min_training_events or len(dates) < MIN_TRAINING_DATES:
                records.append({**record, "status": "insufficient_training"})
                models.append(None)
                continue
            # Equal aggregate date weight before recency weighting; simultaneous
            # asset opportunities are not independent replications of that date.
            count = np.bincount(row, minlength=len(x))
            weight = np.exp2(-(stop - 1 - lower - row) / self.spec.recency_half_life) / count[row]
            weight *= len(weight) / weight.sum()
            model = HistGradientBoostingRegressor(
                loss="quantile" if head == QUANTILE_HEAD else "squared_error",
                quantile=0.25 if head == QUANTILE_HEAD else None,
                max_iter=80,
                max_leaf_nodes=7,
                min_samples_leaf=20,
                learning_rate=0.05,
                l2_regularization=10.0,
                early_stopping=False,
                random_state=20260914,
            )
            model.fit(x[valid], target[lower:stop][valid] * 10000, sample_weight=weight)
            models.append(model)
            records.append({**record, "status": "fitted", "trees": int(model.n_iter_)})
            if head == TIMING_HEAD:
                center = np.median(x[valid], axis=0)
                q = np.quantile(x[valid], [0.25, 0.75], axis=0)
                scale = np.maximum((q[1] - q[0]) / 1.349, 1e-6)
        result = models, records, center, scale
        self.cache[start] = result
        return result

    def predict(self, start: int, stop: int) -> ForecastBlock:
        models, records, center, scale = self.fit(start)
        x = self.features[start:stop]
        valid = np.isfinite(x).all(axis=-1)
        output = np.full((*valid.shape, len(PREDICTION_NAMES)), np.nan)
        for head, model in enumerate(models):
            if model is not None and valid.any():
                output[:, :, head][valid] = model.predict(x[valid])
        # Quantile models may cross the mean; disagreement is used as a penalty,
        # never described as a calibrated confidence interval.
        z = np.max(np.abs((x - center) / scale), axis=-1)
        return ForecastBlock(start, stop, output, z, records)


def build_entry_forecast(
    panel: MarketPanel,
    program: Program,
    spec: EntryModelSpec,
    opportunities: np.ndarray,
) -> EntryForecast:
    if opportunities.shape != panel.adjusted_closes.shape or opportunities.dtype != np.dtype(bool):
        raise ValueError("entry opportunities must be an aligned boolean matrix")
    features, regimes = entry_features(panel)
    outcomes = entry_outcomes(panel, spec.horizon)
    fitter = _Fitter(
        panel, program, spec, features=features, opportunities=opportunities, outcomes=outcomes
    )
    first = panel.sessions.index(program.measured_start)
    width = program.evaluation.inner_validation_sessions
    blocks = []
    # Explicit limits also make repeated fits practical on hosts with many cores.
    with threadpool_limits(limits=1):
        for start in range(first, len(panel), spec.refit_sessions):
            block = fitter.predict(start, min(start + spec.refit_sessions, len(panel)))
            # The policy's timing label matures at t+2. Every fold label must
            # precede the outer prediction and the configured embargo.
            end = max(0, start - 2 - program.evaluation.embargo_sessions)
            validation = tuple(
                fitter.predict(a, a + width)
                for a in range(end - VALIDATION_FOLDS * width, end, width)
                if a >= 0
            )
            blocks.append((block, validation))
    return EntryForecast(features, regimes, opportunities.copy(), outcomes, tuple(blocks))


def timing_utility(predictions: np.ndarray, spec: EntryModelSpec) -> np.ndarray:
    """Basis points of timing benefit, adverse-risk preference and waiting cost."""
    slow, adverse, advantage, lower = (predictions[:, :, i] for i in range(4))
    uncertainty = np.maximum(0, advantage - lower)
    opportunity_cost = np.maximum(slow, 0) / spec.horizon
    return np.asarray(
        advantage
        + spec.adverse_weight * np.maximum(adverse, 0)
        - spec.uncertainty_weight * uncertainty
        - opportunity_cost
    )


def bounded_deferrals(warnings: np.ndarray, maximum: int) -> np.ndarray:
    """A continuously raised warning must periodically restore baseline entry size."""
    output = np.zeros_like(warnings, dtype=bool)
    age = np.zeros(warnings.shape[1], dtype=int)
    for row, warning in enumerate(warnings):
        output[row] = warning & (age < maximum)
        age = np.where(output[row], age + 1, 0)
    return output


def policy_evidence(
    forecast: EntryForecast,
    validation: tuple[ForecastBlock, ...],
    spec: EntryModelSpec,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Score waiting against entering, including every missed favorable move."""
    permitted = np.zeros(len(REGIMES), dtype=bool)
    records = []
    for group in range(len(REGIMES)):
        folds: list[dict[str, Any]] = []
        for block in validation:
            a, b = block.start, block.stop
            warning = timing_utility(block.predictions, spec) > spec.minimum_defer_bps
            warning &= np.isfinite(block.predictions).all(axis=-1) & (
                block.feature_z <= spec.max_feature_z
            )
            warning = bounded_deferrals(warning, spec.max_deferral_sessions)
            valid = (
                warning
                & forecast.opportunities[a:b]
                & np.isfinite(forecast.outcomes.deferral_advantage[a:b])
            )
            if spec.regime_validation:
                valid &= forecast.regimes[a:b] == group
            gain = forecast.outcomes.deferral_advantage[a:b] * 10000
            counts = valid.sum(axis=1)
            by_date = np.divide(
                np.where(valid, gain, 0).sum(axis=1),
                counts,
                out=np.zeros(len(valid)),
                where=counts > 0,
            )[counts > 0]
            sample = gain[valid]
            enough = (
                len(sample) >= spec.min_validation_events and len(by_date) >= MIN_VALIDATION_DATES
            )
            mean = float(by_date.mean()) if len(by_date) else 0.0
            folds.append(
                {
                    "start_index": a,
                    "stop_index": b,
                    "events": int(valid.sum()),
                    "dates": len(by_date),
                    "sufficient": enough,
                    "gain_bps": mean,
                    "avoided_bad_entry_bps": float(np.maximum(sample, 0).sum()),
                    "missed_favorable_entry_bps": float(-np.minimum(sample, 0).sum()),
                    "training": block.fits,
                }
            )
        sufficient = [f for f in folds if f["sufficient"]]
        permitted[group] = (
            len(sufficient) >= MIN_POSITIVE_FOLDS
            and sum(f["gain_bps"] > 0 for f in sufficient) >= MIN_POSITIVE_FOLDS
            and float(np.mean([f["gain_bps"] for f in sufficient])) > spec.minimum_defer_bps
        )
        records.append(
            {"regime": REGIMES[group], "permitted": bool(permitted[group]), "folds": folds}
        )
    return permitted, records


@dataclass(frozen=True)
class EntryModelTrace:
    increase_multipliers: np.ndarray
    reasons: np.ndarray
    regimes: np.ndarray
    predictions: np.ndarray
    utility_bps: np.ndarray
    feature_z: np.ndarray
    raw_warnings: np.ndarray
    recent_gain_bps: np.ndarray
    fits: list[dict[str, Any]]
    features: np.ndarray

    def report(self, panel: MarketPanel, first: int) -> dict[str, Any]:
        def number(value: float) -> float | None:
            return float(value) if np.isfinite(value) else None

        return {
            "model_version": MODEL_VERSION,
            "execution_authority": "none",
            "interpretation": "entry-quality research with window/bar adverse-selection proxies",
            "quantile_interpretation": (
                "conditional lower-quartile estimate; not a calibrated confidence interval"
            ),
            "feature_recipes": [describe(spec) for spec in FEATURES],
            "prediction_units": "basis_points",
            "prediction_names": PREDICTION_NAMES,
            "reason_counts": {
                str(r): int(np.sum(self.reasons[first:] == r))
                for r in np.unique(self.reasons[first:])
            },
            "reduced_asset_sessions": int(np.sum(self.increase_multipliers[first:] < 1)),
            "report_start_session": str(panel.sessions[first]),
            "report_end_session": str(panel.sessions[-1]),
            "fits": self.fits,
            "latest": [
                {
                    "symbol": str(symbol),
                    "regime": REGIMES[self.regimes[-1, c]],
                    "entry_multiplier": float(self.increase_multipliers[-1, c]),
                    "reason": str(self.reasons[-1, c]),
                    "utility_bps": number(self.utility_bps[-1, c]),
                    "predictions": {
                        name: number(self.predictions[-1, c, i])
                        for i, name in enumerate(PREDICTION_NAMES)
                    },
                }
                for c, symbol in enumerate(panel.symbols)
            ],
        }


def entry_diagnostics(
    forecast: EntryForecast,
    trace: EntryModelTrace,
    first: int,
    stop: int,
    spec: EntryModelSpec,
) -> dict[str, Any]:
    """Matured counterfactual price diagnostics, kept separate from portfolio P&L."""
    targets = (
        forecast.outcomes.net_return,
        forecast.outcomes.adverse_excursion,
        forecast.outcomes.deferral_advantage,
        forecast.outcomes.deferral_advantage,
    )
    offsets = (spec.horizon + 1, 5, 2, 2)
    heads = {}
    for head, (target, offset) in enumerate(zip(targets, offsets, strict=True)):
        end = max(first, stop - offset)
        prediction = trace.predictions[first:end, :, head]
        actual = target[first:end] * 10000
        valid = forecast.opportunities[first:end] & np.isfinite(prediction) & np.isfinite(actual)
        error = prediction[valid] - actual[valid]
        heads[PREDICTION_NAMES[head]] = {
            "events": int(valid.sum()),
            "mean_prediction_bps": float(prediction[valid].mean()) if len(error) else None,
            "mean_outcome_bps": float(actual[valid].mean()) if len(error) else None,
            "mean_absolute_error_bps": float(np.abs(error).mean()) if len(error) else None,
            "fraction_outcome_below_prediction": float((error > 0).mean()) if len(error) else None,
        }
    states = []
    end = max(first, stop - 2)
    target = forecast.outcomes.deferral_advantage[first:end] * 10000
    for group, name in enumerate(REGIMES):
        opportunities = (
            forecast.opportunities[first:end]
            & np.isfinite(target)
            & (forecast.regimes[first:end] == group)
        )
        reduced = opportunities & (trace.increase_multipliers[first:end] < 1)
        benefit = target[reduced]
        counts = reduced.sum(axis=1)
        daily = np.divide(
            np.where(reduced, target, 0).sum(axis=1),
            counts,
            out=np.zeros(len(counts)),
            where=counts > 0,
        )[counts > 0]
        states.append(
            {
                "regime": name,
                "baseline_entry_opportunities": int(opportunities.sum()),
                "reduced_opportunities": int(reduced.sum()),
                "dates": len(daily),
                "date_weighted_deferral_advantage_bps": float(daily.mean()) if len(daily) else None,
                "mean_avoided_bad_entry_bps": float(np.maximum(benefit, 0).mean())
                if len(benefit)
                else None,
                "mean_missed_favorable_entry_bps": float(-np.minimum(benefit, 0).mean())
                if len(benefit)
                else None,
            }
        )
    return {
        "interpretation": "matured price counterfactuals on baseline buys; not portfolio P&L",
        "prediction_metrics_weighting": "per asset-entry event; correlated observations",
        "first_index": first,
        "stop_index": stop,
        "heads": heads,
        "regimes": states,
    }


def apply_entry_policy(
    forecast: EntryForecast,
    spec: EntryModelSpec,
    program: Program,
) -> EntryModelTrace:
    shape = forecast.regimes.shape
    predictions = np.full((*shape, len(PREDICTION_NAMES)), np.nan)
    utility, feature_z, health = (np.full(shape, np.nan) for _ in range(3))
    multipliers = np.ones(shape)
    reasons = np.full(shape, "cold_start", dtype="<U32")
    warnings = np.zeros(shape, dtype=bool)
    age = np.zeros(shape[1], dtype=int)
    fits = []
    for block, validation in forecast.blocks:
        a, b = block.start, block.stop
        predictions[a:b] = block.predictions
        utility[a:b] = timing_utility(block.predictions, spec)
        feature_z[a:b] = block.feature_z
        permitted, evidence = policy_evidence(forecast, validation, spec)
        fits.append(
            {"start_index": a, "stop_index": b, "training": block.fits, "policy_evidence": evidence}
        )
        for row in range(a, b):
            complete = np.isfinite(predictions[row]).all(axis=-1)
            familiar = np.isfinite(feature_z[row]) & (feature_z[row] <= spec.max_feature_z)
            warning = complete & familiar & (utility[row] > spec.minimum_defer_bps)
            warnings[row] = warning
            armed = permitted[forecast.regimes[row]]
            reason = np.full(shape[1], "no_timing_edge", dtype="<U32")
            reason[~complete] = "insufficient_model"
            reason[complete & ~familiar] = "feature_drift"
            reason[warning & ~armed] = "insufficient_regime_evidence"
            reduce = warning & armed
            if spec.recent_health:
                end = max(0, row - 2 - program.evaluation.embargo_sessions)
                start = max(0, end - RECENT_SESSIONS)
                sample_mask = (
                    warnings[start:end]
                    & forecast.opportunities[start:end]
                    & np.isfinite(forecast.outcomes.deferral_advantage[start:end])
                )
                for group in range(len(REGIMES)):
                    use = sample_mask.copy()
                    if spec.regime_validation:
                        use &= forecast.regimes[start:end] == group
                    counts = use.sum(axis=1)
                    selected = forecast.outcomes.deferral_advantage[start:end] * 10000
                    by_date = np.divide(
                        np.where(use, selected, 0).sum(axis=1),
                        counts,
                        out=np.zeros(len(use)),
                        where=counts > 0,
                    )[counts > 0]
                    columns = forecast.regimes[row] == group
                    if (
                        use.sum() >= spec.min_validation_events
                        and len(by_date) >= MIN_VALIDATION_DATES
                    ):
                        health[row, columns] = float(by_date.mean())
                        if by_date.mean() <= 0:
                            reason[reduce & columns] = "recent_opportunity_cost"
                            reduce[columns] = False
            exhausted = reduce & (age >= spec.max_deferral_sessions)
            reason[exhausted] = "deferral_budget"
            reduce &= ~exhausted
            multipliers[row, reduce] = 1 - spec.max_entry_reduction
            reason[reduce] = "entry_reduced"
            age = np.where(reduce, age + 1, 0)
            reasons[row] = reason
    return EntryModelTrace(
        multipliers,
        reasons,
        forecast.regimes,
        predictions,
        utility,
        feature_z,
        warnings,
        health,
        fits,
        forecast.features,
    )
